"""BaseLoader: shared batch/validate/upsert/audit logic for all source formats.

Format-specific loaders (CSV/JSONL/Parquet) only implement `iter_batches`,
which yields lists of raw dicts. Everything else - technical validation,
within-batch dedup, upsert, rejection logging, metrics, structured logging -
lives here once so it isn't duplicated per format.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg2.extensions
import psycopg2.extras
from pydantic import ValidationError

from ingestion.audit import RunMetrics, record_rejected
from ingestion.logging_config import get_logger
from ingestion.registry import SourceSpec

logger = get_logger(__name__)


class BaseLoader(ABC):
    def __init__(self, spec: SourceSpec, batch_size: int = 5000):
        self.spec = spec
        self.batch_size = batch_size
        # Populated by iter_batches() implementations for rows that could not
        # even be parsed into a dict (e.g. malformed JSON lines) - these skip
        # pydantic validation entirely and go straight to rejected_records.
        self.parse_errors: list[dict[str, Any]] = []

    @abstractmethod
    def iter_batches(self, path: Path) -> Iterator[list[dict[str, Any]]]:
        """Yield successive batches of raw row dicts from the source file.

        Implementations may append to `self.parse_errors` for rows that
        fail to parse structurally and skip them from the yielded batch.
        """

    def load(
        self,
        conn: psycopg2.extensions.connection,
        path: Path,
        metrics: RunMetrics,
    ) -> None:
        for batch_number, batch in enumerate(self.iter_batches(path), start=1):
            for row in batch:
                row["_source_file"] = path.name
                row["_run_id"] = metrics.run_id
            start = time.monotonic()
            self._load_batch(conn, batch, metrics, batch_number)
            duration_ms = round((time.monotonic() - start) * 1000, 1)
            logger.info(
                "ingestion_batch_complete",
                run_id=metrics.run_id,
                source_name=self.spec.name,
                batch_number=batch_number,
                rows_processed=len(batch),
                rows_inserted=metrics.rows_inserted,
                rows_updated=metrics.rows_updated,
                rows_rejected=metrics.rows_rejected,
                duration_ms=duration_ms,
            )

    def _load_batch(
        self,
        conn: psycopg2.extensions.connection,
        batch: list[dict[str, Any]],
        metrics: RunMetrics,
        batch_number: int,
    ) -> None:
        parse_error_rejects = self.parse_errors
        self.parse_errors = []
        metrics.rows_received += len(batch) + len(parse_error_rejects)

        valid_rows, rejects = self._validate(batch)
        rejects.extend(parse_error_rejects)
        deduped_rows, dedup_rejects = self._deduplicate(valid_rows)
        rejects.extend(dedup_rejects)

        try:
            inserted = updated = 0
            with conn:  # transaction per batch: commits on success, rolls back on exception
                if deduped_rows:
                    inserted, updated = self._upsert(conn, deduped_rows)
                for reject in rejects:
                    record_rejected(
                        conn,
                        run_id=metrics.run_id,
                        source_name=self.spec.name,
                        source_record_identifier=reject["identifier"],
                        reason=reject["reason"],
                        raw_payload=reject["payload"],
                    )
            # Only reflected in metrics once the transaction has actually
            # committed - if anything above raised, none of this landed.
            metrics.rows_inserted += inserted
            metrics.rows_updated += updated
            metrics.rows_rejected += len(rejects)
        except Exception as exc:  # noqa: BLE001 - batch-level failure, not row-level
            logger.error(
                "ingestion_batch_failed",
                run_id=metrics.run_id,
                source_name=self.spec.name,
                batch_number=batch_number,
                error=str(exc),
            )
            # The whole batch (deduped_rows + rejects) failed to land; count it
            # as rejected so the audit totals still reconcile with rows_received.
            metrics.rows_rejected += len(deduped_rows) + len(rejects)
            with conn:
                for row in deduped_rows:
                    record_rejected(
                        conn,
                        run_id=metrics.run_id,
                        source_name=self.spec.name,
                        source_record_identifier=str(row.get(self.spec.primary_key)),
                        reason=f"batch upsert failed: {exc}",
                        raw_payload=row,
                    )
                for reject in rejects:
                    record_rejected(
                        conn,
                        run_id=metrics.run_id,
                        source_name=self.spec.name,
                        source_record_identifier=reject["identifier"],
                        reason=reject["reason"],
                        raw_payload=reject["payload"],
                    )

    def _validate(
        self, batch: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        valid: list[dict[str, Any]] = []
        rejects: list[dict[str, Any]] = []
        for row in batch:
            try:
                self.spec.technical_model.model_validate(row)
            except ValidationError as exc:
                identifier = row.get(self.spec.primary_key)
                rejects.append(
                    {
                        "identifier": str(identifier) if identifier is not None else None,
                        "reason": "; ".join(e["msg"] for e in exc.errors()),
                        "payload": row,
                    }
                )
                continue
            valid.append(row)
        return valid, rejects

    def _deduplicate(
        self, rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Resolve duplicate business keys within a single batch.

        Mutable entities (updated_at_field set): keep the row with the
        latest updated_at. Immutable events: keep the last occurrence in
        file order. The losing rows are logged as rejected (duplicate),
        never silently dropped.
        """
        pk = self.spec.primary_key
        best: dict[str, dict[str, Any]] = {}
        losers: list[dict[str, Any]] = []

        for row in rows:
            key = row.get(pk)
            if key not in best:
                best[key] = row
                continue

            incumbent = best[key]
            if self.spec.updated_at_field:
                incumbent_ts = incumbent.get(self.spec.updated_at_field) or ""
                challenger_ts = row.get(self.spec.updated_at_field) or ""
                if str(challenger_ts) >= str(incumbent_ts):
                    losers.append(incumbent)
                    best[key] = row
                else:
                    losers.append(row)
            else:
                losers.append(incumbent)
                best[key] = row

        dedup_rejects = [
            {
                "identifier": str(row.get(pk)),
                "reason": "duplicate business key within source file",
                "payload": row,
            }
            for row in losers
        ]
        return list(best.values()), dedup_rejects

    def _upsert(
        self, conn: psycopg2.extensions.connection, rows: list[dict[str, Any]]
    ) -> tuple[int, int]:
        columns = list(self.spec.columns) + ["_source_file", "_run_id", "_ingested_at"]
        pk = self.spec.primary_key
        update_columns = [c for c in self.spec.columns if c != pk]

        ingested_at = datetime.now(UTC)
        values = [
            tuple(row.get(col) for col in self.spec.columns)
            + (row.get("_source_file"), row.get("_run_id"), ingested_at)
            for row in rows
        ]

        set_clause = ", ".join(f"{col} = EXCLUDED.{col}" for col in update_columns)
        set_clause += ", _source_file = EXCLUDED._source_file, _run_id = EXCLUDED._run_id, _ingested_at = EXCLUDED._ingested_at"

        sql = f"""
            INSERT INTO {self.spec.table} ({", ".join(columns)})
            VALUES %s
            ON CONFLICT ({pk}) DO UPDATE SET {set_clause}
            RETURNING (xmax = 0) AS is_insert
        """

        with conn.cursor() as cur:
            results = psycopg2.extras.execute_values(cur, sql, values, fetch=True)

        inserted = sum(1 for r in results if r[0])
        updated = len(results) - inserted
        return inserted, updated

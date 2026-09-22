"""Ingestion run tracking and rejected-record logging.

Every pipeline execution gets one row in monitoring.ingestion_runs, created
in RUNNING state before any data is touched and updated to a terminal state
(SUCCESS / PARTIAL_SUCCESS / FAILED) when the run finishes - including on
unhandled exceptions, so failures are never silently unrecorded.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg2.extensions
import psycopg2.extras

TERMINAL_STATUSES = {"SUCCESS", "PARTIAL_SUCCESS", "FAILED"}


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:16]}"


@dataclass
class RunMetrics:
    run_id: str
    pipeline_name: str
    source_name: str
    source_file: str | None = None
    rows_received: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_rejected: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def start_run(
    conn: psycopg2.extensions.connection,
    *,
    pipeline_name: str,
    source_name: str,
    source_file: str | None,
) -> RunMetrics:
    """Insert a RUNNING audit row and commit immediately.

    Committed on its own (not inside the caller's batch transaction) so the
    RUNNING row is visible even if the run subsequently crashes hard.
    """
    metrics = RunMetrics(
        run_id=new_run_id(),
        pipeline_name=pipeline_name,
        source_name=source_name,
        source_file=source_file,
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO monitoring.ingestion_runs
                (run_id, pipeline_name, source_name, source_file, started_at, status)
            VALUES (%s, %s, %s, %s, %s, 'RUNNING')
            """,
            (metrics.run_id, pipeline_name, source_name, source_file, metrics.started_at),
        )
    conn.commit()
    return metrics


def finish_run(
    conn: psycopg2.extensions.connection,
    metrics: RunMetrics,
    *,
    status: str,
    error_message: str | None = None,
) -> None:
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"invalid terminal status: {status}")

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE monitoring.ingestion_runs
            SET finished_at = %s,
                status = %s,
                rows_received = %s,
                rows_inserted = %s,
                rows_updated = %s,
                rows_rejected = %s,
                error_message = %s
            WHERE run_id = %s
            """,
            (
                datetime.now(UTC),
                status,
                metrics.rows_received,
                metrics.rows_inserted,
                metrics.rows_updated,
                metrics.rows_rejected,
                error_message,
                metrics.run_id,
            ),
        )
    conn.commit()


def record_rejected(
    conn: psycopg2.extensions.connection,
    *,
    run_id: str,
    source_name: str,
    source_record_identifier: str | None,
    reason: str,
    raw_payload: Any,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO monitoring.rejected_records
                (run_id, source_name, source_record_identifier, reason, raw_payload)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                run_id,
                source_name,
                source_record_identifier,
                reason,
                psycopg2.extras.Json(raw_payload, dumps=lambda v: json.dumps(v, default=str)),
            ),
        )

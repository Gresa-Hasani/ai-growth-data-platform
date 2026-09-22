"""Ingestion CLI: loads generated source files into the raw schema.

Usage:
    python -m ingestion.pipeline --source users
    python -m ingestion.pipeline --source all
    python -m ingestion.pipeline --source product_events --batch-size 5000
    python -m ingestion.pipeline --report
"""
from __future__ import annotations

import json
from pathlib import Path

import typer
from dotenv import load_dotenv

from ingestion.audit import finish_run, start_run
from ingestion.db import get_connection
from ingestion.loaders.base import BaseLoader
from ingestion.loaders.csv_loader import CSVLoader
from ingestion.loaders.jsonl_loader import JSONLLoader
from ingestion.loaders.parquet_loader import ParquetLoader
from ingestion.logging_config import configure_logging, get_logger
from ingestion.registry import INGESTION_ORDER, SOURCES, SourceSpec

load_dotenv()
configure_logging()
logger = get_logger(__name__)

app = typer.Typer(add_completion=False)

LOADER_CLASSES: dict[str, type[BaseLoader]] = {
    "csv": CSVLoader,
    "jsonl": JSONLLoader,
    "parquet": ParquetLoader,
}


def _build_loader(spec: SourceSpec, batch_size: int) -> BaseLoader:
    loader_cls = LOADER_CLASSES[spec.loader_type]
    return loader_cls(spec, batch_size=batch_size)


def ingest_source(spec: SourceSpec, data_dir: Path, batch_size: int) -> dict:
    """Ingest one source file. Returns a summary dict; never raises for
    row-level problems (those become rejected_records), but DOES raise if
    the run cannot proceed at all (e.g. missing file) - after recording a
    FAILED audit row.
    """
    path = data_dir / spec.file_name

    with get_connection() as conn:
        metrics = start_run(
            conn,
            pipeline_name="ingestion.pipeline",
            source_name=spec.name,
            source_file=spec.file_name,
        )

        if not path.exists():
            error_message = f"source file not found: {path}"
            finish_run(conn, metrics, status="FAILED", error_message=error_message)
            logger.error(
                "ingestion_run_failed",
                run_id=metrics.run_id,
                source_name=spec.name,
                error=error_message,
            )
            raise FileNotFoundError(error_message)

        loader = _build_loader(spec, batch_size)
        try:
            loader.load(conn, path, metrics)
        except Exception as exc:
            finish_run(conn, metrics, status="FAILED", error_message=str(exc))
            logger.error(
                "ingestion_run_failed",
                run_id=metrics.run_id,
                source_name=spec.name,
                error=str(exc),
            )
            raise

        if metrics.rows_received == 0 or metrics.rows_rejected == 0:
            status = "SUCCESS"
        elif metrics.rows_inserted + metrics.rows_updated > 0:
            status = "PARTIAL_SUCCESS"
        else:
            status = "FAILED"

        finish_run(conn, metrics, status=status)

        logger.info(
            "ingestion_run_complete",
            run_id=metrics.run_id,
            source_name=spec.name,
            status=status,
            rows_received=metrics.rows_received,
            rows_inserted=metrics.rows_inserted,
            rows_updated=metrics.rows_updated,
            rows_rejected=metrics.rows_rejected,
        )

        return {
            "source": spec.name,
            "run_id": metrics.run_id,
            "status": status,
            "rows_received": metrics.rows_received,
            "rows_inserted": metrics.rows_inserted,
            "rows_updated": metrics.rows_updated,
            "rows_rejected": metrics.rows_rejected,
        }


def build_quality_report(sources: list[str]) -> list[dict]:
    """Lightweight ingestion observability report (not Phase 7's full
    data-quality framework) - reads back what landed in raw + monitoring."""
    report = []
    with get_connection() as conn:
        for name in sources:
            spec = SOURCES[name]
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {spec.table}")
                row_count = cur.fetchone()[0]

                cur.execute(
                    f"""
                    SELECT {spec.primary_key}, COUNT(*)
                    FROM {spec.table}
                    GROUP BY {spec.primary_key}
                    HAVING COUNT(*) > 1
                    """
                )
                duplicate_keys = len(cur.fetchall())

                cur.execute(
                    f"SELECT COUNT(*) FROM {spec.table} WHERE {spec.primary_key} IS NULL"
                )
                null_critical_ids = cur.fetchone()[0]

                cur.execute(
                    """
                    SELECT
                        COALESCE(SUM(rows_received), 0),
                        COALESCE(SUM(rows_inserted), 0),
                        COALESCE(SUM(rows_updated), 0),
                        COALESCE(SUM(rows_rejected), 0),
                        COUNT(*)
                    FROM monitoring.ingestion_runs
                    WHERE source_name = %s
                    """,
                    (name,),
                )
                rows_received, rows_inserted, rows_updated, rows_rejected, run_count = (
                    cur.fetchone()
                )

            report.append(
                {
                    "source": name,
                    "raw_row_count": row_count,
                    "duplicate_keys_in_raw": duplicate_keys,
                    "null_critical_identifiers": null_critical_ids,
                    "total_runs": run_count,
                    "cumulative_rows_received": rows_received,
                    "cumulative_rows_inserted": rows_inserted,
                    "cumulative_rows_updated": rows_updated,
                    "cumulative_rows_rejected": rows_rejected,
                }
            )
    return report


@app.command()
def main(
    source: str = typer.Option("all", help="Source name, or 'all'."),
    batch_size: int = typer.Option(5000, help="Rows per batch."),
    data_dir: str = typer.Option("data/generated", help="Directory containing source files."),
    report: bool = typer.Option(False, help="Print an ingestion quality report and exit."),
) -> None:
    if report:
        sources = list(INGESTION_ORDER) if source == "all" else [source]
        result = build_quality_report(sources)
        typer.echo(json.dumps(result, indent=2, default=str))
        raise typer.Exit(code=0)

    if source != "all" and source not in SOURCES:
        typer.echo(f"unknown source: {source}. Known sources: {', '.join(SOURCES)}", err=True)
        raise typer.Exit(code=2)

    names = list(INGESTION_ORDER) if source == "all" else [source]
    data_dir_path = Path(data_dir)

    results = []
    had_failure = False
    for name in names:
        spec = SOURCES[name]
        try:
            results.append(ingest_source(spec, data_dir_path, batch_size))
        except Exception as exc:  # noqa: BLE001
            had_failure = True
            results.append({"source": name, "status": "FAILED", "error": str(exc)})

    typer.echo(json.dumps(results, indent=2, default=str))

    if had_failure or any(r.get("status") == "FAILED" for r in results):
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()

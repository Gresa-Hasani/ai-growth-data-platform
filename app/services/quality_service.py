"""Reads from the Phase 7 monitoring tables only - never writes, never
executes a quality check itself (that's scripts/run_quality_checks.py's
job). If the monitoring schema/tables are missing, this raises rather
than returning an empty/healthy-looking result - see
monitoring_tables_available().
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services._shared import clamp_pagination


def monitoring_tables_available(db: Session) -> bool:
    result = db.execute(
        text(
            "SELECT to_regclass('monitoring.quality_runs') IS NOT NULL "
            "AND to_regclass('monitoring.data_quality_results') IS NOT NULL"
        )
    ).scalar_one()
    return bool(result)


def get_latest_run(db: Session) -> dict | None:
    row = db.execute(
        text(
            """
            SELECT run_id, started_at, finished_at, overall_status,
                   total_checks, passed_checks, warned_checks, failed_checks, duration_ms
            FROM monitoring.quality_runs
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
    ).mappings().first()
    if row is None:
        return None
    return get_run_detail(db, row["run_id"])


def list_runs(db: Session, limit: int, offset: int) -> tuple[list[dict], int]:
    limit, offset = clamp_pagination(limit, offset)

    total = db.execute(text("SELECT count(*) FROM monitoring.quality_runs")).scalar_one()

    rows = db.execute(
        text(
            """
            SELECT run_id, started_at, finished_at, overall_status,
                   total_checks, passed_checks, warned_checks, failed_checks, duration_ms
            FROM monitoring.quality_runs
            ORDER BY started_at DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        {"limit": limit, "offset": offset},
    ).mappings().all()
    return [dict(r) for r in rows], total


def get_run_detail(db: Session, run_id: str) -> dict | None:
    run = db.execute(
        text(
            """
            SELECT run_id, started_at, finished_at, overall_status,
                   total_checks, passed_checks, warned_checks, failed_checks, duration_ms
            FROM monitoring.quality_runs
            WHERE run_id = :run_id
            """
        ),
        {"run_id": run_id},
    ).mappings().first()
    if run is None:
        return None

    results = db.execute(
        text(
            """
            SELECT test_name, model_name, category, severity, status,
                   failed_rows, total_rows, failure_rate, error_message
            FROM monitoring.data_quality_results
            WHERE run_id = :run_id
            ORDER BY category, test_name
            """
        ),
        {"run_id": run_id},
    ).mappings().all()

    detail = dict(run)
    detail["results"] = [dict(r) for r in results]
    return detail

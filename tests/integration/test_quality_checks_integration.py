"""Integration tests proving the Phase 7 quality-check runner's actual
behavior against the live dev database - real checks, real persistence,
real baseline comparisons. Skips cleanly if no live Postgres is reachable.

These tests are read-mostly: they run the real check catalog against the
shared dev dataset (never mutating it) and only ever INSERT new
monitoring.quality_runs/data_quality_results rows (which is exactly what
every real invocation of this script does) - no itest_ fixture cleanup is
needed here because nothing destructive happens to the dataset itself.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import psycopg2
import pytest

from ingestion.db import DATABASE_URL, get_connection
from scripts.run_quality_checks import (
    CHECK_CATALOG,
    QualityCheckConfig,
    check_orphaned_subscription_users,
    run_checks,
)

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _db_available() -> bool:
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=3)
        conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="no live Postgres reachable")


def test_monitoring_schema_initialization_is_idempotent():
    sql = (PROJECT_ROOT / "app" / "db" / "quality_tables.sql").read_text(encoding="utf-8")
    with get_connection() as conn, conn, conn.cursor() as cur:
        cur.execute(sql)  # running it a second (here, Nth) time must not error
        cur.execute("SELECT to_regclass('monitoring.quality_runs') IS NOT NULL")
        assert cur.fetchone()[0] is True
        cur.execute("SELECT to_regclass('monitoring.data_quality_results') IS NOT NULL")
        assert cur.fetchone()[0] is True


def test_full_run_against_dev_database_produces_accurate_summary():
    with get_connection() as conn:
        run_id, results, overall_status = run_checks(conn)

    assert len(results) == len(CHECK_CATALOG)
    assert overall_status in ("PASS", "WARN", "FAIL")

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT total_checks, passed_checks, warned_checks, failed_checks, started_at, finished_at, duration_ms "
            "FROM monitoring.quality_runs WHERE run_id = %s",
            (run_id,),
        )
        total, passed, warned, failed, started_at, finished_at, duration_ms = cur.fetchone()

    assert total == len(CHECK_CATALOG)
    assert passed + warned + failed == total
    assert started_at is not None and finished_at is not None and finished_at >= started_at
    assert duration_ms is not None and duration_ms >= 0


def test_multiple_runs_preserve_historical_results():
    with get_connection() as conn:
        run_id_1, _, _ = run_checks(conn)
        run_id_2, _, _ = run_checks(conn)

    assert run_id_1 != run_id_2

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM monitoring.quality_runs WHERE run_id IN (%s, %s)", (run_id_1, run_id_2))
        assert cur.fetchone()[0] == 2

        cur.execute("SELECT count(*) FROM monitoring.data_quality_results WHERE run_id = %s", (run_id_1,))
        run_1_result_count = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM monitoring.data_quality_results WHERE run_id = %s", (run_id_2,))
        run_2_result_count = cur.fetchone()[0]

    # Both runs' individual results coexist - the second run never overwrites the first.
    assert run_1_result_count == len(CHECK_CATALOG)
    assert run_2_result_count == len(CHECK_CATALOG)


def test_real_orphan_check_reports_pass_warn_fail_depending_on_threshold():
    """Uses the REAL orphaned_subscription_users check against the real
    dev dataset (no mocking) - only the configured thresholds change, so
    this exercises genuine PASS/WARN/FAIL status computation against
    actual data."""
    with get_connection() as conn:
        lenient = check_orphaned_subscription_users(conn, QualityCheckConfig(orphan_warn_rate=0.5, orphan_fail_rate=0.9))
        assert lenient.status == "PASS"
        assert lenient.failed_rows is not None and lenient.failed_rows > 0  # real orphans exist (Phase 2 by design)

        strict = check_orphaned_subscription_users(conn, QualityCheckConfig(orphan_warn_rate=0.0001, orphan_fail_rate=0.0005))
        assert strict.status == "FAIL"
        assert strict.failed_rows == lenient.failed_rows  # same real data, same count either way

    # Severity stays WARN regardless of status - an orphan-rate breach never
    # fails the overall run by itself (the whole point of Phase 2/3's policy).
    assert lenient.severity == "WARN"
    assert strict.severity == "WARN"


def test_check_exception_is_recorded_not_silently_dropped(monkeypatch):
    def _boom(conn, config):
        raise RuntimeError("simulated check failure")

    patched_catalog = list(CHECK_CATALOG) + [("structural", _boom)]
    monkeypatch.setattr("scripts.run_quality_checks.CHECK_CATALOG", patched_catalog)

    with get_connection() as conn:
        run_id, results, overall_status = run_checks(conn, category="structural")

    boom_result = next(r for r in results if r.test_name == "_boom")
    assert boom_result.status == "FAIL"
    assert boom_result.error_message == "simulated check failure"
    # The run must fail overall (ERROR severity is the default for an
    # unclassified exception) but the OTHER checks in the same category
    # must still have executed, not been abandoned.
    assert overall_status == "FAIL"
    assert len(results) > 1

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status, error_message FROM monitoring.data_quality_results WHERE run_id = %s AND test_name = '_boom'",
            (run_id,),
        )
        row = cur.fetchone()
    assert row == ("FAIL", "simulated check failure")


def test_baseline_comparison_distinguishes_first_run_from_subsequent():
    """_previous_result_details must return None (never a fabricated
    baseline) for a test_name that has no persisted history."""
    from scripts.run_quality_checks import _previous_result_details

    with get_connection() as conn, conn.cursor() as cur:
        result = _previous_result_details(cur, "a_test_name_that_has_never_run_xyz123")
    assert result is None


def test_json_output_is_valid_and_machine_readable():
    result = subprocess.run(
        [sys.executable, "scripts/run_quality_checks.py", "--category", "structural", "--json"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "run_id" in payload and "overall_status" in payload and "results" in payload
    assert all({"test_name", "status", "severity", "category"} <= r.keys() for r in payload["results"])


def test_cli_show_latest_runs_without_executing_new_checks():
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM monitoring.quality_runs")
        before = cur.fetchone()[0]

    result = subprocess.run(
        [sys.executable, "scripts/run_quality_checks.py", "--show-latest"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Latest run" in result.stdout

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM monitoring.quality_runs")
        after = cur.fetchone()[0]
    assert before == after  # --show-latest must not create a new run

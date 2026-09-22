"""Integration test: running ingestion twice with identical input must not
duplicate business records (Phase 2 spec section 25).

Requires a live Postgres reachable via HOST_DATABASE_URL/DATABASE_URL - skips
cleanly if unavailable rather than failing the whole suite. Uses IDs prefixed
"itest_" so it never collides with real generated data, and cleans up after
itself (delete, not truncate) so it's safe to run against a populated dev DB.
"""
from __future__ import annotations

from pathlib import Path

import psycopg2
import pytest

from ingestion.db import DATABASE_URL, get_connection
from ingestion.pipeline import ingest_source
from ingestion.registry import SOURCES

FIXTURES = Path(__file__).parent.parent / "fixtures" / "idempotency"


def _db_available() -> bool:
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=3)
        conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="no live Postgres reachable")


def _delete_test_rows() -> None:
    with get_connection() as conn, conn, conn.cursor() as cur:
        cur.execute("DELETE FROM raw.organizations WHERE organization_id LIKE 'itest_%'")
        cur.execute(
            "DELETE FROM monitoring.rejected_records WHERE source_record_identifier LIKE 'itest_%'"
        )
        cur.execute(
            "DELETE FROM monitoring.ingestion_runs WHERE pipeline_name = 'ingestion.pipeline.test'"
        )


@pytest.fixture
def clean_test_rows():
    _delete_test_rows()
    yield
    _delete_test_rows()


def _count_test_rows() -> int:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw.organizations WHERE organization_id LIKE 'itest_%'")
        return cur.fetchone()[0]


def test_second_identical_run_inserts_zero_new_rows(clean_test_rows):
    spec = SOURCES["organizations"]

    result_1 = ingest_source(spec, FIXTURES, batch_size=10)
    assert result_1["rows_inserted"] == 3
    assert _count_test_rows() == 3

    result_2 = ingest_source(spec, FIXTURES, batch_size=10)
    assert result_2["rows_inserted"] == 0
    assert result_2["rows_updated"] == 3
    assert _count_test_rows() == 3  # unchanged - no duplicates created


def test_business_keys_stay_unique_after_repeated_ingestion(clean_test_rows):
    spec = SOURCES["organizations"]
    ingest_source(spec, FIXTURES, batch_size=10)
    ingest_source(spec, FIXTURES, batch_size=10)
    ingest_source(spec, FIXTURES, batch_size=10)

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT organization_id, count(*)
                FROM raw.organizations
                WHERE organization_id LIKE 'itest_%'
                GROUP BY organization_id
                HAVING count(*) > 1
                """
        )
        assert cur.fetchall() == []


def test_audit_metrics_reflect_second_run_correctly(clean_test_rows):
    spec = SOURCES["organizations"]
    ingest_source(spec, FIXTURES, batch_size=10)
    result_2 = ingest_source(spec, FIXTURES, batch_size=10)

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT rows_received, rows_inserted, rows_updated, status FROM monitoring.ingestion_runs WHERE run_id = %s",
            (result_2["run_id"],),
        )
        rows_received, rows_inserted, rows_updated, status = cur.fetchone()

    assert rows_received == 3
    assert rows_inserted == 0
    assert rows_updated == 3
    assert status == "SUCCESS"

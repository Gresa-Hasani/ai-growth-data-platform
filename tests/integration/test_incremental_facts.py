"""Integration tests proving the Phase 6 incremental facts actually behave
correctly - real dbt runs against isolated itest_-prefixed fixture rows
(product events, invoices) plus read-only reconciliation checks against
the shared dev dataset (never mutated). Skips cleanly if no live Postgres
is reachable.
"""
from __future__ import annotations

import psycopg2
import pytest

from ingestion.db import DATABASE_URL, get_connection
from tests.integration.dbt_helpers import run_dbt


def _db_available() -> bool:
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=3)
        conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="no live Postgres reachable")

TEST_USER_ID = "itest_user_late_arrival"
TEST_EVENT_DATE = "2026-01-01"


def _cleanup_product_events_fixture() -> None:
    with get_connection() as conn, conn, conn.cursor() as cur:
        cur.execute("DELETE FROM raw.product_events WHERE event_id LIKE 'itest_%'")
        cur.execute("DELETE FROM analytics.fact_product_usage WHERE user_id = %s", (TEST_USER_ID,))


def _cleanup_invoice_fixture() -> None:
    with get_connection() as conn, conn, conn.cursor() as cur:
        cur.execute("DELETE FROM raw.invoices WHERE invoice_id LIKE 'itest_%'")
        cur.execute("DELETE FROM analytics.fact_invoices WHERE invoice_id LIKE 'itest_%'")


@pytest.fixture
def product_events_fixture():
    _cleanup_product_events_fixture()
    yield
    _cleanup_product_events_fixture()


@pytest.fixture
def invoice_fixture():
    _cleanup_invoice_fixture()
    yield
    _cleanup_invoice_fixture()


def _insert_product_event(event_id: str, event_timestamp: str) -> None:
    with get_connection() as conn, conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw.product_events (
                event_id, user_id, organization_id, event_name, event_timestamp,
                session_id, properties, _source_file, _run_id
            ) VALUES (%s, %s, NULL, 'test_event', %s, 's1', '{}', 'itest', 'itest')
            """,
            (event_id, TEST_USER_ID, event_timestamp),
        )


def _fact_product_usage_row():
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_count FROM analytics.fact_product_usage
            WHERE user_id = %s AND date_day = %s AND event_name = 'test_event'
            """,
            (TEST_USER_ID, TEST_EVENT_DATE),
        )
        row = cur.fetchone()
    return row[0] if row else None


def test_incremental_build_from_empty_target_then_second_run_has_no_duplicates(product_events_fixture):
    """Combines scenarios 1-2-3 from the spec: build picks up a brand new
    record, and rerunning with unchanged input does not duplicate it."""
    _insert_product_event("itest_evt_001", f"{TEST_EVENT_DATE}T10:00:00Z")

    assert run_dbt("run", "--select", "fact_product_usage").returncode == 0
    assert _fact_product_usage_row() == 1

    # Rerun with no new input - must not duplicate or inflate.
    assert run_dbt("run", "--select", "fact_product_usage").returncode == 0
    assert _fact_product_usage_row() == 1


def test_late_arriving_event_in_already_aggregated_date_updates_total_not_appends(product_events_fixture):
    _insert_product_event("itest_evt_001", f"{TEST_EVENT_DATE}T10:00:00Z")
    assert run_dbt("run", "--select", "fact_product_usage").returncode == 0
    assert _fact_product_usage_row() == 1

    # A second event lands for the SAME already-aggregated date - this is
    # exactly what "late-arriving" means: _ingested_at is now, but it
    # belongs to a date fact_product_usage already has a row for.
    _insert_product_event("itest_evt_002", f"{TEST_EVENT_DATE}T11:00:00Z")
    assert run_dbt("run", "--select", "fact_product_usage").returncode == 0

    # Must be 2 (both events counted once), not 1 (lost) and not 3 (the
    # pre-existing row plus a duplicate append of the recomputed total).
    assert _fact_product_usage_row() == 2

    # A third, unrelated rerun must not inflate it further.
    assert run_dbt("run", "--select", "fact_product_usage").returncode == 0
    assert _fact_product_usage_row() == 2


def test_invoice_update_is_reflected_without_duplication(invoice_fixture):
    invoice_id = "itest_inv_update_0001"

    with get_connection() as conn, conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw.invoices (
                invoice_id, subscription_id, customer_id, currency, subtotal, tax, total,
                status, issued_at, paid_at, _source_file, _run_id
            ) VALUES (%s, NULL, NULL, 'USD', '92.59', '7.41', '100.00', 'open', '2026-01-01T00:00:00Z', NULL, 'itest', 'itest')
            """,
            (invoice_id,),
        )

    # IMPORTANT: a full, unscoped "dbt run" here, not a narrow --select.
    # dbt-postgres's view materialization always does DROP ... CASCADE +
    # CREATE (Postgres can't CREATE OR REPLACE VIEW with a changed column
    # set) - rebuilding any single staging/intermediate view can cascade-
    # drop its dependent views, and "+"  only rebuilds what's downstream of
    # the SELECTED node, not every other upstream branch a downstream mart
    # might also need (verified directly: a narrow stg_billing__invoices+
    # run failed here because an unrelated earlier test's narrow select had
    # already cascade-dropped int_conversion_funnel, which
    # mart_acquisition_channels also depends on). A full run is the only
    # selector that's safe regardless of what ran before it - cheap at this
    # dev scale (~3s). See docs/incremental_processing.md "Operational
    # considerations" for the full explanation.
    assert run_dbt("run").returncode == 0

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status, billed_amount, paid_amount FROM analytics.fact_invoices WHERE invoice_id = %s",
            (invoice_id,),
        )
        row = cur.fetchone()
    assert row == ("open", 100.00, 0.00)

    # Simulate the invoice being paid - a real source update, re-ingested
    # with a fresh _ingested_at.
    with get_connection() as conn, conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE raw.invoices SET status = 'paid', paid_at = '2026-01-02T00:00:00Z', _ingested_at = now() WHERE invoice_id = %s",
            (invoice_id,),
        )

    assert run_dbt("run").returncode == 0

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), status, billed_amount, paid_amount FROM analytics.fact_invoices WHERE invoice_id = %s GROUP BY 2,3,4",
            (invoice_id,),
        )
        rows = cur.fetchall()

    assert len(rows) == 1, f"expected exactly one (no duplicate) row, got {rows}"
    assert rows[0] == (1, "paid", 100.00, 100.00)


def test_full_refresh_matches_incremental_for_fact_api_usage():
    """Read-only reconciliation against the real dev dataset (never
    mutated): a controlled --full-refresh must produce the same totals as
    the normal incremental state."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*), sum(request_count), sum(total_estimated_cost) FROM analytics.fact_api_usage")
        before = cur.fetchone()

    assert run_dbt("run", "--select", "fact_api_usage", "--full-refresh").returncode == 0

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*), sum(request_count), sum(total_estimated_cost) FROM analytics.fact_api_usage")
        after = cur.fetchone()

    assert before == after, f"full-refresh changed totals: before={before} after={after}"

    # Restore the model to its normal incremental state for any later test/run.
    assert run_dbt("run", "--select", "fact_api_usage").returncode == 0

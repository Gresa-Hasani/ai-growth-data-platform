"""Integration tests proving dbt snapshots actually capture SCD Type 2
history - real dbt snapshot runs against isolated itest_-prefixed fixture
rows in raw.subscriptions/raw.crm_accounts, never the shared dev dataset.

Skips cleanly if no live Postgres is reachable, exactly like Phase 2/3's
integration tests.
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


def _cleanup_subscription_fixture() -> None:
    with get_connection() as conn, conn, conn.cursor() as cur:
        cur.execute("DELETE FROM snapshots.snap_subscriptions WHERE subscription_id LIKE 'itest_%'")
        cur.execute("DELETE FROM raw.subscriptions WHERE subscription_id LIKE 'itest_%'")


def _cleanup_crm_fixture() -> None:
    with get_connection() as conn, conn, conn.cursor() as cur:
        cur.execute("DELETE FROM snapshots.snap_crm_accounts WHERE crm_account_id LIKE 'itest_%'")
        cur.execute("DELETE FROM raw.crm_accounts WHERE crm_account_id LIKE 'itest_%'")


@pytest.fixture
def subscription_fixture():
    _cleanup_subscription_fixture()
    yield
    _cleanup_subscription_fixture()


@pytest.fixture
def crm_fixture():
    _cleanup_crm_fixture()
    yield
    _cleanup_crm_fixture()


def test_subscription_status_and_plan_change_creates_snapshot_history(subscription_fixture):
    sub_id = "itest_sub_scd_0001"

    with get_connection() as conn, conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw.subscriptions (
                subscription_id, user_id, organization_id, plan_name, billing_interval,
                status, currency, amount, started_at, trial_started_at, trial_ended_at,
                cancelled_at, current_period_start, current_period_end, updated_at,
                _source_file, _run_id
            ) VALUES (
                %s, NULL, NULL, 'starter', 'monthly',
                'trialing', 'USD', '15.00', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', NULL,
                NULL, '2026-01-01T00:00:00Z', '2026-02-01T00:00:00Z', '2026-01-01T00:00:00Z',
                'itest', 'itest'
            )
            """,
            (sub_id,),
        )

    result = run_dbt("run", "--select", "stg_billing__subscriptions")
    assert result.returncode == 0, result.stdout + result.stderr
    result = run_dbt("snapshot", "--select", "snap_subscriptions")
    assert result.returncode == 0, result.stdout + result.stderr

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status, plan_name, dbt_valid_to FROM snapshots.snap_subscriptions WHERE subscription_id = %s",
            (sub_id,),
        )
        rows = cur.fetchall()

    assert len(rows) == 1
    assert rows[0] == ("trialing", "starter", None)

    # Simulate a real source update: trial converts to an active paid plan.
    with get_connection() as conn, conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE raw.subscriptions
            SET status = 'active', plan_name = 'pro', updated_at = '2026-01-15T00:00:00Z'
            WHERE subscription_id = %s
            """,
            (sub_id,),
        )

    result = run_dbt("run", "--select", "stg_billing__subscriptions")
    assert result.returncode == 0, result.stdout + result.stderr
    result = run_dbt("snapshot", "--select", "snap_subscriptions")
    assert result.returncode == 0, result.stdout + result.stderr

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, plan_name, dbt_valid_to IS NULL as is_current
            FROM snapshots.snap_subscriptions
            WHERE subscription_id = %s
            ORDER BY dbt_valid_from
            """,
            (sub_id,),
        )
        rows = cur.fetchall()

    assert len(rows) == 2, f"expected 2 historical versions, got {rows}"
    assert rows[0] == ("trialing", "starter", False)  # closed version
    assert rows[1] == ("active", "pro", True)  # current version


def test_crm_account_status_change_creates_snapshot_history(crm_fixture):
    crm_id = "itest_crm_scd_0001"

    with get_connection() as conn, conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw.crm_accounts (
                crm_account_id, organization_id, account_status, segment, owner_team,
                lead_source, created_at, updated_at, _source_file, _run_id
            ) VALUES (
                %s, NULL, 'prospect', 'smb', 'growth',
                'inbound', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'itest', 'itest'
            )
            """,
            (crm_id,),
        )

    assert run_dbt("run", "--select", "stg_crm__accounts").returncode == 0
    assert run_dbt("snapshot", "--select", "snap_crm_accounts").returncode == 0

    with get_connection() as conn, conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE raw.crm_accounts SET account_status = 'active', updated_at = '2026-02-01T00:00:00Z' WHERE crm_account_id = %s",
            (crm_id,),
        )

    assert run_dbt("run", "--select", "stg_crm__accounts").returncode == 0
    assert run_dbt("snapshot", "--select", "snap_crm_accounts").returncode == 0

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT account_status, dbt_valid_to IS NULL as is_current
            FROM snapshots.snap_crm_accounts
            WHERE crm_account_id = %s
            ORDER BY dbt_valid_from
            """,
            (crm_id,),
        )
        rows = cur.fetchall()

    assert len(rows) == 2, f"expected 2 historical versions, got {rows}"
    assert rows[0] == ("prospect", False)
    assert rows[1] == ("active", True)


def test_snapshot_current_record_uniqueness():
    """Build-wide invariant (not isolated test data): every business key in
    each snapshot must have at most one current (dbt_valid_to IS NULL) row."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT subscription_id, count(*)
            FROM snapshots.snap_subscriptions
            WHERE dbt_valid_to IS NULL
            GROUP BY subscription_id
            HAVING count(*) > 1
            """
        )
        duplicate_current_subscriptions = cur.fetchall()

        cur.execute(
            """
            SELECT crm_account_id, count(*)
            FROM snapshots.snap_crm_accounts
            WHERE dbt_valid_to IS NULL
            GROUP BY crm_account_id
            HAVING count(*) > 1
            """
        )
        duplicate_current_crm = cur.fetchall()

    assert duplicate_current_subscriptions == []
    assert duplicate_current_crm == []


def test_snapshot_valid_date_intervals_are_well_formed():
    """dbt_valid_from must never be null, and a closed row's dbt_valid_to
    must always be strictly after its dbt_valid_from - a build-wide
    invariant on every row in both snapshots, not just test fixtures."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM snapshots.snap_subscriptions
            WHERE dbt_valid_from IS NULL
               OR (dbt_valid_to IS NOT NULL AND dbt_valid_to <= dbt_valid_from)
            """
        )
        bad_subscription_intervals = cur.fetchone()[0]

        cur.execute(
            """
            SELECT count(*) FROM snapshots.snap_crm_accounts
            WHERE dbt_valid_from IS NULL
               OR (dbt_valid_to IS NOT NULL AND dbt_valid_to <= dbt_valid_from)
            """
        )
        bad_crm_intervals = cur.fetchone()[0]

    assert bad_subscription_intervals == 0
    assert bad_crm_intervals == 0

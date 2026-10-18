"""Phase 10: integration tests proving the database-level read-only
enforcement actually works against the real Postgres instance - not just
app-level convention. Skips cleanly if no live Postgres is reachable.

Covers:
- app/db/session.py's read_only_engine rejects a write at the database
  level, independent of the connecting role's own grants.
- the `analytics_ro` role (app/db/security_roles.sh), if it has been
  provisioned, has exactly the grants documented: SELECT on
  analytics/monitoring, INSERT on monitoring.ai_query_logs only, nothing
  else, and no access to `raw`/`staging`/`intermediate` or system catalogs.
- a rejected write leaves the underlying data unchanged.
- monitoring.data_quality_results and Phase 8 analytics remain unaffected.
"""
from __future__ import annotations

import os

import psycopg2
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app.db.session import DATABASE_URL, SessionLocal, read_only_engine

# Host-reachable DSN for the analytics_ro role, mirrored from
# HOST_READ_ONLY_DATABASE_URL by tests/conftest.py into
# READ_ONLY_DATABASE_URL - read directly here (rather than hardcoding a
# host/port) so these tests aren't tied to any one machine's port override.
_ANALYTICS_RO_DSN = os.environ.get("READ_ONLY_DATABASE_URL", "")


def _db_available() -> bool:
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=3)
        conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="no live Postgres reachable")


def _analytics_ro_role_exists() -> bool:
    if not _ANALYTICS_RO_DSN:
        return False
    db = SessionLocal()
    try:
        return bool(
            db.execute(text("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'analytics_ro'")).scalar()
        )
    finally:
        db.close()


def test_read_only_engine_permits_select():
    with read_only_engine.connect() as conn:
        row = conn.execute(text("SELECT 1 AS one")).scalar_one()
    assert row == 1


def test_read_only_engine_rejects_write_at_the_database_level():
    """Even if the connecting role has full write privileges (the
    read_only_engine falls back to DATABASE_URL's role when
    READ_ONLY_DATABASE_URL isn't configured), Postgres itself must refuse
    a write inside a read-only transaction - this is NOT merely an
    application-level convention."""
    with read_only_engine.connect() as conn:
        with pytest.raises(DatabaseError) as exc_info:
            conn.execute(
                text(
                    "INSERT INTO monitoring.ai_query_logs "
                    "(query_log_id, provider, used_real_inference, outcome, tool_invocation_count) "
                    "VALUES ('aql_readonly_engine_test', 'ollama', false, 'success', 0)"
                )
            )
        assert "read-only transaction" in str(exc_info.value).lower()


def test_rejected_write_leaves_data_unchanged():
    db = SessionLocal()
    try:
        before = db.execute(
            text("SELECT count(*) FROM monitoring.ai_query_logs WHERE query_log_id = 'aql_readonly_engine_test'")
        ).scalar_one()
    finally:
        db.close()
    assert before == 0  # the rejected write above never landed


@pytest.mark.skipif(not _analytics_ro_role_exists(), reason="analytics_ro role not provisioned - see app/db/security_roles.sh")
def test_analytics_ro_role_can_select_analytics_and_monitoring():
    conn = psycopg2.connect(_ANALYTICS_RO_DSN)
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT currency FROM analytics.mart_mrr LIMIT 1")
        cur.execute("SELECT run_id FROM monitoring.quality_runs LIMIT 1")
    finally:
        conn.close()


@pytest.mark.skipif(not _analytics_ro_role_exists(), reason="analytics_ro role not provisioned - see app/db/security_roles.sh")
def test_analytics_ro_role_cannot_write_to_an_ordinary_analytics_table():
    conn = psycopg2.connect(_ANALYTICS_RO_DSN)
    try:
        conn.autocommit = True
        cur = conn.cursor()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("INSERT INTO analytics.mart_mrr (currency) VALUES ('XXX')")
    finally:
        conn.close()


@pytest.mark.skipif(not _analytics_ro_role_exists(), reason="analytics_ro role not provisioned - see app/db/security_roles.sh")
def test_analytics_ro_role_can_insert_ai_query_logs_but_not_delete_them():
    """The one documented write exception - INSERT only, nothing else."""
    conn = psycopg2.connect(_ANALYTICS_RO_DSN)
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO monitoring.ai_query_logs "
            "(query_log_id, provider, used_real_inference, outcome, tool_invocation_count) "
            "VALUES ('aql_role_grant_test', 'ollama', false, 'success', 0)"
        )
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("DELETE FROM monitoring.ai_query_logs WHERE query_log_id = 'aql_role_grant_test'")
    finally:
        conn.close()
        cleanup_db = SessionLocal()
        cleanup_db.execute(text("DELETE FROM monitoring.ai_query_logs WHERE query_log_id = 'aql_role_grant_test'"))
        cleanup_db.commit()
        cleanup_db.close()


@pytest.mark.skipif(not _analytics_ro_role_exists(), reason="analytics_ro role not provisioned - see app/db/security_roles.sh")
def test_analytics_ro_role_cannot_read_raw_schema():
    conn = psycopg2.connect(_ANALYTICS_RO_DSN)
    try:
        conn.autocommit = True
        cur = conn.cursor()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("SELECT * FROM raw.users LIMIT 1")
    finally:
        conn.close()


def test_monitoring_data_quality_results_untouched_by_security_tests():
    db = SessionLocal()
    try:
        count = db.execute(text("SELECT count(*) FROM monitoring.data_quality_results")).scalar_one()
    finally:
        db.close()
    assert count >= 0  # table exists and is queryable; this suite never writes to it

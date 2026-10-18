"""Tests for the defensive SQL validation boundary (app/security/sql_validator.py).

This module is NOT wired into any endpoint or agent tool today - see the
module docstring. These tests exercise it standalone, proving it correctly
accepts safe read-only SELECTs and rejects every category of unsafe
statement the Phase 10 spec calls out, using sqlglot's structured parser
rather than any regex-only approach.
"""
from __future__ import annotations

import pytest

from app.security.sql_validator import (
    DisallowedConstructError,
    DisallowedFunctionError,
    DisallowedSchemaError,
    MalformedSqlError,
    MultipleStatementsError,
    StatementCategory,
    UnsupportedStatementError,
    classify_statement,
    validate_select_only,
)

# --- valid read-only SELECTs -------------------------------------------------


def test_accepts_simple_select_on_allowed_schema():
    result = validate_select_only("SELECT currency, mrr FROM analytics.mart_mrr WHERE currency = 'USD'")
    assert result.referenced_tables == ("analytics.mart_mrr",)


def test_accepts_select_on_monitoring_schema():
    result = validate_select_only("SELECT run_id, overall_status FROM monitoring.quality_runs ORDER BY started_at DESC LIMIT 1")
    assert result.referenced_tables == ("monitoring.quality_runs",)


def test_accepts_cte_select():
    sql = "WITH recent AS (SELECT * FROM analytics.mart_mrr) SELECT currency FROM recent"
    result = validate_select_only(sql)
    assert "analytics.mart_mrr" in result.referenced_tables


def test_accepts_select_with_join_across_allowed_schemas():
    sql = (
        "SELECT a.currency, a.mrr, b.run_id FROM analytics.mart_mrr a "
        "JOIN monitoring.quality_runs b ON true"
    )
    result = validate_select_only(sql)
    assert set(result.referenced_tables) == {"analytics.mart_mrr", "monitoring.quality_runs"}


def test_accepts_select_with_sql_comment_and_quoted_string_containing_keywords():
    """A comment or string literal containing SQL-looking text must not be
    misinterpreted as a second statement or a different statement type -
    this is exactly the class of bug a regex-only filter would get wrong."""
    sql = "SELECT currency FROM analytics.mart_mrr WHERE currency = 'DROP TABLE x; --' -- also DROP TABLE y"
    result = validate_select_only(sql)
    assert result.referenced_tables == ("analytics.mart_mrr",)


def test_accepts_nested_subquery_and_aggregate():
    sql = "SELECT currency, SUM(mrr) FROM (SELECT currency, mrr FROM analytics.mart_mrr) sub GROUP BY currency"
    result = validate_select_only(sql)
    assert result.referenced_tables == ("analytics.mart_mrr",)


# --- malformed SQL ------------------------------------------------------------


def test_rejects_malformed_sql():
    with pytest.raises(MalformedSqlError):
        validate_select_only("not valid sql at all ((")


def test_rejects_empty_sql():
    with pytest.raises(MalformedSqlError):
        validate_select_only("   ")


# --- multiple statements ------------------------------------------------------


def test_rejects_stacked_statements_via_semicolon():
    with pytest.raises(MultipleStatementsError):
        validate_select_only("SELECT * FROM analytics.mart_mrr; DROP TABLE analytics.mart_mrr;")


def test_rejects_two_select_statements():
    with pytest.raises(MultipleStatementsError):
        validate_select_only("SELECT 1; SELECT 2;")


# --- data-changing operations -------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO analytics.mart_mrr (currency, mrr) VALUES ('USD', 1)",
        "UPDATE analytics.mart_mrr SET mrr = 0",
        "DELETE FROM analytics.mart_mrr",
        "MERGE INTO analytics.mart_mrr USING analytics.dim_organizations ON true WHEN MATCHED THEN DELETE",
    ],
)
def test_rejects_data_changing_statements(sql):
    with pytest.raises(UnsupportedStatementError):
        validate_select_only(sql)


# --- schema-changing operations -----------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE analytics.evil (id int)",
        "ALTER TABLE analytics.mart_mrr ADD COLUMN evil int",
        "DROP TABLE analytics.mart_mrr",
        "TRUNCATE TABLE analytics.mart_mrr",
    ],
)
def test_rejects_schema_changing_statements(sql):
    with pytest.raises(UnsupportedStatementError):
        validate_select_only(sql)


def test_rejects_select_into_as_schema_changing():
    with pytest.raises(DisallowedConstructError):
        validate_select_only("SELECT * INTO analytics.new_table FROM analytics.mart_mrr")


# --- permission / transaction / admin operations ------------------------------


def test_rejects_grant():
    with pytest.raises(UnsupportedStatementError):
        validate_select_only("GRANT SELECT ON analytics.mart_mrr TO some_role")


def test_rejects_revoke():
    with pytest.raises(UnsupportedStatementError):
        validate_select_only("REVOKE SELECT ON analytics.mart_mrr FROM some_role")


def test_rejects_set_statement():
    with pytest.raises(UnsupportedStatementError):
        validate_select_only("SET search_path TO public")


def test_rejects_vacuum_as_unsupported_admin_command():
    with pytest.raises(UnsupportedStatementError):
        validate_select_only("VACUUM analytics.mart_mrr")


def test_rejects_copy():
    with pytest.raises(UnsupportedStatementError):
        validate_select_only("COPY analytics.mart_mrr TO '/tmp/exfil.csv'")


def test_rejects_locking_select():
    with pytest.raises(DisallowedConstructError):
        validate_select_only("SELECT * FROM analytics.mart_mrr FOR UPDATE")


# --- dangerous functions -------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT * FROM analytics.mart_mrr WHERE pg_terminate_backend(pg_backend_pid())",
        "SELECT dblink('host=evil.example.com', 'select 1')",
        "SELECT lo_export(1, '/tmp/x')",
    ],
)
def test_rejects_disallowed_functions(sql):
    with pytest.raises(DisallowedFunctionError):
        validate_select_only(sql)


# --- unauthorized schemas -----------------------------------------------------


def test_rejects_system_catalog_schema():
    with pytest.raises(DisallowedSchemaError):
        validate_select_only("SELECT * FROM pg_catalog.pg_tables")


def test_rejects_information_schema():
    with pytest.raises(DisallowedSchemaError):
        validate_select_only("SELECT * FROM information_schema.tables")


def test_rejects_unqualified_table_name():
    with pytest.raises(DisallowedSchemaError):
        validate_select_only("SELECT * FROM mart_mrr")


def test_rejects_raw_schema_not_exposed_to_api_or_agent():
    """`raw`/`staging`/`intermediate` are real schemas in this platform, but
    neither the API nor the agent are permitted to read them directly - only
    `analytics`/`monitoring` (see app/main.py's required relations and
    app/agent/catalog.py's approved sources)."""
    with pytest.raises(DisallowedSchemaError):
        validate_select_only("SELECT * FROM raw.users")


# --- classify_statement (diagnostic helper, never raises) --------------------


def test_classify_statement_never_raises_on_malformed_input():
    assert classify_statement("not valid sql ((") == StatementCategory.UNKNOWN


def test_classify_statement_identifies_select_only():
    assert classify_statement("SELECT 1") == StatementCategory.SELECT_ONLY


def test_classify_statement_identifies_write():
    assert classify_statement("DELETE FROM analytics.mart_mrr") == StatementCategory.WRITE


def test_classify_statement_identifies_schema_change():
    assert classify_statement("DROP TABLE analytics.mart_mrr") == StatementCategory.SCHEMA_CHANGE


def test_classify_statement_returns_unknown_for_multiple_statements():
    assert classify_statement("SELECT 1; SELECT 2;") == StatementCategory.UNKNOWN

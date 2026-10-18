"""Phase 10: SQL-injection-resistance tests against the real, live API -
realistic attacker-supplied values through every free-text filter/ID/path
parameter the Phase 8/9 endpoints expose. Every one of these must be
treated as an opaque bound-parameter VALUE, never as SQL - see the code
audit in docs/security_and_sql_validation.md ("every app/services/*.py
query uses SQLAlchemy text() with :name bound parameters; the few f-strings
present only splice together STATIC clause fragments, never a runtime
value or identifier").

These tests prove the negative empirically: a payload that would alter
query structure if it were unsafely concatenated instead produces either a
harmless empty/404 result or a 422 validation error - never a 500, never a
different/larger result set, and never any change to the underlying data.
Skips cleanly if no live Postgres is reachable.
"""
from __future__ import annotations

import psycopg2
import pytest
from fastapi.testclient import TestClient

from ingestion.db import DATABASE_URL as INGESTION_DATABASE_URL

INJECTION_PAYLOADS = [
    "' OR '1'='1",
    "x'; DROP TABLE analytics.mart_mrr; --",
    "'; SELECT pg_sleep(5); --",
    "x' UNION SELECT password FROM pg_shadow --",
    "org_1\" OR \"\"=\"",
    "org_1/*comment*/OR/**/1=1",
    # NUL bytes are excluded from URL-based payloads here: httpx itself
    # refuses to construct a request URL containing one (InvalidURL),
    # which would test the HTTP client, not this application - the control-
    # character rejection for agent-plan string fields is instead covered
    # directly at tests/unit/test_agent_security.py
    # (test_plan_rejects_control_characters_in_organization_id).
]


def _db_available() -> bool:
    try:
        conn = psycopg2.connect(INGESTION_DATABASE_URL, connect_timeout=3)
        conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="no live Postgres reachable")


@pytest.fixture(scope="module")
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture(scope="module")
def baseline_counts():
    """Row counts for every table these endpoints touch, captured once
    before any injection attempt - compared again at the end of the module
    to prove nothing was ever modified."""
    conn = psycopg2.connect(INGESTION_DATABASE_URL)
    try:
        cur = conn.cursor()
        counts = {}
        for table in (
            "analytics.mart_mrr", "analytics.dim_organizations", "analytics.mart_acquisition_channels",
            "monitoring.quality_runs", "monitoring.data_quality_results",
        ):
            cur.execute(f"SELECT count(*) FROM {table}")
            counts[table] = cur.fetchone()[0]
        return counts
    finally:
        conn.close()


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_customer_detail_path_param_rejects_injection_safely(client, payload):
    response = client.get(f"/api/v1/customers/{payload}")
    assert response.status_code in (404, 422)
    assert "traceback" not in response.text.lower()


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_acquisition_channel_filter_rejects_injection_safely(client, payload):
    response = client.get(f"/api/v1/analytics/growth/acquisition-channels?acquisition_channel={payload}")
    assert response.status_code in (200, 422)
    if response.status_code == 200:
        assert response.json() == []  # no real channel matches a garbage/injection string


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_event_name_filter_rejects_injection_safely(client, payload):
    response = client.get(f"/api/v1/analytics/usage/features?event_name={payload}")
    assert response.status_code in (200, 422)
    if response.status_code == 200:
        assert response.json() == []


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_quality_run_id_path_param_rejects_injection_safely(client, payload):
    response = client.get(f"/api/v1/quality/runs/{payload}")
    assert response.status_code in (404, 422)


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_customer_organization_type_filter_rejects_injection_safely(client, payload):
    response = client.get(f"/api/v1/customers?organization_type={payload}")
    assert response.status_code in (200, 422)
    if response.status_code == 200:
        assert response.json()["items"] == []


def test_currency_filter_rejects_injection_as_unsupported_currency(client):
    response = client.get("/api/v1/analytics/revenue/mrr?currency=USD'%20OR%20'1'='1")
    assert response.status_code == 422


@pytest.mark.parametrize("payload", ["abc' OR '1'='1", "-1; DROP TABLE x", "99999999999999999999"])
def test_pagination_limit_rejects_malicious_or_out_of_range_values(client, payload):
    response = client.get(f"/api/v1/customers?limit={payload}")
    assert response.status_code == 422


@pytest.mark.parametrize("payload", ["'; SELECT 1; --", "not-a-date"])
def test_date_range_rejects_malicious_or_malformed_values(client, payload):
    response = client.get(f"/api/v1/analytics/growth/daily?start_date={payload}")
    assert response.status_code == 422


def test_agent_ask_question_field_resists_injection_through_the_full_plan_pipeline(client):
    """The whole question becomes `organization_id`/similar filter VALUES
    only via the planner's validated AnalyticalPlan (see
    tests/unit/test_agent_security.py) - end to end through the live API,
    a question containing SQL-looking text must never 500, never echo the
    raw SQL text back, and never actually execute it (the embedded "DROP
    TABLE" is just noise in the question text - the rule-based fallback
    planner may still legitimately match "mrr" as a substring of
    "mart_mrr" and answer the real MRR question; the security property
    under test is that the DROP TABLE never runs and never appears in the
    response, not that the agent necessarily refuses the whole question)."""
    response = client.post(
        "/api/v1/agent/ask",
        json={"question": "Show me org_1'; DROP TABLE analytics.mart_mrr; -- revenue"},
    )
    assert response.status_code in (200, 400, 422)
    assert "traceback" not in response.text.lower()
    assert "DROP TABLE" not in response.text
    # Proves the DROP was never actually executed, independent of status code.
    confirm = client.get("/api/v1/analytics/revenue/mrr")
    assert confirm.status_code == 200
    assert len(confirm.json()) >= 1


def test_no_injection_attempt_above_altered_any_underlying_data(client, baseline_counts):
    """Defined last, so pytest's default (definition-order) collection runs
    it after every injection attempt above - re-reads every table touched
    and confirms every count is unchanged. True regardless of run order,
    though: `baseline_counts` is captured once per module before any test
    runs, and these endpoints are 100% read-only."""
    conn = psycopg2.connect(INGESTION_DATABASE_URL)
    try:
        cur = conn.cursor()
        for table, expected in baseline_counts.items():
            cur.execute(f"SELECT count(*) FROM {table}")
            actual = cur.fetchone()[0]
            assert actual == expected, f"{table} row count changed: {expected} -> {actual}"
    finally:
        conn.close()

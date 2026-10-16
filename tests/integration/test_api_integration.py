"""Integration tests for the Phase 8 API against the real dev database -
real queries, real marts, real response schemas. Read-only: no endpoint
in this API can mutate data, so these tests never need itest_ fixture
cleanup. Skips cleanly if no live Postgres is reachable.
"""
from __future__ import annotations

import psycopg2
import pytest
from fastapi.testclient import TestClient

from ingestion.db import DATABASE_URL as INGESTION_DATABASE_URL


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


def test_ready_succeeds_against_real_database(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_mrr_endpoint_keeps_currencies_separate(client):
    response = client.get("/api/v1/analytics/revenue/mrr")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) >= 1
    currencies = [r["currency"] for r in rows]
    assert len(currencies) == len(set(currencies)), "duplicate currency rows - should be one per currency"
    assert set(currencies) <= {"USD", "EUR", "GBP"}
    # No row has been collapsed/summed across currencies - every row keeps its own currency.
    for row in rows:
        assert row["currency"] in ("USD", "EUR", "GBP")


def test_mrr_filtered_by_currency_returns_only_that_currency(client):
    response = client.get("/api/v1/analytics/revenue/mrr?currency=USD")
    assert response.status_code == 200
    rows = response.json()
    assert all(r["currency"] == "USD" for r in rows)


def test_arr_equals_mrr_times_twelve_per_currency(client):
    mrr_rows = {r["currency"]: r["mrr"] for r in client.get("/api/v1/analytics/revenue/mrr").json()}
    arr_rows = client.get("/api/v1/analytics/revenue/arr").json()
    for row in arr_rows:
        expected = round(float(mrr_rows[row["currency"]]) * 12, 2)
        assert round(float(row["arr"]), 2) == expected


def test_revenue_endpoints_reject_unsupported_currency(client):
    for path in ["mrr", "arr", "arpu", "customer-ltv"]:
        response = client.get(f"/api/v1/analytics/revenue/{path}?currency=JPY")
        assert response.status_code == 422, f"{path} did not reject an unsupported currency"


def test_customer_list_uses_organization_grain_not_user_grain(client):
    response = client.get("/api/v1/customers?limit=5")
    assert response.status_code == 200
    body = response.json()
    assert "items" in body and "meta" in body
    for customer in body["items"]:
        assert customer["organization_id"].startswith("org_"), "customer_id must be an organization_id"


def test_customer_detail_returns_404_for_unknown_organization(client):
    response = client.get("/api/v1/customers/org_does_not_exist_xyz")
    assert response.status_code == 404
    assert "detail" in response.json()


def test_customer_detail_returns_200_for_real_organization(client):
    org_id = client.get("/api/v1/customers?limit=1").json()["items"][0]["organization_id"]
    response = client.get(f"/api/v1/customers/{org_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["organization_id"] == org_id
    assert "revenue_by_currency" in body and "usage_last_30_days" in body
    for revenue_row in body["revenue_by_currency"]:
        assert revenue_row["currency"] in ("USD", "EUR", "GBP")


def test_pagination_limit_is_enforced(client):
    response = client.get("/api/v1/customers?limit=10000")
    assert response.status_code == 422


def test_pagination_meta_reflects_actual_total(client):
    response = client.get("/api/v1/customers?limit=1")
    body = response.json()
    assert body["meta"]["limit"] == 1
    assert body["meta"]["total"] >= len(body["items"])


def test_organization_id_filter_is_parameterized_not_injectable(client):
    """A classic injection payload in a filter value must be treated as a
    literal string, never executed - this should return an empty result
    set (no matching organization_id), not a 500 or an unrelated result."""
    payload = "org_000001' OR '1'='1"
    response = client.get(f"/api/v1/analytics/usage/daily?organization_id={payload}")
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["meta"]["total"] == 0


def test_date_range_validation_rejects_end_before_start(client):
    response = client.get("/api/v1/analytics/growth/daily?start_date=2026-01-10&end_date=2026-01-01")
    assert response.status_code == 422


def test_date_range_validation_rejects_window_too_wide(client):
    response = client.get("/api/v1/analytics/growth/daily?start_date=2020-01-01&end_date=2026-01-01")
    assert response.status_code == 422


def test_empty_result_set_is_handled_consistently(client):
    """A feature_name filter that matches nothing must return an empty
    list with 200, not an error."""
    response = client.get("/api/v1/analytics/usage/features?event_name=this_event_does_not_exist")
    assert response.status_code == 200
    assert response.json() == []


def test_trial_conversion_endpoint_schema_and_pagination(client):
    response = client.get("/api/v1/analytics/growth/trial-conversion?limit=5")
    assert response.status_code == 200
    body = response.json()
    assert "items" in body and "meta" in body
    for record in body["items"]:
        assert "is_converted" in record and "is_observation_complete" in record


def test_quality_latest_reads_real_phase7_data(client):
    response = client.get("/api/v1/quality/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["total_checks"] == body["passed_checks"] + body["warned_checks"] + body["failed_checks"]
    assert len(body["results"]) == body["total_checks"]


def test_quality_runs_pagination(client):
    response = client.get("/api/v1/quality/runs?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) <= 2
    assert body["meta"]["total"] >= len(body["items"])


def test_quality_run_detail_returns_404_for_unknown_run_id(client):
    response = client.get("/api/v1/quality/runs/qrun_does_not_exist")
    assert response.status_code == 404


def test_quality_run_detail_matches_latest_for_the_same_run(client):
    latest = client.get("/api/v1/quality/latest").json()
    detail = client.get(f"/api/v1/quality/runs/{latest['run_id']}").json()
    assert detail["run_id"] == latest["run_id"]
    assert detail["total_checks"] == latest["total_checks"]


def test_missing_monitoring_tables_is_reported_honestly_not_as_healthy(client, monkeypatch):
    """If the monitoring schema/tables are unavailable, quality endpoints
    must return a clear error - never silently report an empty/healthy-
    looking result."""
    from app.services import quality_service

    monkeypatch.setattr(quality_service, "monitoring_tables_available", lambda db: False)
    response = client.get("/api/v1/quality/latest")
    assert response.status_code == 503
    assert "monitoring" in response.json()["detail"].lower()


def test_api_errors_never_leak_connection_details(client):
    response = client.get("/api/v1/customers/org_does_not_exist_xyz")
    body_text = response.text.lower()
    assert "password" not in body_text
    assert "postgresql://" not in body_text
    assert "traceback" not in body_text


def test_full_openapi_schema_generates_without_error(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "/api/v1/analytics/revenue/mrr" in schema["paths"]
    assert "/api/v1/customers/{customer_id}" in schema["paths"]

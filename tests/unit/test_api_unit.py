"""Unit tests for the Phase 8 API's pure validation logic and the
process-liveness/dependency-readiness distinction. These do NOT require a
live PostgreSQL instance for the pure-function tests; the /health vs
/ready distinction tests use the FastAPI TestClient with a deliberately
unreachable database to prove /health never touches it.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.services._shared import clamp_pagination, resolve_date_range, validate_currency


def test_resolve_date_range_defaults_to_trailing_window():
    start, end = resolve_date_range(None, None)
    assert end == date.today()
    assert (end - start).days == 30


def test_resolve_date_range_rejects_end_before_start():
    with pytest.raises(HTTPException) as exc_info:
        resolve_date_range(date(2026, 1, 10), date(2026, 1, 1))
    assert exc_info.value.status_code == 422


def test_resolve_date_range_rejects_window_too_wide():
    with pytest.raises(HTTPException) as exc_info:
        resolve_date_range(date(2020, 1, 1), date(2026, 1, 1))
    assert exc_info.value.status_code == 422
    assert "366 days" in exc_info.value.detail


def test_resolve_date_range_accepts_valid_explicit_range():
    start, end = resolve_date_range(date(2026, 1, 1), date(2026, 1, 31))
    assert start == date(2026, 1, 1)
    assert end == date(2026, 1, 31)


@pytest.mark.parametrize("currency", ["USD", "eur", "GBP"])
def test_validate_currency_accepts_supported_currencies(currency):
    assert validate_currency(currency) == currency.upper()


def test_validate_currency_rejects_unsupported_currency():
    with pytest.raises(HTTPException) as exc_info:
        validate_currency("JPY")
    assert exc_info.value.status_code == 422
    assert "JPY" in exc_info.value.detail


def test_validate_currency_passthrough_none():
    assert validate_currency(None) is None


def test_clamp_pagination_rejects_limit_too_large():
    with pytest.raises(HTTPException):
        clamp_pagination(99999, 0)


def test_clamp_pagination_rejects_negative_offset():
    with pytest.raises(HTTPException):
        clamp_pagination(10, -1)


def test_clamp_pagination_accepts_valid_values():
    assert clamp_pagination(50, 10) == (50, 10)


def test_health_does_not_require_database(monkeypatch):
    """/health must succeed even when the database engine is completely
    unreachable - it is pure process liveness, never a DB query.

    Patches the already-created `engine` attribute (test-scoped, reverted
    by monkeypatch automatically) rather than the DATABASE_URL env var -
    app.db.session.engine is a module-level singleton created once at
    first import, shared by every test in the session; mutating the env
    var here would only affect a *future* create_engine() call, and if
    this were the first-ever import of app.main in the whole suite, it
    would permanently poison that shared singleton with a broken URL for
    every other test that imports app.main afterward.
    """
    from sqlalchemy import create_engine

    from app import main as app_main

    broken_engine = create_engine("postgresql://nouser:nopass@localhost:1/doesnotexist")
    monkeypatch.setattr(app_main, "engine", broken_engine)

    client = TestClient(app_main.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_fails_honestly_when_database_unreachable(monkeypatch):
    """/ready must report 503 - not falsely report healthy - when its
    actual dependency (the database) can't be reached, and must never leak
    the connection string/credentials in the error response."""
    from sqlalchemy import create_engine

    from app import main as app_main

    broken_engine = create_engine("postgresql://nouser:nopass@localhost:1/doesnotexist")
    monkeypatch.setattr(app_main, "engine", broken_engine)

    client = TestClient(app_main.app)
    response = client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert "nouser" not in str(body)
    assert "nopass" not in str(body)

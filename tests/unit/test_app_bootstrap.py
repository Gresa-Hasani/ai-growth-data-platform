"""Phase 1 smoke tests: the API app is importable and exposes /health.

Full integration testing of /health against a live Postgres happens once
docker compose is up (see docs/architecture.md quickstart).
"""
from app.main import app


def test_app_has_health_route() -> None:
    paths = {route.path for route in app.routes}
    assert "/health" in paths


def test_app_metadata() -> None:
    assert app.title == "AI-Powered Growth Data Platform API"

"""Phase 1 smoke tests: the API app is importable and exposes /health.

Full integration testing of /health against a live Postgres happens once
docker compose is up (see docs/architecture.md quickstart).

Route presence is checked via the OpenAPI schema (app.openapi()["paths"])
rather than iterating app.routes directly - as of Phase 8, routes added via
app.include_router() (every /api/v1/* router) are represented by wrapper
objects in app.routes that don't expose a plain `.path` attribute the way
a bare @app.get(...) route does, so the raw `route.path` iteration this
test originally used raises AttributeError once any router is included.
The OpenAPI schema is the stable, public way to ask "does this path exist"
regardless of how a given FastAPI/Starlette version represents it internally.
"""
from app.main import app


def test_app_has_health_and_ready_routes() -> None:
    paths = app.openapi()["paths"]
    assert "/health" in paths
    assert "/ready" in paths


def test_app_has_phase8_analytics_routes() -> None:
    paths = app.openapi()["paths"]
    assert "/api/v1/analytics/revenue/mrr" in paths
    assert "/api/v1/customers/{customer_id}" in paths
    assert "/api/v1/quality/latest" in paths


def test_app_metadata() -> None:
    assert app.title == "AI-Powered Growth Data Platform API"

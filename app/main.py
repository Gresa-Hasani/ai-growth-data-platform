"""Self-service analytics API.

Phase 1 established /health as a DB-backed check; Phase 8 splits that into
two distinct concepts (see docs/analytics_api.md "Health vs readiness"):
  /health - pure process liveness, no DB query. Answers "is the process up."
  /ready  - dependency readiness: DB reachable AND the required analytics
            objects exist. Answers "can this instance actually serve traffic."
docker-compose.yml's healthcheck was updated to call /ready, preserving its
original behavior (container unhealthy if the DB is down) under the new,
more precise name.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from app.api.v1 import agent, customers, growth, quality, revenue, usage
from app.db.session import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("growth_platform.api")

app = FastAPI(
    title="AI-Powered Growth Data Platform API",
    description="Self-service analytics API for Growth, RevOps, Product, and Engineering.",
    version="0.2.0",
)

app.include_router(growth.router)
app.include_router(revenue.router)
app.include_router(usage.router)
app.include_router(customers.router)
app.include_router(quality.router)
app.include_router(agent.router)

# Every analytics object a healthy instance must be able to query against.
_REQUIRED_ANALYTICS_RELATIONS = (
    "analytics.mart_mrr",
    "analytics.mart_arr",
    "analytics.mart_customer_churn",
    "analytics.dim_organizations",
    "analytics.fact_subscriptions",
    "analytics.fact_invoices",
    "analytics.fact_product_usage",
    "analytics.fact_api_usage",
    "monitoring.quality_runs",
)


@app.get("/health", summary="Process liveness", tags=["health"])
def health() -> dict[str, str]:
    """Always returns ok if the process can handle a request at all - does
    NOT touch the database. Use /ready to check dependency availability."""
    return {"status": "ok"}


@app.get("/ready", summary="Dependency readiness", tags=["health"])
def ready() -> dict[str, object]:
    """Confirms the database is reachable AND every analytics object this
    API queries actually exists - not just that *a* connection works."""
    try:
        with engine.connect() as conn:
            missing = [
                relation
                for relation in _REQUIRED_ANALYTICS_RELATIONS
                if not conn.execute(text("SELECT to_regclass(:r) IS NOT NULL"), {"r": relation}).scalar()
            ]
    except Exception as exc:
        logger.error("Readiness check failed: %s", exc)
        raise HTTPException(status_code=503, detail="database unavailable") from exc

    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"required analytics objects missing: {', '.join(missing)}",
        )

    return {"status": "ready"}

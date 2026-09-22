"""Self-service analytics API.

Phase 1 only exposes a health check that verifies the database connection.
Metric and customer endpoints are added in Phase 8.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from app.db.session import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("growth_platform.api")

app = FastAPI(
    title="AI-Powered Growth Data Platform API",
    description="Self-service analytics API for Growth, RevOps, Product, and Engineering.",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness/readiness probe: confirms the API process and DB are reachable."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("Health check failed: %s", exc)
        raise HTTPException(status_code=503, detail="database unavailable") from exc

    return {"status": "ok"}

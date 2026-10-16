"""SQLAlchemy engine/session factory for the analytics API.

Reads configuration from environment variables only — no hardcoded credentials.
"""
from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://analytics:analytics@localhost:5432/growth_platform"
)

# Explicit "+psycopg2" dialect, not bare "postgresql://". Surfaced by a
# Phase 8 Docker rebuild: a fresh `pip install` resolves a SQLAlchemy
# version whose bare postgresql:// URL now defaults to the "psycopg" (v3)
# driver, which this project never installs (only psycopg2-binary, used
# directly by ingestion/dbt elsewhere) - create_engine() then fails at
# import time with ModuleNotFoundError: No module named 'psycopg'. The env
# var itself is left untouched (HOST_DATABASE_URL/DATABASE_URL are also
# consumed directly by psycopg2.connect(), which has no "+driver" syntax
# and would break if the suffix were added there instead).
_SQLALCHEMY_DATABASE_URL = (
    DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    if DATABASE_URL.startswith("postgresql://")
    else DATABASE_URL
)

engine = create_engine(_SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

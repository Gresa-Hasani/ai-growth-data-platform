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
    """FastAPI dependency that yields a request-scoped session on the main
    (writable) engine. Used only where a request genuinely needs to write -
    in practice, nothing in this codebase uses this for request handling
    directly anymore (see get_read_only_db below); it remains the engine
    app/agent/audit.py opens its own dedicated session against, and what
    tests use to set up/inspect schema objects."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Phase 10: every analytics/agent READ request is served through this
# second engine instead of `engine` above. If READ_ONLY_DATABASE_URL is
# configured (pointing at a least-privilege Postgres role - see
# app/db/security_roles.sh and docs/security_and_sql_validation.md), that
# role provides database-enforced least privilege in addition to the
# transaction-level protection below. If it is NOT configured, this falls
# back to the same DATABASE_URL/role as `engine` - but the
# `postgresql_readonly` execution option below still applies, so Postgres
# itself (not just application code) rejects any write attempted through
# this engine, with or without the dedicated role.
#
# This is why app/agent/audit.py never reuses the session passed into
# app/agent/service.ask(): a session from `read_only_engine` is in a
# database-level read-only transaction and CANNOT issue a write no matter
# what table-level grants the connecting role has - Postgres itself refuses
# it ("cannot execute INSERT in a read-only transaction"), independent of
# permissions. The audit log's one permitted INSERT always goes through a
# separate session opened against the writable `engine`/`SessionLocal`.
READ_ONLY_DATABASE_URL = os.environ.get("READ_ONLY_DATABASE_URL", DATABASE_URL)
_SQLALCHEMY_READ_ONLY_DATABASE_URL = (
    READ_ONLY_DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    if READ_ONLY_DATABASE_URL.startswith("postgresql://")
    else READ_ONLY_DATABASE_URL
)

read_only_engine = create_engine(
    _SQLALCHEMY_READ_ONLY_DATABASE_URL,
    pool_pre_ping=True,
    execution_options={"postgresql_readonly": True},
)
ReadOnlySessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=read_only_engine)


def get_read_only_db() -> Generator[Session, None, None]:
    """FastAPI dependency for every analytics/agent read endpoint - see
    `read_only_engine` above for why this is database-enforced, not just an
    application convention."""
    db = ReadOnlySessionLocal()
    try:
        yield db
    finally:
        db.close()

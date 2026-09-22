"""Raw psycopg2 connection helper for the ingestion pipeline.

The API (app/db/session.py) uses SQLAlchemy for ORM-style request-scoped
sessions. Ingestion instead does bulk batched upserts via `execute_values`,
which is most directly and efficiently expressed with plain psycopg2 -
so it gets its own thin connection helper rather than going through the ORM.
Both read DATABASE_URL from the same environment/`.env` convention.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg2
import psycopg2.extensions
import psycopg2.extras

# product_events.properties lands as a Python dict from JSONL parsing; adapt
# it to JSONB automatically wherever it's used as a query parameter.
psycopg2.extensions.register_adapter(dict, psycopg2.extras.Json)

# Ingestion runs on the host (not inside the Docker network), so it needs the
# host-mapped port (5433, see docker-compose.yml). HOST_DATABASE_URL takes
# precedence; DATABASE_URL is accepted as a fallback for whoever exports it.
DATABASE_URL = os.environ.get("HOST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", "postgresql://analytics:analytics@localhost:5433/growth_platform"
)


@contextmanager
def get_connection() -> Iterator[psycopg2.extensions.connection]:
    """Yield a psycopg2 connection; caller controls commit/rollback."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()

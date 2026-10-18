"""Load .env once for the whole test session so integration tests that need
HOST_DATABASE_URL/DATABASE_URL behave the same under pytest as under the CLI.
"""
import os

from dotenv import load_dotenv

load_dotenv()

# app/db/session.py (the API's SQLAlchemy engine, Phase 8) reads DATABASE_URL
# specifically - which .env points at the Docker-internal "postgres" host,
# unreachable from the host process pytest runs in. Tests run on the host,
# like every other integration test in this project, so DATABASE_URL is
# mirrored to HOST_DATABASE_URL here, once, before any test imports app.main
# (which creates its engine at import time). ingestion/db.py is unaffected -
# it already prefers HOST_DATABASE_URL over DATABASE_URL on its own.
if os.environ.get("HOST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["HOST_DATABASE_URL"]

# Same mirroring for Phase 10's read-only role/engine (app/db/session.py's
# read_only_engine) - HOST_READ_ONLY_DATABASE_URL is the host-reachable twin
# of READ_ONLY_DATABASE_URL, which otherwise points at the Docker-internal
# "postgres" host.
if os.environ.get("HOST_READ_ONLY_DATABASE_URL"):
    os.environ["READ_ONLY_DATABASE_URL"] = os.environ["HOST_READ_ONLY_DATABASE_URL"]

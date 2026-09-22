"""Failure-path tests: missing source file, invalid CLI source, and CLI exit codes.

Requires a live Postgres (skips cleanly if unavailable) since a FAILED audit
row can only be verified by actually writing one.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import psycopg2
import pytest

from ingestion.db import DATABASE_URL, get_connection
from ingestion.pipeline import ingest_source
from ingestion.registry import SOURCES


def _db_available() -> bool:
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=3)
        conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="no live Postgres reachable")


def test_missing_source_file_raises_and_records_failed_audit(tmp_path):
    spec = SOURCES["organizations"]
    empty_dir = tmp_path  # no organizations.csv here

    with pytest.raises(FileNotFoundError):
        ingest_source(spec, empty_dir, batch_size=10)

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT status, error_message FROM monitoring.ingestion_runs
                WHERE source_name = 'organizations' AND status = 'FAILED'
                ORDER BY started_at DESC LIMIT 1
                """
        )
        status, error_message = cur.fetchone()

    assert status == "FAILED"
    assert "not found" in error_message


def test_cli_exits_nonzero_on_unknown_source():
    result = subprocess.run(
        [sys.executable, "-m", "ingestion.pipeline", "--source", "not_a_real_source"],
        cwd=Path(__file__).parent.parent.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0


def test_cli_exits_nonzero_when_source_file_missing(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ingestion.pipeline",
            "--source",
            "organizations",
            "--data-dir",
            str(tmp_path),
        ],
        cwd=Path(__file__).parent.parent.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0

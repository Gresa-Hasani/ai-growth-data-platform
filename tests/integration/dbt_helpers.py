"""Shared helper for invoking the real dbt CLI from integration tests.

These tests prove actual behavior (real dbt runs against the live dev
database, with isolated itest_-prefixed fixture rows), not just that SQL
compiles or that config variables exist.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DBT_DIR = Path(__file__).parent.parent.parent / "dbt"
DBT_EXE = Path(sys.executable).parent / ("dbt.exe" if sys.platform == "win32" else "dbt")


def run_dbt(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run the dbt CLI from the dbt/ project directory, inheriting the
    current environment (which already has HOST_DATABASE_URL/
    DBT_POSTGRES_HOST/DBT_POSTGRES_PORT loaded via tests/conftest.py's
    load_dotenv()).
    """
    return subprocess.run(
        [str(DBT_EXE), *args],
        cwd=DBT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )

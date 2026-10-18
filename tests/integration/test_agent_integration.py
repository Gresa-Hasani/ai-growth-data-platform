"""Integration tests proving the agent's end-to-end flow against the real
dev database - real service-layer queries, real monitoring.ai_query_logs
writes. Skips cleanly if no live Postgres is reachable. The LLM provider
itself is NOT exercised for real here (that's the separate, explicitly
bounded tests/integration/test_agent_live_smoke.py) - these tests force the
deterministic rule-based planner path by using an unreachable provider
configuration, so they are fast and don't depend on Ollama being up.
"""
from __future__ import annotations

from pathlib import Path

import psycopg2
import pytest

from app.agent.audit import ai_query_logs_available
from app.agent.config import AgentSettings
from app.agent.exceptions import ClarificationNeededError, InvalidPlanError
from app.agent.service import ask
from app.db.session import DATABASE_URL, SessionLocal

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _db_available() -> bool:
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=3)
        conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="no live Postgres reachable")

# Deliberately points at an unreachable Ollama so every test here exercises
# the deterministic rule-based planner fallback, never real inference.
_OFFLINE_SETTINGS = AgentSettings(llm_provider="ollama", ollama_base_url="http://localhost:1", llm_timeout_seconds=2.0)


def test_ai_query_logs_table_initialization_is_idempotent():
    sql = (PROJECT_ROOT / "app" / "db" / "ai_query_logs.sql").read_text(encoding="utf-8")
    db = SessionLocal()
    try:
        from sqlalchemy import text

        db.execute(text(sql))  # running it a second (here, Nth) time must not error
        db.commit()
        assert ai_query_logs_available(db) is True
    finally:
        db.close()


def test_ask_mrr_question_against_real_data_and_writes_audit_log():
    db = SessionLocal()
    try:
        before = _count_logs(db)
        result = ask(db, "What is our current MRR?", settings=_OFFLINE_SETTINGS)
        assert result.metadata.used_real_inference is False
        assert result.answer.data_sources == ["analytics.mart_mrr"]
        after = _count_logs(db)
        assert after == before + 1
    finally:
        db.close()


def test_ask_unsupported_question_raises_and_still_logs_outcome():
    db = SessionLocal()
    try:
        before = _count_logs(db)
        with pytest.raises(InvalidPlanError):
            ask(db, "what's the weather like today?", settings=_OFFLINE_SETTINGS)
        after = _count_logs(db)
        assert after == before + 1
    finally:
        db.close()


def test_ask_customer_detail_without_organization_id_asks_for_clarification():
    db = SessionLocal()
    try:
        with pytest.raises(ClarificationNeededError):
            ask(db, "tell me about this customer", settings=_OFFLINE_SETTINGS)
    finally:
        db.close()


def test_ask_never_modifies_data_quality_results_table():
    db = SessionLocal()
    try:
        from sqlalchemy import text

        before = db.execute(text("SELECT count(*) FROM monitoring.data_quality_results")).scalar_one()
        ask(db, "What is our current MRR?", settings=_OFFLINE_SETTINGS)
        after = db.execute(text("SELECT count(*) FROM monitoring.data_quality_results")).scalar_one()
        assert before == after
    finally:
        db.close()


def _count_logs(db) -> int:
    from sqlalchemy import text

    return db.execute(text("SELECT count(*) FROM monitoring.ai_query_logs")).scalar_one()

"""Audit-log tests. Use a minimal fake Session that records executed SQL/
params rather than a real database, so these run without PostgreSQL - the
live-DB path (does the table actually exist, does an INSERT actually
succeed against real Postgres) is covered by
tests/integration/test_agent_audit_integration.py instead.
"""
from __future__ import annotations

import logging

import pytest

from app.agent.audit import ai_query_logs_available, log_query
from app.agent.config import AgentSettings
from app.agent.models import AgentExecutionMetadata


class _RecordingSession:
    def __init__(self):
        self.executed: list[tuple] = []
        self.committed = False
        self.rolled_back = False

    def execute(self, stmt, params=None):
        self.executed.append((stmt, params))
        return self

    def scalar_one(self):
        return True

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _FailingSession(_RecordingSession):
    def execute(self, stmt, params=None):
        raise RuntimeError("relation \"monitoring.ai_query_logs\" does not exist, dsn=postgresql://user:pass@host/db")


def _metadata(**overrides) -> AgentExecutionMetadata:
    base = {
        "provider": "ollama", "model": "qwen3:8b", "used_real_inference": True,
        "latency_ms": 10, "tool_invocation_count": 1, "error_category": None,
    }
    base.update(overrides)
    return AgentExecutionMetadata(**base)


def test_log_query_inserts_safe_metadata_only_and_commits():
    db = _RecordingSession()
    settings = AgentSettings()
    log_query(db, settings, _metadata(), "success", intent="revenue", metric_id="mrr")
    assert db.committed is True
    _, params = db.executed[0]
    assert params["provider"] == "ollama"
    assert params["outcome"] == "success"
    assert "question_text" not in params
    assert "answer_text" not in params


def test_log_query_never_persists_credentials_or_connection_strings():
    db = _RecordingSession()
    settings = AgentSettings()
    log_query(db, settings, _metadata(error_category="tool_execution_failure"), "error")
    _, params = db.executed[0]
    serialized = str(params)
    assert "postgresql://" not in serialized
    assert "password" not in serialized.lower()


def test_log_query_failure_is_swallowed_by_default_non_blocking_policy(caplog):
    db = _FailingSession()
    settings = AgentSettings(agent_audit_log_failures_block_request=False)
    with caplog.at_level(logging.WARNING):
        log_query(db, settings, _metadata(), "success")  # must not raise
    assert db.rolled_back is True
    assert any("ai_query_logs insert failed" in r.message for r in caplog.records)


def test_log_query_failure_raises_when_policy_configured_to_block():
    db = _FailingSession()
    settings = AgentSettings(agent_audit_log_failures_block_request=True)
    with pytest.raises(RuntimeError):
        log_query(db, settings, _metadata(), "success")


def test_ai_query_logs_available_reflects_to_regclass_result():
    class _Session:
        def execute(self, stmt, params=None):
            return self

        def scalar_one(self):
            return False

    assert ai_query_logs_available(_Session()) is False

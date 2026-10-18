"""API-layer tests for POST /api/v1/agent/ask and GET /api/v1/agent/health.
`app.agent.service.ask`/`check_health` are monkeypatched directly so these
never touch a real database or a real LLM provider - they only verify the
HTTP contract: status codes, response shape, and that nothing unsafe (stack
traces, provider payloads, credentials) ever reaches the client. A separate
test confirms the pre-existing Phase 1-8 endpoints are unaffected by this
router being added.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.agent import service
from app.agent.exceptions import (
    ClarificationNeededError,
    InvalidPlanError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ToolExecutionError,
)
from app.agent.models import AgentAnswer, AgentExecutionMetadata
from app.agent.providers.base import ProviderHealth
from app.agent.service import AgentAskResult
from app.main import app

client = TestClient(app)


def _make_result(answer_text: str = "Current USD MRR is $12,345.") -> AgentAskResult:
    answer = AgentAnswer(
        answer=answer_text,
        key_findings=["USD MRR: 12345"],
        data_sources=["analytics.mart_mrr"],
        applied_filters={"currency": "USD"},
        currency="USD",
        data_as_of="2026-10-16T00:00:00+00:00",
        limitations=["point-in-time snapshot"],
        warnings=[],
        follow_up_suggestions=["What about ARR?"],
    )
    metadata = AgentExecutionMetadata(
        provider="ollama", model="qwen3:8b", used_real_inference=True, latency_ms=42, tool_invocation_count=1,
    )
    return AgentAskResult(answer=answer, metadata=metadata)


def test_ask_success_returns_grounded_answer(monkeypatch):
    monkeypatch.setattr(service, "ask", lambda db, question: _make_result())
    response = client.post("/api/v1/agent/ask", json={"question": "What is our current MRR?"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Current USD MRR is $12,345."
    assert body["currency"] == "USD"
    assert body["meta"]["provider"] == "ollama"
    assert body["meta"]["used_real_inference"] is True


def test_ask_rejects_empty_question():
    response = client.post("/api/v1/agent/ask", json={"question": ""})
    assert response.status_code == 422


def test_ask_rejects_oversized_question():
    response = client.post("/api/v1/agent/ask", json={"question": "x" * 5000})
    assert response.status_code == 422


def test_ask_rejects_missing_question_field():
    response = client.post("/api/v1/agent/ask", json={})
    assert response.status_code == 422


def test_ask_unsupported_question_returns_400(monkeypatch):
    def raise_unsupported(db, question):
        raise InvalidPlanError("question does not map to any supported metric in the catalog")

    monkeypatch.setattr(service, "ask", raise_unsupported)
    response = client.post("/api/v1/agent/ask", json={"question": "what's the weather today?"})
    assert response.status_code == 400
    assert "stack" not in response.text.lower()
    assert "traceback" not in response.text.lower()


def test_ask_clarification_needed_returns_422_with_question(monkeypatch):
    def raise_clarification(db, question):
        raise ClarificationNeededError("Which organization_id should I look up?")

    monkeypatch.setattr(service, "ask", raise_clarification)
    response = client.post("/api/v1/agent/ask", json={"question": "tell me about this customer"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Which organization_id should I look up?"


def test_ask_provider_unavailable_returns_503(monkeypatch):
    def raise_unavailable(db, question):
        raise ProviderUnavailableError("Ollama unreachable at http://internal-host:11434")

    monkeypatch.setattr(service, "ask", raise_unavailable)
    response = client.post("/api/v1/agent/ask", json={"question": "what is our MRR?"})
    assert response.status_code == 503
    assert "internal-host" not in response.text


def test_ask_provider_timeout_returns_504(monkeypatch):
    def raise_timeout(db, question):
        raise ProviderTimeoutError("Ollama request timed out after 30.0s")

    monkeypatch.setattr(service, "ask", raise_timeout)
    response = client.post("/api/v1/agent/ask", json={"question": "what is our MRR?"})
    assert response.status_code == 504


def test_ask_tool_execution_failure_returns_502_without_leaking_details(monkeypatch):
    def raise_tool_failure(db, question):
        raise ToolExecutionError("tool execution failed: OperationalError")

    monkeypatch.setattr(service, "ask", raise_tool_failure)
    response = client.post("/api/v1/agent/ask", json={"question": "what is our MRR?"})
    assert response.status_code == 502
    assert "OperationalError" not in response.text


def test_health_endpoint_reflects_provider_health(monkeypatch):
    monkeypatch.setattr(
        service, "check_health", lambda: ProviderHealth(provider="ollama", model="qwen3:8b", reachable=True, detail="ok")
    )
    response = client.get("/api/v1/agent/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"provider": "ollama", "model": "qwen3:8b", "reachable": True, "detail": "ok"}


def test_health_endpoint_reports_unreachable_honestly_not_as_success(monkeypatch):
    monkeypatch.setattr(
        service, "check_health", lambda: ProviderHealth(provider="ollama", model="qwen3:8b", reachable=False, detail="unreachable: ConnectError")
    )
    response = client.get("/api/v1/agent/health")
    assert response.status_code == 200  # health check itself succeeded; reachable=False is the honest payload
    assert response.json()["reachable"] is False


def test_existing_phase8_endpoints_still_registered_after_agent_router_added():
    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/v1/analytics/growth/daily" in paths
    assert "/api/v1/analytics/revenue/mrr" in paths
    assert "/api/v1/agent/ask" in paths
    assert "/api/v1/agent/health" in paths

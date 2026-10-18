"""Provider contract tests - all HTTP is deterministically mocked via
monkeypatch; none of these require a running Ollama server or any
commercial API credentials. A separate, explicitly bounded live smoke test
(tests/integration/test_agent_live_smoke.py) covers real inference.
"""
from __future__ import annotations

import httpx
import pytest

from app.agent.config import AgentSettings
from app.agent.exceptions import (
    InvalidModelOutputError,
    ModelNotFoundError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.agent.providers.factory import build_provider
from app.agent.providers.ollama import OllamaProvider


def _settings(**overrides) -> AgentSettings:
    base = {"llm_provider": "ollama"}
    base.update(overrides)
    return AgentSettings(**base)


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None):
        self.status_code = status_code
        self._json_body = json_body or {}

    def json(self):
        return self._json_body


def test_ollama_generate_json_success(monkeypatch):
    provider = OllamaProvider(_settings())

    def fake_post(url, json, timeout):
        assert url.endswith("/api/generate")
        assert json["format"] == "json"
        assert json["think"] is False
        return _FakeResponse(200, {"response": '{"intent": "growth"}'})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = provider.generate_json("system", "user")
    assert result == {"intent": "growth"}


def test_ollama_generate_json_timeout(monkeypatch):
    provider = OllamaProvider(_settings())

    def fake_post(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(ProviderTimeoutError):
        provider.generate_json("system", "user")


def test_ollama_generate_json_unreachable(monkeypatch):
    provider = OllamaProvider(_settings())

    def fake_post(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(ProviderUnavailableError):
        provider.generate_json("system", "user")


def test_ollama_generate_json_model_not_found(monkeypatch):
    provider = OllamaProvider(_settings())
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(404))
    with pytest.raises(ModelNotFoundError):
        provider.generate_json("system", "user")


def test_ollama_generate_json_non_200(monkeypatch):
    provider = OllamaProvider(_settings())
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(500))
    with pytest.raises(ProviderUnavailableError):
        provider.generate_json("system", "user")


def test_ollama_generate_json_malformed_json(monkeypatch):
    provider = OllamaProvider(_settings())
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(200, {"response": "not json"}))
    with pytest.raises(InvalidModelOutputError):
        provider.generate_json("system", "user")


def test_ollama_generate_json_missing_response_field(monkeypatch):
    provider = OllamaProvider(_settings())
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(200, {"unexpected": "shape"}))
    with pytest.raises(InvalidModelOutputError):
        provider.generate_json("system", "user")


def test_ollama_health_check_reachable_model_pulled(monkeypatch):
    provider = OllamaProvider(_settings(ollama_model="qwen3:8b"))
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse(200, {"models": [{"name": "qwen3:8b"}]})
    )
    health = provider.health_check()
    assert health.reachable is True
    assert health.detail == "ok"


def test_ollama_health_check_model_not_pulled(monkeypatch):
    provider = OllamaProvider(_settings(ollama_model="qwen3:8b"))
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, {"models": [{"name": "llama3"}]}))
    health = provider.health_check()
    assert health.reachable is True
    assert "not pulled yet" in health.detail


def test_ollama_health_check_unreachable_never_raises(monkeypatch):
    provider = OllamaProvider(_settings())

    def fake_get(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", fake_get)
    health = provider.health_check()
    assert health.reachable is False


def test_factory_defaults_to_ollama_without_any_credentials():
    settings = AgentSettings(llm_provider="ollama", openai_api_key="", anthropic_api_key="")
    provider = build_provider(settings)
    assert provider.name == "ollama"


def test_factory_never_falls_back_to_a_commercial_provider_on_bad_name():
    settings = AgentSettings(llm_provider="not-a-real-provider")
    with pytest.raises(ProviderUnavailableError):
        build_provider(settings)


def test_factory_openai_requires_explicit_api_key():
    settings = AgentSettings(llm_provider="openai", openai_api_key="")
    with pytest.raises(ProviderUnavailableError):
        build_provider(settings)


def test_factory_anthropic_requires_explicit_api_key():
    settings = AgentSettings(llm_provider="anthropic", anthropic_api_key="")
    with pytest.raises(ProviderUnavailableError):
        build_provider(settings)


def test_openai_and_anthropic_are_never_instantiated_in_default_mode(monkeypatch):
    """Proves the default (ollama) path never even imports the optional
    provider modules - selecting Ollama cannot accidentally reach out to a
    commercial API."""
    import sys

    sys.modules.pop("app.agent.providers.openai", None)
    sys.modules.pop("app.agent.providers.anthropic", None)

    settings = AgentSettings(llm_provider="ollama")
    build_provider(settings)

    assert "app.agent.providers.openai" not in sys.modules
    assert "app.agent.providers.anthropic" not in sys.modules

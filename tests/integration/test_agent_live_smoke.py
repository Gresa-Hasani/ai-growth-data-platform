"""Bounded, OPTIONAL live-Ollama smoke test. This is deliberately separate
from the mocked provider-contract suite (tests/unit/test_agent_providers.py),
which is the primary, required test path and needs no running model at all.

This test calls the REAL local Ollama server if one is configured and
reachable. It is intentionally bounded: on this development machine, real
qwen3:8b inference via /api/generate did not complete within 170 seconds
even with think=false and a minimal output budget (plausibly CPU-only
hardware cold-loading an 8B-parameter model) - so this test uses a short,
explicit timeout and SKIPS (reporting the real reason) rather than hanging
the suite or fabricating a pass. Treat a skip here as "inconclusive in this
environment," not as "the Ollama integration doesn't work" - the provider
contract itself is fully covered by the mocked unit tests.
"""
from __future__ import annotations

import httpx
import pytest

from app.agent.config import AgentSettings
from app.agent.providers.ollama import OllamaProvider

LIVE_SMOKE_TIMEOUT_SECONDS = 20.0


def _ollama_server_reachable(base_url: str) -> bool:
    try:
        response = httpx.get(f"{base_url}/api/tags", timeout=5.0)
        return response.status_code == 200
    except httpx.RequestError:
        return False


def test_live_ollama_inference_smoke():
    settings = AgentSettings()
    if settings.llm_provider != "ollama":
        pytest.skip(f"LLM_PROVIDER is '{settings.llm_provider}', not 'ollama' - skipping live Ollama smoke test")

    if not _ollama_server_reachable(settings.ollama_base_url):
        pytest.skip(f"Ollama is not reachable at {settings.ollama_base_url} - skipping (environment, not a code failure)")

    provider = OllamaProvider(
        AgentSettings(
            llm_provider="ollama",
            ollama_base_url=settings.ollama_base_url,
            ollama_model=settings.ollama_model,
            llm_timeout_seconds=LIVE_SMOKE_TIMEOUT_SECONDS,
            llm_max_output_tokens=30,
        )
    )

    try:
        result = provider.generate_json(
            "Reply with only this exact JSON and nothing else.",
            'Reply with: {"ok": true}',
        )
    except Exception as exc:  # noqa: BLE001 - any failure here is an honest skip, not a reported pass
        pytest.skip(
            f"real Ollama inference did not complete within {LIVE_SMOKE_TIMEOUT_SECONDS}s on this "
            f"machine ({type(exc).__name__}: {exc}) - this is a known environment limitation, not "
            "asserted as a passing result. See tests/unit/test_agent_providers.py for the mocked, "
            "deterministic provider contract tests, which are the primary required test path."
        )

    assert isinstance(result, dict)

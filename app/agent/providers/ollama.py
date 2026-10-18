"""Ollama provider - the default, local-only LLM backend. No data ever
leaves the machine running Ollama; this is why Ollama (not a commercial
API) is the default provider for this project.
"""
from __future__ import annotations

import json

import httpx

from app.agent.config import AgentSettings
from app.agent.exceptions import (
    InvalidModelOutputError,
    ModelNotFoundError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.agent.providers.base import LLMProvider, ProviderHealth


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, settings: AgentSettings) -> None:
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._model = settings.ollama_model
        self._timeout = settings.llm_timeout_seconds
        self._max_tokens = settings.llm_max_output_tokens

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        prompt = f"{system_prompt}\n\n{user_prompt}"
        try:
            response = httpx.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "format": "json",
                    "stream": False,
                    # qwen3 (and other hybrid-reasoning models) support
                    # disabling the "thinking" pass via this option on
                    # recent Ollama versions - skipping it matters a lot
                    # for latency on a structured, single-turn extraction
                    # task like this, where the reasoning trace itself is
                    # never shown to the user anyway. Ignored harmlessly by
                    # models/Ollama versions that don't support it.
                    "think": False,
                    "options": {"num_predict": self._max_tokens},
                },
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"Ollama request timed out after {self._timeout}s") from exc
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(f"Ollama unreachable at {self._base_url}") from exc

        if response.status_code == 404:
            raise ModelNotFoundError(f"Ollama model '{self._model}' not found - pull it with `ollama pull {self._model}`")
        if response.status_code != 200:
            raise ProviderUnavailableError(f"Ollama returned HTTP {response.status_code}")

        try:
            body = response.json()
            raw_text = body["response"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise InvalidModelOutputError("Ollama response missing expected 'response' field") from exc

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise InvalidModelOutputError(f"model output was not valid JSON: {raw_text[:200]!r}") from exc

    def health_check(self) -> ProviderHealth:
        try:
            response = httpx.get(f"{self._base_url}/api/tags", timeout=5.0)
        except httpx.RequestError as exc:
            return ProviderHealth(provider=self.name, model=self._model, reachable=False, detail=f"unreachable: {type(exc).__name__}")

        if response.status_code != 200:
            return ProviderHealth(provider=self.name, model=self._model, reachable=False, detail=f"HTTP {response.status_code}")

        try:
            models = [m["name"] for m in response.json().get("models", [])]
        except (json.JSONDecodeError, KeyError):
            return ProviderHealth(provider=self.name, model=self._model, reachable=True, detail="unexpected response shape")

        if self._model not in models:
            return ProviderHealth(
                provider=self.name, model=self._model, reachable=True,
                detail=f"model '{self._model}' not pulled yet (available: {', '.join(models) or 'none'})",
            )
        return ProviderHealth(provider=self.name, model=self._model, reachable=True, detail="ok")

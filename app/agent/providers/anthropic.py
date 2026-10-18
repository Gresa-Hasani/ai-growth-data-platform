"""Anthropic provider - optional, opt-in only (LLM_PROVIDER=anthropic). The
`anthropic` package is an optional dependency (`pip install -e ".[anthropic]"`);
importing it is deferred to inside __init__ so selecting Ollama (the
default) never requires the package to be installed.
"""
from __future__ import annotations

from app.agent.config import AgentSettings
from app.agent.exceptions import (
    InvalidModelOutputError,
    ModelNotFoundError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.agent.providers.base import LLMProvider, ProviderHealth


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, settings: AgentSettings) -> None:
        if not settings.anthropic_api_key:
            raise ProviderUnavailableError(
                "LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY - none configured"
            )
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderUnavailableError(
                "the 'anthropic' package is not installed - run `pip install -e \".[anthropic]\"`"
            ) from exc

        self._model = settings.anthropic_model
        self._timeout = settings.llm_timeout_seconds
        self._max_tokens = settings.llm_max_output_tokens
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=self._timeout)

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        import json

        import anthropic as anthropic_module

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic_module.APITimeoutError as exc:
            raise ProviderTimeoutError(f"Anthropic request timed out after {self._timeout}s") from exc
        except anthropic_module.NotFoundError as exc:
            raise ModelNotFoundError(f"Anthropic model '{self._model}' not found") from exc
        except anthropic_module.APIConnectionError as exc:
            raise ProviderUnavailableError("Anthropic unreachable") from exc
        except anthropic_module.APIError as exc:
            raise ProviderUnavailableError(f"Anthropic API error: {type(exc).__name__}") from exc

        raw_text = "".join(block.text for block in response.content if block.type == "text")
        raw_text = raw_text.strip()
        # Anthropic has no forced JSON mode - tolerate a fenced ```json block.
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`")
            raw_text = raw_text.removeprefix("json")
            raw_text = raw_text.strip()
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise InvalidModelOutputError(f"model output was not valid JSON: {raw_text[:200]!r}") from exc

    def health_check(self) -> ProviderHealth:
        try:
            self._client.messages.create(
                model=self._model,
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
        except Exception as exc:  # noqa: BLE001 - health check must never raise
            return ProviderHealth(provider=self.name, model=self._model, reachable=False, detail=type(exc).__name__)
        return ProviderHealth(provider=self.name, model=self._model, reachable=True, detail="ok")

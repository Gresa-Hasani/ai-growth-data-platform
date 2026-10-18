"""OpenAI provider - optional, opt-in only (LLM_PROVIDER=openai). The
`openai` package is an optional dependency (`pip install -e ".[openai]"`);
importing this module is deferred to inside __init__ so selecting Ollama
(the default) never requires the package to be installed.
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


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, settings: AgentSettings) -> None:
        if not settings.openai_api_key:
            raise ProviderUnavailableError(
                "LLM_PROVIDER=openai requires OPENAI_API_KEY - none configured"
            )
        try:
            import openai
        except ImportError as exc:
            raise ProviderUnavailableError(
                "the 'openai' package is not installed - run `pip install -e \".[openai]\"`"
            ) from exc

        self._model = settings.openai_model
        self._timeout = settings.llm_timeout_seconds
        self._max_tokens = settings.llm_max_output_tokens
        self._client = openai.OpenAI(api_key=settings.openai_api_key, timeout=self._timeout)

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        import json

        import openai as openai_module

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                max_tokens=self._max_tokens,
            )
        except openai_module.APITimeoutError as exc:
            raise ProviderTimeoutError(f"OpenAI request timed out after {self._timeout}s") from exc
        except openai_module.NotFoundError as exc:
            raise ModelNotFoundError(f"OpenAI model '{self._model}' not found") from exc
        except openai_module.APIConnectionError as exc:
            raise ProviderUnavailableError("OpenAI unreachable") from exc
        except openai_module.APIError as exc:
            raise ProviderUnavailableError(f"OpenAI API error: {type(exc).__name__}") from exc

        raw_text = response.choices[0].message.content or ""
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise InvalidModelOutputError(f"model output was not valid JSON: {raw_text[:200]!r}") from exc

    def health_check(self) -> ProviderHealth:
        try:
            self._client.models.retrieve(self._model)
        except Exception as exc:  # noqa: BLE001 - health check must never raise
            return ProviderHealth(provider=self.name, model=self._model, reachable=False, detail=type(exc).__name__)
        return ProviderHealth(provider=self.name, model=self._model, reachable=True, detail="ok")

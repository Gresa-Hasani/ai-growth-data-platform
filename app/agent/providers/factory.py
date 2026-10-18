"""Provider selection from AgentSettings.llm_provider. This is the ONLY
place a provider is instantiated - Ollama by default, OpenAI/Anthropic only
when explicitly selected via LLM_PROVIDER. There is no fallback between
providers: if the explicitly-selected provider's credentials/SDK are
missing, this raises rather than silently switching to another provider.
"""
from __future__ import annotations

from app.agent.config import AgentSettings
from app.agent.exceptions import ProviderUnavailableError
from app.agent.providers.base import LLMProvider


def build_provider(settings: AgentSettings) -> LLMProvider:
    provider = settings.llm_provider.strip().lower()

    if provider == "ollama":
        from app.agent.providers.ollama import OllamaProvider

        return OllamaProvider(settings)

    if provider == "openai":
        from app.agent.providers.openai import OpenAIProvider

        return OpenAIProvider(settings)

    if provider == "anthropic":
        from app.agent.providers.anthropic import AnthropicProvider

        return AnthropicProvider(settings)

    raise ProviderUnavailableError(
        f"unknown LLM_PROVIDER '{settings.llm_provider}' - expected 'ollama', 'openai', or 'anthropic'"
    )

"""Agent-specific configuration - environment-driven, no secrets baked in.
Separate from app/config.py (API-wide settings) since these are specific
to LLM provider selection and bounding the agent's own execution.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="")

    # Default provider is Ollama - never an implicit external fallback.
    # A provider change must be explicit (LLM_PROVIDER=openai/anthropic),
    # never automatic just because Ollama happens to be unreachable.
    llm_provider: str = "ollama"

    # Ollama. In Docker, "localhost" refers to the API CONTAINER itself,
    # not the host running Ollama - set OLLAMA_BASE_URL to
    # http://host.docker.internal:11434 (Docker Desktop) or the host's LAN
    # IP when running Ollama on the host and the API in a container. See
    # docs/ai_data_analyst_agent.md "Docker networking".
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"

    # Optional external providers - opt-in only, never called unless
    # llm_provider is explicitly set to "openai"/"anthropic".
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-haiku-latest"

    llm_timeout_seconds: float = 30.0
    llm_max_output_tokens: int = 1024
    llm_max_tool_calls: int = 1  # one metric -> one tool call per question, by design
    llm_max_question_length: int = 500

    # Audit logging policy (see app/agent/audit.py): a logging failure
    # never blocks the analytical answer from being returned to the user -
    # it is logged to the application logger instead. Documented, not
    # silent either way.
    agent_audit_log_failures_block_request: bool = False


settings = AgentSettings()

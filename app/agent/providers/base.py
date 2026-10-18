"""Provider-neutral LLM interface. Orchestration code (planner.py,
answer.py) depends only on this abstract contract, never on a specific
vendor - so adding/removing a provider never touches orchestration logic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class ProviderHealth(BaseModel):
    provider: str
    model: str | None
    reachable: bool
    detail: str | None = None


class LLMProvider(ABC):
    """Every method here must raise one of app.agent.exceptions' typed
    errors on failure - never a bare Exception, so the service layer can
    distinguish provider-unavailable from model-not-found from timeout
    from malformed output.
    """

    name: str

    @abstractmethod
    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        """Returns a parsed JSON dict from the model's structured/JSON-mode
        output. Raises ProviderUnavailableError / ModelNotFoundError /
        ProviderTimeoutError / InvalidModelOutputError as appropriate -
        never returns a malformed or partial dict silently."""

    @abstractmethod
    def health_check(self) -> ProviderHealth:
        """A short, bounded check (e.g. list models / ping) - never a full
        inference call. Must not raise; failures are reported via the
        returned ProviderHealth.reachable=False."""

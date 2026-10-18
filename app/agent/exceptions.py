"""Explicit agent/provider/validation error categories - used both to pick
the right HTTP status in the API layer and as the `error_category` value
persisted to monitoring.ai_query_logs. Keeping these as distinct exception
types (not one generic AgentError) is what lets the service layer tell
"provider unavailable" apart from "unsupported question" apart from "tool
execution failure" rather than collapsing every failure into a fabricated
answer.
"""
from __future__ import annotations


class AgentError(Exception):
    """Base class. `category` is the safe, stable string persisted to the
    audit log and returned to the API layer - never a raw exception message
    that might contain internal details."""

    category: str = "unknown_error"


class ProviderUnavailableError(AgentError):
    category = "provider_unavailable"


class ModelNotFoundError(AgentError):
    category = "model_not_found"


class ProviderTimeoutError(AgentError):
    category = "timeout"


class InvalidModelOutputError(AgentError):
    category = "invalid_model_output"


class InvalidPlanError(AgentError):
    category = "invalid_plan"


class UnsupportedQuestionError(AgentError):
    category = "unsupported_question"


class ClarificationNeededError(AgentError):
    """Not a failure - a legitimate, documented outcome. Carries the
    clarification question to surface back to the user."""

    category = "clarification_needed"

    def __init__(self, clarification_question: str) -> None:
        super().__init__(clarification_question)
        self.clarification_question = clarification_question


class ToolExecutionError(AgentError):
    category = "tool_execution_failure"


class InvalidAnswerError(AgentError):
    category = "invalid_answer"

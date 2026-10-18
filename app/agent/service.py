"""End-to-end orchestration for the AI Data Analyst Agent.

Bounded flow:
 1. Validate question length (AgentSettings.llm_max_question_length).
 2. Build the configured provider (factory.build_provider) - never an
    implicit fallback to a different provider.
 3. Ask the provider for a plan (planner.build_plan); if the provider is
    unavailable/times out/returns invalid output, fall back to
    planner.rule_based_plan (deterministic, conservative).
 4. Validate the plan against the catalog (planner.validate_plan) - already
    done inside build_plan/rule_based_plan, re-raised here as needed.
 5. Catch ClarificationNeededError and return early - not a failure.
 6. Execute exactly one tool call (executor.execute_plan).
 7. Catch ToolExecutionError (including "empty but valid" results, which are
    NOT an error - they are returned as a normal, empty-data answer).
 8. Generate the answer (answer.build_answer), falling back to
    answer.template_answer if the provider is unavailable.
 9. Validate the answer schema (already enforced by build_answer raising
    InvalidAnswerError on mismatch).
10. Record safe audit metadata (audit.log_query) - non-blocking by policy.
11. Return AgentAnswer to the caller.
12. Any uncaught failure category is mapped to a safe, typed AgentError by
    the API layer - never a raw stack trace / credential / connection string.
"""
from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from app.agent import answer as answer_mod
from app.agent import planner
from app.agent.audit import ai_query_logs_available, log_query
from app.agent.catalog import get_entry
from app.agent.config import AgentSettings
from app.agent.config import settings as default_settings
from app.agent.exceptions import (
    AgentError,
    ClarificationNeededError,
    InvalidModelOutputError,
    InvalidPlanError,
    ModelNotFoundError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.agent.executor import execute_plan
from app.agent.models import AgentAnswer, AgentExecutionMetadata
from app.agent.providers.base import LLMProvider, ProviderHealth
from app.agent.providers.factory import build_provider

logger = logging.getLogger("app.agent.service")


class AgentAskResult:
    def __init__(self, answer: AgentAnswer, metadata: AgentExecutionMetadata) -> None:
        self.answer = answer
        self.metadata = metadata


def check_health(settings: AgentSettings | None = None) -> ProviderHealth:
    settings = settings or default_settings
    try:
        provider = build_provider(settings)
    except AgentError as exc:
        return ProviderHealth(provider=settings.llm_provider, model=None, reachable=False, detail=exc.category)
    return provider.health_check()


def ask(db: Session, question: str, settings: AgentSettings | None = None) -> AgentAskResult:
    settings = settings or default_settings
    start = time.monotonic()
    used_real_inference = False
    plan = None

    try:
        if not question or not question.strip():
            raise InvalidPlanError("question must not be empty")
        if len(question) > settings.llm_max_question_length:
            raise InvalidPlanError(
                f"question exceeds the maximum length of {settings.llm_max_question_length} characters"
            )

        provider: LLMProvider | None
        try:
            provider = build_provider(settings)
        except AgentError:
            provider = None

        if provider is not None:
            try:
                plan = planner.build_plan(provider, question)
                used_real_inference = True
            except ClarificationNeededError:
                raise
            except (ProviderUnavailableError, ModelNotFoundError, ProviderTimeoutError, InvalidModelOutputError, InvalidPlanError):
                plan = None  # fall through to the deterministic planner below

        if plan is None:
            plan = planner.rule_based_plan(question)
            used_real_inference = False

        entry = get_entry(plan.metric_id) if plan.metric_id else None
        if entry is None:
            raise InvalidPlanError("question does not map to any supported metric in the catalog")

        tool_result = execute_plan(db, plan)

        answer_provider = provider if used_real_inference else None
        try:
            agent_answer = answer_mod.build_answer(answer_provider, plan, entry, tool_result)
        except AgentError:
            agent_answer = answer_mod.template_answer(plan, entry, tool_result)
    except AgentError as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        metadata = AgentExecutionMetadata(
            provider=settings.llm_provider,
            model=_model_name(settings),
            used_real_inference=used_real_inference,
            latency_ms=latency_ms,
            tool_invocation_count=1 if plan is not None else 0,
            error_category=exc.category,
        )
        outcome = "clarification_needed" if isinstance(exc, ClarificationNeededError) else "error"
        _safe_audit(
            db, settings, metadata, outcome,
            plan.intent.value if plan else None,
            plan.metric_id.value if plan and plan.metric_id else None,
        )
        raise

    latency_ms = int((time.monotonic() - start) * 1000)
    metadata = AgentExecutionMetadata(
        provider=settings.llm_provider,
        model=_model_name(settings),
        used_real_inference=used_real_inference,
        latency_ms=latency_ms,
        tool_invocation_count=1,
        error_category=None,
    )
    _safe_audit(db, settings, metadata, "success", plan.intent.value, plan.metric_id.value if plan.metric_id else None)
    return AgentAskResult(answer=agent_answer, metadata=metadata)


def _model_name(settings: AgentSettings) -> str | None:
    if settings.llm_provider == "ollama":
        return settings.ollama_model
    if settings.llm_provider == "openai":
        return settings.openai_model
    if settings.llm_provider == "anthropic":
        return settings.anthropic_model
    return None


def _safe_audit(
    db: Session,
    settings: AgentSettings,
    metadata: AgentExecutionMetadata,
    outcome: str,
    intent: str | None,
    metric_id: str | None,
) -> None:
    try:
        if ai_query_logs_available(db):
            log_query(db, settings, metadata, outcome, intent, metric_id)
    except Exception as exc:  # noqa: BLE001 - audit is strictly non-blocking by policy
        logger.warning("agent audit logging failed (non-blocking): %s", type(exc).__name__)

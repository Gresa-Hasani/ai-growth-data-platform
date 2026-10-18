"""POST /api/v1/agent/ask, GET /api/v1/agent/health - the only HTTP surface
for the AI Data Analyst Agent. Never returns a raw exception message, stack
trace, or provider payload - every failure is mapped to a stable error code
via app.agent.exceptions' `category` and a safe, generic detail string.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agent import service
from app.agent.exceptions import (
    AgentError,
    ClarificationNeededError,
    InvalidPlanError,
    ModelNotFoundError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ToolExecutionError,
    UnsupportedQuestionError,
)
from app.db.session import get_read_only_db
from app.schemas.agent import (
    AgentAskRequest,
    AgentAskResponse,
    AgentAskResponseMeta,
    AgentHealthResponse,
)

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

_STATUS_BY_CATEGORY = {
    "provider_unavailable": 503,
    "model_not_found": 503,
    "timeout": 504,
    "invalid_model_output": 502,
    "invalid_plan": 400,
    "unsupported_question": 400,
    "tool_execution_failure": 502,
    "invalid_answer": 502,
}


@router.post(
    "/ask",
    response_model=AgentAskResponse,
    summary="Ask the AI Data Analyst Agent a natural-language analytics question",
    description=(
        "Answers are strictly grounded in data returned by the existing, "
        "read-only analytics service layer (Phase 8) - the agent cannot "
        "execute arbitrary SQL, write data, or trigger ingestion/dbt. See "
        "docs/ai_data_analyst_agent.md."
    ),
)
def ask(request: AgentAskRequest, db: Session = Depends(get_read_only_db)) -> AgentAskResponse:
    try:
        result = service.ask(db, request.question)
    except ClarificationNeededError as exc:
        raise HTTPException(status_code=422, detail=exc.clarification_question) from None
    except (InvalidPlanError, UnsupportedQuestionError):
        raise HTTPException(status_code=400, detail="the question could not be mapped to a supported metric") from None
    except (ProviderUnavailableError, ModelNotFoundError):
        raise HTTPException(status_code=503, detail="the configured LLM provider is unavailable") from None
    except ProviderTimeoutError:
        raise HTTPException(status_code=504, detail="the LLM provider timed out") from None
    except ToolExecutionError:
        raise HTTPException(status_code=502, detail="a downstream analytics tool failed") from None
    except AgentError as exc:
        status_code = _STATUS_BY_CATEGORY.get(exc.category, 500)
        raise HTTPException(status_code=status_code, detail="the agent could not answer this question") from None

    answer = result.answer
    return AgentAskResponse(
        answer=answer.answer,
        key_findings=answer.key_findings,
        data_sources=answer.data_sources,
        applied_filters=answer.applied_filters,
        currency=answer.currency,
        data_as_of=answer.data_as_of,
        limitations=answer.limitations,
        warnings=answer.warnings,
        follow_up_suggestions=answer.follow_up_suggestions,
        meta=AgentAskResponseMeta(
            provider=result.metadata.provider,
            used_real_inference=result.metadata.used_real_inference,
            latency_ms=result.metadata.latency_ms,
        ),
    )


@router.get(
    "/health",
    response_model=AgentHealthResponse,
    summary="Bounded health check for the configured LLM provider",
    description="A short, bounded liveness check (never a full inference call) - safe to poll frequently.",
)
def agent_health() -> AgentHealthResponse:
    health = service.check_health()
    return AgentHealthResponse(
        provider=health.provider,
        model=health.model,
        reachable=health.reachable,
        detail=health.detail,
    )

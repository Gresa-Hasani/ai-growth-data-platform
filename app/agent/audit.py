"""Writes safe metadata to monitoring.ai_query_logs. Documented policy (see
AgentSettings.agent_audit_log_failures_block_request, default False): a
logging failure is swallowed and reported to the application logger - it
never blocks or fails the analytical response the user is waiting on. By
default question_text/answer_text are NOT persisted (passing them is an
explicit opt-in call site decision, not a default here) to avoid storing
free-text content without a documented retention justification.
"""
from __future__ import annotations

import logging
import secrets

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agent.config import AgentSettings
from app.agent.models import AgentExecutionMetadata

logger = logging.getLogger("app.agent.audit")


def log_query(
    db: Session,
    settings: AgentSettings,
    metadata: AgentExecutionMetadata,
    outcome: str,
    intent: str | None = None,
    metric_id: str | None = None,
) -> None:
    """outcome in {"success", "clarification_needed", "error"}. Never raises
    - on any failure it logs via the standard logger and returns, per the
    documented non-blocking audit policy."""
    try:
        db.execute(
            text(
                """
                INSERT INTO monitoring.ai_query_logs
                    (query_log_id, provider, model, used_real_inference, intent,
                     metric_id, outcome, error_category, latency_ms,
                     tool_invocation_count)
                VALUES
                    (:query_log_id, :provider, :model, :used_real_inference, :intent,
                     :metric_id, :outcome, :error_category, :latency_ms,
                     :tool_invocation_count)
                """
            ),
            {
                "query_log_id": f"aql_{secrets.token_hex(12)}",
                "provider": metadata.provider,
                "model": metadata.model,
                "used_real_inference": metadata.used_real_inference,
                "intent": intent,
                "metric_id": metric_id,
                "outcome": outcome,
                "error_category": metadata.error_category,
                "latency_ms": metadata.latency_ms,
                "tool_invocation_count": metadata.tool_invocation_count,
            },
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("ai_query_logs insert failed (non-blocking): %s", type(exc).__name__)
        if settings.agent_audit_log_failures_block_request:
            raise


def ai_query_logs_available(db: Session) -> bool:
    result = db.execute(text("SELECT to_regclass('monitoring.ai_query_logs') IS NOT NULL")).scalar_one()
    return bool(result)

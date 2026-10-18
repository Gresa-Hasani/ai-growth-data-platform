"""Bounded tool execution. The plan has already been validated against the
catalog before this runs - this module's only remaining job is to invoke
the ONE corresponding tool (never more than AgentSettings.llm_max_tool_calls)
and translate any failure into ToolExecutionError, never letting a raw
exception (with a stack trace / connection string) escape to the caller.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.agent.exceptions import ToolExecutionError
from app.agent.models import AnalyticalPlan, MetricId, ToolResult
from app.agent.tools import (
    get_customer_metrics,
    get_growth_metrics,
    get_quality_run_details,
    get_quality_summary,
    get_revenue_metrics,
    get_usage_metrics,
)

_GROWTH_METRICS = {MetricId.DAILY_GROWTH, MetricId.ACQUISITION_CHANNELS, MetricId.TRIAL_CONVERSION, MetricId.RETENTION_COHORTS}
_REVENUE_METRICS = {MetricId.MRR, MetricId.ARR, MetricId.CUSTOMER_CHURN, MetricId.ARPU, MetricId.CUSTOMER_LTV}
_USAGE_METRICS = {MetricId.FEATURE_ADOPTION, MetricId.API_USAGE}
_CUSTOMER_METRICS = {MetricId.CUSTOMER_DETAIL}


def execute_plan(db: Session, plan: AnalyticalPlan) -> ToolResult:
    """Exactly one tool invocation per plan - this agent is deliberately
    single-hop (see AgentSettings.llm_max_tool_calls)."""
    try:
        if plan.metric_id in _GROWTH_METRICS:
            return get_growth_metrics(db, plan)
        if plan.metric_id in _REVENUE_METRICS:
            return get_revenue_metrics(db, plan)
        if plan.metric_id in _USAGE_METRICS:
            return get_usage_metrics(db, plan)
        if plan.metric_id in _CUSTOMER_METRICS:
            return get_customer_metrics(db, plan)
        if plan.metric_id == MetricId.QUALITY_LATEST:
            return get_quality_summary(db)
        if plan.metric_id == MetricId.QUALITY_RUN_DETAIL:
            if not plan.run_id:
                raise ToolExecutionError("quality_run_detail requires run_id")
            return get_quality_run_details(db, plan.run_id)
    except ToolExecutionError:
        raise
    except Exception as exc:
        raise ToolExecutionError(f"tool execution failed: {type(exc).__name__}") from exc

    raise ToolExecutionError(f"no tool is registered for metric_id '{plan.metric_id}'")

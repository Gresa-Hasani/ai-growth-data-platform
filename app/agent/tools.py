"""Typed tools - thin wrappers around the EXISTING Phase 8 service layer.
No tool here writes SQL of its own; each one calls a function already
defined in app/services/*.py and shapes the result into a ToolResult with
provenance. There is deliberately no generic "run a query" tool: the set
of callable functions below is the entire read surface the agent has.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.agent.exceptions import ToolExecutionError
from app.agent.models import AnalyticalPlan, MetricId, ToolProvenance, ToolResult
from app.services import (
    customer_service,
    growth_service,
    quality_service,
    revenue_service,
    usage_service,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _result(tool_name: str, source_model: str, rows: list[dict], data_as_of: str | None = None, warnings: list[str] | None = None) -> ToolResult:
    return ToolResult(
        provenance=ToolProvenance(
            tool_name=tool_name,
            source_model=source_model,
            row_count=len(rows),
            data_as_of=data_as_of or _now_iso(),
        ),
        rows=rows,
        is_empty=len(rows) == 0,
        warnings=warnings or [],
    )


def get_growth_metrics(db: Session, plan: AnalyticalPlan) -> ToolResult:
    if plan.metric_id == MetricId.DAILY_GROWTH:
        rows = growth_service.get_daily_growth(db, _as_date(plan.start_date), _as_date(plan.end_date))
        return _result("get_growth_metrics", "analytics.mart_daily_growth", rows)
    if plan.metric_id == MetricId.ACQUISITION_CHANNELS:
        rows = growth_service.get_acquisition_channels(db, plan.acquisition_channel)
        return _result("get_growth_metrics", "analytics.mart_acquisition_channels", rows)
    if plan.metric_id == MetricId.TRIAL_CONVERSION:
        rows, _total = growth_service.get_trial_conversion(db, None, None, plan.limit, 0)
        return _result("get_growth_metrics", "analytics.mart_trial_conversion", rows)
    if plan.metric_id == MetricId.RETENTION_COHORTS:
        rows = growth_service.get_retention_cohorts(db, None)
        return _result("get_growth_metrics", "analytics.mart_retention_cohorts", rows)
    raise ToolExecutionError(f"get_growth_metrics does not support metric_id '{plan.metric_id}'")


def get_revenue_metrics(db: Session, plan: AnalyticalPlan) -> ToolResult:
    if plan.metric_id == MetricId.MRR:
        rows = revenue_service.get_mrr(db, plan.currency)
        return _result("get_revenue_metrics", "analytics.mart_mrr", rows, warnings=["point-in-time snapshot, not a historical series"])
    if plan.metric_id == MetricId.ARR:
        rows = revenue_service.get_arr(db, plan.currency)
        return _result("get_revenue_metrics", "analytics.mart_arr", rows, warnings=["point-in-time snapshot, not a historical series"])
    if plan.metric_id == MetricId.CUSTOMER_CHURN:
        rows = revenue_service.get_churn(db, plan.organization_type)
        return _result("get_revenue_metrics", "analytics.mart_customer_churn", rows)
    if plan.metric_id == MetricId.ARPU:
        rows = revenue_service.get_arpu(db, plan.currency)
        return _result("get_revenue_metrics", "analytics.mart_arpu", rows, warnings=["point-in-time snapshot, not a historical series"])
    if plan.metric_id == MetricId.CUSTOMER_LTV:
        rows, _total = revenue_service.get_customer_ltv(db, plan.currency, plan.limit, 0)
        return _result(
            "get_revenue_metrics", "analytics.mart_customer_ltv", rows,
            warnings=["realized_ltv_to_date is actual billed revenue; estimated_ltv is a model projection - do not conflate"],
        )
    raise ToolExecutionError(f"get_revenue_metrics does not support metric_id '{plan.metric_id}'")


def get_usage_metrics(db: Session, plan: AnalyticalPlan) -> ToolResult:
    if plan.metric_id == MetricId.FEATURE_ADOPTION:
        rows = usage_service.get_feature_adoption(db, plan.event_name)
        return _result("get_usage_metrics", "analytics.mart_feature_adoption", rows)
    if plan.metric_id == MetricId.API_USAGE:
        rows, _total = usage_service.get_api_usage(
            db, _as_date(plan.start_date), _as_date(plan.end_date), plan.organization_id, plan.limit, 0
        )
        return _result("get_usage_metrics", "analytics.mart_api_usage", rows)
    raise ToolExecutionError(f"get_usage_metrics does not support metric_id '{plan.metric_id}'")


def get_customer_metrics(db: Session, plan: AnalyticalPlan) -> ToolResult:
    if plan.metric_id == MetricId.CUSTOMER_DETAIL:
        if not plan.organization_id:
            raise ToolExecutionError("get_customer_metrics requires organization_id")
        detail = customer_service.get_customer_detail(db, plan.organization_id)
        rows = [detail] if detail is not None else []
        return _result("get_customer_metrics", "analytics.dim_organizations", rows)
    raise ToolExecutionError(f"get_customer_metrics does not support metric_id '{plan.metric_id}'")


def get_quality_summary(db: Session) -> ToolResult:
    if not quality_service.monitoring_tables_available(db):
        raise ToolExecutionError("monitoring.quality_runs/data_quality_results are not available")
    run = quality_service.get_latest_run(db)
    rows = [run] if run is not None else []
    return _result(
        "get_quality_summary", "monitoring.quality_runs", rows,
        warnings=["WARN-severity results reflect intentionally-seeded synthetic anomalies, not genuine defects"],
    )


def get_quality_run_details(db: Session, run_id: str) -> ToolResult:
    if not quality_service.monitoring_tables_available(db):
        raise ToolExecutionError("monitoring.quality_runs/data_quality_results are not available")
    run = quality_service.get_run_detail(db, run_id)
    rows = [run] if run is not None else []
    return _result(
        "get_quality_run_details", "monitoring.quality_runs", rows,
        warnings=["WARN-severity results reflect intentionally-seeded synthetic anomalies, not genuine defects"],
    )


def get_source_freshness(db: Session) -> ToolResult:
    """Reuses the latest quality run's finished_at as a proxy for "data as of"
    - there is no separate freshness table; this deliberately does not invent
    one."""
    if not quality_service.monitoring_tables_available(db):
        raise ToolExecutionError("monitoring.quality_runs is not available")
    run = quality_service.get_latest_run(db)
    rows = [{"last_quality_run_finished_at": run["finished_at"], "overall_status": run["overall_status"]}] if run else []
    return _result("get_source_freshness", "monitoring.quality_runs", rows)


def _as_date(value: date | str | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(value)


METRIC_TO_TOOL = {
    MetricId.DAILY_GROWTH: get_growth_metrics,
    MetricId.ACQUISITION_CHANNELS: get_growth_metrics,
    MetricId.TRIAL_CONVERSION: get_growth_metrics,
    MetricId.RETENTION_COHORTS: get_growth_metrics,
    MetricId.MRR: get_revenue_metrics,
    MetricId.ARR: get_revenue_metrics,
    MetricId.CUSTOMER_CHURN: get_revenue_metrics,
    MetricId.ARPU: get_revenue_metrics,
    MetricId.CUSTOMER_LTV: get_revenue_metrics,
    MetricId.FEATURE_ADOPTION: get_usage_metrics,
    MetricId.API_USAGE: get_usage_metrics,
    MetricId.CUSTOMER_DETAIL: get_customer_metrics,
    MetricId.QUALITY_LATEST: get_quality_summary,
    MetricId.QUALITY_RUN_DETAIL: get_quality_run_details,
}

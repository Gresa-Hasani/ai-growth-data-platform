"""Typed internal structures for the AI Data Analyst Agent. The LLM's raw
output is untrusted - it is parsed into AnalyticalPlan and validated against
app.agent.catalog BEFORE anything touches a database. Nothing downstream of
that validation step ever sees unvalidated LLM output again.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Intent(str, Enum):
    GROWTH = "growth"
    REVENUE = "revenue"
    USAGE = "usage"
    CUSTOMER = "customer"
    QUALITY = "quality"
    UNSUPPORTED = "unsupported"


class MetricId(str, Enum):
    """Canonical metric identifiers - the ONLY values the executor will
    ever act on. See app/agent/catalog.py for each one's full definition."""

    DAILY_GROWTH = "daily_growth"
    ACQUISITION_CHANNELS = "acquisition_channels"
    TRIAL_CONVERSION = "trial_conversion"
    RETENTION_COHORTS = "retention_cohorts"
    FEATURE_ADOPTION = "feature_adoption"
    MRR = "mrr"
    ARR = "arr"
    CUSTOMER_CHURN = "customer_churn"
    ARPU = "arpu"
    CUSTOMER_LTV = "customer_ltv"
    API_USAGE = "api_usage"
    CUSTOMER_DETAIL = "customer_detail"
    QUALITY_LATEST = "quality_latest"
    QUALITY_RUN_DETAIL = "quality_run_detail"


class AnalyticalPlan(BaseModel):
    """The LLM's question-understanding output, in a bounded, typed shape.
    Every field is validated against app.agent.catalog before execution -
    an unrecognized metric_id, a currency outside {USD,EUR,GBP}, a limit
    above the configured ceiling, etc. all raise InvalidPlanError rather
    than being silently coerced or passed through.
    """

    intent: Intent
    metric_id: MetricId | None = None
    start_date: date | None = None
    end_date: date | None = None
    currency: str | None = None
    organization_id: str | None = None
    organization_type: str | None = None
    acquisition_channel: str | None = None
    event_name: str | None = None
    run_id: str | None = None
    limit: int = 20
    requires_clarification: bool = False
    clarification_question: str | None = None

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class ToolProvenance(BaseModel):
    tool_name: str
    source_model: str
    row_count: int
    data_as_of: str | None = None


class ToolResult(BaseModel):
    provenance: ToolProvenance
    rows: list[dict[str, Any]]
    is_empty: bool
    warnings: list[str] = Field(default_factory=list)


class AgentAnswer(BaseModel):
    answer: str
    key_findings: list[str] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
    applied_filters: dict[str, Any] = Field(default_factory=dict)
    currency: str | None = None
    data_as_of: str | None = None
    limitations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    follow_up_suggestions: list[str] = Field(default_factory=list)


class AgentExecutionMetadata(BaseModel):
    """Safe diagnostic metadata only - never a stack trace, credential, or
    raw provider payload."""

    provider: str
    model: str | None
    used_real_inference: bool
    latency_ms: int
    tool_invocation_count: int
    error_category: str | None = None

"""Typed internal structures for the AI Data Analyst Agent. The LLM's raw
output is untrusted - it is parsed into AnalyticalPlan and validated against
app.agent.catalog BEFORE anything touches a database. Nothing downstream of
that validation step ever sees unvalidated LLM output again.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


_FILTER_FIELD_MAX_LENGTH = 200

# Phase 10: reject a filter value containing a NUL byte or a raw ASCII
# control character outright. These values are always passed through
# SQLAlchemy bound parameters, so they can never alter SQL statement
# structure - this is defense-in-depth against malformed/anomalous input
# reaching the service layer at all, not an injection fix (there is no
# injection to fix; see docs/security_and_sql_validation.md's SQL-injection
# audit for the evidence).
_CONTROL_CHARS = frozenset(chr(c) for c in range(32) if c not in (9, 10, 13))


def _reject_control_characters(v: str | None, field_name: str) -> str | None:
    if v is not None and any(c in _CONTROL_CHARS for c in v):
        raise ValueError(f"{field_name} must not contain control characters")
    return v


class AnalyticalPlan(BaseModel):
    """The LLM's question-understanding output, in a bounded, typed shape.
    Every field is validated against app.agent.catalog before execution -
    an unrecognized metric_id, a currency outside {USD,EUR,GBP}, a limit
    above the configured ceiling, etc. all raise InvalidPlanError rather
    than being silently coerced or passed through.

    `extra="forbid"`: a field the LLM invents that isn't declared here
    (e.g. an attempted "sql"/"tool_name"/"table" override) is a validation
    FAILURE, not a silently-ignored extra key - see
    tests/unit/test_agent_security.py for the adversarial-field tests this
    exists for.
    """

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    metric_id: MetricId | None = None
    start_date: date | None = None
    end_date: date | None = None
    currency: str | None = Field(default=None, max_length=10)
    organization_id: str | None = Field(default=None, max_length=_FILTER_FIELD_MAX_LENGTH)
    organization_type: str | None = Field(default=None, max_length=_FILTER_FIELD_MAX_LENGTH)
    acquisition_channel: str | None = Field(default=None, max_length=_FILTER_FIELD_MAX_LENGTH)
    event_name: str | None = Field(default=None, max_length=_FILTER_FIELD_MAX_LENGTH)
    run_id: str | None = Field(default=None, max_length=_FILTER_FIELD_MAX_LENGTH)
    limit: int = 20
    requires_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=500)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str | None) -> str | None:
        return v.upper() if v else v

    @field_validator(
        "organization_id", "organization_type", "acquisition_channel", "event_name", "run_id",
    )
    @classmethod
    def _no_control_characters(cls, v: str | None, info) -> str | None:
        return _reject_control_characters(v, info.field_name)


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

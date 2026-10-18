"""The trusted analytics catalog - the single source of truth for what the
agent is allowed to know exists. Every entry here is a restatement of a
metric/source already documented in docs/business_metrics.md,
docs/dbt_marts.md, and docs/analytics_api.md from Phase 8 - this module
invents nothing new. The LLM never sees table/column names; it only ever
sees the `name`/`definition`/`limitations` text below, and its plan is
validated against `metric_id` membership here, never against free-form
table names.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.agent.models import Intent, MetricId


@dataclass(frozen=True)
class CatalogEntry:
    metric_id: MetricId
    intent: Intent
    name: str
    definition: str
    approved_source: str
    required_filters: tuple[str, ...]
    optional_filters: tuple[str, ...]
    output_fields: tuple[str, ...]
    grain: str
    currency_semantics: str
    limitations: str
    supports_date_range: bool
    is_snapshot_only: bool
    supports_currency_filter: bool = False


CATALOG: dict[MetricId, CatalogEntry] = {
    MetricId.DAILY_GROWTH: CatalogEntry(
        metric_id=MetricId.DAILY_GROWTH,
        intent=Intent.GROWTH,
        name="Daily growth (signups & organizations)",
        definition=(
            "Daily new user signups and new organizations, plus cumulative "
            "running totals, over a date range."
        ),
        approved_source="analytics.mart_daily_growth",
        required_filters=(),
        optional_filters=("start_date", "end_date"),
        output_fields=(
            "date_day", "new_user_signups", "new_organizations",
            "cumulative_user_signups", "cumulative_organizations",
        ),
        grain="one row per calendar day",
        currency_semantics="not applicable - no monetary fields",
        limitations="genuine historical time series, not a snapshot.",
        supports_date_range=True,
        is_snapshot_only=False,
    ),
    MetricId.ACQUISITION_CHANNELS: CatalogEntry(
        metric_id=MetricId.ACQUISITION_CHANNELS,
        intent=Intent.GROWTH,
        name="Acquisition channel performance",
        definition=(
            "Signup, trial-start, and paid-conversion counts and amounts by "
            "acquisition channel, broken out by currency."
        ),
        approved_source="analytics.mart_acquisition_channels",
        required_filters=(),
        optional_filters=("acquisition_channel",),
        output_fields=(
            "acquisition_channel", "currency", "signup_count", "trial_start_count",
            "paid_conversion_count", "trial_to_paid_conversion_rate",
            "billed_amount", "paid_amount",
        ),
        grain="one row per (acquisition_channel, currency)",
        currency_semantics="billed_amount/paid_amount are per-currency; never sum across currency rows.",
        limitations="cumulative to date, not a point-in-time snapshot and not a trend series.",
        supports_date_range=False,
        is_snapshot_only=False,
    ),
    MetricId.TRIAL_CONVERSION: CatalogEntry(
        metric_id=MetricId.TRIAL_CONVERSION,
        intent=Intent.GROWTH,
        name="Trial conversion detail",
        definition="Per-user trial-to-paid conversion outcomes and timing.",
        approved_source="analytics.mart_trial_conversion",
        required_filters=(),
        optional_filters=("is_converted", "is_observation_complete"),
        output_fields=(
            "user_id", "organization_id", "signup_at", "trial_start_at",
            "paid_conversion_at", "is_converted", "is_observation_complete",
            "days_trial_to_paid_conversion",
        ),
        grain="one row per user who entered a trial",
        currency_semantics="not applicable - no monetary fields",
        limitations=(
            "is_observation_complete distinguishes trials with enough elapsed "
            "time to judge conversion from trials still in progress - treat "
            "incomplete observations as not-yet-decided, not as non-conversions."
        ),
        supports_date_range=False,
        is_snapshot_only=False,
    ),
    MetricId.RETENTION_COHORTS: CatalogEntry(
        metric_id=MetricId.RETENTION_COHORTS,
        intent=Intent.GROWTH,
        name="Retention cohorts",
        definition="Monthly signup-cohort retention curves by period index.",
        approved_source="analytics.mart_retention_cohorts",
        required_filters=(),
        optional_filters=("cohort_month",),
        output_fields=("cohort_month", "cohort_size", "period_index", "retained_users", "retention_rate"),
        grain="one row per (cohort_month, period_index)",
        currency_semantics="not applicable - no monetary fields",
        limitations="genuine historical cohort analysis, not a snapshot.",
        supports_date_range=False,
        is_snapshot_only=False,
    ),
    MetricId.FEATURE_ADOPTION: CatalogEntry(
        metric_id=MetricId.FEATURE_ADOPTION,
        intent=Intent.USAGE,
        name="Feature adoption",
        definition="Adoption rate of individual product features/events across the eligible user base.",
        approved_source="analytics.mart_feature_adoption",
        required_filters=(),
        optional_filters=("event_name",),
        output_fields=("event_name", "adopting_user_count", "total_event_count", "eligible_user_count", "adoption_rate"),
        grain="one row per event_name",
        currency_semantics="not applicable - no monetary fields",
        limitations="cumulative to date, not a trend series.",
        supports_date_range=False,
        is_snapshot_only=False,
    ),
    MetricId.MRR: CatalogEntry(
        metric_id=MetricId.MRR,
        intent=Intent.REVENUE,
        name="Monthly Recurring Revenue (MRR)",
        definition="Current active-subscription monthly recurring revenue, by currency.",
        approved_source="analytics.mart_mrr",
        required_filters=(),
        optional_filters=("currency",),
        output_fields=("as_of_date", "currency", "active_subscription_count", "mrr"),
        grain="one row per currency, as of the current snapshot date",
        currency_semantics="USD/EUR/GBP are reported separately and must never be summed into one total.",
        limitations=(
            "POINT-IN-TIME SNAPSHOT ONLY, as of as_of_date - this is not a "
            "historical time series and cannot answer 'how has MRR changed "
            "over time'."
        ),
        supports_date_range=False,
        is_snapshot_only=True,
        supports_currency_filter=True,
    ),
    MetricId.ARR: CatalogEntry(
        metric_id=MetricId.ARR,
        intent=Intent.REVENUE,
        name="Annual Recurring Revenue (ARR)",
        definition="Current ARR (MRR x 12), by currency.",
        approved_source="analytics.mart_arr",
        required_filters=(),
        optional_filters=("currency",),
        output_fields=("as_of_date", "currency", "active_subscription_count", "mrr", "arr"),
        grain="one row per currency, as of the current snapshot date",
        currency_semantics="USD/EUR/GBP are reported separately and must never be summed into one total.",
        limitations="POINT-IN-TIME SNAPSHOT ONLY, as of as_of_date - not a historical time series.",
        supports_date_range=False,
        is_snapshot_only=True,
        supports_currency_filter=True,
    ),
    MetricId.CUSTOMER_CHURN: CatalogEntry(
        metric_id=MetricId.CUSTOMER_CHURN,
        intent=Intent.REVENUE,
        name="Customer churn",
        definition="Customer (organization) churn rate by period and organization type, computed from real subscription start/cancellation dates.",
        approved_source="analytics.mart_customer_churn",
        required_filters=(),
        optional_filters=("organization_type",),
        output_fields=("period_start", "period_end", "organization_type", "customers_active_at_start", "customers_churned", "churn_rate"),
        grain="one row per (period, organization_type)",
        currency_semantics="not applicable - customer counts, not monetary amounts",
        limitations="genuine historical computation from started_at/cancelled_at - NOT a point-in-time snapshot.",
        supports_date_range=False,
        is_snapshot_only=False,
    ),
    MetricId.ARPU: CatalogEntry(
        metric_id=MetricId.ARPU,
        intent=Intent.REVENUE,
        name="Average Revenue Per User (ARPU)",
        definition="Current MRR divided by paying-organization count, by currency.",
        approved_source="analytics.mart_arpu",
        required_filters=(),
        optional_filters=("currency",),
        output_fields=("as_of_date", "currency", "mrr", "paying_organization_count", "arpu"),
        grain="one row per currency, as of the current snapshot date",
        currency_semantics="USD/EUR/GBP are reported separately and must never be summed into one total.",
        limitations="POINT-IN-TIME SNAPSHOT ONLY, as of as_of_date - not a historical time series.",
        supports_date_range=False,
        is_snapshot_only=True,
        supports_currency_filter=True,
    ),
    MetricId.CUSTOMER_LTV: CatalogEntry(
        metric_id=MetricId.CUSTOMER_LTV,
        intent=Intent.REVENUE,
        name="Customer lifetime value (LTV)",
        definition="Per-organization realized-to-date LTV (actual billed revenue) and a separate estimated LTV projection, by currency.",
        approved_source="analytics.mart_customer_ltv",
        required_filters=(),
        optional_filters=("currency",),
        output_fields=("organization_id", "currency", "realized_ltv_to_date", "current_arpu", "platform_churn_rate", "estimated_ltv"),
        grain="one row per (organization_id, currency)",
        currency_semantics="per-currency; never sum across currency rows.",
        limitations=(
            "realized_ltv_to_date is actual billed revenue to date; "
            "estimated_ltv is a model-based projection - the two must never "
            "be conflated or presented as the same quantity."
        ),
        supports_date_range=False,
        is_snapshot_only=True,
        supports_currency_filter=True,
    ),
    MetricId.API_USAGE: CatalogEntry(
        metric_id=MetricId.API_USAGE,
        intent=Intent.USAGE,
        name="API usage & cost",
        definition="Monthly API request counts, error rate, and estimated cost per organization.",
        approved_source="analytics.mart_api_usage",
        required_filters=(),
        optional_filters=("start_date", "end_date", "organization_id"),
        output_fields=("month_start_date", "organization_id", "request_count", "error_request_count", "error_rate", "total_estimated_cost"),
        grain="one row per (month_start_date, organization_id)",
        currency_semantics="total_estimated_cost is USD-denominated internal cost estimate, not customer-facing billed revenue.",
        limitations="genuine historical monthly series, not a snapshot.",
        supports_date_range=True,
        is_snapshot_only=False,
    ),
    MetricId.CUSTOMER_DETAIL: CatalogEntry(
        metric_id=MetricId.CUSTOMER_DETAIL,
        intent=Intent.CUSTOMER,
        name="Customer (organization) detail",
        definition="Profile, per-currency revenue summary, and last-30-day usage for one specific organization.",
        approved_source="analytics.dim_organizations + analytics.mart_customer_ltv + analytics.fact_subscriptions + usage facts",
        required_filters=("organization_id",),
        optional_filters=(),
        output_fields=(
            "organization_id", "organization_name", "organization_type", "is_enterprise",
            "country_code", "created_date", "revenue_by_currency", "usage_last_30_days",
        ),
        grain="one row per organization_id",
        currency_semantics="revenue_by_currency is a list, one entry per currency the organization has transacted in; never summed.",
        limitations="usage_last_30_days is a fixed trailing 30-day window, not configurable.",
        supports_date_range=False,
        is_snapshot_only=False,
    ),
    MetricId.QUALITY_LATEST: CatalogEntry(
        metric_id=MetricId.QUALITY_LATEST,
        intent=Intent.QUALITY,
        name="Latest data quality run",
        definition="The most recent dbt data-quality check run, with its overall status and per-test results.",
        approved_source="monitoring.quality_runs + monitoring.data_quality_results",
        required_filters=(),
        optional_filters=(),
        output_fields=(
            "run_id", "started_at", "finished_at", "overall_status", "total_checks",
            "passed_checks", "warned_checks", "failed_checks", "duration_ms", "results",
        ),
        grain="one quality run, with nested per-test result rows",
        currency_semantics="not applicable",
        limitations=(
            "WARN-severity results reflect intentionally-seeded synthetic data "
            "anomalies (by design, see docs/data_quality.md) and must be "
            "described as expected/tracked, never reported as the same thing "
            "as a FAIL or a genuine defect."
        ),
        supports_date_range=False,
        is_snapshot_only=True,
    ),
    MetricId.QUALITY_RUN_DETAIL: CatalogEntry(
        metric_id=MetricId.QUALITY_RUN_DETAIL,
        intent=Intent.QUALITY,
        name="Specific data quality run detail",
        definition="A specific past data-quality check run (by run_id), with its overall status and per-test results.",
        approved_source="monitoring.quality_runs + monitoring.data_quality_results",
        required_filters=("run_id",),
        optional_filters=(),
        output_fields=(
            "run_id", "started_at", "finished_at", "overall_status", "total_checks",
            "passed_checks", "warned_checks", "failed_checks", "duration_ms", "results",
        ),
        grain="one quality run, with nested per-test result rows",
        currency_semantics="not applicable",
        limitations="WARN-severity results reflect intentionally-seeded synthetic anomalies, never genuine defects.",
        supports_date_range=False,
        is_snapshot_only=True,
    ),
}

SUPPORTED_CURRENCIES: tuple[str, ...] = ("USD", "EUR", "GBP")
MAX_RESULT_LIMIT = 200


def get_entry(metric_id: MetricId) -> CatalogEntry | None:
    return CATALOG.get(metric_id)


def metric_ids_for_intent(intent: Intent) -> list[MetricId]:
    return [m for m, e in CATALOG.items() if e.intent == intent]


def catalog_summary_for_prompt() -> str:
    """A compact, LLM-facing restatement of the catalog - this is the ONLY
    description of available data the model ever sees. It never mentions a
    table or column name that isn't also in `output_fields`/`approved_source`
    here, and it is not itself trusted - the model's chosen metric_id is
    still re-validated against CATALOG after the fact.
    """
    lines: list[str] = []
    for entry in CATALOG.values():
        lines.append(
            f"- {entry.metric_id.value} ({entry.intent.value}): {entry.name}. "
            f"{entry.definition} "
            f"Filters: required={list(entry.required_filters)}, optional={list(entry.optional_filters)}. "
            f"Limitations: {entry.limitations}"
        )
    return "\n".join(lines)

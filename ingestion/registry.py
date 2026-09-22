"""Maps each source system to its file, raw table, and validation config.

This is the single place that knows how a source's file becomes a raw table
row. Loaders and the pipeline CLI are all driven off this registry instead
of hardcoding per-source logic.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from ingestion.schemas.api_usage import ApiUsageTechnical
from ingestion.schemas.crm_accounts import CrmAccountTechnical
from ingestion.schemas.invoices import InvoiceTechnical
from ingestion.schemas.marketing_events import MarketingEventTechnical
from ingestion.schemas.organizations import OrganizationTechnical
from ingestion.schemas.product_events import ProductEventTechnical
from ingestion.schemas.subscriptions import SubscriptionTechnical
from ingestion.schemas.users import UserTechnical


@dataclass(frozen=True)
class SourceSpec:
    name: str
    file_name: str
    loader_type: str  # "csv" | "jsonl" | "parquet"
    table: str  # fully-qualified raw table, e.g. "raw.organizations"
    primary_key: str
    columns: tuple[str, ...]  # raw table's source columns, in DDL order
    technical_model: type[BaseModel]
    # If set, duplicate business keys within a file are resolved by keeping
    # the row with the latest value of this field (mutable-entity semantics).
    # If None, duplicates are resolved by keeping the last occurrence in the
    # file (immutable-event semantics: last write in file order wins).
    updated_at_field: str | None = None


SOURCES: dict[str, SourceSpec] = {
    "organizations": SourceSpec(
        name="organizations",
        file_name="organizations.csv",
        loader_type="csv",
        table="raw.organizations",
        primary_key="organization_id",
        columns=(
            "organization_id",
            "organization_name",
            "domain",
            "country_code",
            "organization_type",
            "created_at",
            "updated_at",
        ),
        technical_model=OrganizationTechnical,
        updated_at_field="updated_at",
    ),
    "users": SourceSpec(
        name="users",
        file_name="users.csv",
        loader_type="csv",
        table="raw.users",
        primary_key="user_id",
        columns=(
            "user_id",
            "organization_id",
            "email",
            "country_code",
            "signup_at",
            "acquisition_channel",
            "status",
            "created_at",
            "updated_at",
        ),
        technical_model=UserTechnical,
        updated_at_field="updated_at",
    ),
    "subscriptions": SourceSpec(
        name="subscriptions",
        file_name="subscriptions.csv",
        loader_type="csv",
        table="raw.subscriptions",
        primary_key="subscription_id",
        columns=(
            "subscription_id",
            "user_id",
            "organization_id",
            "plan_name",
            "billing_interval",
            "status",
            "currency",
            "amount",
            "started_at",
            "trial_started_at",
            "trial_ended_at",
            "cancelled_at",
            "current_period_start",
            "current_period_end",
            "updated_at",
        ),
        technical_model=SubscriptionTechnical,
        updated_at_field="updated_at",
    ),
    "invoices": SourceSpec(
        name="invoices",
        file_name="invoices.csv",
        loader_type="csv",
        table="raw.invoices",
        primary_key="invoice_id",
        columns=(
            "invoice_id",
            "subscription_id",
            "customer_id",
            "currency",
            "subtotal",
            "tax",
            "total",
            "status",
            "issued_at",
            "paid_at",
        ),
        technical_model=InvoiceTechnical,
        updated_at_field=None,  # invoices are near-immutable once issued; last-in-file wins
    ),
    "product_events": SourceSpec(
        name="product_events",
        file_name="product_events.jsonl",
        loader_type="jsonl",
        table="raw.product_events",
        primary_key="event_id",
        columns=(
            "event_id",
            "user_id",
            "organization_id",
            "event_name",
            "event_timestamp",
            "session_id",
            "properties",
        ),
        technical_model=ProductEventTechnical,
        updated_at_field=None,
    ),
    "api_usage": SourceSpec(
        name="api_usage",
        file_name="api_usage.parquet",
        loader_type="parquet",
        table="raw.api_usage",
        primary_key="request_id",
        columns=(
            "request_id",
            "user_id",
            "organization_id",
            "endpoint",
            "model_family",
            "request_timestamp",
            "latency_ms",
            "input_units",
            "output_units",
            "status_code",
            "estimated_cost",
        ),
        technical_model=ApiUsageTechnical,
        updated_at_field=None,
    ),
    "crm_accounts": SourceSpec(
        name="crm_accounts",
        file_name="crm_accounts.csv",
        loader_type="csv",
        table="raw.crm_accounts",
        primary_key="crm_account_id",
        columns=(
            "crm_account_id",
            "organization_id",
            "account_status",
            "segment",
            "owner_team",
            "lead_source",
            "created_at",
            "updated_at",
        ),
        technical_model=CrmAccountTechnical,
        updated_at_field="updated_at",
    ),
    "marketing_events": SourceSpec(
        name="marketing_events",
        file_name="marketing_events.csv",
        loader_type="csv",
        table="raw.marketing_events",
        primary_key="marketing_event_id",
        columns=(
            "marketing_event_id",
            "user_id",
            "anonymous_id",
            "campaign_id",
            "channel",
            "event_type",
            "event_timestamp",
            "utm_source",
            "utm_medium",
            "utm_campaign",
        ),
        technical_model=MarketingEventTechnical,
        updated_at_field=None,
    ),
}

# Ingestion order: entities with foreign keys come after what they reference
# so orphan-detection reporting (Phase 2 quality report) has both sides loaded.
INGESTION_ORDER: tuple[str, ...] = (
    "organizations",
    "users",
    "subscriptions",
    "invoices",
    "crm_accounts",
    "product_events",
    "api_usage",
    "marketing_events",
)

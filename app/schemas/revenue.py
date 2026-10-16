"""Revenue response models. Every monetary amount uses Decimal (never
float) to avoid floating-point rounding drift in financial output, and
every record that carries money also carries its own `currency` field -
no endpoint ever sums across currencies (see docs/analytics_api.md
"Currency semantics")."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class MrrPoint(BaseModel):
    as_of_date: date = Field(description="Snapshot date - NOT a historical time series point. See docs/dbt_marts.md.")
    currency: str
    active_subscription_count: int
    mrr: Decimal


class ArrPoint(BaseModel):
    as_of_date: date = Field(description="Snapshot date - NOT a historical time series point.")
    currency: str
    active_subscription_count: int
    mrr: Decimal
    arr: Decimal


class ChurnPoint(BaseModel):
    period_start: date
    period_end: date
    organization_type: str
    customers_active_at_start: int
    customers_churned: int
    churn_rate: float | None


class ArpuPoint(BaseModel):
    as_of_date: date
    currency: str
    mrr: Decimal
    paying_organization_count: int
    arpu: Decimal | None


class CustomerLtvRecord(BaseModel):
    organization_id: str
    currency: str
    realized_ltv_to_date: Decimal
    current_arpu: Decimal | None
    platform_churn_rate: float | None
    estimated_ltv: Decimal | None = Field(
        description="Speculative ARPU/churn-rate projection - NOT observed revenue. See realized_ltv_to_date for the historical fact."
    )

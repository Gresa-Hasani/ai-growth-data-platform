"""Customer = organization throughout this API, matching the revenue/usage
marts' "customer identity" convention established in Phase 4/5
(docs/dbt_marts.md "Customer identity") - customer_id in every route below
is organization_id, never user_id."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class CustomerSummary(BaseModel):
    organization_id: str
    organization_name: str | None
    organization_type: str
    is_enterprise: bool
    country_code: str | None
    created_date: date | None


class CustomerRevenueByCurrency(BaseModel):
    currency: str
    active_monthly_recurring_revenue: Decimal
    realized_ltv_to_date: Decimal | None
    estimated_ltv: Decimal | None


class CustomerUsageSummary(BaseModel):
    window_days: int
    product_event_count: int
    api_request_count: int


class CustomerDetail(CustomerSummary):
    revenue_by_currency: list[CustomerRevenueByCurrency]
    usage_last_30_days: CustomerUsageSummary

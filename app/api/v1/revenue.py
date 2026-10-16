from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.schemas.common import Page, PageMeta
from app.schemas.revenue import ArpuPoint, ArrPoint, ChurnPoint, CustomerLtvRecord, MrrPoint
from app.services import revenue_service

router = APIRouter(prefix="/api/v1/analytics/revenue", tags=["revenue"])

CURRENCY_QUERY = Query(default=None, description="USD, EUR, or GBP. Omit to return all currencies separately.")


@router.get(
    "/mrr",
    response_model=list[MrrPoint],
    summary="Monthly Recurring Revenue, as of today",
    description=(
        "One row per currency - never summed across currencies. "
        "IMPORTANT: this is a POINT-IN-TIME SNAPSHOT (as_of_date = today), not a historical time "
        "series - the underlying subscription source has no status-change history to reconstruct "
        "one from. See docs/dbt_marts.md 'mart_mrr / mart_arr'."
    ),
)
def mrr(currency: str | None = CURRENCY_QUERY, db: Session = Depends(get_db)) -> list[dict]:
    return revenue_service.get_mrr(db, currency)


@router.get(
    "/arr",
    response_model=list[ArrPoint],
    summary="Annualized Recurring Revenue (MRR x 12), as of today",
    description="Same point-in-time snapshot semantics as /mrr. arr = mrr * 12 for the identical eligible-subscription set, per currency.",
)
def arr(currency: str | None = CURRENCY_QUERY, db: Session = Depends(get_db)) -> list[dict]:
    return revenue_service.get_arr(db, currency)


@router.get(
    "/churn",
    response_model=list[ChurnPoint],
    summary="Customer churn rate over a fixed trailing 30-day period",
    description=(
        "One row per organization_type (plus one 'all' row). Customer = organization. Computed "
        "from genuine historical started_at/cancelled_at timestamps (unlike MRR, this is not a "
        "current-state-only approximation) - see docs/dbt_marts.md 'mart_customer_churn'."
    ),
)
def churn(
    organization_type: str | None = Query(default=None, description="e.g. 'enterprise', 'startup', or 'all' for the platform-wide figure."),
    db: Session = Depends(get_db),
) -> list[dict]:
    return revenue_service.get_churn(db, organization_type)


@router.get(
    "/arpu",
    response_model=list[ArpuPoint],
    summary="Average Revenue Per paying organization, as of today",
    description="Per-currency snapshot (same semantics as /mrr). ARPU is per PAYING ORGANIZATION, not per user, and not the same as average invoice value.",
)
def arpu(currency: str | None = CURRENCY_QUERY, db: Session = Depends(get_db)) -> list[dict]:
    return revenue_service.get_arpu(db, currency)


@router.get(
    "/customer-ltv",
    response_model=Page[CustomerLtvRecord],
    summary="Per-customer lifetime value: realized (historical) and estimated (projected)",
    description=(
        "Grain: one row per (organization_id, currency). realized_ltv_to_date is an observed "
        "historical fact; estimated_ltv is a speculative ARPU/churn-rate projection using the "
        "platform-wide churn rate - the two are never blended into one number."
    ),
)
def customer_ltv(
    currency: str | None = CURRENCY_QUERY,
    limit: int = Query(default=settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    items, total = revenue_service.get_customer_ltv(db, currency, limit, offset)
    return {"items": items, "meta": PageMeta(limit=limit, offset=offset, total=total)}

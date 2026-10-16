from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.schemas.common import Page, PageMeta
from app.schemas.customers import CustomerDetail, CustomerSummary
from app.services import customer_service

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])


@router.get(
    "",
    response_model=Page[CustomerSummary],
    summary="List customers (organizations)",
    description="Customer = organization throughout this API (see docs/analytics_api.md 'Customer identity') - never a user_id.",
)
def list_customers(
    organization_type: str | None = Query(default=None, description="e.g. 'enterprise', 'startup', 'business', 'individual'."),
    limit: int = Query(default=settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    items, total = customer_service.list_customers(db, organization_type, limit, offset)
    return {"items": items, "meta": PageMeta(limit=limit, offset=offset, total=total)}


@router.get(
    "/{customer_id}",
    response_model=CustomerDetail,
    summary="Customer (organization) detail: revenue by currency and recent usage",
    responses={404: {"description": "No organization with this organization_id exists."}},
)
def get_customer(customer_id: str, db: Session = Depends(get_db)) -> dict:
    detail = customer_service.get_customer_detail(db, customer_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="customer not found")
    return detail

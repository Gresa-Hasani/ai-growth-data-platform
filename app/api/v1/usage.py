from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.schemas.common import Page, PageMeta
from app.schemas.usage import ApiUsageRecord, DailyUsageRecord, FeatureAdoptionRecord
from app.services import usage_service

router = APIRouter(prefix="/api/v1/analytics/usage", tags=["usage"])


@router.get(
    "/features",
    response_model=list[FeatureAdoptionRecord],
    summary="Feature adoption: distinct adopting users per event type",
    description="Grain: one row per event_name. adopting_user_count (distinct users) is kept separate from total_event_count (raw volume) - a power user's volume never inflates the adoption rate.",
)
def feature_adoption(
    event_name: str | None = Query(default=None, description="Filter to one event type."),
    db: Session = Depends(get_db),
) -> list[dict]:
    return usage_service.get_feature_adoption(db, event_name)


@router.get(
    "/daily",
    response_model=Page[DailyUsageRecord],
    summary="Daily product usage by organization and event type",
    description=(
        "Grain: one row per (date_day, organization_id, event_name) - organization_id is nullable "
        "(unresolvable source rows, preserved not dropped). Defaults to the trailing 30 days, "
        f"capped at {settings.max_date_window_days} days."
    ),
)
def daily_usage(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    organization_id: str | None = Query(default=None),
    limit: int = Query(default=settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    items, total = usage_service.get_daily_usage(db, start_date, end_date, organization_id, limit, offset)
    return {"items": items, "meta": PageMeta(limit=limit, offset=offset, total=total)}


@router.get(
    "/api",
    response_model=Page[ApiUsageRecord],
    summary="Monthly API usage by organization",
    description=(
        "Grain: one row per (month_start_date, organization_id). Defaults to the trailing 30 days "
        f"of months, capped at {settings.max_date_window_days} days."
    ),
)
def api_usage(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    organization_id: str | None = Query(default=None),
    limit: int = Query(default=settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    items, total = usage_service.get_api_usage(db, start_date, end_date, organization_id, limit, offset)
    return {"items": items, "meta": PageMeta(limit=limit, offset=offset, total=total)}

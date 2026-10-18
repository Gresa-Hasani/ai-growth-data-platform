from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_read_only_db
from app.schemas.common import Page, PageMeta
from app.schemas.growth import (
    AcquisitionChannelStat,
    DailyGrowthPoint,
    RetentionCohortPoint,
    TrialConversionRecord,
)
from app.services import growth_service

router = APIRouter(prefix="/api/v1/analytics/growth", tags=["growth"])


@router.get(
    "/daily",
    response_model=list[DailyGrowthPoint],
    summary="Daily new-user-signup and new-organization counts",
    description=(
        "Grain: one row per calendar date (dense - every date in range, not just dates with "
        "signups). Defaults to the trailing 30 days; the window is capped at "
        f"{settings.max_date_window_days} days."
    ),
)
def daily_growth(
    start_date: date | None = Query(default=None, description="Inclusive start date."),
    end_date: date | None = Query(default=None, description="Inclusive end date."),
    db: Session = Depends(get_read_only_db),
) -> list[dict]:
    return growth_service.get_daily_growth(db, start_date, end_date)


@router.get(
    "/acquisition-channels",
    response_model=list[AcquisitionChannelStat],
    summary="Signup/trial/conversion/revenue counts by acquisition channel",
    description=(
        "Grain: one row per (acquisition_channel, currency). signup_count/trial_start_count/"
        "paid_conversion_count are channel-level and repeated across a channel's currency rows - "
        "do not sum them across rows for the same channel. Uses the existing single-touch, "
        "first-touch attribution policy (see docs/dbt_marts.md) - never multi-touch."
    ),
)
def acquisition_channels(
    acquisition_channel: str | None = Query(default=None, max_length=100, description="Filter to one channel."),
    db: Session = Depends(get_read_only_db),
) -> list[dict]:
    return growth_service.get_acquisition_channels(db, acquisition_channel)


@router.get(
    "/trial-conversion",
    response_model=Page[TrialConversionRecord],
    summary="Per-user trial-to-paid conversion records",
    description=(
        "Grain: one row per user who started a trial. is_observation_complete distinguishes a "
        "trial that genuinely hasn't converted from one that's too recent to judge yet - a user "
        "with is_observation_complete=false must never be treated as a failed conversion."
    ),
)
def trial_conversion(
    is_converted: bool | None = Query(default=None),
    is_observation_complete: bool | None = Query(default=None),
    limit: int = Query(default=settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_read_only_db),
) -> dict:
    items, total = growth_service.get_trial_conversion(
        db, is_converted, is_observation_complete, limit, offset
    )
    return {"items": items, "meta": PageMeta(limit=limit, offset=offset, total=total)}


@router.get(
    "/retention-cohorts",
    response_model=list[RetentionCohortPoint],
    summary="Monthly signup-cohort product retention",
    description=(
        "Grain: one row per (cohort_month, period_index), observable periods only - a period that "
        "hasn't happened yet is never fabricated as a 0%% row. retained_users=0 is a meaningful "
        "observation here, not a data gap (see docs/dbt_marts.md)."
    ),
)
def retention_cohorts(
    cohort_month: date | None = Query(default=None, description="Filter to one cohort's signup month."),
    db: Session = Depends(get_read_only_db),
) -> list[dict]:
    return growth_service.get_retention_cohorts(db, cohort_month)

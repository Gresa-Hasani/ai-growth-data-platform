"""Shared helpers for the service layer: date-range validation/defaulting
and pagination clamping, used identically by every domain service so the
same invalid-input error shape and default window apply everywhere."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import HTTPException

from app.config import SUPPORTED_CURRENCIES, settings


def resolve_date_range(
    start_date: date | None, end_date: date | None
) -> tuple[date, date]:
    """Defaults to the trailing `default_date_window_days` ending today
    when neither bound is given; rejects an end before a start, and a
    window wider than `max_date_window_days` (a request for "all time" on
    an unbounded range would otherwise force a full-table scan)."""
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=settings.default_date_window_days)

    if end_date < start_date:
        raise HTTPException(status_code=422, detail="end_date must not be before start_date")

    if (end_date - start_date).days > settings.max_date_window_days:
        raise HTTPException(
            status_code=422,
            detail=f"date range must not exceed {settings.max_date_window_days} days",
        )

    return start_date, end_date


def validate_currency(currency: str | None) -> str | None:
    if currency is None:
        return None
    normalized = currency.upper()
    if normalized not in SUPPORTED_CURRENCIES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported currency '{currency}' - must be one of {', '.join(SUPPORTED_CURRENCIES)}",
        )
    return normalized


def clamp_pagination(limit: int, offset: int) -> tuple[int, int]:
    if limit < 1 or limit > settings.max_page_size:
        raise HTTPException(
            status_code=422, detail=f"limit must be between 1 and {settings.max_page_size}"
        )
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must not be negative")
    return limit, offset

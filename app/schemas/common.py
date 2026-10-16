"""Shared Pydantic response models: pagination envelope and the error
shape every handled failure returns, so every endpoint group looks the
same to a client rather than inventing its own ad-hoc dict shape."""
from __future__ import annotations

from datetime import date
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageMeta(BaseModel):
    limit: int
    offset: int
    total: int


class Page(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta


class ErrorResponse(BaseModel):
    """Never includes a stack trace, SQL text, or connection details -
    only a stable error code and a human-readable, non-sensitive message."""

    error: str
    detail: str


class DateRangeParams(BaseModel):
    """Shared validation for every date-range filter: end >= start, and
    the window never exceeds ApiSettings.max_date_window_days (checked by
    the caller, which has access to the configured limit)."""

    start_date: date | None = Field(default=None, description="Inclusive start date (UTC).")
    end_date: date | None = Field(default=None, description="Inclusive end date (UTC).")

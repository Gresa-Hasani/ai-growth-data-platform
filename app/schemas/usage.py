from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class FeatureAdoptionRecord(BaseModel):
    event_name: str
    adopting_user_count: int
    total_event_count: int
    eligible_user_count: int
    adoption_rate: float | None


class DailyUsageRecord(BaseModel):
    date_day: date
    organization_id: str | None
    event_name: str
    event_count: int
    distinct_session_count: int


class ApiUsageRecord(BaseModel):
    month_start_date: date
    organization_id: str | None
    request_count: Decimal
    error_request_count: Decimal
    error_rate: float | None
    total_estimated_cost: Decimal | None

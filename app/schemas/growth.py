from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class DailyGrowthPoint(BaseModel):
    date_day: date
    new_user_signups: int
    new_organizations: int
    cumulative_user_signups: int
    cumulative_organizations: int


class AcquisitionChannelStat(BaseModel):
    acquisition_channel: str
    currency: str | None
    signup_count: int
    trial_start_count: int
    paid_conversion_count: int
    trial_to_paid_conversion_rate: float | None
    billed_amount: Decimal
    paid_amount: Decimal


class TrialConversionRecord(BaseModel):
    user_id: str
    organization_id: str | None
    signup_at: datetime
    trial_start_at: datetime
    paid_conversion_at: datetime | None
    is_converted: bool
    is_observation_complete: bool
    days_trial_to_paid_conversion: float | None


class RetentionCohortPoint(BaseModel):
    cohort_month: date
    cohort_size: int
    period_index: int
    retained_users: int
    retention_rate: float | None

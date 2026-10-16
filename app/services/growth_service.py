"""Growth analytics queries against mart_daily_growth, mart_acquisition_channels,
mart_trial_conversion, mart_retention_cohorts. Every query is a fixed,
explicit-column SQL string - filter VALUES are bound parameters, table
names are never derived from request input.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services._shared import clamp_pagination, resolve_date_range


def get_daily_growth(db: Session, start_date: date | None, end_date: date | None) -> list[dict]:
    start, end = resolve_date_range(start_date, end_date)
    rows = db.execute(
        text(
            """
            SELECT date_day, new_user_signups, new_organizations,
                   cumulative_user_signups, cumulative_organizations
            FROM analytics.mart_daily_growth
            WHERE date_day BETWEEN :start AND :end
            ORDER BY date_day
            """
        ),
        {"start": start, "end": end},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_acquisition_channels(db: Session, acquisition_channel: str | None) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT acquisition_channel, currency, signup_count, trial_start_count,
                   paid_conversion_count, trial_to_paid_conversion_rate, billed_amount, paid_amount
            FROM analytics.mart_acquisition_channels
            WHERE (:channel IS NULL OR acquisition_channel = :channel)
            ORDER BY acquisition_channel, currency
            """
        ),
        {"channel": acquisition_channel},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_trial_conversion(
    db: Session,
    is_converted: bool | None,
    is_observation_complete: bool | None,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    limit, offset = clamp_pagination(limit, offset)
    base_where = """
        WHERE (:is_converted IS NULL OR is_converted = :is_converted)
          AND (:is_complete IS NULL OR is_observation_complete = :is_complete)
    """
    params = {"is_converted": is_converted, "is_complete": is_observation_complete}

    total = db.execute(
        text(f"SELECT count(*) FROM analytics.mart_trial_conversion {base_where}"), params
    ).scalar_one()

    rows = db.execute(
        text(
            f"""
            SELECT user_id, organization_id, signup_at, trial_start_at, paid_conversion_at,
                   is_converted, is_observation_complete, days_trial_to_paid_conversion
            FROM analytics.mart_trial_conversion
            {base_where}
            ORDER BY user_id
            LIMIT :limit OFFSET :offset
            """
        ),
        {**params, "limit": limit, "offset": offset},
    ).mappings().all()
    return [dict(r) for r in rows], total


def get_retention_cohorts(db: Session, cohort_month: date | None) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT cohort_month, cohort_size, period_index, retained_users, retention_rate
            FROM analytics.mart_retention_cohorts
            WHERE (:cohort_month IS NULL OR cohort_month = :cohort_month)
            ORDER BY cohort_month, period_index
            """
        ),
        {"cohort_month": cohort_month},
    ).mappings().all()
    return [dict(r) for r in rows]

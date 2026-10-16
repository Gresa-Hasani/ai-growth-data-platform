from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services._shared import clamp_pagination, resolve_date_range


def get_feature_adoption(db: Session, event_name: str | None) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT event_name, adopting_user_count, total_event_count,
                   eligible_user_count, adoption_rate
            FROM analytics.mart_feature_adoption
            WHERE (:event_name IS NULL OR event_name = :event_name)
            ORDER BY event_name
            """
        ),
        {"event_name": event_name},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_daily_usage(
    db: Session,
    start_date: date | None,
    end_date: date | None,
    organization_id: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    start, end = resolve_date_range(start_date, end_date)
    limit, offset = clamp_pagination(limit, offset)
    where = """
        WHERE date_day BETWEEN :start AND :end
          AND (:organization_id IS NULL OR organization_id = :organization_id)
    """
    params = {"start": start, "end": end, "organization_id": organization_id}

    total = db.execute(
        text(f"SELECT count(*) FROM analytics.fact_product_usage {where}"), params
    ).scalar_one()

    rows = db.execute(
        text(
            f"""
            SELECT date_day, organization_id, event_name, event_count, distinct_session_count
            FROM analytics.fact_product_usage
            {where}
            ORDER BY date_day, organization_id, event_name
            LIMIT :limit OFFSET :offset
            """
        ),
        {**params, "limit": limit, "offset": offset},
    ).mappings().all()
    return [dict(r) for r in rows], total


def get_api_usage(
    db: Session,
    start_date: date | None,
    end_date: date | None,
    organization_id: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    """mart_api_usage's grain is (month_start_date, organization_key) -
    dim_organizations is joined to expose organization_id, the external
    identifier, rather than the internal surrogate key."""
    start, end = resolve_date_range(start_date, end_date)
    limit, offset = clamp_pagination(limit, offset)
    where = """
        WHERE mart_api_usage.month_start_date BETWEEN :start AND :end
          AND (:organization_id IS NULL OR dim_organizations.organization_id = :organization_id)
    """
    params = {"start": start, "end": end, "organization_id": organization_id}
    join = """
        FROM analytics.mart_api_usage
        LEFT JOIN analytics.dim_organizations
            ON mart_api_usage.organization_key = dim_organizations.organization_key
    """

    total = db.execute(text(f"SELECT count(*) {join} {where}"), params).scalar_one()

    rows = db.execute(
        text(
            f"""
            SELECT mart_api_usage.month_start_date, dim_organizations.organization_id,
                   mart_api_usage.request_count, mart_api_usage.error_request_count,
                   mart_api_usage.error_rate, mart_api_usage.total_estimated_cost
            {join}
            {where}
            ORDER BY mart_api_usage.month_start_date, dim_organizations.organization_id
            LIMIT :limit OFFSET :offset
            """
        ),
        {**params, "limit": limit, "offset": offset},
    ).mappings().all()
    return [dict(r) for r in rows], total

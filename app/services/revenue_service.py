"""Revenue analytics queries. Every query selects `currency` explicitly
and never aggregates `SUM(...)` across multiple currency rows - the marts
themselves are already grouped by currency (Phase 5), and these queries
preserve that grouping all the way to the response.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services._shared import clamp_pagination, validate_currency


def get_mrr(db: Session, currency: str | None) -> list[dict]:
    currency = validate_currency(currency)
    rows = db.execute(
        text(
            """
            SELECT as_of_date, currency, active_subscription_count, mrr
            FROM analytics.mart_mrr
            WHERE (:currency IS NULL OR currency = :currency)
            ORDER BY currency
            """
        ),
        {"currency": currency},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_arr(db: Session, currency: str | None) -> list[dict]:
    currency = validate_currency(currency)
    rows = db.execute(
        text(
            """
            SELECT as_of_date, currency, active_subscription_count, mrr, arr
            FROM analytics.mart_arr
            WHERE (:currency IS NULL OR currency = :currency)
            ORDER BY currency
            """
        ),
        {"currency": currency},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_churn(db: Session, organization_type: str | None) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT period_start, period_end, organization_type,
                   customers_active_at_start, customers_churned, churn_rate
            FROM analytics.mart_customer_churn
            WHERE (:org_type IS NULL OR organization_type = :org_type)
            ORDER BY organization_type
            """
        ),
        {"org_type": organization_type},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_arpu(db: Session, currency: str | None) -> list[dict]:
    currency = validate_currency(currency)
    rows = db.execute(
        text(
            """
            SELECT as_of_date, currency, mrr, paying_organization_count, arpu
            FROM analytics.mart_arpu
            WHERE (:currency IS NULL OR currency = :currency)
            ORDER BY currency
            """
        ),
        {"currency": currency},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_customer_ltv(
    db: Session, currency: str | None, limit: int, offset: int
) -> tuple[list[dict], int]:
    currency = validate_currency(currency)
    limit, offset = clamp_pagination(limit, offset)

    total = db.execute(
        text(
            """
            SELECT count(*)
            FROM analytics.mart_customer_ltv
            WHERE (:currency IS NULL OR currency = :currency)
            """
        ),
        {"currency": currency},
    ).scalar_one()

    rows = db.execute(
        text(
            """
            SELECT dim_organizations.organization_id,
                   mart_customer_ltv.currency,
                   mart_customer_ltv.realized_ltv_to_date,
                   mart_customer_ltv.current_arpu,
                   mart_customer_ltv.platform_churn_rate,
                   mart_customer_ltv.estimated_ltv
            FROM analytics.mart_customer_ltv
            JOIN analytics.dim_organizations
                ON mart_customer_ltv.organization_key = dim_organizations.organization_key
            WHERE (:currency IS NULL OR mart_customer_ltv.currency = :currency)
            ORDER BY dim_organizations.organization_id, mart_customer_ltv.currency
            LIMIT :limit OFFSET :offset
            """
        ),
        {"currency": currency, "limit": limit, "offset": offset},
    ).mappings().all()
    return [dict(r) for r in rows], total

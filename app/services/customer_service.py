"""Customer = organization (see app/schemas/customers.py). list_customers
is kept deliberately light (no joins) to avoid N+1 cost at list scale;
get_customer_detail runs a handful of targeted single-row queries for one
specific organization, which is not an N+1 pattern (N is always 1 here).
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services._shared import clamp_pagination


def list_customers(
    db: Session, organization_type: str | None, limit: int, offset: int
) -> tuple[list[dict], int]:
    limit, offset = clamp_pagination(limit, offset)
    where = "WHERE (:org_type IS NULL OR organization_type = :org_type)"
    params = {"org_type": organization_type}

    total = db.execute(
        text(f"SELECT count(*) FROM analytics.dim_organizations {where}"), params
    ).scalar_one()

    rows = db.execute(
        text(
            f"""
            SELECT organization_id, organization_name, organization_type,
                   is_enterprise, country_code, created_date
            FROM analytics.dim_organizations
            {where}
            ORDER BY organization_id
            LIMIT :limit OFFSET :offset
            """
        ),
        {**params, "limit": limit, "offset": offset},
    ).mappings().all()
    return [dict(r) for r in rows], total


def get_customer_detail(db: Session, organization_id: str) -> dict | None:
    org = db.execute(
        text(
            """
            SELECT organization_id, organization_key, organization_name, organization_type,
                   is_enterprise, country_code, created_date
            FROM analytics.dim_organizations
            WHERE organization_id = :organization_id
            """
        ),
        {"organization_id": organization_id},
    ).mappings().first()
    if org is None:
        return None

    organization_key = org["organization_key"]

    active_mrr_rows = db.execute(
        text(
            """
            SELECT currency, sum(monthly_equivalent_amount) AS active_monthly_recurring_revenue
            FROM analytics.fact_subscriptions
            WHERE organization_key = :organization_key AND is_recurring_active_now
            GROUP BY currency
            """
        ),
        {"organization_key": organization_key},
    ).mappings().all()
    mrr_by_currency = {r["currency"]: r["active_monthly_recurring_revenue"] for r in active_mrr_rows}

    ltv_rows = db.execute(
        text(
            """
            SELECT currency, realized_ltv_to_date, estimated_ltv
            FROM analytics.mart_customer_ltv
            WHERE organization_key = :organization_key
            """
        ),
        {"organization_key": organization_key},
    ).mappings().all()

    revenue_by_currency = []
    seen_currencies = set()
    for r in ltv_rows:
        seen_currencies.add(r["currency"])
        revenue_by_currency.append(
            {
                "currency": r["currency"],
                "active_monthly_recurring_revenue": mrr_by_currency.get(r["currency"], 0),
                "realized_ltv_to_date": r["realized_ltv_to_date"],
                "estimated_ltv": r["estimated_ltv"],
            }
        )
    for currency, mrr in mrr_by_currency.items():
        if currency not in seen_currencies:
            revenue_by_currency.append(
                {
                    "currency": currency,
                    "active_monthly_recurring_revenue": mrr,
                    "realized_ltv_to_date": None,
                    "estimated_ltv": None,
                }
            )

    product_events = db.execute(
        text(
            """
            SELECT coalesce(sum(event_count), 0) AS n
            FROM analytics.fact_product_usage
            WHERE organization_id = :organization_id
              AND date_day > current_date - interval '30 days'
            """
        ),
        {"organization_id": organization_id},
    ).scalar_one()

    api_requests = db.execute(
        text(
            """
            SELECT coalesce(sum(request_count), 0) AS n
            FROM analytics.fact_api_usage
            WHERE organization_id = :organization_id
              AND date_day > current_date - interval '30 days'
            """
        ),
        {"organization_id": organization_id},
    ).scalar_one()

    return {
        "organization_id": org["organization_id"],
        "organization_name": org["organization_name"],
        "organization_type": org["organization_type"],
        "is_enterprise": org["is_enterprise"],
        "country_code": org["country_code"],
        "created_date": org["created_date"],
        "revenue_by_currency": revenue_by_currency,
        "usage_last_30_days": {
            "window_days": 30,
            "product_event_count": int(product_events),
            "api_request_count": int(api_requests),
        },
    }

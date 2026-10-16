-- Grain: one row per (organization_key, currency). Two DISTINCT metrics,
-- never blended into one number:
--   - realized_ltv_to_date: REALIZED HISTORICAL revenue (sum of paid_amount
--     to date) - an observed fact, not a forecast.
--   - estimated_ltv: the classic SaaS estimate ARPU / churn_rate, which IS
--     a speculative projection (it assumes current ARPU and the platform-
--     wide churn rate hold indefinitely) - labeled accordingly, never
--     presented as something that already happened. Uses the platform-wide
--     ('all' segment) churn rate from mart_customer_churn, not an
--     org-specific prediction (this source has no per-organization churn
--     history to estimate from individually).
-- NULL, not a divide-by-zero error or a fabricated number, when churn_rate
-- is zero, NULL, or otherwise unusable - see the safe_divide guard below.

with realized_revenue as (

    select
        organization_key,
        currency,
        sum(paid_amount) as realized_ltv_to_date
    from {{ ref('fact_invoices') }}
    where organization_key is not null
      and currency is not null
    group by organization_key, currency

),

arpu_by_currency as (

    select currency, arpu
    from {{ ref('mart_arpu') }}

),

platform_churn as (

    select churn_rate
    from {{ ref('mart_customer_churn') }}
    where organization_type = 'all'

)

select
    realized_revenue.organization_key,
    realized_revenue.currency,
    realized_revenue.realized_ltv_to_date,
    arpu_by_currency.arpu as current_arpu,
    platform_churn.churn_rate as platform_churn_rate,
    case
        when platform_churn.churn_rate is not null and platform_churn.churn_rate > 0
            then arpu_by_currency.arpu / platform_churn.churn_rate
        else null
    end as estimated_ltv
from realized_revenue
left join arpu_by_currency on realized_revenue.currency = arpu_by_currency.currency
cross join platform_churn

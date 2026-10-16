-- Grain: one row per enterprise organization (dim_organizations.is_enterprise).
-- "Growth" is measured two ways, deliberately NOT blended into one number:
--   1. current_monthly_equivalent_revenue - a POINT-IN-TIME figure (same
--      snapshot semantics as mart_mrr; see its header comment for why a
--      historical revenue trend cannot be reconstructed from this source).
--      This is NOT a revenue "growth" measure - there is no prior-period
--      revenue snapshot to compare it against.
--   2. usage_change_pct - a GENUINE trend, because product/API events ARE
--      real historical time series (unlike subscription status). Compares
--      the trailing 30 days against the preceding 30 days.
-- Mixing these into a single "enterprise growth score" would misrepresent
-- a point-in-time revenue figure as a trend, which is exactly what this
-- model is designed to avoid.

with enterprise_orgs as (

    select organization_key, organization_id
    from {{ ref('dim_organizations') }}
    where is_enterprise

),

current_revenue as (

    select
        organization_key,
        count(*) as active_subscription_count,
        sum(monthly_equivalent_amount) as current_monthly_equivalent_revenue
    from {{ ref('fact_subscriptions') }}
    where is_recurring_active_now
    group by organization_key

),

product_usage_recent as (

    select
        organization_key,
        sum(event_count) filter (
            where date_day > current_date - interval '30 days' and date_day <= current_date
        ) as usage_last_30_days,
        sum(event_count) filter (
            where date_day > current_date - interval '60 days' and date_day <= current_date - interval '30 days'
        ) as usage_prior_30_days
    from {{ ref('fact_product_usage') }}
    where organization_key is not null
    group by organization_key

),

api_usage_recent as (

    select
        organization_key,
        sum(request_count) filter (
            where date_day > current_date - interval '30 days' and date_day <= current_date
        ) as usage_last_30_days,
        sum(request_count) filter (
            where date_day > current_date - interval '60 days' and date_day <= current_date - interval '30 days'
        ) as usage_prior_30_days
    from {{ ref('fact_api_usage') }}
    where organization_key is not null
    group by organization_key

),

combined_usage as (

    select
        organization_key,
        coalesce(sum(usage_last_30_days), 0) as usage_last_30_days,
        coalesce(sum(usage_prior_30_days), 0) as usage_prior_30_days
    from (
        select organization_key, usage_last_30_days, usage_prior_30_days from product_usage_recent
        union all
        select organization_key, usage_last_30_days, usage_prior_30_days from api_usage_recent
    ) combined
    group by organization_key

)

select
    enterprise_orgs.organization_key,
    enterprise_orgs.organization_id,
    coalesce(current_revenue.active_subscription_count, 0) as active_subscription_count,
    current_revenue.current_monthly_equivalent_revenue,
    coalesce(combined_usage.usage_last_30_days, 0) as usage_last_30_days,
    coalesce(combined_usage.usage_prior_30_days, 0) as usage_prior_30_days,
    coalesce(combined_usage.usage_last_30_days, 0) - coalesce(combined_usage.usage_prior_30_days, 0)
        as usage_change_count,
    {{ safe_divide(
        "coalesce(combined_usage.usage_last_30_days, 0) - coalesce(combined_usage.usage_prior_30_days, 0)",
        "combined_usage.usage_prior_30_days"
    ) }} as usage_change_pct
from enterprise_orgs
left join current_revenue on enterprise_orgs.organization_key = current_revenue.organization_key
left join combined_usage on enterprise_orgs.organization_key = combined_usage.organization_key

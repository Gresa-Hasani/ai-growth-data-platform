-- Grain: one row per (plan_name, billing_interval) combination actually
-- observed in subscriptions. plan_name alone is NOT a valid natural key -
-- the same plan+interval shows real price variance in this dataset (e.g.
-- business/monthly ranges $284.94-$313.79), consistent with the ~5% random
-- jitter scripts/generate_data.py applies, not negotiated/tiered pricing.
-- There is no separate plan-pricing catalog table in this source, so
-- typical_monthly_equivalent_amount below is an OBSERVED AVERAGE computed
-- from actual subscriptions, not a price lookup - it should be read as
-- "what this plan+interval combination typically costs in this dataset,"
-- not as an authoritative list price.
--
-- A NULL plan_name (is_plan_missing in staging) gets its own row here
-- (plan_name = 'unknown') rather than being silently excluded - a
-- subscription with no known plan still needs a dim_plans row to join to.

with subscriptions as (

    select
        coalesce(plan_name, 'unknown') as plan_name,
        billing_interval,
        is_plan_missing,
        subscription_amount,
        {{ monthly_equivalent_amount('subscription_amount', 'billing_interval') }} as monthly_equivalent_amount
    from {{ ref('stg_billing__subscriptions') }}
    where billing_interval is not null

),

aggregated as (

    select
        plan_name,
        billing_interval,
        bool_or(is_plan_missing) as is_plan_missing,
        count(*) as observed_subscription_count,
        avg(monthly_equivalent_amount) as typical_monthly_equivalent_amount
    from subscriptions
    group by plan_name, billing_interval

)

select
    {{ dbt_utils.generate_surrogate_key(['plan_name', 'billing_interval']) }} as plan_key,
    plan_name,
    billing_interval,
    is_plan_missing,
    observed_subscription_count,
    typical_monthly_equivalent_amount
from aggregated

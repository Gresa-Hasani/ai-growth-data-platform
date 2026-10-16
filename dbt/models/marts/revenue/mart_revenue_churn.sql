-- Grain: one row per currency, for the same fixed trailing-30-day period
-- as mart_customer_churn. GROSS revenue churn only:
--   lost recurring revenue during the period / recurring revenue at the
--   start of the period
-- Lost revenue = monthly_equivalent_amount of subscriptions that were
-- active at period_start but are no longer active at period_end (reuses
-- the exact same active-at-start/active-at-end logic as mart_customer_churn,
-- at the SUBSCRIPTION grain rather than the organization grain, since
-- revenue is lost per subscription even when the organization survives
-- via another subscription).
--
-- NET revenue churn (which would net lost revenue against expansion and
-- reactivation revenue) is NOT implemented: this source has no history of
-- a subscription's PAST amount - scripts/generate_data.py never models a
-- plan upgrade/downgrade, and raw.subscriptions keeps only the current
-- amount. Without a prior amount to compare against, "expansion" and
-- "contraction" cannot be detected, only invented - so only gross churn is
-- reported, per the project's policy against overstating accuracy.
-- New-subscription revenue during the period is correctly excluded from
-- the denominator by construction (it only includes subscriptions already
-- active_at_start).

with period as (

    select
        (current_date - interval '30 days')::date as period_start,
        current_date as period_end

),

subscriptions as (

    select
        subscription_id,
        currency,
        started_at,
        cancelled_at,
        status,
        monthly_equivalent_amount
    from {{ ref('fact_subscriptions') }}
    where currency is not null
      and monthly_equivalent_amount is not null

),

active_at_start as (

    select
        subscription_id,
        currency,
        monthly_equivalent_amount
    from subscriptions, period
    where started_at <= period.period_start
      and (cancelled_at is null or cancelled_at > period.period_start)

),

active_at_end as (

    select subscription_id
    from subscriptions
    where {{ is_subscription_recurring_active('status', 'cancelled_at') }}

),

opening as (

    select
        currency,
        sum(monthly_equivalent_amount) as opening_recurring_revenue
    from active_at_start
    group by currency

),

churned as (

    select
        active_at_start.currency,
        sum(active_at_start.monthly_equivalent_amount) as churned_recurring_revenue
    from active_at_start
    left join active_at_end on active_at_start.subscription_id = active_at_end.subscription_id
    where active_at_end.subscription_id is null
    group by active_at_start.currency

)

select
    period.period_start,
    period.period_end,
    opening.currency,
    opening.opening_recurring_revenue,
    coalesce(churned.churned_recurring_revenue, 0) as churned_recurring_revenue,
    {{ safe_divide('coalesce(churned.churned_recurring_revenue, 0)', 'opening.opening_recurring_revenue') }}
        as gross_revenue_churn_rate
from opening
cross join period
left join churned on opening.currency = churned.currency

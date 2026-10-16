-- Grain: one row per organization_type (plus one 'all' row for the
-- platform-wide figure) for a single fixed trailing-30-day period. Customer
-- = organization (not subscription, not user) - see docs/dbt_marts.md
-- "Customer identity." Churn formula (exact, per the project's definition):
--   customers active at the start of the period who churned during the
--   period / customers active at the start of the period.
--
-- "Active at period_start": the organization had >=1 subscription whose
-- started_at <= period_start AND (cancelled_at is null OR cancelled_at >
-- period_start) - i.e. genuinely in effect at that past moment, using the
-- subscription's own historical timestamps (not its current `status`,
-- which only reflects the present). "Active at period_end" reuses the
-- same is_subscription_recurring_active definition as mart_mrr, evaluated
-- at period_end = today.
--
-- An organization with MULTIPLE subscriptions (163 organizations in the
-- current dataset have more than one simultaneously-active subscription)
-- only counts as churned if it has NO remaining active subscription at
-- period_end - a single subscription cancelling while another stays
-- active does NOT make that organization churned. This is reconstructed
-- from genuinely historical fields (started_at/cancelled_at), so it is a
-- real (not merely approximated) churn measurement for this period -
-- unlike MRR/ARR, no current-state-only assumption is required here.

with period as (

    select
        (current_date - interval '30 days')::date as period_start,
        current_date as period_end

),

org_subscriptions as (

    select
        organization_id,
        started_at,
        cancelled_at,
        status
    from {{ ref('int_user_subscription_history') }}
    where organization_id is not null

),

active_at_start as (

    select distinct organization_id
    from org_subscriptions, period
    where started_at <= period.period_start
      and (cancelled_at is null or cancelled_at > period.period_start)

),

active_at_end as (

    select distinct organization_id
    from org_subscriptions
    where {{ is_subscription_recurring_active('status', 'cancelled_at') }}

),

org_status as (

    select
        active_at_start.organization_id,
        (active_at_end.organization_id is null) as is_churned
    from active_at_start
    left join active_at_end on active_at_start.organization_id = active_at_end.organization_id

),

with_segment as (

    select
        org_status.organization_id,
        org_status.is_churned,
        coalesce(dim_organizations.organization_type, 'unknown') as organization_type
    from org_status
    left join {{ ref('dim_organizations') }} on org_status.organization_id = dim_organizations.organization_id

),

by_segment as (

    select
        organization_type,
        count(*) as customers_active_at_start,
        count(*) filter (where is_churned) as customers_churned
    from with_segment
    group by organization_type

),

overall as (

    select
        'all' as organization_type,
        count(*) as customers_active_at_start,
        count(*) filter (where is_churned) as customers_churned
    from with_segment

)

select
    period.period_start,
    period.period_end,
    combined.organization_type,
    combined.customers_active_at_start,
    combined.customers_churned,
    {{ safe_divide('combined.customers_churned', 'combined.customers_active_at_start') }} as churn_rate
from (select * from by_segment union all select * from overall) as combined
cross join period

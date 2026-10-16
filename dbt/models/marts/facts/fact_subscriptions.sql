-- Grain: one row per source subscription_id (matches
-- stg_billing__subscriptions exactly - no aggregation, no fan-out).
-- Unmatched-key policy (applied consistently across every fact in this
-- project, see docs/dbt_marts.md "Unmatched dimension keys"): a dimension
-- surrogate key is NULL when the natural key doesn't resolve, paired with
-- an explicit boolean flag inherited from the intermediate layer - never a
-- fabricated "-1/unknown" dimension row standing in for a real entity.

with subscription_history as (

    select
        subscription_id,
        user_id,
        organization_id,
        is_user_orphaned,
        plan_name,
        is_plan_missing,
        billing_interval,
        status,
        is_status_unknown,
        currency,
        is_currency_recognized,
        subscription_amount,
        started_at,
        trial_started_at,
        trial_ended_at,
        cancelled_at,
        current_period_start,
        current_period_end,
        updated_at
    from {{ ref('int_user_subscription_history') }}

),

users as (
    select user_id as _user_id, user_key from {{ ref('dim_users') }}
),

organizations as (
    select organization_id as _organization_id, organization_key from {{ ref('dim_organizations') }}
),

plans as (
    select
        coalesce(plan_name, 'unknown') as _plan_name,
        billing_interval as _billing_interval,
        plan_key
    from {{ ref('dim_plans') }}
)

select
    subscription_history.subscription_id,
    users.user_key,
    subscription_history.is_user_orphaned,
    organizations.organization_key,
    (subscription_history.organization_id is not null and organizations.organization_key is null)
        as is_organization_unmatched,
    plans.plan_key,
    subscription_history.plan_name,
    subscription_history.is_plan_missing,
    subscription_history.billing_interval,
    subscription_history.status,
    subscription_history.is_status_unknown,
    {{ is_subscription_recurring_active('subscription_history.status', 'subscription_history.cancelled_at') }}
        as is_recurring_active_now,
    subscription_history.currency,
    subscription_history.is_currency_recognized,
    subscription_history.subscription_amount,
    {{ monthly_equivalent_amount('subscription_history.subscription_amount', 'subscription_history.billing_interval') }}
        as monthly_equivalent_amount,
    subscription_history.started_at,
    subscription_history.trial_started_at,
    subscription_history.trial_ended_at,
    subscription_history.cancelled_at,
    subscription_history.current_period_start,
    subscription_history.current_period_end,
    subscription_history.updated_at
from subscription_history
left join users on subscription_history.user_id = users._user_id
left join organizations on subscription_history.organization_id = organizations._organization_id
left join plans
    on coalesce(subscription_history.plan_name, 'unknown') = plans._plan_name
    and subscription_history.billing_interval = plans._billing_interval

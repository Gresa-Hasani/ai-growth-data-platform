-- Grain: one row per user_id. NOTE on terminology: unlike
-- int_customer_revenue / int_api_usage_by_customer / int_customer_activity
-- (where "customer" = organization), this model's grain is the individual
-- USER - signup/trial/paid/cancellation milestones are inherently
-- user-scoped events in this schema (a user signs up, a user's
-- subscription trials). See docs/dbt_intermediate.md "Customer identity"
-- for the full user-vs-organization-vs-customer distinction. An
-- organization-level lifecycle roll-up is a Phase 5 concern.
--
-- Reuses int_conversion_funnel for signup/trial/paid milestones rather
-- than re-deriving the same logic twice. lifecycle_stage is read directly
-- off the user's MOST RECENT subscription's current status - it is
-- explicitly a snapshot of today's state, not a reconstructed historical
-- journey (this source has no change-event history to reconstruct from;
-- see int_subscription_transitions for the same limitation). A user with
-- no subscription at all gets 'no_subscription', never a guessed stage.

with funnel as (

    select
        user_id,
        organization_id,
        signup_at as first_signup_at,
        trial_start_at as first_trial_started_at,
        paid_conversion_at as first_paid_started_at
    from {{ ref('int_conversion_funnel') }}

),

cancellations as (

    select
        user_id,
        min(cancelled_at) as first_cancelled_at
    from {{ ref('stg_billing__subscriptions') }}
    where user_id is not null
      and cancelled_at is not null
    group by user_id

),

-- Deterministic "current" subscription per user: latest updated_at, tied
-- broken by subscription_id so the choice never varies between runs.
most_recent_subscription_ranked as (

    select
        user_id,
        status,
        plan_name,
        row_number() over (
            partition by user_id
            order by updated_at desc nulls last, subscription_id desc
        ) as recency_rank
    from {{ ref('stg_billing__subscriptions') }}
    where user_id is not null

),

most_recent_subscription as (

    select
        user_id,
        status as most_recent_subscription_status,
        plan_name as most_recent_plan_name
    from most_recent_subscription_ranked
    where recency_rank = 1

),

joined as (

    select
        funnel.user_id,
        funnel.organization_id,
        funnel.first_signup_at,
        funnel.first_trial_started_at,
        funnel.first_paid_started_at,
        cancellations.first_cancelled_at,
        most_recent_subscription.most_recent_subscription_status,
        most_recent_subscription.most_recent_plan_name
    from funnel
    left join cancellations on funnel.user_id = cancellations.user_id
    left join most_recent_subscription on funnel.user_id = most_recent_subscription.user_id

)

select
    user_id,
    organization_id,
    first_signup_at,
    first_trial_started_at,
    first_paid_started_at,
    first_cancelled_at,
    most_recent_subscription_status,
    most_recent_plan_name,
    coalesce(most_recent_subscription_status, 'no_subscription') as lifecycle_stage
from joined

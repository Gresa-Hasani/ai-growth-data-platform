-- Grain: one row per user_id (the funnel's population). Each user appears
-- exactly once - milestone timestamps are aggregated with MIN() across all
-- of a user's subscriptions, which is what prevents a user with multiple
-- subscriptions from being counted more than once in any funnel stage.
--
-- Milestone definitions:
--   signup_at           = stg_app__users.signup_at (always present)
--   trial_start_at      = earliest trial_started_at across the user's subscriptions
--   paid_conversion_at  = earliest started_at among subscriptions with a real,
--                         non-missing, non-free plan_name (plan_name is not null
--                         and plan_name != 'free') - i.e. the first time this
--                         user had a paying plan begin, regardless of that
--                         subscription's CURRENT status (conversion is a
--                         historical event, not a snapshot of today's state).
--
-- This model deliberately stops at exposing milestones/durations/quality
-- flags - it does NOT classify a user as "converted" or "not converted."
-- A user with no paid_conversion_at may simply not have reached that stage
-- within the observation window yet; treating that as a failed conversion
-- is exactly what Phase 5's conversion-rate mart must decide explicitly,
-- not something inferred here.

with users as (

    select
        user_id,
        organization_id,
        signup_at
    from {{ ref('stg_app__users') }}

),

subscription_milestones as (

    select
        user_id,
        min(trial_started_at) as trial_start_at,
        min(started_at) filter (
            where plan_name is not null and plan_name != 'free'
        ) as paid_conversion_at
    from {{ ref('stg_billing__subscriptions') }}
    where user_id is not null
    group by user_id

),

joined as (

    select
        users.user_id,
        users.organization_id,
        users.signup_at,
        subscription_milestones.trial_start_at,
        subscription_milestones.paid_conversion_at
    from users
    left join subscription_milestones on users.user_id = subscription_milestones.user_id

)

select
    user_id,
    organization_id,
    signup_at,
    trial_start_at,
    paid_conversion_at,
    (trial_start_at is not null and trial_start_at < signup_at) as is_trial_before_signup,
    (paid_conversion_at is not null and paid_conversion_at < signup_at) as is_paid_conversion_before_signup,
    (
        trial_start_at is not null
        and paid_conversion_at is not null
        and paid_conversion_at < trial_start_at
    ) as is_paid_conversion_before_trial_start,
    case
        when signup_at is not null and trial_start_at is not null
            then extract(day from trial_start_at - signup_at)
        else null
    end as days_signup_to_trial,
    case
        when trial_start_at is not null and paid_conversion_at is not null
            then extract(day from paid_conversion_at - trial_start_at)
        else null
    end as days_trial_to_paid,
    case
        when signup_at is not null and paid_conversion_at is not null
            then extract(day from paid_conversion_at - signup_at)
        else null
    end as days_signup_to_paid
from joined

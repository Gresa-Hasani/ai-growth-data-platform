-- IMPORTANT LIMITATION (see docs/dbt_intermediate.md "Subscription
-- transitions"): raw.subscriptions is a CURRENT-STATE record, not a change-
-- event log - there is no history of past status values to derive true
-- state-change events from. This model instead derives an ORDERED MILESTONE
-- TIMELINE from the timestamp fields already present on each subscription's
-- single current row (trial_started_at, trial_ended_at, started_at,
-- cancelled_at). These are observed milestones, not inferred ones - every
-- transition_at value here is copied directly from a real staging column,
-- never guessed. Plan-change transitions are NOT included: a current-state
-- row cannot reveal what plan a subscription had before its latest update.
-- True state-change detection (including plan changes) requires the SCD
-- Type 2 subscription-status snapshot planned for Phase 6.

with subscriptions as (

    select
        subscription_id,
        user_id,
        organization_id,
        status,
        plan_name,
        trial_started_at,
        trial_ended_at,
        started_at,
        cancelled_at
    from {{ ref('stg_billing__subscriptions') }}

),

milestones as (

    select subscription_id, user_id, organization_id, 'trial_started' as transition_type, trial_started_at as transition_at
    from subscriptions
    where trial_started_at is not null

    union all

    select subscription_id, user_id, organization_id, 'trial_ended' as transition_type, trial_ended_at as transition_at
    from subscriptions
    where trial_ended_at is not null

    union all

    select subscription_id, user_id, organization_id, 'subscription_started' as transition_type, started_at as transition_at
    from subscriptions
    where started_at is not null

    union all

    select subscription_id, user_id, organization_id, 'subscription_cancelled' as transition_type, cancelled_at as transition_at
    from subscriptions
    where cancelled_at is not null

),

-- Deterministic ordering within a subscription: transition_at first, then
-- a fixed rank over transition_type for the (rare) case of an exact tie
-- between two distinct milestones at the same timestamp.
ordered as (

    select
        *,
        row_number() over (
            partition by subscription_id
            order by
                transition_at asc,
                case transition_type
                    when 'trial_started' then 1
                    when 'trial_ended' then 2
                    when 'subscription_started' then 3
                    when 'subscription_cancelled' then 4
                end asc
        ) as transition_sequence
    from milestones

),

with_lag as (

    select
        *,
        lag(transition_type) over (partition by subscription_id order by transition_sequence) as previous_transition_type,
        lag(transition_at) over (partition by subscription_id order by transition_sequence) as previous_transition_at
    from ordered

)

select
    subscription_id,
    user_id,
    organization_id,
    transition_sequence,
    transition_type,
    transition_at,
    previous_transition_type,
    previous_transition_at,
    case
        when previous_transition_at is not null
            then extract(day from transition_at - previous_transition_at)
        else null
    end as days_since_previous_transition
from with_lag

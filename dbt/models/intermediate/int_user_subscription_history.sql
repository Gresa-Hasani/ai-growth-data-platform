with subscriptions as (

    select
        subscription_id,
        user_id,
        organization_id,
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
    from {{ ref('stg_billing__subscriptions') }}

),

-- stg_app__users.user_id is unique (tested), so this join cannot fan out
-- subscription rows - one subscription row stays one row after the join.
users as (

    select
        user_id as _user_id,
        signup_at as user_signup_at
    from {{ ref('stg_app__users') }}

),

joined as (

    select
        subscriptions.subscription_id,
        subscriptions.user_id,
        subscriptions.organization_id,
        (subscriptions.user_id is not null and users._user_id is null) as is_user_orphaned,
        users.user_signup_at,
        subscriptions.plan_name,
        subscriptions.is_plan_missing,
        subscriptions.billing_interval,
        subscriptions.status,
        subscriptions.is_status_unknown,
        subscriptions.currency,
        subscriptions.is_currency_recognized,
        subscriptions.subscription_amount,
        subscriptions.started_at,
        subscriptions.trial_started_at,
        subscriptions.trial_ended_at,
        subscriptions.cancelled_at,
        subscriptions.current_period_start,
        subscriptions.current_period_end,
        subscriptions.updated_at,
        case
            when users.user_signup_at is not null and subscriptions.started_at is not null
                then extract(day from subscriptions.started_at - users.user_signup_at)
            else null
        end as days_from_signup_to_subscription_start
    from subscriptions
    left join users on subscriptions.user_id = users._user_id

)

select
    subscription_id,
    user_id,
    organization_id,
    is_user_orphaned,
    user_signup_at,
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
    updated_at,
    days_from_signup_to_subscription_start
from joined

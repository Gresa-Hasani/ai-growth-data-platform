{{ config(severity='error', tags=['intermediate']) }}

-- Within a single subscription, scripts/generate_data.py always derives
-- trial_started_at and started_at as signup_at + a positive offset - a
-- trial or paid conversion before the user's own signup is impossible by
-- construction. A violation here means int_conversion_funnel's milestone
-- logic (or the generator) regressed, not an expected source-corruption
-- category - see int_conversion_funnel.sql for why cross-subscription
-- ordering (is_paid_conversion_before_trial_start) is NOT included here:
-- that one IS a legitimate scenario for users with multiple subscriptions.

select
    user_id,
    signup_at,
    trial_start_at,
    paid_conversion_at
from {{ ref('int_conversion_funnel') }}
where is_trial_before_signup
   or is_paid_conversion_before_signup

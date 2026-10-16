-- Grain: one row per user who started a trial (trial_start_at is not
-- null) - the eligible cohort. Conversion event: a paid subscription
-- (plan_name not null/not 'free') that started AT OR AFTER this user's
-- trial start - deliberately excludes the (legitimate, documented in
-- int_conversion_funnel) case where a different, earlier, non-trial
-- subscription makes paid_conversion_at precede trial_start_at, since that
-- earlier subscription isn't this trial converting.
--
-- Observation window: a trial is only treated as "resolved" once at least
-- 30 days have passed since trial_start_at (generator trials run 14 days,
-- so 30 days safely covers the trial plus a short decision window). Users
-- whose trial is younger than that are marked is_observation_complete =
-- false and must NOT be counted as failed conversions in any rate -
-- compute rates as
--   sum(is_converted::int) / count(*) filter (where is_observation_complete)
-- (see docs/dbt_marts.md "Trial conversion" for the full formula).

with funnel as (

    select
        user_id,
        organization_id,
        signup_at,
        trial_start_at,
        paid_conversion_at
    from {{ ref('int_conversion_funnel') }}
    where trial_start_at is not null

)

select
    user_id,
    organization_id,
    signup_at,
    trial_start_at,
    paid_conversion_at,
    (paid_conversion_at is not null and paid_conversion_at >= trial_start_at) as is_converted,
    (current_timestamp - trial_start_at >= interval '30 days') as is_observation_complete,
    case
        when paid_conversion_at is not null and paid_conversion_at >= trial_start_at
            then extract(day from paid_conversion_at - trial_start_at)
        else null
    end as days_trial_to_paid_conversion
from funnel

-- Grain: one row per (acquisition_channel, currency). Uses the EXISTING
-- single-touch, first-touch attribution policy from int_user_acquisition
-- unchanged - no multi-touch attribution is implemented or implied here.
-- Missing attribution is represented explicitly: has_known_marketing_touch
-- = false rows are NOT folded into "organic" - acquisition_channel itself
-- is always populated (it's authoritative, from stg_app__users), so
-- "unattributed" only ever applies to the supplementary campaign/UTM
-- detail, never to the channel grouping used here.
--
-- Revenue is attributed via each invoice's own subscription user_id ->
-- that user's acquisition_channel - NOT via organization, specifically to
-- avoid the ambiguity of an organization with multiple users on different
-- channels (which would make "the" organization's channel an arbitrary
-- choice). This keeps revenue attribution exactly as reliable as the
-- underlying single-touch policy, with no additional assumption layered on.
--
-- signup_count/trial_start_count/paid_conversion_count are channel-level
-- (not currency-specific) and are therefore REPEATED across every currency
-- row for the same channel - this is intentional denormalization for a
-- flat, single-grain output, not inflation: summing them across a
-- channel's currency rows would overcount and should not be done.

with acquisition as (

    select
        user_id,
        acquisition_channel
    from {{ ref('int_user_acquisition') }}

),

funnel as (

    -- converted_to_paid is deliberately scoped to "converted FROM a trial"
    -- (trial_start_at not null AND paid_conversion_at >= trial_start_at),
    -- not "any paid conversion" - a user who went straight to paid with no
    -- trial is a real, legitimate outcome, but counting them here would
    -- make paid_conversion_count exceed trial_start_count and push
    -- trial_to_paid_conversion_rate above 1 (caught by a build-time test).
    select
        user_id,
        (trial_start_at is not null) as started_trial,
        (trial_start_at is not null and paid_conversion_at is not null and paid_conversion_at >= trial_start_at)
            as converted_to_paid
    from {{ ref('int_conversion_funnel') }}

),

channel_funnel as (

    select
        acquisition.acquisition_channel,
        count(*) as signup_count,
        count(*) filter (where funnel.started_trial) as trial_start_count,
        count(*) filter (where funnel.converted_to_paid) as paid_conversion_count
    from acquisition
    left join funnel on acquisition.user_id = funnel.user_id
    group by acquisition.acquisition_channel

),

channel_revenue as (

    select
        acquisition.acquisition_channel,
        revenue.currency,
        sum(revenue.billed_amount) as billed_amount,
        sum(revenue.paid_amount) as paid_amount
    from {{ ref('int_customer_revenue') }} as revenue
    inner join acquisition on revenue.user_id = acquisition.user_id
    where revenue.user_id is not null
    group by acquisition.acquisition_channel, revenue.currency

)

select
    channel_funnel.acquisition_channel,
    channel_revenue.currency,
    channel_funnel.signup_count,
    channel_funnel.trial_start_count,
    channel_funnel.paid_conversion_count,
    {{ safe_divide('channel_funnel.paid_conversion_count', 'channel_funnel.trial_start_count') }}
        as trial_to_paid_conversion_rate,
    coalesce(channel_revenue.billed_amount, 0) as billed_amount,
    coalesce(channel_revenue.paid_amount, 0) as paid_amount
from channel_funnel
left join channel_revenue on channel_funnel.acquisition_channel = channel_revenue.acquisition_channel

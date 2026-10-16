-- Attribution policy (see docs/dbt_intermediate.md "Attribution" for the
-- full rationale): acquisition_channel on stg_app__users is the
-- authoritative, always-present channel - it is a directly recorded field
-- on the user record, not something marketing_events needs to reconstruct.
-- marketing_events only supplements it with campaign/UTM detail from the
-- single earliest ("first touch") marketing event that is actually linked
-- to this user_id. This is deliberately single-touch, first-touch
-- attribution - NOT multi-touch attribution. ~35% of marketing_events have
-- no user_id at all (anonymous pre-signup touches) and are structurally
-- unusable for this join; they are out of scope for this model.

with users as (

    select
        user_id,
        organization_id,
        acquisition_channel,
        signup_at
    from {{ ref('stg_app__users') }}

),

marketing_touches as (

    select
        marketing_event_id,
        user_id,
        campaign_id,
        is_campaign_attributed,
        channel,
        utm_source,
        utm_medium,
        utm_campaign,
        event_timestamp
    from {{ ref('stg_marketing__events') }}
    where user_id is not null

),

-- Deterministic tie-break: earliest event_timestamp wins; ties broken by
-- marketing_event_id so the choice never varies between runs.
ranked_touches as (

    select
        *,
        row_number() over (
            partition by user_id
            order by event_timestamp asc nulls last, marketing_event_id asc
        ) as touch_rank
    from marketing_touches

),

-- Already exactly one row per user_id - joining this onto users cannot
-- fan out a user row.
first_touch as (

    select
        user_id,
        marketing_event_id as attributed_marketing_event_id,
        event_timestamp as attributed_touch_at,
        campaign_id as attributed_campaign_id,
        is_campaign_attributed,
        channel as attributed_marketing_channel,
        utm_source as attributed_utm_source,
        utm_medium as attributed_utm_medium,
        utm_campaign as attributed_utm_campaign
    from ranked_touches
    where touch_rank = 1

),

joined as (

    select
        users.user_id,
        users.organization_id,
        users.acquisition_channel,
        (first_touch.user_id is not null) as has_known_marketing_touch,
        first_touch.attributed_marketing_event_id,
        first_touch.attributed_touch_at,
        first_touch.attributed_campaign_id,
        coalesce(first_touch.is_campaign_attributed, false) as is_campaign_attributed,
        first_touch.attributed_marketing_channel,
        first_touch.attributed_utm_source,
        first_touch.attributed_utm_medium,
        first_touch.attributed_utm_campaign
    from users
    left join first_touch on users.user_id = first_touch.user_id

)

select
    user_id,
    organization_id,
    acquisition_channel,
    has_known_marketing_touch,
    attributed_marketing_event_id,
    attributed_touch_at,
    attributed_campaign_id,
    is_campaign_attributed,
    attributed_marketing_channel,
    attributed_utm_source,
    attributed_utm_medium,
    attributed_utm_campaign
from joined

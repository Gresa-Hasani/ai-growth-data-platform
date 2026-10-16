with source as (

    select * from {{ source('marketing', 'marketing_events') }}

),

renamed as (

    select
        marketing_event_id,
        user_id,
        anonymous_id,
        campaign_id,
        channel,
        event_type,
        event_timestamp,
        utm_source,
        utm_medium,
        utm_campaign,
        _ingested_at,
        _source_file,
        _run_id
    from source

),

normalized as (

    select
        marketing_event_id,
        -- Not every marketing event has a known user - anonymous_id is the
        -- only identity for pre-signup touches, and that's expected, not a
        -- quality problem.
        user_id,
        anonymous_id,
        nullif(trim(campaign_id), '') as campaign_id,
        {{ normalize_text('channel', lower=True) }} as channel,
        {{ normalize_text('event_type', lower=True) }} as event_type,
        {{ safe_cast_timestamp('event_timestamp') }} as event_timestamp,
        {{ normalize_text('utm_source', lower=True) }} as utm_source,
        {{ normalize_text('utm_medium', lower=True) }} as utm_medium,
        nullif(trim(utm_campaign), '') as utm_campaign,
        _ingested_at,
        _source_file,
        _run_id
    from renamed

),

flagged as (

    select
        *,
        -- Staging-level availability flag only - NOT an attribution model.
        (campaign_id is not null) as is_campaign_attributed
    from normalized

),

deduplicated as (

    {{ deduplicate(
        relation='flagged',
        partition_by='marketing_event_id',
        order_by='_ingested_at desc nulls last, marketing_event_id desc'
    ) }}

)

select
    marketing_event_id,
    user_id,
    anonymous_id,
    campaign_id,
    is_campaign_attributed,
    channel,
    event_type,
    event_timestamp,
    utm_source,
    utm_medium,
    utm_campaign,
    _ingested_at,
    _source_file,
    _run_id
from deduplicated

with source as (

    select * from {{ source('product', 'product_events') }}

),

renamed as (

    select
        event_id,
        user_id,
        organization_id,
        event_name,
        event_timestamp,
        session_id,
        properties,
        _ingested_at,
        _source_file,
        _run_id
    from source

),

normalized as (

    select
        event_id,
        user_id,
        organization_id,
        {{ normalize_text('event_name', lower=True) }} as event_name,
        -- event_timestamp is the event's OWN time, never coerced to match
        -- ingestion order - late-arriving/out-of-order events stay exactly
        -- as generated so later phases can reason about event time vs.
        -- processing time (_ingested_at) independently. See docs/dbt_staging.md.
        {{ safe_cast_timestamp('event_timestamp') }} as event_timestamp,
        session_id,
        -- Preserved as native jsonb rather than flattened: properties are
        -- event-specific and open-ended: flattening would either lose
        -- fields or force a wide, mostly-null schema. Downstream models can
        -- pull specific keys with -> / ->> as needed.
        properties,
        _ingested_at,
        _source_file,
        _run_id
    from renamed

),

flagged as (

    select
        *,
        (event_timestamp is null) as is_event_timestamp_missing
    from normalized

),

deduplicated as (

    {{ deduplicate(
        relation='flagged',
        partition_by='event_id',
        order_by='_ingested_at desc nulls last, event_id desc'
    ) }}

)

select
    event_id,
    user_id,
    organization_id,
    event_name,
    event_timestamp,
    is_event_timestamp_missing,
    session_id,
    properties,
    _ingested_at,
    _source_file,
    _run_id
from deduplicated

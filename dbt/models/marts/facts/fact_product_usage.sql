{{
    config(
        materialized='incremental',
        unique_key='usage_grain_key',
        incremental_strategy='delete+insert',
        tags=['facts', 'incremental']
    )
}}

-- Grain: one row per (date_day, user_id, organization_id, event_name) -
-- unchanged from Phase 4/5. Incremental as of Phase 6; see
-- docs/incremental_processing.md for the full strategy. Summary:
--
-- The incremental filter is on _ingested_at (processing time), NOT
-- event_timestamp (business/event time) - a late-arriving event can have
-- an event_timestamp from weeks ago but an _ingested_at from today, and
-- _ingested_at is the only reliable signal that a row is new to us. Every
-- calendar date that had ANY event ingested within the lookback window is
-- fully RECOMPUTED from ALL of that date's events (not just the newly-
-- ingested ones) and REPLACES the existing rows for that date
-- (delete+insert on the full grain) - never appended on top of the
-- existing aggregate. This is what prevents a rerun, or a late arrival
-- landing in an already-aggregated date, from inflating event_count.
--
-- usage_grain_key exists only because user_id/organization_id are
-- legitimately nullable (orphaned/unresolvable source rows, preserved not
-- dropped per Phase 3-5 policy) - delete+insert's row-matching compares
-- unique_key columns with plain equality, and SQL NULL never equals NULL,
-- so a nullable multi-column unique_key would silently fail to replace
-- old NULL-keyed rows on rerun. Hashing the COALESCE-guarded grain into one
-- never-null surrogate key sidesteps that correctly. user_id/organization_id
-- (nullable) are still exposed as their own columns below, unchanged in
-- meaning from Phase 5 - this is an additive fix, not a grain change.

with events as (

    select
        event_id,
        user_id,
        organization_id,
        event_name,
        event_timestamp,
        session_id,
        _ingested_at
    from {{ ref('stg_product__events') }}
    where not is_event_timestamp_missing

),

{% if is_incremental() %}

high_water_mark as (

    select coalesce(max(_last_ingested_at), '1970-01-01'::timestamptz) as watermark
    from {{ this }}

),

affected_dates as (

    select distinct events.event_timestamp::date as event_date
    from events, high_water_mark
    where events._ingested_at > high_water_mark.watermark - interval '{{ var("incremental_lookback_days", 3) }} days'

),

filtered_events as (

    select events.*
    from events
    inner join affected_dates on events.event_timestamp::date = affected_dates.event_date

)

{% else %}

filtered_events as (

    select * from events

)

{% endif %}

,aggregated as (

    select
        filtered_events.event_timestamp::date as date_day,
        filtered_events.user_id,
        filtered_events.organization_id,
        filtered_events.event_name,
        count(*) as event_count,
        count(distinct filtered_events.session_id) as distinct_session_count,
        min(filtered_events.event_timestamp) as first_event_at,
        max(filtered_events.event_timestamp) as last_event_at,
        max(filtered_events._ingested_at) as _last_ingested_at
    from filtered_events
    group by 1, 2, 3, 4

),

users as (
    select user_id as _user_id, user_key from {{ ref('dim_users') }}
),

organizations as (
    select organization_id as _organization_id, organization_key from {{ ref('dim_organizations') }}
)

select
    {{ dbt_utils.generate_surrogate_key([
        'aggregated.date_day',
        "coalesce(aggregated.user_id, '')",
        "coalesce(aggregated.organization_id, '')",
        'aggregated.event_name'
    ]) }} as usage_grain_key,
    aggregated.date_day,
    aggregated.user_id,
    users.user_key,
    (aggregated.user_id is not null and users.user_key is null) as is_user_unmatched,
    aggregated.organization_id,
    organizations.organization_key,
    (aggregated.organization_id is not null and organizations.organization_key is null)
        as is_organization_unmatched,
    aggregated.event_name,
    aggregated.event_count,
    aggregated.distinct_session_count,
    aggregated.first_event_at,
    aggregated.last_event_at,
    aggregated._last_ingested_at
from aggregated
left join users on aggregated.user_id = users._user_id
left join organizations on aggregated.organization_id = organizations._organization_id

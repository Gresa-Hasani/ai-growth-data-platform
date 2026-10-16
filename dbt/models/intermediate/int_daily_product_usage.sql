-- Grain: one row per (event_date, user_id, organization_id, event_name).
-- event_name stays in the grain (rather than collapsing to one row per
-- user-day) so downstream feature-adoption analysis can filter/group by
-- feature without re-deriving it from a flattened total. A simple
-- user-day total is just `sum(event_count) group by event_date, user_id`
-- away from this grain, so nothing is lost by keeping event_name here.
--
-- event_date is derived from event_timestamp (the event's OWN time), not
-- _ingested_at - a late-arriving event lands in the historical day it
-- actually happened on, not the day it was processed. No cutoff/window
-- filtering is applied: every event with a parseable timestamp is
-- counted on its own date, however late it arrived.
--
-- Rows with no parseable event_timestamp (is_event_timestamp_missing) have
-- no date to bucket by and are excluded from this model - in practice
-- there are none, since Phase 2 ingestion already rejects product events
-- with an unparseable/missing event_timestamp before they ever reach raw.

with events as (

    select
        event_id,
        user_id,
        organization_id,
        event_name,
        event_timestamp,
        session_id
    from {{ ref('stg_product__events') }}
    where not is_event_timestamp_missing

),

aggregated as (

    select
        event_timestamp::date as event_date,
        user_id,
        organization_id,
        event_name,
        count(*) as event_count,
        count(distinct session_id) as distinct_session_count,
        min(event_timestamp) as first_event_at,
        max(event_timestamp) as last_event_at
    from events
    group by 1, 2, 3, 4

)

select
    event_date,
    user_id,
    organization_id,
    event_name,
    event_count,
    distinct_session_count,
    first_event_at,
    last_event_at
from aggregated

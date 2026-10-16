-- Grain: one row per (date_day, user_id, organization_id, event_name) -
-- an AGGREGATED usage grain, inherited unchanged from int_daily_product_usage
-- (Phase 4). This fact layer only adds resolved dimension keys on top; it
-- does not re-aggregate or re-derive anything, so it cannot introduce
-- usage inflation beyond what Phase 4 already verified.

with daily_usage as (

    select
        event_date,
        user_id,
        organization_id,
        event_name,
        event_count,
        distinct_session_count,
        first_event_at,
        last_event_at
    from {{ ref('int_daily_product_usage') }}

),

users as (
    select user_id as _user_id, user_key from {{ ref('dim_users') }}
),

organizations as (
    select organization_id as _organization_id, organization_key from {{ ref('dim_organizations') }}
)

select
    daily_usage.event_date as date_day,
    users.user_key,
    (daily_usage.user_id is not null and users.user_key is null) as is_user_unmatched,
    organizations.organization_key,
    (daily_usage.organization_id is not null and organizations.organization_key is null)
        as is_organization_unmatched,
    daily_usage.event_name,
    daily_usage.event_count,
    daily_usage.distinct_session_count,
    daily_usage.first_event_at,
    daily_usage.last_event_at
from daily_usage
left join users on daily_usage.user_id = users._user_id
left join organizations on daily_usage.organization_id = organizations._organization_id

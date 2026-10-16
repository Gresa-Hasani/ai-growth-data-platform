-- Grain: one row per (date_day, organization_id) - inherited unchanged
-- from int_api_usage_by_customer (Phase 4). Adds a resolved organization_key
-- only; no re-aggregation, so it cannot introduce usage inflation.

with daily_usage as (

    select
        usage_date,
        organization_id,
        is_organization_unknown,
        request_count,
        successful_request_count,
        error_request_count,
        error_rate,
        total_input_units,
        total_output_units,
        total_estimated_cost,
        avg_latency_ms
    from {{ ref('int_api_usage_by_customer') }}

),

organizations as (
    select organization_id as _organization_id, organization_key from {{ ref('dim_organizations') }}
)

select
    daily_usage.usage_date as date_day,
    organizations.organization_key,
    daily_usage.is_organization_unknown,
    (daily_usage.organization_id is not null and organizations.organization_key is null)
        as is_organization_unmatched,
    daily_usage.request_count,
    daily_usage.successful_request_count,
    daily_usage.error_request_count,
    daily_usage.error_rate,
    daily_usage.total_input_units,
    daily_usage.total_output_units,
    daily_usage.total_estimated_cost,
    daily_usage.avg_latency_ms
from daily_usage
left join organizations on daily_usage.organization_id = organizations._organization_id

{{
    config(
        materialized='incremental',
        unique_key='usage_grain_key',
        incremental_strategy='delete+insert',
        tags=['facts', 'incremental']
    )
}}

-- Grain: one row per (date_day, organization_id) - unchanged from
-- Phase 4/5. Incremental as of Phase 6; see docs/incremental_processing.md
-- for the full strategy (identical pattern to fact_product_usage, applied
-- to raw.api_usage's request_timestamp/organization_id/_ingested_at).
--
-- usage_grain_key exists for the same reason as fact_product_usage's:
-- organization_id is legitimately nullable, and delete+insert's equality-
-- based row matching cannot replace a NULL-keyed row on rerun without a
-- COALESCE-guarded hashed key.

with usage as (

    select
        request_id,
        organization_id,
        request_timestamp,
        status_code,
        is_successful_request,
        input_units,
        output_units,
        estimated_cost,
        latency_ms,
        _ingested_at
    from {{ ref('stg_api__usage') }}
    where request_timestamp is not null

),

{% if is_incremental() %}

high_water_mark as (

    select coalesce(max(_last_ingested_at), '1970-01-01'::timestamptz) as watermark
    from {{ this }}

),

affected_dates as (

    select distinct usage.request_timestamp::date as usage_date
    from usage, high_water_mark
    where usage._ingested_at > high_water_mark.watermark - interval '{{ var("incremental_lookback_days", 3) }} days'

),

filtered_usage as (

    select usage.*
    from usage
    inner join affected_dates on usage.request_timestamp::date = affected_dates.usage_date

)

{% else %}

filtered_usage as (

    select * from usage

)

{% endif %}

,aggregated as (

    select
        filtered_usage.request_timestamp::date as usage_date,
        filtered_usage.organization_id,
        count(*) as request_count,
        count(*) filter (where filtered_usage.is_successful_request) as successful_request_count,
        count(*) filter (where not filtered_usage.is_successful_request) as error_request_count,
        sum(filtered_usage.input_units) as total_input_units,
        sum(filtered_usage.output_units) as total_output_units,
        sum(filtered_usage.estimated_cost) as total_estimated_cost,
        avg(filtered_usage.latency_ms) as avg_latency_ms,
        max(filtered_usage._ingested_at) as _last_ingested_at
    from filtered_usage
    group by 1, 2

),

organizations as (
    select organization_id as _organization_id, organization_key from {{ ref('dim_organizations') }}
)

select
    {{ dbt_utils.generate_surrogate_key([
        'aggregated.usage_date',
        "coalesce(aggregated.organization_id, '')"
    ]) }} as usage_grain_key,
    aggregated.usage_date as date_day,
    aggregated.organization_id,
    organizations.organization_key,
    (aggregated.organization_id is null) as is_organization_unknown,
    (aggregated.organization_id is not null and organizations.organization_key is null)
        as is_organization_unmatched,
    aggregated.request_count,
    aggregated.successful_request_count,
    aggregated.error_request_count,
    {{ safe_divide('aggregated.error_request_count', 'aggregated.request_count') }} as error_rate,
    aggregated.total_input_units,
    aggregated.total_output_units,
    aggregated.total_estimated_cost,
    aggregated.avg_latency_ms,
    aggregated._last_ingested_at
from aggregated
left join organizations on aggregated.organization_id = organizations._organization_id

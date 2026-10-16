-- Customer grain: organization_id (see docs/dbt_intermediate.md "Customer
-- identity" - API usage is billed/attributed at the organization level,
-- consistent with int_customer_revenue and int_customer_activity).
-- Time grain: daily, derived from request_timestamp.
--
-- Rows with organization_id is null are kept, not dropped - they are
-- grouped under organization_id = null for data-quality visibility
-- (distinct from a genuinely-zero-usage organization, which simply never
-- appears in this model for that date, since it has no activity to roll up).

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
        latency_ms
    from {{ ref('stg_api__usage') }}
    where request_timestamp is not null

),

aggregated as (

    select
        request_timestamp::date as usage_date,
        organization_id,
        count(*) as request_count,
        count(*) filter (where is_successful_request) as successful_request_count,
        count(*) filter (where not is_successful_request) as error_request_count,
        sum(input_units) as total_input_units,
        sum(output_units) as total_output_units,
        sum(estimated_cost) as total_estimated_cost,
        avg(latency_ms) as avg_latency_ms
    from usage
    group by 1, 2

)

select
    usage_date,
    organization_id,
    (organization_id is null) as is_organization_unknown,
    request_count,
    successful_request_count,
    error_request_count,
    {{ safe_divide('error_request_count', 'request_count') }} as error_rate,
    total_input_units,
    total_output_units,
    total_estimated_cost,
    avg_latency_ms
from aggregated

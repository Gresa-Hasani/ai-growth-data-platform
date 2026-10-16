with source as (

    select * from {{ source('api', 'api_usage') }}

),

renamed as (

    select
        request_id,
        user_id,
        organization_id,
        endpoint,
        model_family,
        request_timestamp,
        latency_ms,
        input_units,
        output_units,
        status_code,
        estimated_cost,
        _ingested_at,
        _source_file,
        _run_id
    from source

),

normalized as (

    select
        request_id,
        user_id,
        organization_id,
        {{ normalize_text('endpoint', lower=True) }} as endpoint,
        {{ normalize_text('model_family', lower=True) }} as model_family,
        {{ safe_cast_timestamp('request_timestamp') }} as request_timestamp,
        {{ safe_cast_integer('latency_ms') }} as latency_ms,
        {{ safe_cast_integer('input_units') }} as input_units,
        {{ safe_cast_integer('output_units') }} as output_units,
        {{ safe_cast_integer('status_code') }} as status_code,
        {{ safe_cast_numeric('estimated_cost') }} as estimated_cost,
        _ingested_at,
        _source_file,
        _run_id
    from renamed

),

flagged as (

    select
        *,
        -- Straightforward source-level derivation (HTTP 2xx), not a
        -- business KPI - reliability/error-rate metrics belong later.
        (status_code between 200 and 299) as is_successful_request
    from normalized

),

deduplicated as (

    {{ deduplicate(
        relation='flagged',
        partition_by='request_id',
        order_by='_ingested_at desc nulls last, request_id desc'
    ) }}

)

select
    request_id,
    user_id,
    organization_id,
    endpoint,
    model_family,
    request_timestamp,
    latency_ms,
    input_units,
    output_units,
    status_code,
    is_successful_request,
    estimated_cost,
    _ingested_at,
    _source_file,
    _run_id
from deduplicated

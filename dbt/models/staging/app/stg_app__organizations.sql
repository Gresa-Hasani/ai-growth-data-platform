with source as (

    select * from {{ source('app', 'organizations') }}

),

renamed as (

    select
        organization_id,
        organization_name,
        domain,
        country_code,
        organization_type,
        created_at,
        updated_at,
        _ingested_at,
        _source_file,
        _run_id
    from source

),

normalized as (

    select
        organization_id,
        {{ normalize_text('organization_name') }} as organization_name,
        {{ normalize_text('domain', lower=True) }} as domain,
        {{ normalize_country_code('country_code') }} as country_code,
        case
            when lower(trim(organization_type)) in ('individual', 'startup', 'business', 'enterprise')
                then lower(trim(organization_type))
            else 'unknown'
        end as organization_type,
        {{ safe_cast_timestamp('created_at') }} as created_at,
        {{ safe_cast_timestamp('updated_at') }} as updated_at,
        _ingested_at,
        _source_file,
        _run_id
    from renamed

),

flagged as (

    select
        *,
        {{ is_country_code_recognized('country_code') }} as is_country_code_recognized,
        (organization_type = 'unknown') as is_organization_type_unknown
    from normalized

),

deduplicated as (

    {{ deduplicate(
        relation='flagged',
        partition_by='organization_id',
        order_by='updated_at desc nulls last, _ingested_at desc nulls last, organization_id desc'
    ) }}

)

select
    organization_id,
    organization_name,
    domain,
    country_code,
    is_country_code_recognized,
    organization_type,
    is_organization_type_unknown,
    created_at,
    updated_at,
    _ingested_at,
    _source_file,
    _run_id
from deduplicated

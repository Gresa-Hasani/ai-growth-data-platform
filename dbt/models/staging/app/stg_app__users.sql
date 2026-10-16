with source as (

    select * from {{ source('app', 'users') }}

),

renamed as (

    select
        user_id,
        organization_id,
        email,
        country_code,
        signup_at,
        acquisition_channel,
        status,
        created_at,
        updated_at,
        _ingested_at,
        _source_file,
        _run_id
    from source

),

normalized as (

    select
        user_id,
        organization_id,
        {{ normalize_email('email') }} as email,
        {{ normalize_country_code('country_code') }} as country_code,
        {{ safe_cast_timestamp('signup_at') }} as signup_at,
        {{ normalize_text('acquisition_channel', lower=True) }} as acquisition_channel,
        case
            when lower(trim(status)) in ('active', 'churned', 'trial') then lower(trim(status))
            else 'unknown'
        end as status,
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
        {{ is_email_valid('email') }} as is_email_valid,
        {{ is_country_code_recognized('country_code') }} as is_country_code_recognized,
        (status = 'unknown') as is_status_unknown
    from normalized

),

deduplicated as (

    {{ deduplicate(
        relation='flagged',
        partition_by='user_id',
        order_by='updated_at desc nulls last, _ingested_at desc nulls last, user_id desc'
    ) }}

)

select
    user_id,
    organization_id,
    email,
    is_email_valid,
    country_code,
    is_country_code_recognized,
    signup_at,
    acquisition_channel,
    status,
    is_status_unknown,
    created_at,
    updated_at,
    _ingested_at,
    _source_file,
    _run_id
from deduplicated

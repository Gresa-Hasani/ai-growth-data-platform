with source as (

    select * from {{ source('crm', 'crm_accounts') }}

),

renamed as (

    select
        crm_account_id,
        organization_id,
        account_status,
        segment,
        owner_team,
        lead_source,
        created_at,
        updated_at,
        _ingested_at,
        _source_file,
        _run_id
    from source

),

normalized as (

    select
        crm_account_id,
        organization_id,
        case
            when lower(trim(account_status)) in ('prospect', 'active', 'at_risk', 'churned')
                then lower(trim(account_status))
            else 'unknown'
        end as account_status,
        case
            when lower(trim(segment)) in ('smb', 'mid_market', 'enterprise')
                then lower(trim(segment))
            else 'unknown'
        end as segment,
        {{ normalize_text('owner_team', lower=True) }} as owner_team,
        nullif({{ normalize_text('lead_source', lower=True) }}, '') as lead_source,
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
        (account_status = 'unknown') as is_account_status_unknown,
        (lead_source is null) as is_lead_source_missing
    from normalized

),

deduplicated as (

    {{ deduplicate(
        relation='flagged',
        partition_by='crm_account_id',
        order_by='updated_at desc nulls last, _ingested_at desc nulls last, crm_account_id desc'
    ) }}

)

select
    crm_account_id,
    organization_id,
    account_status,
    is_account_status_unknown,
    segment,
    owner_team,
    lead_source,
    is_lead_source_missing,
    created_at,
    updated_at,
    _ingested_at,
    _source_file,
    _run_id
from deduplicated

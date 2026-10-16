with source as (

    select * from {{ source('billing', 'subscriptions') }}

),

renamed as (

    select
        subscription_id,
        user_id,
        organization_id,
        plan_name,
        billing_interval,
        status,
        currency,
        amount,
        started_at,
        trial_started_at,
        trial_ended_at,
        cancelled_at,
        current_period_start,
        current_period_end,
        updated_at,
        _ingested_at,
        _source_file,
        _run_id
    from source

),

normalized as (

    select
        subscription_id,
        user_id,
        organization_id,
        -- A missing plan is preserved as NULL, never invented as a real
        -- plan - see is_plan_missing below and docs/dbt_staging.md.
        nullif({{ normalize_text('plan_name', lower=True) }}, '') as plan_name,
        {{ normalize_text('billing_interval', lower=True) }} as billing_interval,
        case
            when lower(trim(status)) in ('trialing', 'active', 'canceled', 'past_due')
                then lower(trim(status))
            else 'unknown'
        end as status,
        {{ normalize_currency('currency') }} as currency,
        {{ safe_cast_numeric('amount') }} as subscription_amount,
        {{ safe_cast_timestamp('started_at') }} as started_at,
        {{ safe_cast_timestamp('trial_started_at') }} as trial_started_at,
        {{ safe_cast_timestamp('trial_ended_at') }} as trial_ended_at,
        {{ safe_cast_timestamp('cancelled_at') }} as cancelled_at,
        {{ safe_cast_timestamp('current_period_start') }} as current_period_start,
        {{ safe_cast_timestamp('current_period_end') }} as current_period_end,
        {{ safe_cast_timestamp('updated_at') }} as updated_at,
        _ingested_at,
        _source_file,
        _run_id
    from renamed

),

flagged as (

    select
        *,
        (plan_name is null) as is_plan_missing,
        {{ is_currency_code_recognized('currency') }} as is_currency_recognized,
        (status = 'unknown') as is_status_unknown
    from normalized

),

deduplicated as (

    {{ deduplicate(
        relation='flagged',
        partition_by='subscription_id',
        order_by='updated_at desc nulls last, _ingested_at desc nulls last, subscription_id desc'
    ) }}

)

select
    subscription_id,
    user_id,
    organization_id,
    plan_name,
    is_plan_missing,
    billing_interval,
    status,
    is_status_unknown,
    currency,
    is_currency_recognized,
    subscription_amount,
    started_at,
    trial_started_at,
    trial_ended_at,
    cancelled_at,
    current_period_start,
    current_period_end,
    updated_at,
    _ingested_at,
    _source_file,
    _run_id
from deduplicated

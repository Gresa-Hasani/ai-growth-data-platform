with source as (

    select * from {{ source('billing', 'invoices') }}

),

renamed as (

    select
        invoice_id,
        subscription_id,
        customer_id,
        currency,
        subtotal,
        tax,
        total,
        status,
        issued_at,
        paid_at,
        _ingested_at,
        _source_file,
        _run_id
    from source

),

normalized as (

    select
        invoice_id,
        subscription_id,
        customer_id,
        {{ normalize_currency('currency') }} as currency,
        {{ safe_cast_numeric('subtotal') }} as subtotal_amount,
        {{ safe_cast_numeric('tax') }} as tax_amount,
        {{ safe_cast_numeric('total') }} as total_amount,
        case
            when lower(trim(status)) in ('paid', 'open', 'failed', 'void') then lower(trim(status))
            else 'unknown'
        end as status,
        {{ safe_cast_timestamp('issued_at') }} as issued_at,
        {{ safe_cast_timestamp('paid_at') }} as paid_at,
        _ingested_at,
        _source_file,
        _run_id
    from renamed

),

flagged as (

    select
        *,
        {{ is_currency_code_recognized('currency') }} as is_currency_recognized,
        (status = 'unknown') as is_status_unknown,
        -- A paid invoice with no paid_at, or vice versa, is a source-quality
        -- observation surfaced here rather than silently corrected.
        (status = 'paid' and paid_at is null) as is_paid_without_paid_at
    from normalized

),

deduplicated as (

    {{ deduplicate(
        relation='flagged',
        partition_by='invoice_id',
        order_by='_ingested_at desc nulls last, invoice_id desc'
    ) }}

)

select
    invoice_id,
    subscription_id,
    customer_id,
    currency,
    is_currency_recognized,
    subtotal_amount,
    tax_amount,
    total_amount,
    status,
    is_status_unknown,
    is_paid_without_paid_at,
    issued_at,
    paid_at,
    _ingested_at,
    _source_file,
    _run_id
from deduplicated

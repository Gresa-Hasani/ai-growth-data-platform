{{
    config(
        materialized='incremental',
        unique_key='invoice_id',
        incremental_strategy='delete+insert',
        tags=['facts', 'incremental']
    )
}}

-- Grain: one row per source invoice_id - unchanged from Phase 4/5.
-- Incremental as of Phase 6; see docs/incremental_processing.md.
--
-- int_customer_revenue (Phase 4, untouched) doesn't carry _ingested_at, so
-- the incremental filter joins back to stg_billing__invoices for it
-- specifically - this avoids modifying a completed Phase 4 model just to
-- add a Phase 6 concern. invoice_id is never null (tested unique/not_null
-- since Phase 3), so unique_key is the plain business key - no COALESCE-
-- guarded surrogate needed here, unlike the two aggregate usage facts.
--
-- raw.invoices has no updated_at column at all (confirmed in Phase 3/4) -
-- _ingested_at is the ONLY available change-detection signal, and it only
-- tells us a row was (re-)touched by ingestion, not what specifically
-- changed about it. The Phase 2 ingestion pipeline upserts on invoice_id
-- and refreshes _ingested_at on every touch (new OR re-ingested existing
-- row), so "invoice_id ingested within the lookback window" correctly
-- covers both new invoices and updates to existing ones - there is no
-- separate "status changed" signal to filter on more precisely than that.

with revenue as (

    select
        invoice_id,
        subscription_id,
        is_subscription_orphaned,
        customer_id,
        user_id,
        plan_name,
        billing_interval,
        currency,
        is_currency_recognized,
        subtotal_amount,
        tax_amount,
        total_amount,
        status,
        is_status_unknown,
        is_paid_without_paid_at,
        billed_amount,
        paid_amount,
        outstanding_amount,
        issued_at,
        paid_at
    from {{ ref('int_customer_revenue') }}

),

invoice_ingestion as (
    select invoice_id, _ingested_at from {{ ref('stg_billing__invoices') }}
)

{% if is_incremental() %}

,high_water_mark as (

    select coalesce(max(_last_ingested_at), '1970-01-01'::timestamptz) as watermark
    from {{ this }}

),

recently_touched_invoices as (

    select invoice_ingestion.invoice_id
    from invoice_ingestion, high_water_mark
    where invoice_ingestion._ingested_at > high_water_mark.watermark - interval '{{ var("incremental_lookback_days", 3) }} days'

),

filtered_revenue as (

    select revenue.*
    from revenue
    inner join recently_touched_invoices on revenue.invoice_id = recently_touched_invoices.invoice_id

)

{% else %}

,filtered_revenue as (

    select revenue.* from revenue

)

{% endif %}

,organizations as (
    select organization_id as _organization_id, organization_key from {{ ref('dim_organizations') }}
),

plans as (
    select
        coalesce(plan_name, 'unknown') as _plan_name,
        billing_interval as _billing_interval,
        plan_key
    from {{ ref('dim_plans') }}
)

select
    filtered_revenue.invoice_id,
    filtered_revenue.subscription_id,
    filtered_revenue.is_subscription_orphaned,
    organizations.organization_key,
    (filtered_revenue.customer_id is not null and organizations.organization_key is null)
        as is_organization_unmatched,
    plans.plan_key,
    filtered_revenue.user_id,
    filtered_revenue.currency,
    filtered_revenue.is_currency_recognized,
    filtered_revenue.subtotal_amount,
    filtered_revenue.tax_amount,
    filtered_revenue.total_amount,
    filtered_revenue.status,
    filtered_revenue.is_status_unknown,
    filtered_revenue.is_paid_without_paid_at,
    filtered_revenue.billed_amount,
    filtered_revenue.paid_amount,
    filtered_revenue.outstanding_amount,
    filtered_revenue.issued_at,
    filtered_revenue.paid_at,
    invoice_ingestion._ingested_at as _last_ingested_at
from filtered_revenue
inner join invoice_ingestion on filtered_revenue.invoice_id = invoice_ingestion.invoice_id
left join organizations on filtered_revenue.customer_id = organizations._organization_id
left join plans
    on coalesce(filtered_revenue.plan_name, 'unknown') = plans._plan_name
    and filtered_revenue.billing_interval = plans._billing_interval

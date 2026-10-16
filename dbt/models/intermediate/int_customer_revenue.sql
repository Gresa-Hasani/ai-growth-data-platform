-- Customer grain: an organization (see docs/dbt_intermediate.md "Customer
-- identity"). raw.invoices.customer_id is generated as the subscription's
-- organization_id in every case this synthetic environment produces (every
-- user belongs to an organization), so "customer" = organization here, not
-- the individual user.
--
-- Grain: one row per invoice_id - this is a reusable intermediate FACT, not
-- a customer-period aggregate. Aggregating into MRR/ARR-style metrics
-- belongs to Phase 5; doing it here would require deciding how to treat
-- different billing intervals and currencies, which this model explicitly
-- does not attempt (see "currency" below).
--
-- This source has no refund/credit/outstanding-balance fields - only
-- subtotal/tax/total and a status. billed/paid/outstanding amounts below
-- are derived from status alone, not from separate ledger entries that
-- don't exist in this source.

with invoices as (

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
        paid_at
    from {{ ref('stg_billing__invoices') }}

),

-- stg_billing__subscriptions.subscription_id is unique (tested), so this
-- join cannot fan out invoice rows.
subscriptions as (

    select
        subscription_id as _subscription_id,
        user_id,
        organization_id,
        plan_name,
        billing_interval
    from {{ ref('stg_billing__subscriptions') }}

),

joined as (

    select
        invoices.invoice_id,
        invoices.subscription_id,
        (invoices.subscription_id is not null and subscriptions._subscription_id is null)
            as is_subscription_orphaned,
        -- Falls back to the invoice's own customer_id when the subscription
        -- can't be resolved, rather than losing the row's customer entirely.
        coalesce(subscriptions.organization_id, invoices.customer_id) as customer_id,
        subscriptions.user_id,
        subscriptions.plan_name,
        subscriptions.billing_interval,
        invoices.currency,
        invoices.is_currency_recognized,
        invoices.subtotal_amount,
        invoices.tax_amount,
        invoices.total_amount,
        invoices.status,
        invoices.is_status_unknown,
        invoices.is_paid_without_paid_at,
        -- Billed = what was invoiced, regardless of payment outcome.
        invoices.total_amount as billed_amount,
        -- Paid = cash actually collected, per the invoice's own status.
        case when invoices.status = 'paid' then invoices.total_amount else 0 end as paid_amount,
        -- Outstanding = billed but not (yet) resolved either way.
        case
            when invoices.status in ('open', 'failed') then invoices.total_amount
            else 0
        end as outstanding_amount,
        invoices.issued_at,
        invoices.paid_at
    from invoices
    left join subscriptions on invoices.subscription_id = subscriptions._subscription_id

)

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
from joined

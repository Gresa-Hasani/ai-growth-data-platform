-- Grain: one row per source invoice_id (matches stg_billing__invoices
-- exactly). Built on int_customer_revenue, which already resolved the
-- subscription join 1:1 (subscription_id is unique) - adding dimension
-- keys here cannot introduce fan-out. See fact_subscriptions for the
-- project's unmatched-key policy.

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

organizations as (
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
    revenue.invoice_id,
    revenue.subscription_id,
    revenue.is_subscription_orphaned,
    organizations.organization_key,
    (revenue.customer_id is not null and organizations.organization_key is null) as is_organization_unmatched,
    plans.plan_key,
    revenue.user_id,
    revenue.currency,
    revenue.is_currency_recognized,
    revenue.subtotal_amount,
    revenue.tax_amount,
    revenue.total_amount,
    revenue.status,
    revenue.is_status_unknown,
    revenue.is_paid_without_paid_at,
    revenue.billed_amount,
    revenue.paid_amount,
    revenue.outstanding_amount,
    revenue.issued_at,
    revenue.paid_at
from revenue
left join organizations on revenue.customer_id = organizations._organization_id
left join plans
    on coalesce(revenue.plan_name, 'unknown') = plans._plan_name
    and revenue.billing_interval = plans._billing_interval

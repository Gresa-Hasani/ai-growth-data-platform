-- Grain: one row per currency, as of the same point-in-time snapshot as
-- mart_mrr (see its header comment). ARPU is defined PER PAYING
-- ORGANIZATION (not per user), consistent with "customer" = organization
-- throughout the revenue marts. Numerator = current monthly recurring
-- revenue (the same monthly_equivalent_amount sum as mart_mrr, not invoice
-- totals) - so ARPU is never confused with average invoice value, which
-- would include one-time/non-recurring timing noise from the billing
-- cycle. Denominator = count of DISTINCT paying organizations (an
-- organization with multiple active subscriptions counts once). Division
-- is NULL-safe via safe_divide - a currency with zero paying organizations
-- (should not occur in this dataset, but is not assumed impossible)
-- produces NULL, not a divide-by-zero error.
--
-- NOTE: this model's `mrr` total will be slightly LOWER than mart_mrr's for
-- the same currency - verified in the dev dataset as a $382.46 USD gap,
-- exactly the monthly_equivalent_amount of 6 active subscriptions whose
-- organization_key didn't resolve (orphaned organization_id). mart_mrr
-- counts all active recurring revenue regardless of whether the owning
-- organization is known; this model, being PER-organization by
-- definition, can only ever attribute revenue to organizations it can
-- actually identify. This is intentional, not a reconciliation bug.

with active_subscriptions as (

    select
        organization_key,
        currency,
        monthly_equivalent_amount
    from {{ ref('fact_subscriptions') }}
    where is_recurring_active_now
      and organization_key is not null
      and currency is not null

),

by_currency as (

    select
        currency,
        sum(monthly_equivalent_amount) as mrr,
        count(distinct organization_key) as paying_organization_count
    from active_subscriptions
    group by currency

)

select
    current_date as as_of_date,
    currency,
    mrr,
    paying_organization_count,
    {{ safe_divide('mrr', 'paying_organization_count') }} as arpu
from by_currency

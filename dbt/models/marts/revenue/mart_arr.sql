-- Grain: one row per currency, same single-point-in-time snapshot
-- semantics as mart_mrr (see its header comment) - ARR here is defined as
-- annualized RECURRING SUBSCRIPTION revenue (mrr * 12 for the exact same
-- eligible-subscription set as mart_mrr), never as a sum of annual invoice
-- totals, which would conflate one-time billing timing with recurring
-- revenue. Built directly from fact_subscriptions (not from mart_mrr) so
-- the two models share the identical filter and currency grouping, with
-- the *12 annualization as the only difference - nothing drifts between
-- them.

select
    current_date as as_of_date,
    currency,
    count(*) as active_subscription_count,
    sum(monthly_equivalent_amount) as mrr,
    sum(monthly_equivalent_amount) * 12 as arr
from {{ ref('fact_subscriptions') }}
where is_recurring_active_now
  and currency is not null
group by currency

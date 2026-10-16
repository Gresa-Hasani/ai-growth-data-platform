-- Grain: one row per currency, as of a SINGLE point in time (as_of_date =
-- the date this model was last built). This is a CURRENT-STATE SNAPSHOT,
-- NOT a historical monthly time series - raw.subscriptions has exactly one
-- row per subscription with its latest status, so there is no reliable way
-- to reconstruct what MRR was in any past month from this source (doing so
-- would require assuming every subscription's current status held
-- continuously since its started_at, which Phase 4's int_subscription_transitions
-- already established cannot be verified). Re-running this model on a
-- later date produces a new as_of_date with that date's own MRR - a true
-- historical series would require periodic snapshots (dbt snapshots,
-- Phase 6), not something this phase fabricates from one row per subscription.
--
-- Active-recurring-subscription definition: is_subscription_recurring_active
-- (status = 'active', or 'canceled' with a still-future cancelled_at -
-- see that macro's comment). Trialing subscriptions are excluded (no
-- revenue yet); past_due is excluded (conservative - payment has already
-- failed). One-time charges are out of scope entirely: this source only
-- has recurring subscriptions, no separate one-time-charge records.
-- monthly_equivalent_amount (not raw subscription_amount) is summed, so
-- annual subscriptions never overstate MRR by their full annual amount.
-- Currency is never mixed: one row per currency, never summed together.

select
    current_date as as_of_date,
    currency,
    count(*) as active_subscription_count,
    sum(monthly_equivalent_amount) as mrr
from {{ ref('fact_subscriptions') }}
where is_recurring_active_now
  and currency is not null
group by currency

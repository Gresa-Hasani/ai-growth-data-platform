{{ config(severity='error', tags=['facts']) }}

-- The monthly_equivalent_amount macro's documented policy: annual amounts
-- are divided by 12. This test re-derives that formula independently and
-- checks it against what actually landed in fact_subscriptions, so a
-- future edit to the macro or the model can't silently drift from the
-- documented ARR/MRR normalization policy.

select
    subscription_id,
    billing_interval,
    subscription_amount,
    monthly_equivalent_amount
from {{ ref('fact_subscriptions') }}
where subscription_amount is not null
  and (
      (billing_interval = 'monthly' and round(monthly_equivalent_amount, 2) != round(subscription_amount, 2))
      or (billing_interval = 'annual' and round(monthly_equivalent_amount, 2) != round(subscription_amount / 12, 2))
  )

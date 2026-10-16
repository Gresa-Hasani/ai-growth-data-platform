{{ config(severity='error', tags=['facts']) }}

-- fact_subscriptions left-joins dim_users/dim_organizations/dim_plans onto
-- int_user_subscription_history. All three dimension keys are unique by
-- construction, so the join must preserve the exact source row count.

with fact_count as (
    select count(*) as n from {{ ref('fact_subscriptions') }}
),
source_count as (
    select count(*) as n from {{ ref('int_user_subscription_history') }}
)

select fact_count.n as fact_rows, source_count.n as source_rows
from fact_count, source_count
where fact_count.n != source_count.n

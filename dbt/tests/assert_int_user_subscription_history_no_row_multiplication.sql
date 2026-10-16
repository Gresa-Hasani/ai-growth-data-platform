{{ config(severity='error', tags=['intermediate']) }}

-- int_user_subscription_history left-joins stg_app__users onto
-- stg_billing__subscriptions. stg_app__users.user_id is unique (tested),
-- so the join must preserve the subscription row count exactly - any
-- difference means the join unexpectedly fanned out.

with intermediate_count as (
    select count(*) as n from {{ ref('int_user_subscription_history') }}
),
staging_count as (
    select count(*) as n from {{ ref('stg_billing__subscriptions') }}
)

select intermediate_count.n as intermediate_rows, staging_count.n as staging_rows
from intermediate_count, staging_count
where intermediate_count.n != staging_count.n

{{ config(severity='error', tags=['intermediate']) }}

-- int_customer_revenue left-joins stg_billing__subscriptions onto
-- stg_billing__invoices. subscription_id is unique in that model (tested),
-- so the join must preserve the invoice row count exactly.

with intermediate_count as (
    select count(*) as n from {{ ref('int_customer_revenue') }}
),
staging_count as (
    select count(*) as n from {{ ref('stg_billing__invoices') }}
)

select intermediate_count.n as intermediate_rows, staging_count.n as staging_rows
from intermediate_count, staging_count
where intermediate_count.n != staging_count.n

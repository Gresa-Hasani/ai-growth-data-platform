{{ config(severity='error', tags=['intermediate']) }}

-- billed_amount is a direct passthrough of stg_billing__invoices.total_amount
-- - no aggregation or join should ever change this sum. A difference here
-- means the join introduced inflation (or, less likely, lost rows).

with intermediate_total as (
    select coalesce(sum(billed_amount), 0) as total from {{ ref('int_customer_revenue') }}
),
staging_total as (
    select coalesce(sum(total_amount), 0) as total from {{ ref('stg_billing__invoices') }}
)

select intermediate_total.total as intermediate_billed_total, staging_total.total as staging_total_amount
from intermediate_total, staging_total
where intermediate_total.total != staging_total.total

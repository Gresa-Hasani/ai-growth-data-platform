{{ config(severity='error', tags=['facts']) }}

-- fact_invoices left-joins dim_organizations/dim_plans onto
-- int_customer_revenue. Row count must be preserved exactly - no invoice
-- duplication through the dimensional join.

with fact_count as (
    select count(*) as n from {{ ref('fact_invoices') }}
),
source_count as (
    select count(*) as n from {{ ref('int_customer_revenue') }}
)

select fact_count.n as fact_rows, source_count.n as source_rows
from fact_count, source_count
where fact_count.n != source_count.n

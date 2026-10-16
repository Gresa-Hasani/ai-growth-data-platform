{{ config(severity='error', tags=['facts']) }}

-- fact_product_usage only adds resolved dimension keys on top of
-- int_daily_product_usage - the event_count total must be identical.

with fact_total as (
    select coalesce(sum(event_count), 0) as total from {{ ref('fact_product_usage') }}
),
source_total as (
    select coalesce(sum(event_count), 0) as total from {{ ref('int_daily_product_usage') }}
)

select fact_total.total as fact_event_count, source_total.total as source_event_count
from fact_total, source_total
where fact_total.total != source_total.total

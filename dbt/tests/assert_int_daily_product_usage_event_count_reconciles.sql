{{ config(severity='error', tags=['intermediate']) }}

-- Every valid-timestamp event in staging must be counted exactly once
-- across the daily/user/org/event_name aggregation - no event duplicated
-- or dropped by the group-by.

with intermediate_total as (
    select coalesce(sum(event_count), 0) as total from {{ ref('int_daily_product_usage') }}
),
staging_total as (
    select count(*) as total
    from {{ ref('stg_product__events') }}
    where not is_event_timestamp_missing
)

select intermediate_total.total as intermediate_event_count, staging_total.total as staging_event_count
from intermediate_total, staging_total
where intermediate_total.total != staging_total.total

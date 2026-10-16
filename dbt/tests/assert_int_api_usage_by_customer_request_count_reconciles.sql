{{ config(severity='error', tags=['intermediate']) }}

-- Every valid-timestamp request in staging must be counted exactly once
-- across the daily/organization aggregation.

with intermediate_total as (
    select coalesce(sum(request_count), 0) as total from {{ ref('int_api_usage_by_customer') }}
),
staging_total as (
    select count(*) as total
    from {{ ref('stg_api__usage') }}
    where request_timestamp is not null
)

select intermediate_total.total as intermediate_request_count, staging_total.total as staging_request_count
from intermediate_total, staging_total
where intermediate_total.total != staging_total.total

{{ config(severity='error', tags=['facts']) }}

-- fact_api_usage only adds a resolved organization_key on top of
-- int_api_usage_by_customer - the request_count total must be identical.

with fact_total as (
    select coalesce(sum(request_count), 0) as total from {{ ref('fact_api_usage') }}
),
source_total as (
    select coalesce(sum(request_count), 0) as total from {{ ref('int_api_usage_by_customer') }}
)

select fact_total.total as fact_request_count, source_total.total as source_request_count
from fact_total, source_total
where fact_total.total != source_total.total

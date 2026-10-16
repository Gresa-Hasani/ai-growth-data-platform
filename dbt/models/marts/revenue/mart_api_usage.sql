-- Grain: one row per (month_start_date, organization_key) - a business-
-- facing monthly rollup of fact_api_usage's daily grain. Pure
-- re-aggregation of an already-reconciled daily fact (Phase 4 verified
-- fact_api_usage's request_count sums reconcile to staging with zero
-- inflation), so summing full calendar months here cannot double-count:
-- each day belongs to exactly one month.

with daily as (

    select
        date_day,
        organization_key,
        request_count,
        error_request_count,
        total_estimated_cost
    from {{ ref('fact_api_usage') }}
    where organization_key is not null

),

monthly as (

    select
        dim_date.month_start_date,
        daily.organization_key,
        sum(daily.request_count) as request_count,
        sum(daily.error_request_count) as error_request_count,
        sum(daily.total_estimated_cost) as total_estimated_cost
    from daily
    inner join {{ ref('dim_date') }} on daily.date_day = dim_date.date_day
    group by dim_date.month_start_date, daily.organization_key

)

select
    month_start_date,
    organization_key,
    request_count,
    error_request_count,
    {{ safe_divide('error_request_count', 'request_count') }} as error_rate,
    total_estimated_cost
from monthly

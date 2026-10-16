-- Customer grain: organization_id, daily. Combines product usage and API
-- usage WITHOUT row multiplication: both sources are pre-aggregated to a
-- unique (activity_date, organization_id) grain in their own CTE below
-- *before* being combined with a full outer join, so the join itself can
-- only ever produce one output row per (date, organization_id) pair - it
-- cannot fan out either side's counts.
--
-- Rows with organization_id = null are excluded here (unlike
-- int_api_usage_by_customer, which keeps them for visibility) because this
-- model's entire grain IS "per organization" - an unattributed row cannot
-- be an organization's activity. Unknown-organization activity remains
-- visible upstream in int_daily_product_usage / int_api_usage_by_customer.
--
-- This model only contains dates where SOME activity was observed - it is
-- NOT a dense calendar spine. A date/organization combination with no row
-- here means "no activity observed," which is distinct from (and should
-- not be confused with) "this organization doesn't exist" - that
-- distinction belongs to whoever joins this against a full organization
-- dimension (dim_organizations, Phase 5), which can decide how to fill gaps.

with product_usage_by_org_day as (

    select
        event_date as activity_date,
        organization_id,
        sum(event_count) as product_event_count
    from {{ ref('int_daily_product_usage') }}
    where organization_id is not null
    group by 1, 2

),

api_usage_by_org_day as (

    select
        usage_date as activity_date,
        organization_id,
        request_count as api_request_count
    from {{ ref('int_api_usage_by_customer') }}
    where organization_id is not null

),

combined as (

    select
        coalesce(product_usage_by_org_day.activity_date, api_usage_by_org_day.activity_date) as activity_date,
        coalesce(product_usage_by_org_day.organization_id, api_usage_by_org_day.organization_id) as organization_id,
        (product_usage_by_org_day.organization_id is not null) as has_product_activity,
        (api_usage_by_org_day.organization_id is not null) as has_api_activity,
        coalesce(product_usage_by_org_day.product_event_count, 0) as product_event_count,
        coalesce(api_usage_by_org_day.api_request_count, 0) as api_request_count
    from product_usage_by_org_day
    full outer join api_usage_by_org_day
        on product_usage_by_org_day.activity_date = api_usage_by_org_day.activity_date
        and product_usage_by_org_day.organization_id = api_usage_by_org_day.organization_id

)

select
    activity_date,
    organization_id,
    has_product_activity,
    has_api_activity,
    product_event_count,
    api_request_count,
    product_event_count + api_request_count as total_activity_count,
    min(activity_date) over (partition by organization_id) as first_observed_activity_date,
    max(activity_date) over (partition by organization_id) as last_observed_activity_date
from combined

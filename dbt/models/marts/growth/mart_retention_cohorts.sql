-- Grain: one row per (cohort_month, period_index). Cohort entry event:
-- user signup (dim_users.signup_date). Cohort period: calendar month of
-- signup. Retention event: at least one product event
-- (int_daily_product_usage) in a later calendar month. Period index:
-- months elapsed since the cohort's signup month (0 = signup month itself).
--
-- Unlike int_customer_activity's sparse date grain (where a missing row
-- could mean several things - see docs/dbt_intermediate.md), the
-- population here is fully enumerated from dim_users and checked against
-- a complete event log for every (user, month) combination - so "no event
-- that month" reliably means zero observed activity for that user, not
-- "unknown." This model therefore safely reports explicit retained_users
-- = 0 rows rather than omitting them.
--
-- Only OBSERVABLE periods are included (cohort_month + period_index <=
-- the current calendar month) - a period that hasn't happened yet is
-- never fabricated as a 0%-retention row, since "not yet observed" and
-- "observed with zero activity" are different things.

with cohorts as (

    select
        user_id,
        date_trunc('month', signup_date)::date as cohort_month
    from {{ ref('dim_users') }}
    where signup_date is not null

),

cohort_sizes as (

    select
        cohort_month,
        count(*) as cohort_size
    from cohorts
    group by cohort_month

),

activity_months as (

    select distinct
        user_id,
        date_trunc('month', event_date)::date as activity_month
    from {{ ref('int_daily_product_usage') }}
    where user_id is not null

),

periods_grid as (

    select
        cohort_sizes.cohort_month,
        period_index.period_index
    from cohort_sizes
    cross join generate_series(0, 24) as period_index(period_index)
    where (cohort_sizes.cohort_month + (period_index.period_index || ' months')::interval)
        <= date_trunc('month', current_date)

),

retained as (

    select
        cohorts.cohort_month,
        (
            (date_part('year', activity_months.activity_month) - date_part('year', cohorts.cohort_month)) * 12
            + (date_part('month', activity_months.activity_month) - date_part('month', cohorts.cohort_month))
        )::int as period_index,
        count(distinct cohorts.user_id) as retained_users
    from cohorts
    inner join activity_months on cohorts.user_id = activity_months.user_id
    where activity_months.activity_month >= cohorts.cohort_month
    group by cohorts.cohort_month, 2

)

select
    periods_grid.cohort_month,
    cohort_sizes.cohort_size,
    periods_grid.period_index,
    coalesce(retained.retained_users, 0) as retained_users,
    {{ safe_divide('coalesce(retained.retained_users, 0)', 'cohort_sizes.cohort_size') }} as retention_rate
from periods_grid
inner join cohort_sizes on periods_grid.cohort_month = cohort_sizes.cohort_month
left join retained
    on periods_grid.cohort_month = retained.cohort_month
    and periods_grid.period_index = retained.period_index

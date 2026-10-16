{{ config(severity='error', tags=['growth']) }}

-- mart_retention_cohorts.cohort_size must match the actual count of
-- dim_users whose signup month equals that cohort_month - the documented
-- cohort denominator must never silently diverge from the real population.

with actual_cohorts as (
    select
        date_trunc('month', signup_date)::date as cohort_month,
        count(*) as actual_cohort_size
    from {{ ref('dim_users') }}
    where signup_date is not null
    group by 1
),
mart_cohorts as (
    select distinct cohort_month, cohort_size
    from {{ ref('mart_retention_cohorts') }}
)

select
    actual_cohorts.cohort_month,
    actual_cohorts.actual_cohort_size,
    mart_cohorts.cohort_size as mart_cohort_size
from actual_cohorts
inner join mart_cohorts on actual_cohorts.cohort_month = mart_cohorts.cohort_month
where actual_cohorts.actual_cohort_size != mart_cohorts.cohort_size

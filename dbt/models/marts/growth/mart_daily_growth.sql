-- Grain: one row per calendar date_day (dense - every date in dim_date,
-- not just dates with signups). Distinguishes user signups from new
-- organizations, since they are different entities with different growth
-- dynamics (one organization can later gain many users without itself
-- being "new" again).

with user_signups as (

    select
        signup_date as date_day,
        count(*) as new_user_signups
    from {{ ref('dim_users') }}
    where signup_date is not null
    group by 1

),

new_organizations as (

    select
        created_date as date_day,
        count(*) as new_organizations
    from {{ ref('dim_organizations') }}
    where created_date is not null
    group by 1

),

daily as (

    select
        dim_date.date_day,
        coalesce(user_signups.new_user_signups, 0) as new_user_signups,
        coalesce(new_organizations.new_organizations, 0) as new_organizations
    from {{ ref('dim_date') }}
    left join user_signups on dim_date.date_day = user_signups.date_day
    left join new_organizations on dim_date.date_day = new_organizations.date_day
    where dim_date.date_day <= current_date

)

select
    date_day,
    new_user_signups,
    new_organizations,
    sum(new_user_signups) over (order by date_day) as cumulative_user_signups,
    sum(new_organizations) over (order by date_day) as cumulative_organizations
from daily

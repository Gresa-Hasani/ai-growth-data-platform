-- Grain: one row per event_name. "Adoption" is deliberately defined as
-- adopting_user_count (distinct users with >=1 occurrence ever), kept
-- separate from total_event_count (raw volume) - a power user generating
-- hundreds of events must not inflate the adoption rate the way summing
-- event volume would. Eligible population = all users in dim_users
-- (every signed-up user is eligible to have adopted any feature).

with usage as (

    select
        event_name,
        user_id,
        sum(event_count) as event_count
    from {{ ref('int_daily_product_usage') }}
    where user_id is not null
    group by event_name, user_id

),

by_feature as (

    select
        event_name,
        count(distinct user_id) as adopting_user_count,
        sum(event_count) as total_event_count
    from usage
    group by event_name

),

eligible_population as (

    select count(*) as eligible_user_count
    from {{ ref('dim_users') }}

)

select
    by_feature.event_name,
    by_feature.adopting_user_count,
    by_feature.total_event_count,
    eligible_population.eligible_user_count,
    {{ safe_divide('by_feature.adopting_user_count', 'eligible_population.eligible_user_count') }} as adoption_rate
from by_feature
cross join eligible_population

-- Grain: one row per user. Deliberately excludes email and other
-- potentially sensitive fields - downstream marts that need to validate
-- email structure should reference stg_app__users directly, not this
-- business-facing dimension.

with users as (

    select
        user_id,
        organization_id,
        country_code,
        is_country_code_recognized,
        signup_at,
        acquisition_channel,
        status as current_status
    from {{ ref('stg_app__users') }}

),

lifecycle as (

    select
        user_id,
        lifecycle_stage
    from {{ ref('int_customer_lifecycle') }}

)

select
    {{ dbt_utils.generate_surrogate_key(['users.user_id']) }} as user_key,
    users.user_id,
    users.organization_id,
    users.country_code,
    users.is_country_code_recognized,
    users.signup_at,
    users.signup_at::date as signup_date,
    users.acquisition_channel,
    users.current_status,
    lifecycle.lifecycle_stage
from users
left join lifecycle on users.user_id = lifecycle.user_id

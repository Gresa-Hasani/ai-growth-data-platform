-- Grain: one row per organization. "individual" is itself one of the four
-- organization_type values this environment generates - a single-person
-- account is still represented as its own organization row here, not
-- merged into/confused with dim_users. See docs/dbt_marts.md "Customer
-- identity" for why organization is the billing/account entity throughout
-- the revenue marts, while dim_users is a separate, person-level dimension.

select
    {{ dbt_utils.generate_surrogate_key(['organization_id']) }} as organization_key,
    organization_id,
    organization_name,
    domain,
    organization_type,
    (organization_type = 'enterprise') as is_enterprise,
    is_organization_type_unknown,
    country_code,
    is_country_code_recognized,
    created_at,
    created_at::date as created_date
from {{ ref('stg_app__organizations') }}

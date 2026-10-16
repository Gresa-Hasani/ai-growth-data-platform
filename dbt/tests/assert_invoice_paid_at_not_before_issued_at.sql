{{ config(severity='warn', tags=['staging', 'billing']) }}

-- paid_at preceding issued_at would be unusual (invoices are generated
-- issued -> paid in that order) but not un-interpretable - it could reflect
-- a legitimate backdated correction from the billing system. WARN: worth
-- surfacing to whoever owns billing data, but not severe enough to block
-- `dbt build` the way a genuinely corrupted primary key would.

select
    invoice_id,
    issued_at,
    paid_at
from {{ ref('stg_billing__invoices') }}
where issued_at is not null
  and paid_at is not null
  and paid_at < issued_at

{{ config(severity='error', tags=['staging', 'billing']) }}

-- A cancelled subscription's cancelled_at can never logically precede its
-- own started_at. scripts/generate_data.py never constructs such a row, so
-- this should always return zero rows - it exists as a regression guard on
-- the staging transformation (safe_cast_timestamp / dedup), not as a known
-- source-corruption check. ERROR: a violation would mean staged data is
-- unsafe to use for subscription lifecycle analysis.

select
    subscription_id,
    started_at,
    cancelled_at
from {{ ref('stg_billing__subscriptions') }}
where started_at is not null
  and cancelled_at is not null
  and cancelled_at < started_at

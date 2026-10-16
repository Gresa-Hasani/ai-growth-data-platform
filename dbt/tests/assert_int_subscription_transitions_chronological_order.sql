{{ config(severity='error', tags=['intermediate']) }}

-- The window-function ordering in int_subscription_transitions must
-- produce a non-decreasing timeline per subscription - a transition can
-- never be recorded as happening before the one immediately preceding it.
-- This is a regression guard on the model's own LAG()/ROW_NUMBER() logic,
-- not a check on source data quality.

select
    subscription_id,
    transition_sequence,
    previous_transition_at,
    transition_at
from {{ ref('int_subscription_transitions') }}
where previous_transition_at is not null
  and transition_at < previous_transition_at

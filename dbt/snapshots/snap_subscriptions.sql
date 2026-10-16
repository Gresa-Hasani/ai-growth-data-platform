{% snapshot snap_subscriptions %}
{#-
    SCD Type 2 history for subscription status and plan changes.

    Strategy: timestamp, on stg_billing__subscriptions.updated_at - NOT
    _ingested_at. updated_at is a genuine SOURCE/business timestamp (set by
    scripts/generate_data.py to reflect when the subscription record itself
    last meaningfully changed, e.g. min(now, current_period_end)); it is
    passed through ingestion unmodified, never overwritten by the
    ingestion pipeline. _ingested_at would only tell us when WE last
    touched the row, which is not the same thing and would be an invented,
    untrustworthy "update" signal - so it is deliberately not used here.

    Snapshotting stg_billing__subscriptions (not raw.subscriptions)
    specifically so the history captured is already typed and normalized
    (safe-cast timestamps, normalized currency/status) - there is no
    reason to snapshot raw's unnormalized TEXT values when the same
    business event is available already cleaned.

    CRITICAL LIMITATION (see docs/incremental_processing.md "Snapshot
    limitations" for the full explanation): raw.subscriptions stores only
    the CURRENT state of each subscription - ingestion overwrites the
    previous row on every upsert. This snapshot can only capture a change
    if it executes BETWEEN two source updates. Any status/plan transition
    that happened before this snapshot's first run, or between two runs
    spaced further apart than the source changed, is permanently
    unobservable - it was never captured anywhere. This is why
    int_subscription_transitions' milestone timeline (Phase 4) is not
    rewritten to use this snapshot: that model's limitations are about
    reconstructing the PAST (before Phase 6 existed), which no snapshot
    taken from today forward can fix.

    Deletions: invalidate_hard_deletes=True closes (sets dbt_valid_to) any
    subscription_id that disappears from the source entirely. In this
    source, subscriptions are never hard-deleted by the generator/ingestion
    (they are upserted, not deleted) - this setting is a defensive
    guarantee for the future, not a response to an observed deletion today.
-#}

{{
    config(
        target_schema='snapshots',
        unique_key='subscription_id',
        strategy='timestamp',
        updated_at='updated_at',
        invalidate_hard_deletes=True,
    )
}}

select
    subscription_id,
    user_id,
    organization_id,
    plan_name,
    is_plan_missing,
    billing_interval,
    status,
    is_status_unknown,
    currency,
    is_currency_recognized,
    subscription_amount,
    started_at,
    trial_started_at,
    trial_ended_at,
    cancelled_at,
    current_period_start,
    current_period_end,
    -- Cast to timestamp (no tz) to match dbt's internal dbt_valid_from/
    -- dbt_valid_to column type exactly - silences a harmless but noisy
    -- dtype-mismatch warning. All timestamps in this project are UTC
    -- already (see safe_cast_timestamp), so no information is lost.
    updated_at::timestamp as updated_at
from {{ ref('stg_billing__subscriptions') }}

{% endsnapshot %}

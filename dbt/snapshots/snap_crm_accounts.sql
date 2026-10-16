{% snapshot snap_crm_accounts %}
{#-
    SCD Type 2 history for CRM account status, segment, and ownership.

    Strategy: timestamp, on stg_crm__accounts.updated_at - the same
    reasoning as snap_subscriptions: updated_at is a genuine source
    timestamp (scripts/generate_data.py sets it to created_at + a lifecycle
    offset), passed through ingestion unmodified; _ingested_at is
    deliberately not used as it only reflects our own processing time.

    organization_id (the relationship to the billing/account entity -
    see docs/dbt_marts.md "Customer identity") is preserved unchanged,
    including when it's an orphaned reference - this snapshot does not
    filter or repair Phase 3's documented orphan-FK handling.

    CRITICAL LIMITATION: identical to snap_subscriptions - raw.crm_accounts
    stores only each CRM touch's current state; a status change is only
    captured if this snapshot executes between two source updates. Do not
    claim CRM status history is complete before this snapshot's first run,
    and do not claim any history predating it.

    Deletions: invalidate_hard_deletes=True closes any crm_account_id that
    disappears from the source. CRM touches are upserted, never hard-
    deleted, in this source today - again, a defensive guarantee, not a
    response to an observed case.
-#}

{{
    config(
        target_schema='snapshots',
        unique_key='crm_account_id',
        strategy='timestamp',
        updated_at='updated_at',
        invalidate_hard_deletes=True,
    )
}}

select
    crm_account_id,
    organization_id,
    account_status,
    is_account_status_unknown,
    segment,
    owner_team,
    lead_source,
    is_lead_source_missing,
    created_at,
    -- See snap_subscriptions.sql for why this is cast to timestamp (no tz).
    updated_at::timestamp as updated_at
from {{ ref('stg_crm__accounts') }}

{% endsnapshot %}

# Data Dictionary

Staging (Phase 3) and intermediate (Phase 4) models. See
[dbt_staging.md](dbt_staging.md) for normalization/deduplication/testing
rules and [dbt_intermediate.md](dbt_intermediate.md) for join
cardinality/attribution/revenue-modeling decisions, and
[ingestion.md](ingestion.md) for the raw sources everything is built from.
Full column-level descriptions live in each domain's/layer's
`_*__models.yml` and are browsable via `dbt docs serve`; this file is the
narrative summary.

## Staging models

---

### stg_app__users

**Grain:** one row per `user_id`.
**Source:** `raw.users` (`app.users`).
**Purpose:** clean, deduplicated representation of application-database user accounts.
**Key transformations:** email trimmed+lowercased (`is_email_valid` flags structural validity); country code normalized to ISO2 where recognized; status constrained to `{active, trial, churned, unknown}`.
**Quality behavior:** invalid/orphaned data is preserved and flagged, never dropped.

### stg_app__organizations

**Grain:** one row per `organization_id`.
**Source:** `raw.organizations` (`app.organizations`).
**Purpose:** clean, deduplicated representation of customer organizations/accounts.
**Key transformations:** name/domain trimmed (casing preserved on name); country code normalized; `organization_type` constrained to `{individual, startup, business, enterprise, unknown}`.

### stg_billing__subscriptions

**Grain:** one row per `subscription_id`.
**Source:** `raw.subscriptions` (`billing.subscriptions`).
**Purpose:** clean, deduplicated representation of billing subscriptions.
**Key transformations:** `plan_name` preserved as NULL when missing (never invented - see `is_plan_missing`); currency normalized (no conversion); amount safely cast to `subscription_amount`; status constrained to `{trialing, active, canceled, past_due, unknown}`.
**Quality behavior:** `valid_timestamp_order` tests guarantee `trial_started_at <= trial_ended_at` and `started_at <= cancelled_at` when both sides are present.

### stg_billing__invoices

**Grain:** one row per `invoice_id`.
**Source:** `raw.invoices` (`billing.invoices`).
**Purpose:** clean, deduplicated representation of billing invoices.
**Key transformations:** currency normalized; `subtotal`/`tax`/`total` safely cast to `subtotal_amount`/`tax_amount`/`total_amount`; status constrained to `{paid, open, failed, void, unknown}`.
**Quality behavior:** `is_paid_without_paid_at` surfaces (not corrects) a paid invoice with no payment timestamp; relationship to `stg_billing__subscriptions` is WARN-severity (intentional orphans).

### stg_product__events

**Grain:** one row per `event_id`.
**Source:** `raw.product_events` (`product.product_events`).
**Purpose:** clean, deduplicated representation of in-product usage events (voice generation, agent creation, feature usage, ...).
**Key transformations:** `event_name` lowercased; `event_timestamp` safely cast **without reordering** - late-arriving/out-of-order events are preserved as generated; `properties` kept as native `jsonb`, not flattened.
**Quality behavior:** `_ingested_at` (processing time) is preserved alongside `event_timestamp` (event time) specifically so downstream models can reason about the gap between them.

### stg_api__usage

**Grain:** one row per `request_id`.
**Source:** `raw.api_usage` (`api.api_usage`).
**Purpose:** clean, deduplicated representation of per-request API gateway usage/metering.
**Key transformations:** `endpoint`/`model_family` lowercased; `latency_ms`/`input_units`/`output_units`/`status_code`/`estimated_cost` safely cast to numeric types (NULL on parse failure, never 0).
**Quality behavior:** `is_successful_request` is a direct `status_code BETWEEN 200 AND 299` derivation - not a reliability/SLA metric.

### stg_crm__accounts

**Grain:** one row per `crm_account_id` (an organization has *multiple* CRM touches over its lifecycle - this is **not** one row per organization).
**Source:** `raw.crm_accounts` (`crm.crm_accounts`).
**Purpose:** clean, deduplicated representation of CRM account lifecycle touches.
**Key transformations:** `account_status` constrained to `{prospect, active, at_risk, churned, unknown}`; `segment` constrained to `{smb, mid_market, enterprise, unknown}`; `lead_source` preserved as NULL when missing.

### stg_marketing__events

**Grain:** one row per `marketing_event_id`.
**Source:** `raw.marketing_events` (`marketing.marketing_events`).
**Purpose:** clean, deduplicated representation of marketing touchpoints across known users and anonymous visitors.
**Key transformations:** `channel`/`event_type`/UTM fields lowercased; `campaign_id`/`utm_campaign` preserved as NULL when missing (`is_campaign_attributed` surfaces availability only - **not** an attribution model).
**Quality behavior:** `user_id` is legitimately NULL for anonymous (pre-signup) touches - `anonymous_id` is always present as the fallback identity.

## Intermediate models

See [dbt_intermediate.md](dbt_intermediate.md) for the full customer-identity, attribution, revenue, and transition-derivation rationale behind these models.

### int_user_subscription_history

**Grain:** one row per `subscription_id`.
**Source:** `stg_billing__subscriptions` LEFT JOIN `stg_app__users`.
**Purpose:** subscription history enriched with user signup context, with orphaned `user_id` references flagged rather than dropped.
**Key transformations:** `is_user_orphaned` flag; `days_from_signup_to_subscription_start`.

### int_user_acquisition

**Grain:** one row per `user_id`.
**Source:** `stg_app__users` LEFT JOIN a deduplicated first-touch subset of `stg_marketing__events`.
**Purpose:** single-touch (first-touch) acquisition attribution - **not** multi-touch.
**Key transformations:** `acquisition_channel` (authoritative, from `stg_app__users`) vs. `attributed_marketing_channel`/`attributed_campaign_id` (supplementary, from the earliest linked marketing touch, deterministically tie-broken).

### int_customer_revenue

**Grain:** one row per `invoice_id` (a reusable fact, not an MRR/ARR aggregate).
**Source:** `stg_billing__invoices` LEFT JOIN `stg_billing__subscriptions`.
**Purpose:** billed/paid/outstanding amounts derived purely from invoice status (no refund/credit ledger exists in this source).
**Key transformations:** `customer_id` = organization (falls back to the invoice's own `customer_id` if the subscription is orphaned); currency never summed across rows.

### int_daily_product_usage

**Grain:** one row per (`event_date`, `user_id`, `organization_id`, `event_name`).
**Source:** `stg_product__events`.
**Purpose:** daily feature-level usage aggregation, feeding feature-adoption/retention marts.
**Key transformations:** `event_date` from `event_timestamp` (event time, not ingestion time) - late-arriving events land on their real historical day.

### int_api_usage_by_customer

**Grain:** one row per (`usage_date`, `organization_id`).
**Source:** `stg_api__usage`.
**Purpose:** daily organization-level API usage/error-rate metrics.
**Key transformations:** `error_rate` via the `safe_divide` macro (NULL, never a division error, when `request_count` is 0); `organization_id = null` rows kept and flagged (`is_organization_unknown`), not dropped.

### int_subscription_transitions

**Grain:** one row per (`subscription_id`, `transition_type`).
**Source:** `stg_billing__subscriptions`.
**Purpose:** an ordered milestone timeline (trial start/end, subscription start/cancellation) derived from a single current-state row per subscription - **not** a true historical change-event log (no plan-change detection; see limitations in dbt_intermediate.md).
**Key transformations:** `UNION ALL` to pivot columns to rows; `LAG()` for `previous_transition_type`/`days_since_previous_transition`.

### int_customer_activity

**Grain:** one row per (`activity_date`, `organization_id`).
**Source:** `int_daily_product_usage` (pre-aggregated) FULL OUTER JOIN `int_api_usage_by_customer`.
**Purpose:** combined product + API activity per organization per day, without row/metric multiplication.
**Key transformations:** `first_observed_activity_date`/`last_observed_activity_date` via window functions; not a dense calendar (absence of a row = "not observed," not "zero").

### int_conversion_funnel

**Grain:** one row per `user_id`.
**Source:** `stg_app__users` LEFT JOIN subscription milestones aggregated from `stg_billing__subscriptions`.
**Purpose:** signup/trial/paid-conversion milestones and durations - does **not** classify users as converted/failed (that's Phase 5).
**Key transformations:** `paid_conversion_at` = earliest `started_at` among non-missing, non-free-plan subscriptions.

### int_customer_lifecycle

**Grain:** one row per `user_id` (user-level, unlike the organization-level revenue/activity/API-usage models - see "Customer identity" in dbt_intermediate.md).
**Source:** `int_conversion_funnel` + cancellation/most-recent-status from `stg_billing__subscriptions`.
**Purpose:** lifecycle milestones plus a current-state `lifecycle_stage` snapshot - not a reconstructed historical journey.
**Key transformations:** `lifecycle_stage` = most recent subscription's status (deterministic tie-break on `updated_at`/`subscription_id`), or `'no_subscription'`.

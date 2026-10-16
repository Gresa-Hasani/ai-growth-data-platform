# dbt Intermediate Layer (Phase 4)

The intermediate layer turns the 8 trusted `stg_*` views (Phase 3) into reusable
business entities and relationships. It answers **"what is a reliable fact or
relationship I can build marts from?"** - it still does not answer "what KPI
should Growth/RevOps look at" (MRR, churn, conversion rate, cohort retention -
those are Phase 5 marts).

## Customer identity: user vs. organization vs. customer

This schema has no single "customer" table, so each model states its grain
explicitly rather than using "customer" loosely:

| Term | What it means here | Used by |
|---|---|---|
| **user** | An individual person (`stg_app__users.user_id`). Signup, trial, and subscription events are recorded against users. | `int_user_subscription_history`, `int_user_acquisition`, `int_conversion_funnel`, `int_customer_lifecycle` |
| **organization** | The billing/account entity (`stg_app__organizations.organization_id`). Every user belongs to exactly one organization, including "individual" org types (a single-user account is still modeled as an organization). | `int_customer_revenue`, `int_api_usage_by_customer`, `int_customer_activity` |
| **"customer"** | Used in this project specifically to mean **organization** wherever it appears (revenue, API usage, activity) - verified directly: `raw.invoices.customer_id` is generated as the subscription's `organization_id` in every case this environment produces, never a bare user_id with no organization. | - |

`int_customer_lifecycle` and `int_conversion_funnel` are the exception: their
grain is the **user**, not the organization, because signup/trial/cancellation
are literally user-scoped fields in this data. This is called out explicitly in
both models' SQL comments and YAML descriptions so the two conventions are
never confused.

## Model inventory

| Model | Grain | Materialization |
|---|---|---|
| `int_user_subscription_history` | one row per `subscription_id` | view |
| `int_user_acquisition` | one row per `user_id` | view |
| `int_customer_revenue` | one row per `invoice_id` | view |
| `int_daily_product_usage` | one row per (`event_date`, `user_id`, `organization_id`, `event_name`) | view |
| `int_api_usage_by_customer` | one row per (`usage_date`, `organization_id`) | view |
| `int_subscription_transitions` | one row per (`subscription_id`, `transition_type`) | view |
| `int_customer_activity` | one row per (`activity_date`, `organization_id`) | view |
| `int_conversion_funnel` | one row per `user_id` | view |
| `int_customer_lifecycle` | one row per `user_id` | view |

All views (same rationale as staging - see `docs/dbt_staging.md` "Materializations").
Intermediate was explicitly kept out of incremental materialization this
phase; that's a Phase 6 concern once there's a real volume/latency reason
for it.

## Join relationships and cardinality

Every join in this layer was deliberately checked against the side that must
stay unique, and verified with a dedicated singular test (not just asserted
in a comment):

- `int_user_subscription_history`: `stg_billing__subscriptions` LEFT JOIN
  `stg_app__users` on `user_id` (unique, tested in Phase 3) -> cannot fan out.
  Verified: `count(*)` matches `stg_billing__subscriptions` exactly (1,500 = 1,500).
- `int_customer_revenue`: `stg_billing__invoices` LEFT JOIN
  `stg_billing__subscriptions` on `subscription_id` (unique) -> cannot fan out.
  Verified: `count(*)` matches `stg_billing__invoices` exactly (5,000 = 5,000),
  and `sum(billed_amount)` matches `sum(stg_billing__invoices.total_amount)`
  exactly ($4,429,580.90 = $4,429,580.90).
- `int_user_acquisition`: `stg_app__users` LEFT JOIN a *pre-deduplicated*
  (one row per `user_id`, via `ROW_NUMBER()`) first-touch subset of
  `stg_marketing__events` -> cannot fan out, because the right side is
  already unique by `user_id` before the join happens.
- `int_customer_activity`: two *independently pre-aggregated* sources
  (`int_daily_product_usage` rolled up to org/day, `int_api_usage_by_customer`
  already at org/day) combined with a `FULL OUTER JOIN` on
  (`activity_date`, `organization_id`) - both sides are already unique on
  that key before the join, so the join cannot multiply either metric.
- `int_conversion_funnel` / `int_customer_lifecycle`: subscriptions are
  aggregated with `MIN()`/window functions down to one row per `user_id`
  *before* being exposed - never joined 1:many without a preceding aggregation.

No model in this layer joins a many-side onto another many-side without
pre-aggregating one of them first.

## Attribution model (`int_user_acquisition`)

**Policy: single-touch, first-touch only - not multi-touch attribution.**

1. `acquisition_channel` (on `stg_app__users`, always present, directly
   recorded at signup) is the **authoritative** channel. It never comes from
   joining `marketing_events`.
2. Campaign/UTM detail is a *supplement*, pulled from the single marketing
   touch with the lowest `(event_timestamp, marketing_event_id)` among rows
   where `marketing_events.user_id` matches - a deterministic tie-break that
   never varies between runs.
3. ~35% of `marketing_events` rows have no `user_id` at all (anonymous,
   pre-signup touches identified only by `anonymous_id`) and are structurally
   impossible to join to a user reliably - they are out of scope for this
   model's join, by design, not an oversight.

Verified distribution in the dev dataset: of 1,000 users, 665 have both a
known marketing touch *and* a campaign (`is_campaign_attributed`), 127 have a
known touch with no campaign, and 208 have no known marketing touch linked to
their `user_id` at all (channel is still populated for all 1,000 from
`stg_app__users`).

## Revenue modeling decisions (`int_customer_revenue`)

- Grain is **invoice**, not customer-period. Aggregating to MRR/ARR is
  explicitly deferred to Phase 5 - doing it here would require deciding how
  to treat mixed billing intervals (`monthly` vs `annual`) and currencies,
  which this model does not attempt.
- This source has **no refund/credit/outstanding-ledger tables** - only
  `subtotal`/`tax`/`total` and a `status` on the invoice itself.
  `billed_amount`/`paid_amount`/`outstanding_amount` are therefore derived
  purely from `status`, not from separate ledger entries that don't exist:
  - `billed_amount` = `total_amount` always
  - `paid_amount` = `total_amount` when `status = 'paid'`, else 0
  - `outstanding_amount` = `total_amount` when `status in ('open', 'failed')`, else 0
- **Currency is never summed across different currencies** anywhere in this
  model - every row keeps its own `currency`, and no cross-currency total is
  computed. A real conversion step (with a documented exchange-rate source)
  would be required before that's safe, and is out of scope here.
- `customer_id` falls back to the invoice's own `customer_id` when
  `subscription_id` doesn't resolve (`is_subscription_orphaned`), rather than
  losing the row's customer identity entirely.

## Subscription transitions - a milestone timeline, not a change log

**This is the most important limitation in the whole intermediate layer, and
is documented in three places on purpose (this file, the model's SQL
comments, and its YAML description) because it's easy to misread the model
as something it isn't.**

`raw.subscriptions` (and therefore `stg_billing__subscriptions`) is a
**current-state** record - each subscription has exactly one row with its
latest status, not a history of every status it ever held. There is no
change-event log to derive true state transitions from.

`int_subscription_transitions` instead builds an **ordered milestone
timeline** from the four timestamp columns already present on that single
row (`trial_started_at`, `trial_ended_at`, `started_at`, `cancelled_at`),
using `UNION ALL` to turn columns into rows and `LAG()` (partitioned by
`subscription_id`, ordered by timestamp with a deterministic tiebreaker) to
compute `previous_transition_type`/`days_since_previous_transition`. Every
`transition_at` value is copied directly from a real column - none are
inferred or guessed.

**What this means in practice**: it can tell you *that* a subscription had a
trial that started and ended, and when it started and was cancelled - but it
cannot tell you about a **plan change** (a current-state row can't reveal
what plan a subscription had before its last update), nor can it detect a
status that was set and later reverted. True state-change detection,
including plan history, requires the SCD Type 2 subscription-status snapshot
planned for Phase 6 - this model is not a substitute for that, and should not
be treated as one.

## Activity logic (`int_customer_activity`)

Rolls up `int_daily_product_usage` and `int_api_usage_by_customer` (both
pre-aggregated, see "Join relationships" above) to one row per
(`activity_date`, `organization_id`). This model only contains dates where
**some** activity was observed for that organization - it is **not** a dense
calendar spine. A missing (date, org) combination means "no activity
observed on that date," which is explicitly different from "this
organization doesn't exist" (that distinction requires joining against a
full organization dimension, which belongs to Phase 5's `dim_organizations`).
`first_observed_activity_date`/`last_observed_activity_date` are computed
with window functions over this same table, not a separate join.

## Conversion funnel logic (`int_conversion_funnel`)

Milestones, not a classification:

- `signup_at` - always present.
- `trial_start_at` - earliest `trial_started_at` across the user's subscriptions.
- `paid_conversion_at` - earliest `started_at` among subscriptions with a
  real plan (`plan_name is not null and plan_name != 'free'`) - a
  **historical** event, independent of that subscription's current status
  (a subscription that later cancelled still counts as having converted once).

This model explicitly does **not** label anyone "converted" or "not
converted" - a NULL `paid_conversion_at` might mean the user genuinely never
converted, or might mean the observation window just hasn't caught up yet.
Making that call is Phase 5's job, with an explicit policy on how it treats
incomplete observation windows.

Two ordering quality flags are tested as **ERROR** (`is_trial_before_signup`,
`is_paid_conversion_before_signup`) because the generator makes these
impossible by construction within a single subscription - a violation would
mean a real regression. A third (`is_paid_conversion_before_trial_start`) is
deliberately **not** tested as an error: a user with *multiple*
subscriptions can legitimately have a non-trial paid plan that started
before a different subscription's trial - that's a real scenario, not
corruption.

## Testing: reconciliation and row-multiplication guards

Beyond the usual `not_null`/`unique`/`accepted_values`/`relationships`
pattern inherited from staging conventions, this layer adds singular tests
specifically to catch the failure modes joins/aggregations are prone to
(all ERROR severity - these check the layer's own transformation logic, not
known source-data corruption):

- `assert_int_user_subscription_history_no_row_multiplication` - row count must equal `stg_billing__subscriptions`.
- `assert_int_customer_revenue_no_row_multiplication` - row count must equal `stg_billing__invoices`.
- `assert_int_customer_revenue_billed_amount_reconciles` - `sum(billed_amount)` must equal `sum(stg_billing__invoices.total_amount)` exactly.
- `assert_int_daily_product_usage_event_count_reconciles` - `sum(event_count)` must equal the staging row count.
- `assert_int_api_usage_by_customer_request_count_reconciles` - `sum(request_count)` must equal the staging row count.
- `assert_int_subscription_transitions_chronological_order` - the model's own `LAG()` output must never show a transition before the one preceding it.
- `assert_int_conversion_funnel_no_within_subscription_order_violation` - see "Conversion funnel logic" above.

All of the above passed with zero failing rows against the current dev
dataset (verified, not assumed - see the Phase 4 completion report).

## Known limitations

- `int_subscription_transitions` cannot detect plan changes or reverted
  statuses - see "Subscription transitions" above. This is a source-data
  limitation (no change-event history exists yet), not a modeling shortcut.
- `int_user_acquisition`'s campaign/UTM detail only covers users with a
  `marketing_events` row that has their `user_id` - anonymous-only touches
  before signup are unreachable by any join on `user_id`.
- `int_customer_activity`/`int_daily_product_usage`/`int_api_usage_by_customer`
  are not dense calendars; absence of a row is "not observed," not "zero."
- No `dim_users`/`dim_organizations`/`dim_plans` or fact tables exist yet -
  those, along with MRR/ARR/churn/retention-cohort marts, are Phase 5.

## How this feeds Phase 5

Every Phase 5 mart should be buildable by referencing these models rather
than reaching back into staging:

- `mart_mrr`/`mart_arr`/`mart_arpu` -> aggregate `int_customer_revenue`
  (decide the billing-interval/currency-normalization policy there, since
  this layer deliberately didn't).
- `mart_customer_churn`/`mart_revenue_churn` -> `int_subscription_transitions`
  + `int_customer_lifecycle.lifecycle_stage`.
- `mart_trial_conversion` -> `int_conversion_funnel` (decide the
  converted/not-converted/still-observing classification there).
- `mart_feature_adoption`/`mart_retention_cohorts` -> `int_daily_product_usage`.
- `mart_api_usage` -> `int_api_usage_by_customer`.
- `mart_acquisition_channels` -> `int_user_acquisition`.
- `mart_enterprise_growth` -> `int_customer_activity` + `int_customer_revenue`.

## How to run

```bash
cd dbt
export DBT_POSTGRES_HOST=localhost DBT_POSTGRES_PORT=5435   # see .env / profiles_example.yml
dbt run --select intermediate
dbt test --select intermediate
dbt build --select intermediate
dbt build --select staging+        # staging + everything downstream of it
```

# dbt Dimensions, Facts & Marts (Phase 5)

Business-facing dimensional models and Growth/Revenue marts built on the
Phase 4 intermediate layer. See [dbt_intermediate.md](dbt_intermediate.md)
for the layer underneath and [dbt_staging.md](dbt_staging.md) for the
normalization rules both layers depend on.

## Customer identity (recap, now load-bearing across every mart)

"Customer" = **organization** throughout every dimension, fact, and mart in
this phase (revenue, API usage, activity, churn, ARPU, LTV, enterprise
growth) - verified directly against the data: `raw.invoices.customer_id` is
always generated as the subscription's `organization_id`, never a bare
user with no organization. `dim_users` is a separate, person-level
dimension used only where the business question is genuinely about
individuals (signups, trial starts, acquisition attribution, product
adoption). Two marts are explicitly user-grained rather than
organization-grained, and say so in their own description:
`mart_trial_conversion` and the per-user rows that feed
`mart_acquisition_channels`' funnel counts.

## Unmatched dimension keys (project-wide policy)

Every fact (`fact_subscriptions`, `fact_invoices`, `fact_product_usage`,
`fact_api_usage`) resolves its dimension keys with a `LEFT JOIN` and, when a
natural key doesn't resolve, leaves the surrogate key `NULL` paired with an
explicit `is_*_unmatched` boolean - never a fabricated `-1`/`'unknown'`
dimension row standing in for a real entity. This is the same philosophy
Phase 3/4 already used for orphaned foreign keys (flag and preserve, never
drop or invent); Phase 5 just extends it to dimensional joins.

## Dimension and fact grains

| Model | Grain |
|---|---|
| `dim_users` | one row per user |
| `dim_organizations` | one row per organization |
| `dim_plans` | one row per (plan_name, billing_interval) actually observed |
| `dim_date` | one row per calendar date, dynamically bounded to the data's real min/max |
| `fact_subscriptions` | one row per subscription_id |
| `fact_invoices` | one row per invoice_id |
| `fact_product_usage` | one row per (date_day, user_id, organization_id, event_name) |
| `fact_api_usage` | one row per (date_day, organization_id) |

`dim_plans.typical_monthly_equivalent_amount` is an **observed average**,
not a price-catalog lookup - verified directly: the same plan+interval
combination shows real price variance in this dataset (e.g.
business/monthly ranges $284.94-$313.79), consistent with the generator's
~5% random jitter, not negotiated/tiered pricing. There is no separate
plan-pricing table in this source.

## Revenue semantics - the five amounts, kept distinct everywhere

| Term | Definition | Where |
|---|---|---|
| Billed / invoiced amount | `total_amount` on the invoice, regardless of outcome | `fact_invoices.billed_amount` |
| Paid / collected amount | `total_amount` when `status = 'paid'`, else 0 | `fact_invoices.paid_amount` |
| Outstanding amount | `total_amount` when `status in ('open','failed')`, else 0 | `fact_invoices.outstanding_amount` |
| Recurring subscription revenue | `monthly_equivalent_amount` of currently-active-recurring subscriptions | `fact_subscriptions`, `mart_mrr` |
| Annualized recurring revenue | recurring revenue * 12 | `mart_arr` |

**Refunds and credits**: this source has no refund/credit ledger at all -
not implemented, not approximated, not invented. **Currency conversion**:
this source has no exchange-rate table - every mart groups by `currency`
and never sums across currencies; there is no platform-wide "total revenue"
number anywhere in this phase, by design.

### The "currently active recurring subscription" definition (`is_subscription_recurring_active` macro)

Used identically by `mart_mrr`, `mart_arr`, `mart_customer_churn`,
`mart_revenue_churn`, and `mart_arpu` - the single most important shared
definition in this phase:

```
status = 'active'
OR (status = 'canceled' AND cancelled_at > now())
```

The second clause matters: **verified directly against the dev dataset**,
111 of 295 "canceled" subscriptions have a `cancelled_at` that is still in
the future (a scheduled cancellation - e.g. "cancel at period end" - that
hasn't taken effect yet). Treating `status = 'canceled'` as unconditionally
inactive would understate today's real recurring revenue by ignoring
subscriptions that are still being paid for right now. `trialing` (no
revenue yet) and `past_due` (payment already failed - a conservative,
documented exclusion) are never counted as active-recurring.

### Billing-interval normalization (`monthly_equivalent_amount` macro)

```
monthly | subscription_amount
annual  | subscription_amount / 12
```

This is the **standard SaaS MRR convention** (divide by 12 calendar
months) - deliberately **not** the inverse of whatever discount
`scripts/generate_data.py` baked into its annual pricing. Verified: annual
amounts in this dataset are ~10x the monthly price (a promotional
discount), not a 12x calendar multiple - MRR normalization reflects
calendar time, not the vendor's discount structure.

## mart_mrr / mart_arr - point-in-time snapshots, not a time series

**This is the single most important limitation in this phase, stated three
times on purpose (here, in each model's SQL header, and in its YAML
description) because it is easy to misread otherwise.**

`raw.subscriptions` has exactly one row per subscription with its *current*
status - there is no history of what any subscription's status was on any
past date. Reconstructing "MRR as of March" would require assuming every
subscription's current status held continuously since its `started_at`,
which Phase 4's `int_subscription_transitions` already established cannot
be verified (no plan-change or status-reversion history exists). So
`mart_mrr`/`mart_arr` report **today's MRR/ARR only** - one row per
currency, `as_of_date = current_date`. A true historical series requires
periodic snapshots, which is explicitly Phase 6 scope (dbt snapshots), not
something fabricated here from single current-state rows.

Verified in the dev dataset (2026-10-16): MRR = $120,841.91 USD / €37,991.93
/ £17,275.85; ARR = MRR × 12 exactly (tested - `assert_mrr_arr_consistency`).

## mart_customer_churn - organization-level, using genuine historical timestamps

Formula (exact, per the project's definition): *customers active at the
start of the period who churn during the period, divided by customers
active at the start of the period.*

Unlike MRR, this calculation is **not** limited to current-state-only
approximation, because `started_at`/`cancelled_at` are genuinely historical,
append-only timestamps (never overwritten by a later change in this
generator) - "active at period_start" is reconstructed from real history,
not assumed from current status:

- **Active at period_start** (30 days ago): `started_at <= period_start AND
  (cancelled_at IS NULL OR cancelled_at > period_start)`.
- **Active at period_end** (today): the same `is_subscription_recurring_active`
  definition used everywhere else.
- **Churned**: active at start, but the organization has **zero**
  subscriptions still active at period_end. Verified: 163 organizations in
  the dev dataset have more than one simultaneously-active subscription -
  a single cancellation while another subscription stays active does
  **not** make that organization churned (tested by construction: the
  churn calculation is a `LEFT JOIN ... WHERE active_at_end IS NULL`
  against the full active-at-end set per organization, not per
  subscription).

Segmented by `organization_type` plus one `'all'` row. Verified in the dev
dataset: 197 organizations active at period start, 6 churned (3.05%
overall); enterprise and startup orgs both show 0% churn this period,
individual orgs the highest at 6.49%.

## mart_revenue_churn - gross only, documented why net isn't implemented

Gross revenue churn = lost recurring revenue during the period / recurring
revenue at the start of the period, computed at the **subscription** grain
(not organization) - revenue is lost per subscription even when its
organization survives via another one. Uses the identical
active-at-start/active-at-end reconstruction as `mart_customer_churn`.

**Net revenue churn (expansion/contraction-aware) is explicitly NOT
implemented**: this source has no history of a subscription's *past*
amount - `scripts/generate_data.py` never models a plan upgrade/downgrade,
and `raw.subscriptions` keeps only the current amount. Without a prior
amount to compare against, "expansion" and "contraction" cannot be
detected, only invented. Per the project's policy against overstating
accuracy, only gross churn is reported.

New-subscription revenue during the period is correctly excluded from the
denominator by construction (it's only ever in the active-at-start set if
it started before `period_start`).

Verified: USD gross revenue churn 22.1%, EUR 18.4%, GBP 36.6% (small
denominators at this dev scale - see "Known limitations").

## mart_customer_ltv - two metrics, never blended

- `realized_ltv_to_date` - **observed historical fact**: `sum(paid_amount)`
  per organization, to date. Not a forecast.
- `estimated_ltv` - **speculative projection**: `ARPU / platform-wide churn_rate`
  (the classic SaaS formula), using the `'all'`-segment churn rate from
  `mart_customer_churn`, not an organization-specific prediction (no
  per-organization churn history exists to estimate from individually).
  `NULL`, not a divide-by-zero error or a fabricated number, whenever
  `churn_rate` is zero or unavailable.

Grain: `(organization_key, currency)` - never summed across currencies.

## mart_arpu - per paying organization, not per invoice

ARPU = current MRR / count of distinct paying organizations, **not**
average invoice value (which would include one-time billing-cycle timing
noise that MRR deliberately excludes). Verified: this model's `mrr` total
is intentionally slightly lower than `mart_mrr`'s for the same currency (a
$382.46 gap in USD) - it only attributes revenue to organizations it can
actually resolve, consistent with the project's unmatched-key policy; see
`mart_arpu.sql`'s header for the exact reconciliation.

## Growth marts

- **mart_daily_growth**: dense daily grid (`dim_date`), new user signups
  vs. new organizations kept as separate columns (never conflated), plus
  running cumulative totals.
- **mart_trial_conversion**: eligible cohort = users with `trial_start_at`.
  A 30-day observation window (generator trials run 14 days) gates whether
  a user counts toward the rate denominator at all - an in-progress trial
  is never counted as a failed conversion. Verified: 506 trials, 423
  converted (one subscription actually continuing from that specific
  trial, not just any paid conversion), 486 with a complete observation
  window.
- **mart_acquisition_channels**: funnel counts (signup/trial/paid-conversion)
  are channel-level and intentionally repeated across a channel's
  per-currency revenue rows - summing them across those rows would
  overcount. Revenue is attributed via each invoice's own subscription
  `user_id` -> that user's `acquisition_channel`, specifically to avoid the
  ambiguity of an organization with multiple users on different channels.
  `paid_conversion_count` only counts conversions that actually followed a
  trial (fixed during verification - see "Known limitations" for the bug
  this caught).
- **mart_retention_cohorts**: cohort = signup month; retention event = any
  product usage in a later month. Unlike `int_customer_activity`'s sparse
  grain, the population here is fully enumerated (`dim_users`) and checked
  against a complete event log, so `retained_users = 0` is a meaningful,
  explicit observation, not a gap. Only **observable** periods
  (`cohort_month + period_index <= current month`) are included - an
  unobserved future period is never fabricated as 0% retention.
- **mart_feature_adoption**: `adopting_user_count` (distinct users) kept
  separate from `total_event_count` (raw volume) so a power user's event
  volume cannot inflate the adoption rate.
- **mart_enterprise_growth**: `is_enterprise` from `dim_organizations`
  (verified classification, not invented). Two **separate** metrics never
  blended: `current_monthly_equivalent_revenue` (point-in-time, same
  snapshot limitation as MRR) and `usage_change_pct` (a genuine trend -
  trailing 30 days vs. the preceding 30 days of real event timestamps).

## Join paths and cardinality (the project-wide no-inflation guarantee)

Every fact/mart join was checked against a side proven unique before being
joined, and verified with a dedicated test, not just asserted in a comment:

- `fact_subscriptions`/`fact_invoices`: dimension keys are unique by
  construction (`dim_users.user_key`, `dim_organizations.organization_key`,
  `dim_plans.plan_key` are all tested `unique`) - `LEFT JOIN`ing them onto
  the fact's natural grain cannot fan it out. Verified: `fact_subscriptions`
  row count = `int_user_subscription_history` row count = 1,500 exactly;
  `fact_invoices` = `int_customer_revenue` = 5,000 exactly.
- `fact_product_usage`/`fact_api_usage`: pure pass-through of dimension
  keys onto an already-aggregated grain - no re-aggregation happens here,
  so usage cannot be inflated beyond what Phase 4 already verified.
  Verified: summed `event_count`/`request_count` totals match the
  intermediate layer exactly.
- `mart_mrr`/`mart_arr`: built from the identical `fact_subscriptions`
  filter and grouping - `assert_mrr_arr_consistency` verifies `arr = mrr * 12`
  exactly, every build.
- `mart_customer_ltv`: joins `mart_arpu` (per currency) and
  `mart_customer_churn`'s single `'all'` row via `CROSS JOIN` - a 1-row
  table broadcast, not a fan-out risk.

## Known limitations

- **MRR/ARR/ARPU/current-enterprise-revenue are point-in-time snapshots,
  not historical time series** - the single biggest limitation in this
  phase; see "mart_mrr / mart_arr" above. Phase 6 snapshots are the
  intended fix.
- **Net revenue churn is not implemented** - no historical subscription-
  amount data exists to detect expansion/contraction against.
- **Revenue churn denominators are small at dev scale** (tens to low
  hundreds of subscriptions per currency per 30-day window), so the
  computed rates are directionally correct but noisy - a genuine
  dev-dataset-scale limitation, not a logic bug.
- **A real bug was caught and fixed during Phase 5 verification**:
  `mart_acquisition_channels.paid_conversion_count` originally counted
  *any* paid conversion per channel, including users who converted without
  ever starting a trial - this could make `trial_to_paid_conversion_rate`
  exceed 1.0. The `percentage_between_zero_and_one` test caught this
  immediately (21 failing rows); fixed by scoping `converted_to_paid` to
  conversions that actually followed a trial. Left in this document as a
  concrete example of the test suite doing its job, not swept under the rug.
- Pre-existing Phase 3 WARNs (9, orphaned-FK relationship tests) remain
  unchanged and are not part of this phase's scope.

## How this feeds Phase 6

Snapshots (SCD Type 2 on `subscription_status`, `plan_changes`,
`crm_account_status`) will let `mart_mrr`/`mart_arr`/`mart_revenue_churn`'s
net-churn gap be filled with a true historical series instead of a single
snapshot - the exact limitation documented above.

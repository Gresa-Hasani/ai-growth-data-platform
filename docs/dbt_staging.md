# dbt Staging Layer (Phase 3)

The staging layer converts messy, source-preserving `raw.*` tables (Phase 2) into
clean, standardized, tested building blocks. It answers **"what is the clean
canonical representation of this source?"**, never **"what business decision
should we make from it?"** - business logic belongs to intermediate models
(Phase 4) and marts (Phase 5+).

## Project layout

```
dbt/
  dbt_project.yml
  packages.yml                  dbt-utils (surrogate keys, generic test helpers)
  profiles_example.yml          copy to ~/.dbt/profiles.yml
  macros/
    generate_schema_name.sql      models land in schema/, not <target>_schema/
    deduplicate.sql               shared ROW_NUMBER() dedup pattern
    normalize_text.sql            trim / trim+lower
    normalize_email.sql           normalize_email() + is_email_valid()
    normalize_country_code.sql    normalize_country_code() + is_country_code_recognized()
    normalize_currency.sql        normalize_currency() + is_currency_code_recognized()
    safe_cast.sql                 safe_cast_numeric/integer/timestamp() via pg_input_is_valid()
  models/staging/
    app/         stg_app__users, stg_app__organizations
    billing/     stg_billing__subscriptions, stg_billing__invoices
    product/     stg_product__events
    api/         stg_api__usage
    crm/         stg_crm__accounts
    marketing/   stg_marketing__events
  tests/
    generic/       positive_or_zero, valid_currency_code, valid_timestamp_order
    assert_*.sql   singular tests (see "Singular tests" below)
```

Each domain folder has `_<domain>__sources.yml` (source defs + freshness) and
`_<domain>__models.yml` (model/column docs + tests), matching the
`stg_<domain>__<entity>.sql` naming used for the models themselves.

## Naming conventions

- `*_id` for identifiers, `*_at` for timestamps, `is_*` for booleans,
  `*_amount` for monetary values (`amount` -> `subscription_amount`,
  `subtotal`/`tax`/`total` -> `subtotal_amount`/`tax_amount`/`total_amount`
  to disambiguate which entity's amount).
- `country` -> `country_code`, `created`/`updated` -> `created_at`/`updated_at`
  (already the raw convention - preserved).
- Categorical columns are normalized to lowercase canonical values
  (`status`, `plan_name`, `channel`, ...); anything unrecognized becomes the
  literal string `'unknown'` plus a paired `is_*_unknown` flag - it is never
  silently dropped or miscategorized.

## Normalization rules

| Concern | Rule | Macro |
|---|---|---|
| Email | trim + lowercase only; never rewrite the local part | `normalize_email()` |
| Country code | map known messy variants (`USA`/`us`/`United States`/` US` -> `US`; `UK`/`gb`/`United Kingdom` -> `GB`), else trim+upper passthrough | `normalize_country_code()` |
| Currency | trim + uppercase (`usd`/` USD ` -> `USD`); **no conversion** | `normalize_currency()` |
| Free text (names, domains) | trim only - casing is presentational | `normalize_text(col)` |
| Categorical text (status, channel, plan) | trim + lowercase | `normalize_text(col, lower=True)` |
| Numeric/timestamp casting | NULL on parse failure, never 0 or a guessed value | `safe_cast_numeric/integer/timestamp()` |

Country/currency normalization is deliberately narrow: it maps the specific
messy variants this synthetic environment generates
(`scripts/generate_data.py::COUNTRY_DIRTY_VARIANTS`/`CURRENCY_DIRTY_VARIANTS`),
not a general geography/ISO reference. An unrecognized value is preserved
(trimmed/uppercased) and flagged via `is_country_code_recognized`/
`is_currency_code_recognized` - never silently mapped to the wrong country
or currency.

## Safe casting

PostgreSQL has no built-in `TRY_CAST`. `safe_cast_*` macros use PostgreSQL
16's `pg_input_is_valid(text, type)` to check parseability before casting,
so a malformed numeric/timestamp string becomes `NULL` instead of crashing
the model or - worse - silently becoming `0`. Zero and unknown are not the
same thing.

## Deduplication

Every staging model ends with `{{ deduplicate(relation, partition_by, order_by) }}`
(`macros/deduplicate.sql`): `ROW_NUMBER() OVER (PARTITION BY <business key>
ORDER BY <tiebreakers>) = 1`.

- **Mutable entities** (have `updated_at`: users, organizations,
  subscriptions, crm_accounts): `updated_at DESC NULLS LAST, _ingested_at
  DESC NULLS LAST, <pk> DESC` - latest business update wins, ties broken by
  latest ingestion, then a deterministic final tiebreaker on the key itself
  (never nondeterministic).
- **Immutable/near-immutable records** (no `updated_at`: invoices, product
  events, api usage, marketing events): `_ingested_at DESC NULLS LAST, <pk>
  DESC`.

**Important finding from actually running this against Phase 2 data**: raw
tables already enforce primary-key uniqueness via Phase 2's upsert-based
ingestion (`INSERT ... ON CONFLICT (pk) DO UPDATE`), so `raw.* row count ==
distinct primary keys` **before staging ever runs** - verified directly
(e.g. `raw.users`: 1000 rows, 1000 distinct `user_id`). The `deduplicate()`
macro therefore currently removes **zero** rows. It stays in every model
anyway as a defensive, documented guarantee that stays true independent of
how raw is populated in the future (e.g. if a future source ever became
append-only) - not as a fix for an observed problem today.

## Data quality flags

Exposed only where they inform downstream modeling, not exhaustively:

| Flag | Model | Meaning |
|---|---|---|
| `is_email_valid` | `stg_app__users` | Structural validity only (has `@`, a domain with a dot, no embedded whitespace) - not deliverability |
| `is_country_code_recognized` | users, organizations | `country_code` is in this environment's known ISO2 set |
| `is_status_unknown` | users, subscriptions, invoices | Source status didn't match a known value |
| `is_organization_type_unknown` | organizations | Source `organization_type` didn't match a known value |
| `is_plan_missing` | subscriptions | Source delivered no `plan_name` - **never** defaulted to a real plan |
| `is_currency_recognized` | subscriptions, invoices | `currency` is in this environment's known set (USD/EUR/GBP) |
| `is_paid_without_paid_at` | invoices | `status = 'paid'` but `paid_at IS NULL` - surfaced, not corrected |
| `is_event_timestamp_missing` | product_events | `event_timestamp` failed to parse (should be rare - ingestion already rejects unparseable event timestamps) |
| `is_successful_request` | api_usage | `status_code` in 200-299 - a direct derivation, not a reliability KPI |
| `is_account_status_unknown` | crm_accounts | Source `account_status` didn't match a known value |
| `is_lead_source_missing` | crm_accounts | Source delivered no `lead_source` |
| `is_campaign_attributed` | marketing_events | `campaign_id` is present - availability only, **not** an attribution model |

## Testing philosophy: WARN vs ERROR

**ERROR** - conditions that make staged data unsafe to use:
- primary key `not_null`/`unique` on every staging model (raw already
  guarantees this via PK constraints, so these should never fire - they are
  a guardrail against a future regression, not a known risk today)
- `accepted_values` on every normalized categorical column (staging
  guarantees `'unknown'` as a safety valve, so this can only fail if the
  normalization CASE statement itself has a bug)
- `valid_timestamp_order` on subscription trial/cancellation dates (should
  never fire given how `scripts/generate_data.py` constructs these fields -
  a violation means the staging SQL or the generator regressed)
- `positive_or_zero` on every monetary/count column
- `valid_currency_code` on every normalized currency column

**WARN** - known, Phase-2-intentional source-quality conditions that
downstream models must explicitly handle, not treat as a build blocker:
- every `relationships` test (`stg_*.{user_id,organization_id,
  subscription_id}` -> parent staging model) - Phase 2 injects orphaned
  foreign keys at ~0.6% per FK column on purpose
- `assert_invoice_paid_at_not_before_issued_at` (singular test) - would be
  unusual but not un-interpretable (e.g. a backdated correction)

This project deliberately does **not** put `accepted_values`/`relationships`
tests on **raw sources** - only on staging output. Raw is allowed to be
messy by design; testing it against a clean vocabulary would either
permanently fail `dbt test` or force weakening the tests. Only structural
guarantees that raw genuinely provides (`not_null`/`unique` on the primary
key, backed by the actual PK constraint) are tested at the source level.

## Custom generic tests (`tests/generic/`)

- `positive_or_zero(column_name)` - fails on negative monetary/count values; NULL passes.
- `valid_currency_code(column_name)` - fails on a normalized currency outside `{USD, EUR, GBP}`.
- `valid_timestamp_order(start_column, end_column)` - fails if `end_column < start_column` when both are present.

No `percentage_between_zero_and_one` test was implemented - Phase 3 has no
percentage-typed columns to apply it to; adding it now would be an unused
abstraction.

## Singular tests (`tests/`)

- `assert_subscription_cancelled_not_before_started.sql` (**ERROR**) - a
  cancelled subscription's `cancelled_at` can never precede its own
  `started_at`. Not an expected corruption category; a regression guard.
- `assert_invoice_paid_at_not_before_issued_at.sql` (**WARN**) - `paid_at`
  before `issued_at` would be unusual but not impossible in a real billing
  system (backdated correction); worth flagging, not worth blocking `dbt build`.

## Model contracts

Not implemented in Phase 3. dbt model contracts require every column's data
type to be pinned in YAML and are strongest when a model is materialized as
a table (contract enforcement happens at `CREATE TABLE`, not `CREATE VIEW`,
for the Postgres adapter). Staging models here are views by design (see
Materializations below) precisely so re-running ingestion/staging never
requires a rebuild, which cuts against contract enforcement's main value.
Contracts are a better fit for the Phase 5 marts, where grain and schema
stability actually need to be guaranteed to consumers (the API, the AI
agent) - revisit there.

## Materializations

All staging models are `view`s (configured once, centrally, in
`dbt_project.yml`'s `models.growth_platform.staging.+materialized: view`) -
they're cheap to recompute on a dev-scale dataset and this guarantees
staging output never goes stale relative to `raw`. Staging is explicitly
never incremental (that's for Phase 6's fact tables, which face real
volume/latency tradeoffs staging doesn't).

## Source freshness

`loaded_at_field: _ingested_at`, `warn_after: 24h`, `error_after: 48h`, set
once at the source level (applies to every table in that source) per
domain's `_<domain>__sources.yml`. `_ingested_at` is used specifically
because it reflects when Phase 2 last wrote the row - the business
timestamps in this synthetic data (`created_at`, `signup_at`, ...)
deliberately span back to 2023 and would make a freshness check against
them meaningless. Re-running `make ingest` at least once every 24h keeps
`dbt source freshness` green in local development.

## dbt-utils

`dbt-labs/dbt_utils` (>=1.3.0, <2.0.0) is pinned in `packages.yml`. It's
available for `generate_surrogate_key()` if/when a later phase needs a
stable hash-based technical key; staging itself doesn't need one yet since
every model already has a genuine source-system primary key. No local
`generate_surrogate_key` macro was written to wrap it - that would just be
indirection with no behavior of its own.

## How to run

```bash
cd dbt
export DBT_POSTGRES_HOST=localhost DBT_POSTGRES_PORT=5433   # host-side, see profiles_example.yml
dbt deps
dbt debug
dbt source freshness
dbt compile
dbt run --select staging
dbt test --select staging
dbt build --select staging
dbt docs generate && dbt docs serve
```

Useful selection examples:

```bash
dbt run --select tag:staging
dbt test --select tag:billing
dbt build --select stg_billing__subscriptions+     # this model and everything downstream
dbt run --select staging.app                       # just the app domain
```

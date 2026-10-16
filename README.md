# ai-growth-data-platform

Production-grade growth data platform with dbt, Python, PostgreSQL, automated data quality, self-service analytics, and an AI-powered data analyst.

A production-style analytics platform simulating the data engineering infrastructure of a
fast-growing AI SaaS company: Python ingestion, a layered dbt warehouse, a self-service analytics
API, and a governed AI data analyst agent.

> **Status:** Phase 1 (Architecture & Infrastructure), Phase 2 (Synthetic Source Systems &
> Ingestion), Phase 3 (dbt Staging Layer), Phase 4 (dbt Intermediate Layer), Phase 5 (Dimensions,
> Facts & Growth/Revenue Marts), Phase 6 (Incremental Processing & SCD Type 2 Snapshots), Phase 7
> (Data Quality & Observability), and Phase 8 (Self-Service Analytics API) complete. See
> [docs/architecture.md](docs/architecture.md) for the full design,
> [docs/ingestion.md](docs/ingestion.md) for source systems and ingestion, and
> [docs/dbt_staging.md](docs/dbt_staging.md) / [docs/dbt_intermediate.md](docs/dbt_intermediate.md) /
> [docs/dbt_marts.md](docs/dbt_marts.md) / [docs/incremental_processing.md](docs/incremental_processing.md) /
> [docs/data_quality_observability.md](docs/data_quality_observability.md) /
> [docs/analytics_api.md](docs/analytics_api.md) / [docs/business_metrics.md](docs/business_metrics.md) /
> [docs/data_dictionary.md](docs/data_dictionary.md) for the full layer-by-layer detail.
> **Not yet implemented:** the AI Data Analyst Agent, natural-language-to-SQL, and the full SQL
> security/validation layer - do not assume these exist. The complete README (business problem,
> security, results) is written incrementally as later phases land.

## Quickstart

```bash
cp .env.example .env
docker compose up -d
curl http://localhost:8000/health

pip install -e ".[dev]"
python scripts/generate_data.py --scale small --seed 42   # or: make generate-data
export HOST_DATABASE_URL=postgresql://analytics:change_me_locally@localhost:5433/growth_platform
python -m ingestion.pipeline --source all                 # or: make ingest

cd dbt && dbt deps && dbt debug && dbt build --select staging+
dbt build --select tag:dimensions tag:facts tag:growth tag:revenue   # or: make dbt-build-marts
```

## dbt Transformation Layer

`raw.*` (Phase 2, intentionally messy) -> dbt `source()` (with freshness checks) -> `stg_<domain>__<entity>`
views in the `staging` schema (Phase 3, clean and tested). 8 source systems across 6 domains
(app, billing, product, api, crm, marketing), each standardizing email/country/currency/categorical
values, deterministically deduplicating by business key, and safely casting types (`NULL` on parse
failure, never `0`). Tests follow a deliberate **WARN vs ERROR** policy: primary-key/schema-shape
violations are ERROR (should never happen); Phase 2's intentionally-injected orphaned foreign keys
are WARN (expected, and downstream models must handle them explicitly). Full design, macro
reference, and the WARN/ERROR rationale: [docs/dbt_staging.md](docs/dbt_staging.md); per-model grain
and transformations: [docs/data_dictionary.md](docs/data_dictionary.md).

```bash
make dbt-debug            # validate connection
make dbt-freshness        # dbt source freshness
make dbt-run-staging      # dbt run --select staging
make dbt-test-staging     # dbt test --select staging
make dbt-build-staging    # run + test together
make dbt-docs             # dbt docs generate && serve  (alias: make docs)
```

### dbt Intermediate Layer (Phase 4)

`stg_*` -> 9 `int_*` models in the `intermediate` schema: subscription history, acquisition
attribution, revenue facts, daily product/API usage, a subscription-milestone timeline, customer
activity, conversion-funnel milestones, and customer lifecycle. No final KPIs yet (MRR, churn,
conversion rate are Phase 5) - this layer is about reusable, join-safe business entities with
explicit "customer identity" (user vs. organization) semantics and verified no-row-multiplication
guarantees. Full design: [docs/dbt_intermediate.md](docs/dbt_intermediate.md).

```bash
make dbt-run-intermediate      # dbt run --select intermediate
make dbt-test-intermediate     # dbt test --select intermediate
make dbt-build-intermediate    # run + test together
```

### Dimensions, Facts & Growth/Revenue Marts (Phase 5)

`int_*` -> 4 dimensions (`dim_users`, `dim_organizations`, `dim_plans`, `dim_date`) + 4 facts
(`fact_subscriptions`, `fact_invoices`, `fact_product_usage`, `fact_api_usage`) + 13 business
marts in the `analytics` schema: 6 Growth marts (daily growth, trial conversion, acquisition
channels, retention cohorts, feature adoption, enterprise growth) and 7 Revenue marts (MRR, ARR,
customer churn, revenue churn, customer LTV, ARPU, API usage).

**MRR/ARR/ARPU are point-in-time snapshots (as of today), not historical time series** - this
source has no subscription-status-change history to reconstruct one from (that's what Phase 6
snapshots are for). Customer churn and revenue churn ARE computed from genuine history
(`started_at`/`cancelled_at` are real historical timestamps), over a fixed trailing-30-day window.
"Customer" means organization throughout revenue/usage/churn/LTV; "user" is used only where the
question is genuinely person-level (signups, trials, acquisition, adoption). Full rationale,
every formula, and verified example figures from the dev dataset: [docs/dbt_marts.md](docs/dbt_marts.md)
and [docs/business_metrics.md](docs/business_metrics.md).

```bash
make dbt-run-marts      # dbt run --select tag:dimensions tag:facts tag:growth tag:revenue
make dbt-test-marts     # dbt test --select tag:dimensions tag:facts tag:growth tag:revenue
make dbt-build-marts    # run + test together
```

### Incremental Processing & SCD Type 2 Snapshots (Phase 6)

`fact_product_usage`, `fact_api_usage`, and `fact_invoices` are now `materialized='incremental'`
(delete+insert, a configurable `incremental_lookback_days` window - default 3 days - filtered on
`_ingested_at`, **never** business event time). Late arrivals landing in an already-aggregated
date correctly update that date's totals instead of duplicating them. `fact_subscriptions` stays
full-refresh on purpose - `raw.subscriptions` is current-state-only, with no event stream to
process incrementally; that's exactly what the new `snap_subscriptions`/`snap_crm_accounts` dbt
snapshots solve instead, giving SCD Type 2 history (`dbt_valid_from`/`dbt_valid_to`) for
subscription status/plan and CRM account status changes **from this point forward** - a snapshot
cannot recover a transition that happened before its first run; this is documented, not glossed
over. Full strategy, a real NULL-unique-key bug this phase found and fixed, and the documented
snapshot limitations: [docs/incremental_processing.md](docs/incremental_processing.md).

```bash
make dbt-run-incremental            # normal incremental run of the 3 converted facts
make dbt-full-refresh-incremental   # controlled full-refresh recovery
make dbt-snapshot                   # run both SCD Type 2 snapshots
make dbt-test-snapshots             # test both snapshots
```

### Data Quality & Observability (Phase 7)

`scripts/run_quality_checks.py` runs a 16-check catalog (structural, referential, business-rule,
freshness, snapshot, volume-anomaly) against the live database, persisting every run and every
individual check's outcome to `monitoring.quality_runs`/`monitoring.data_quality_results` - never
silently dropping a failing check, never fabricating a historical baseline on the first run.
Extends (never replaces) Phase 3's WARN-vs-ERROR policy for intentional synthetic-data anomalies:
the 9 pre-existing dbt WARNs remain exactly as visible as before. Exit codes: `0` (PASS/WARN -
checks ran, possibly found something worth a look), `1` (FAIL - a critical rule was violated),
`2` (the runner itself couldn't execute, e.g. no DB connection). Full catalog, severity rationale,
and diagnostic SQL: [docs/data_quality_observability.md](docs/data_quality_observability.md).

```bash
make quality          # run every check
make quality-latest   # show the most recently persisted run, without re-running
make quality-json     # machine-readable JSON output
make test-quality     # run the quality-framework's own test suite
```

### Self-Service Analytics API (Phase 8)

A read-only FastAPI service over the `analytics.*`/`monitoring.*` layer: 17 endpoints across
growth, revenue, product usage, customers, and data-quality summaries (`/api/v1/analytics/growth/*`,
`/api/v1/analytics/revenue/*`, `/api/v1/analytics/usage/*`, `/api/v1/customers*`,
`/api/v1/quality/*`), plus `/health` (pure process liveness) and `/ready` (DB + required analytics
objects reachable). Every query is a fixed, parameterized SQL string - no client-supplied SQL or
table names. Currency is never mixed across rows (every revenue response is per-currency, using
`Decimal` throughout - never `float`); `customer_id` is always `organization_id`, matching the
"customer = organization" convention from Phase 4/5. MRR/ARR/ARPU are documented as point-in-time
snapshots, inherited unchanged from the marts underneath. Full endpoint inventory, example
requests/responses, and validation rules: [docs/analytics_api.md](docs/analytics_api.md).

```bash
docker compose up -d
curl http://localhost:8000/health
curl http://localhost:8000/ready
open http://localhost:8000/docs                          # interactive OpenAPI UI
pytest tests/unit/test_api_unit.py tests/integration/test_api_integration.py -v
```

## Repository structure

See [docs/architecture.md](docs/architecture.md) for the target end-state structure and the
schema layout (`raw` / `staging` / `intermediate` / `analytics` / `monitoring`),
[docs/ingestion.md](docs/ingestion.md) for the `scripts/generate_data.py` and `ingestion/`
package structure, and [docs/dbt_staging.md](docs/dbt_staging.md) for the `dbt/` project structure.

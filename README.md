# ai-growth-data-platform

Production-grade growth data platform with dbt, Python, PostgreSQL, automated data quality, self-service analytics, and an AI-powered data analyst.

A production-style analytics platform simulating the data engineering infrastructure of a
fast-growing AI SaaS company: Python ingestion, a layered dbt warehouse, a self-service analytics
API, and a governed AI data analyst agent.

> **Status:** All 12 planned phases complete - Architecture & Infrastructure; Synthetic Source
> Systems & Ingestion; dbt Staging/Intermediate/Marts layers; Incremental Processing & SCD Type 2
> Snapshots; Data Quality & Observability; Self-Service Analytics API; AI Data Analyst Agent;
> Security Hardening & SQL Validation; CI/CD & Automated Testing; and Documentation & Performance
> (this phase). See [docs/architecture.md](docs/architecture.md) for the full design,
> [docs/ingestion.md](docs/ingestion.md) for source systems and ingestion,
> [docs/dbt_staging.md](docs/dbt_staging.md) / [docs/dbt_intermediate.md](docs/dbt_intermediate.md) /
> [docs/dbt_marts.md](docs/dbt_marts.md) / [docs/incremental_processing.md](docs/incremental_processing.md) /
> [docs/data_quality_observability.md](docs/data_quality_observability.md) /
> [docs/analytics_api.md](docs/analytics_api.md) / [docs/business_metrics.md](docs/business_metrics.md) /
> [docs/data_dictionary.md](docs/data_dictionary.md) /
> [docs/ai_data_analyst_agent.md](docs/ai_data_analyst_agent.md) /
> [docs/security_and_sql_validation.md](docs/security_and_sql_validation.md) /
> [docs/ci_cd_and_testing.md](docs/ci_cd_and_testing.md) /
> [docs/performance.md](docs/performance.md) for the full layer-by-layer detail.
> **Not yet implemented:** general natural-language-to-SQL, a standalone API authentication/
> authorization layer, and request-size-limiting middleware - the AI Agent remains deliberately
> scoped to a fixed metric catalog with no arbitrary query execution, and the SQL validator
> (`app/security/sql_validator.py`) is a defensive, independently-tested boundary that is **not**
> wired into any endpoint; do not assume unrestricted NL-to-SQL, authenticated endpoints, or a
> bounded request body size exist. See "Known limitations" below for the full, honest list -
> including the performance review's conclusion that no code-level optimization was needed at this
> project's current data scale.

## Prerequisites

- Docker and Docker Compose (v2 - `docker compose`, not the legacy `docker-compose`).
- Python 3.12+ and `pip`.
- [dbt-core](https://docs.getdbt.com/) and `dbt-postgres` (installed via `pip install -e ".[dev]"`
  below - not a separate install).
- Optional: [Ollama](https://ollama.ai) installed locally if you want to exercise the AI Agent with
  real LLM inference rather than its deterministic fallback path (see "AI Data Analyst Agent"
  below) - nothing else in this README requires it.

## Environment configuration: `.env.example` vs. `.env`

`.env.example` is committed and contains **placeholders only** (e.g. `change_me_locally`) - never a
real credential. `.env` is your own local copy (gitignored, never committed) that `docker-compose`,
`dbt`, and the ingestion/quality scripts all read from. Create it once with `cp .env.example .env`;
the defaults work as-is for local development. See
[security_and_sql_validation.md](docs/security_and_sql_validation.md) §6 for what each credential-
shaped variable is actually used for and why none of the defaults are real secrets.

## Quickstart

```bash
cp .env.example .env
docker compose up -d
curl http://localhost:8000/health
curl http://localhost:8000/ready    # 503 until the steps below have populated real data - expected

pip install -e ".[dev]"
python scripts/generate_data.py --scale small --seed 42   # or: make generate-data
export HOST_DATABASE_URL=postgresql://analytics:change_me_locally@localhost:5433/growth_platform
python -m ingestion.pipeline --source all                 # or: make ingest

# dbt runs on the HOST, not inside a container, and needs its own profile -
# see dbt/profiles_example.yml's header comment for the full explanation.
mkdir -p ~/.dbt && cp dbt/profiles_example.yml ~/.dbt/profiles.yml
export DBT_POSTGRES_HOST=localhost DBT_POSTGRES_PORT=5433
export POSTGRES_USER=analytics POSTGRES_PASSWORD=change_me_locally POSTGRES_DB=growth_platform

cd dbt && dbt deps && dbt debug && dbt build   # full build: staging -> intermediate -> marts -> snapshots
cd ..

python scripts/run_quality_checks.py           # or: make quality
curl http://localhost:8000/ready               # now 200 - the analytics objects it checks exist
```

If port `5433` is already taken on your machine (common - many dev machines run a native Postgres
on `5432`, which `docker-compose.yml` already avoids by mapping to `5433` instead), override
`POSTGRES_HOST_PORT` in `.env` and update the two `5433`s above to match - see
`.env.example`'s own comments for the full host-port story.

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

### AI Data Analyst Agent (Phase 9)

A constrained, trustworthy natural-language interface onto Phase 8's analytics API - not a general
chatbot and not text-to-SQL. Every question is interpreted into a typed plan, validated against a
fixed, already-documented metric catalog (`app/agent/catalog.py`), executed through exactly one
read-only tool that reuses the existing `app/services/*` functions (no SQL of its own, no write
path), and answered strictly from the data that tool returned. **Ollama (local-only) is the default
LLM provider** - the platform and every non-agent endpoint run with zero commercial API credentials;
OpenAI/Anthropic are supported but strictly opt-in and are never called as an implicit fallback. If
the configured provider is unavailable, the agent degrades to a deterministic rule-based planner and
a template-based answer writer rather than failing outright. Full design, the catalog, failure-mode
table, and a known environment limitation around local-model inference latency, reported honestly
rather than glossed over: [docs/ai_data_analyst_agent.md](docs/ai_data_analyst_agent.md).

```bash
ollama pull qwen3:8b                                     # or any model; set OLLAMA_MODEL to match
docker compose up -d
curl -X POST http://localhost:8000/api/v1/agent/ask -H "Content-Type: application/json" \
     -d '{"question": "What is our current MRR?"}'
curl http://localhost:8000/api/v1/agent/health
pytest tests/unit/test_agent_providers.py tests/unit/test_agent_planning.py \
       tests/unit/test_agent_execution.py tests/unit/test_agent_api.py \
       tests/unit/test_agent_audit.py -v
```

### Security Hardening & SQL Validation (Phase 10)

An audit-and-harden pass over Phases 8-9, not a rebuild: a line-by-line SQL-injection audit of
every `app/services/*`/`app/agent/tools.py` query found no vulnerability (bound parameters
throughout), proven empirically with live injection-attempt tests. What actually changed: every
analytics/agent read now goes through a **database-enforced read-only transaction**
(`app/db/session.py`'s `read_only_engine`, `postgresql_readonly=True`), backed by a new
least-privilege `analytics_ro` Postgres role (`SELECT` on `analytics`/`monitoring` only, plus the
one documented exception - `INSERT` on `monitoring.ai_query_logs` for the AI agent's own audit
log); the agent's `AnalyticalPlan` now rejects unexpected fields and oversized/control-character
filter values; LLM-generated answers are rejected (falling back to the deterministic template) if
they mention a currency not actually present in the tool's returned data; and a standalone,
**unwired** defensive SQL-validation module (`app/security/sql_validator.py`, structured parsing
via `sqlglot`, never regex) was added for a possible future controlled-SQL feature - nothing in
this platform executes client- or model-supplied SQL today. Full verified-controls list, the SQL
validator's documented limitations, and the honestly-reported known gaps (no request-body-size
limit, no authentication layer):
[docs/security_and_sql_validation.md](docs/security_and_sql_validation.md).

```bash
docker compose up -d
pytest tests/unit/test_security_sql_validator.py tests/unit/test_agent_security.py -v
pytest tests/integration/test_security_db_integration.py \
       tests/integration/test_security_sql_injection.py -v
```

### CI/CD & Automated Testing (Phase 11)

Two GitHub Actions workflows (`.github/workflows/ci.yml`, `.github/workflows/security.yml`), on
every pull request, every push to `main`, and manual dispatch: lint (`ruff check .` + import/
OpenAPI-schema/YAML-config checks), unit tests (mocked providers, no database, no live LLM), a
Docker Compose config-validation + image-build + liveness smoke test, a `dbt build` +
PostgreSQL-integration job against a disposable, freshly-provisioned database (real
`analytics_ro` least-privilege role, real SQL-injection-resistance tests, real audit-log writes -
never a superuser standing in for the real role separation), and a separate security workflow
(dependency-vulnerability scanning via `pip-audit`, offline git-history secret scanning, and
config-safety checks). The established dbt quality baseline (`PASS=247 WARN=9 ERROR=0 TOTAL=256`)
is reproduced from a completely fresh database and compared explicitly - a genuine `ERROR` always
fails the build; a baseline drift is surfaced as a reviewable warning, never silently absorbed.
Nothing in the mandatory pipeline depends on live Ollama inference or a commercial LLM API being
reachable. Full workflow structure, local-reproduction commands, and known limitations (no
real-Ollama CI job; no remote-run badge until an actual workflow has executed on GitHub):
[docs/ci_cd_and_testing.md](docs/ci_cd_and_testing.md).

```bash
ruff check .
pytest tests/unit -v
docker compose config --quiet && docker compose build analytics-api
python scripts/scan_for_secrets.py
```

### Documentation and Performance (Phase 12)

A documentation audit and an evidence-based performance review, closing out the project.
Documentation was corrected where it had drifted from the actual code (stale `db/session.py`
description, a missing dbt-profile setup step in this very README's Quickstart, an outdated
"out of scope" claim about the AI Agent). Performance was investigated with `EXPLAIN ANALYZE` on
real queries, a new repeatable benchmark script, and direct code inspection of every candidate area
the phase called out (indexes, SQLAlchemy session usage, FastAPI sync/async routes, agent
catalog/provider construction, dbt/ingestion throughput) - **no code-level optimization was
needed**: every area was already correct by design or already fast at this project's data scale,
each with recorded evidence rather than a guess. Full investigation and the one real (non-
performance) fix it surfaced along the way: [docs/performance.md](docs/performance.md).

```bash
python scripts/benchmark.py              # live API/agent latency, against a running stack
python scripts/benchmark.py --json       # machine-readable output
```

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `docker compose up -d` containers unhealthy | Check `docker compose logs postgres` / `docker compose logs analytics-api`. A common cause is port `5433` already in use - see "Prerequisites"/`.env.example`'s `POSTGRES_HOST_PORT` override. |
| `dbt debug`/`dbt build` fails to connect | You likely skipped copying `dbt/profiles_example.yml` to `~/.dbt/profiles.yml`, or didn't export `DBT_POSTGRES_HOST`/`DBT_POSTGRES_PORT`/`POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` first - see the corrected Quickstart above. |
| `GET /ready` returns 503 | Expected until you've run the full data pipeline (generate → ingest → `dbt build`) at least once - `/ready` checks that real analytics objects exist, not just that Postgres is reachable. `GET /health` (pure liveness) should still return 200 at this point. |
| `scripts/run_quality_checks.py` reports `required_relations_exist` FAIL | A model your dbt run didn't include is missing - run a full `cd dbt && dbt build` (no `--select`) rather than one of the `make dbt-build-*` partial targets, then re-run the check. |
| `scripts/run_quality_checks.py` reports `raw_source_freshness` FAIL | Your ingested data is simply old relative to the freshness check's time window - re-run `generate_data.py` + the ingestion pipeline to refresh it; this is expected behavior, not a bug. |
| AI Agent `/api/v1/agent/ask` always uses the deterministic fallback, never real inference | `LLM_PROVIDER=ollama` but Ollama isn't reachable from wherever the API process is running - see [ai_data_analyst_agent.md](docs/ai_data_analyst_agent.md) "Docker networking" for the `host.docker.internal` vs `localhost` distinction between running the API in Docker vs. directly on the host. |
| `pytest tests/integration` all skip | No live Postgres reachable at `HOST_DATABASE_URL` - this is an honest `SKIP`, not a failure; start `docker compose up -d` (or a disposable test database - see [ci_cd_and_testing.md](docs/ci_cd_and_testing.md)) first. |

## Known limitations

- No authentication/authorization layer on any endpoint - appropriate for this project's local-dev
  scope; see [analytics_api.md](docs/analytics_api.md) "Known limitations."
- No request-body-size-limiting middleware - see [security_and_sql_validation.md](docs/security_and_sql_validation.md) "Known limitations."
- `app/security/sql_validator.py` is a standalone, unwired defensive module - no endpoint executes
  client- or model-supplied SQL today; do not treat its existence as an active runtime control.
- No real-Ollama GitHub Actions job - the mandatory CI pipeline never depends on live LLM inference
  being reachable; see [ci_cd_and_testing.md](docs/ci_cd_and_testing.md) "AI Agent tests in CI."
- No response caching on any analytics endpoint - not currently a measured performance problem at
  this project's data scale; see [performance.md](docs/performance.md).
- This is a learning/portfolio project simulating a company's data stack with synthetic data - it
  has not been load-tested, has no multi-tenant isolation, and is not intended for production
  deployment as-is.

## Repository structure

See [docs/architecture.md](docs/architecture.md) for the target end-state structure and the
schema layout (`raw` / `staging` / `intermediate` / `analytics` / `monitoring`),
[docs/ingestion.md](docs/ingestion.md) for the `scripts/generate_data.py` and `ingestion/`
package structure, and [docs/dbt_staging.md](docs/dbt_staging.md) for the `dbt/` project structure.

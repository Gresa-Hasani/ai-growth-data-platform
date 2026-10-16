# ai-growth-data-platform

Production-grade growth data platform with dbt, Python, PostgreSQL, automated data quality, self-service analytics, and an AI-powered data analyst.

A production-style analytics platform simulating the data engineering infrastructure of a
fast-growing AI SaaS company: Python ingestion, a layered dbt warehouse, a self-service analytics
API, and a governed AI data analyst agent.

> **Status:** Phase 1 (Architecture & Infrastructure), Phase 2 (Synthetic Source Systems &
> Ingestion), Phase 3 (dbt Staging Layer), and Phase 4 (dbt Intermediate Layer) complete. See
> [docs/architecture.md](docs/architecture.md) for the full design, [docs/ingestion.md](docs/ingestion.md)
> for source systems and ingestion, and [docs/dbt_staging.md](docs/dbt_staging.md) /
> [docs/dbt_intermediate.md](docs/dbt_intermediate.md) / [docs/data_dictionary.md](docs/data_dictionary.md)
> for the dbt layers. The complete README (business problem, security, results) is written
> incrementally as later phases land.

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

## Repository structure

See [docs/architecture.md](docs/architecture.md) for the target end-state structure and the
schema layout (`raw` / `staging` / `intermediate` / `analytics` / `monitoring`),
[docs/ingestion.md](docs/ingestion.md) for the `scripts/generate_data.py` and `ingestion/`
package structure, and [docs/dbt_staging.md](docs/dbt_staging.md) for the `dbt/` project structure.

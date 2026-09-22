# ai-growth-data-platform

Production-grade growth data platform with dbt, Python, PostgreSQL, automated data quality, self-service analytics, and an AI-powered data analyst.

A production-style analytics platform simulating the data engineering infrastructure of a
fast-growing AI SaaS company: Python ingestion, a layered dbt warehouse, a self-service analytics
API, and a governed AI data analyst agent.

> **Status:** Phase 1 (Architecture & Infrastructure) and Phase 2 (Synthetic Source Systems &
> Ingestion) complete. See [docs/architecture.md](docs/architecture.md) for the full design and
> [docs/ingestion.md](docs/ingestion.md) for source systems, data generation, and the ingestion
> framework. The complete README (business problem, data model, security, results) is written
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
python -m ingestion.pipeline --report                     # ingestion quality report
```

## Repository structure

See [docs/architecture.md](docs/architecture.md) for the target end-state structure and the
schema layout (`raw` / `staging` / `intermediate` / `analytics` / `monitoring`), and
[docs/ingestion.md](docs/ingestion.md) for the `scripts/generate_data.py` and `ingestion/`
package structure.

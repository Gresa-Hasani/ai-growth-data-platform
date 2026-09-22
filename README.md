# ai-growth-data-platform

Production-grade growth data platform with dbt, Python, PostgreSQL, automated data quality, self-service analytics, and an AI-powered data analyst.

A production-style analytics platform simulating the data engineering infrastructure of a
fast-growing AI SaaS company: Python ingestion, a layered dbt warehouse, a self-service analytics
API, and a governed AI data analyst agent.

> **Status:** Phase 1 (Architecture & Infrastructure) complete. See [docs/architecture.md](docs/architecture.md)
> for the full design. The complete README (business problem, data model, security, results) is
> written incrementally as later phases land.

## Quickstart (Phase 1)

```bash
cp .env.example .env
docker compose up -d
curl http://localhost:8000/health
```

## Repository structure

See [docs/architecture.md](docs/architecture.md) for the target end-state structure and the
schema layout (`raw` / `staging` / `intermediate` / `analytics` / `monitoring`).

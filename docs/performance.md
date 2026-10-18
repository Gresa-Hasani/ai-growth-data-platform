# Performance Investigation (Phase 12)

This document records the evidence-based performance review done in Phase 12. Every claim below
is backed by an actual measurement taken against this project's real (small-scale synthetic) dev
database and the running API/agent stack - none of it is estimated or assumed.

**Headline result: no code-level performance optimization was implemented.** Every area inspected
turned out to already be correct by design, or already fast enough that there was nothing to fix
without inventing a problem. This is reported honestly rather than manufacturing a change to
satisfy the phase - see each section below for the specific evidence.

## Methodology

- `EXPLAIN (ANALYZE, BUFFERS)` on the actual SQL `app/services/*.py` executes (not approximations),
  against the real dev database (`growth_platform`, reachable at `localhost:5435`).
- A new repeatable benchmark script, `scripts/benchmark.py`, timing representative live HTTP
  requests against the running API/agent stack (median/p95/min/max over N reps - see its own
  docstring for usage). It reports timings; it does not assert a pass/fail threshold, since a fixed
  latency threshold would be fragile across machines and Docker/network conditions.
- Direct code inspection of the candidate areas the phase calls out (query construction, session/
  engine usage, dbt materialization config, ingestion's DB-write pattern, FastAPI route signatures,
  agent catalog/provider construction).
- Table/row-count context: this is a "small"-scale synthetic dataset (`--scale small --seed 42`) -
  `dim_organizations` 200 rows, `fact_subscriptions` 1,500, `fact_product_usage` ~9,800,
  `fact_api_usage` ~4,500, `fact_invoices` 5,000, `mart_customer_ltv` ~400. All findings below are
  scoped to this scale; a materially larger dataset could change some of these conclusions, and
  that's noted explicitly where relevant.

## PostgreSQL and analytics query performance

**Finding: no index was added.** `pg_indexes` against the `analytics` schema shows zero
non-default indexes before this investigation. `EXPLAIN ANALYZE` on the three queries judged most
representative of real filter patterns - all already parameterized, bound-value queries from
`app/services/*.py`, not hypothetical ones:

| Query (actual service-layer SQL) | Plan chosen | Execution time |
|---|---|---|
| `fact_product_usage` filtered by `organization_id` + 30-day date range, ordered, paginated | Sequential scan + sort | 17.8 ms |
| `fact_subscriptions` active-MRR-by-currency for one `organization_key` | Sequential scan + group aggregate | 3.0 ms |
| `mart_api_usage` LEFT JOIN `dim_organizations`, filtered by `organization_id` + date range | Nested loop, both sides sequential-scanned | 0.9 ms |

PostgreSQL's planner chose a sequential scan in every case - correctly: at a few thousand rows per
table, a full scan is cheaper than the random I/O of an index lookup would be, and the planner's
own cost estimates confirm this (`cost=0.00..1315.90` for the largest table, finishing in
single-digit-to-low-double-digit milliseconds either way). **Adding an index here would be the
exact "blind index creation" the phase explicitly warns against** - real write overhead on every
dbt rebuild, for zero measured read benefit at this scale. No index was added.

This conclusion is scale-dependent and stated as such: if `fact_product_usage` grew to millions of
rows, a composite index on `(organization_id, date_day)` would likely become worth its write cost.
That is not evidenca this dataset currently provides.

**Phase 10's read-only controls were not touched.** This investigation used the existing
`analytics` (admin) and `analytics_ro` (read-only) roles exactly as they already exist - no new
grant, no new connection, no permission change of any kind. See
[security_and_sql_validation.md](security_and_sql_validation.md) for that design; this phase did
not modify it.

## FastAPI and AI Agent performance

**`scripts/benchmark.py` results** (20 reps each, against the live local stack; `used_real_inference:
false` for every `/agent/ask` sample - Ollama was unreachable from the container in this
environment, the same documented condition as Phases 9-11, so this measures the deterministic
fallback path, never real LLM latency):

| Endpoint | Median | p95 |
|---|---|---|
| `GET /health` | 6.6-10.9 ms | 25-29 ms |
| `GET /ready` | 26-29 ms | 36-37 ms |
| `GET /api/v1/analytics/revenue/mrr` | 15-16 ms | 32 ms |
| `GET /api/v1/analytics/growth/daily` | 10-20 ms | 31-35 ms |
| `GET /api/v1/customers?limit=50` | 12-20 ms | 33-37 ms |
| `GET /api/v1/quality/latest` | 20-25 ms | 33-59 ms |
| `GET /api/v1/agent/health` | 17-18 ms | 35-37 ms |
| `POST /api/v1/agent/ask` (deterministic) | 25-33 ms | see below |

One run showed a single outlier pair (~1.2s) on `/agent/ask` out of 20 samples. A follow-up,
dedicated 30-sample run targeting only that endpoint found every sample between 21 ms and 78 ms -
**the outlier did not reproduce**. Per the phase's own instruction ("if a suspected bottleneck
cannot be reproduced, document the investigation and avoid unnecessary changes"), this is recorded
as an unreproduced anomaly (most likely a one-off Docker-networking or connection-pool cold-start
blip), not a defect, and no change was made chasing it.

**Code-level findings, investigated and confirmed already correct - no change needed:**

- **Metric-catalog processing is not repeated per request.** `app/agent/planner.py`'s
  `SYSTEM_PROMPT` (which embeds `catalog_summary_for_prompt()`'s output) is a **module-level
  constant**, computed exactly once at import time - not on every `ask()` call. The phase's
  candidate concern ("redundant metric-catalog processing") does not apply; there was nothing to
  cache that wasn't already effectively cached by normal Python import semantics.
- **No blocking-event-loop risk.** Every FastAPI route in `app/api/v1/*.py` is a plain `def`, not
  `async def` - confirmed by inspection of all 20 route handlers. FastAPI/Starlette automatically
  runs synchronous route handlers in a thread pool, which is the correct and idiomatic way to use a
  synchronous driver (`psycopg2`) from FastAPI - there is no event-loop-blocking defect to fix.
- **Ingestion already batches writes.** `ingestion/db.py`'s own docstring and implementation use
  psycopg2's `execute_values` for batched upserts, not row-by-row Python-level inserts - the
  "excessive Python-level processing" concern the phase calls out does not apply here either.
- **Provider/settings construction is cheap and not re-fetched per field access.**
  `AgentSettings()` is a module-level singleton (`app/agent/config.py`); `build_provider(settings)`
  constructs a lightweight object (stores config values; no network I/O, no connection pool setup)
  - there is no expensive provider-initialization cost being incurred repeatedly.

**Preserved, unchanged, as required:** the fixed metric catalog, typed read-only tools, evidence-
grounded answers, currency separation, point-in-time disclosures, the deterministic fallback path,
audit privacy/failure policy, and provider opt-in/no-silent-fallback semantics - none of this
investigation touched `app/agent/*` logic.

## dbt and data pipeline performance

- Full `dbt build` (fresh, disposable database, `--scale small --seed 42`): **~26 seconds**
  (3 incremental models, 2 snapshots, 18 table models, 216 data tests, 17 view models).
- Ingestion (`python -m ingestion.pipeline --source all`, same dataset): **~29 seconds**.
- `scripts/run_quality_checks.py`'s full 16-check catalog against the live dev database:
  **~3.8 seconds** (dominated by the three `orphaned_*` referential checks' own full-table scans,
  each 100-700ms - still well within what a manual/CI invocation needs).

None of these were judged to need an optimization: a sub-30-second full pipeline rebuild and a
sub-4-second quality run are not the "repeated full scans" or "excessive intermediate relations"
failure modes the phase calls out - they are what a correctly-scoped incremental+snapshot pipeline
at this data volume should look like. Incremental correctness, unique-key/deduplication semantics,
SCD Type 2 historical correctness, and currency separation were all re-verified as unchanged (see
"Regression results" below) - no model logic was changed to chase a performance number.

**One real (non-performance) finding, fixed**: while investigating, `scripts/run_quality_checks.py`
reported `required_relations_exist` as `FAIL` against the long-lived dev database - the
intermediate model `int_customer_revenue` had no view in `intermediate.int_customer_revenue`. This
was environment drift, not a code bug: this project's various `make dbt-*` targets each build a
specific `--select` subset, and the dev database had apparently never had an unrestricted `dbt
build` run against it. Running one (`cd dbt && dbt build`, no `--select`) built the missing view
and restored `required_relations_exist` to `PASS`, with the dbt test suite still reporting the
exact established baseline (`PASS=247 WARN=9 ERROR=0 TOTAL=256`) - see "Regression results" below.
No model file was edited to produce this fix.

A second, still-failing check (`raw_source_freshness`, 8/8 stale) was investigated and found to be
purely a function of wall-clock time: this dev database's synthetic data was generated and ingested
during earlier phases of this same multi-day project, and the freshness check's threshold is
time-based (hours since `_ingested_at`). Re-running `generate_data`/`ingestion` would clear it, but
doing so wasn't necessary for this phase and wasn't performed, to avoid an unrelated, unrequested
change to the dev database's data. This is noted here as a known, benign, pre-existing condition of
this long-lived local environment - not a Phase 12 regression and not a performance issue.

## Docker and startup

Healthcheck intervals (`docker-compose.yml`: Postgres 5s/5s/10 retries, API 10s/5s/5 retries + 10s
start period) were reviewed and left unchanged - they are already short enough that `docker compose
up -d` reaching "healthy" takes a few seconds in practice (observed directly in Phase 10/11 work),
not the "excessive" case the phase warns about. No change made.

## Benchmark script

`scripts/benchmark.py` (new in this phase) is the tool used to produce the HTTP-latency numbers
above. It is also usable standalone:

```bash
python scripts/benchmark.py                      # default: localhost:8000, 20 reps per endpoint
python scripts/benchmark.py --reps 50 --json      # more samples, machine-readable output
python scripts/benchmark.py --skip-agent          # skip the agent endpoint (faster, no provider dependency)
```

It reports median/p95/min/max per endpoint and deliberately does **not** assert a pass/fail
threshold - per the phase's own caution against fragile absolute latency thresholds in ordinary
tests, this is a comparison tool for a developer to run before/after a change, not a CI gate.

## Regression results (after the one dev-database fix above)

```
dbt build  ->  Done. PASS=247 WARN=9 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=256   (matches baseline exactly)
```

See the main [final report](../README.md) for the full `pytest`/`ruff`/Docker verification run for
this phase - this document covers performance evidence specifically.

## Summary

| Area | Investigated? | Change made? | Evidence |
|---|---|---|---|
| Postgres query plans/indexes | Yes | No | `EXPLAIN ANALYZE`, 3 representative queries |
| SQLAlchemy session/engine usage | Yes | No | Code inspection (Phase 10's design already correct) |
| FastAPI route sync/async | Yes | No | All 20 routes confirmed `def`, correctly threadpooled |
| Agent catalog/provider construction | Yes | No | Confirmed already module-level/one-time |
| API endpoint latency | Yes | No | `scripts/benchmark.py`, 20-50 reps per endpoint |
| dbt build/ingestion/quality-check duration | Yes | No | Timed directly; all well within acceptable range |
| Docker healthcheck timing | Yes | No | Reviewed, already reasonable |
| Dev-database drift (`int_customer_revenue` missing) | Yes | **Yes - ran a full `dbt build`** | Not a performance change; restored a missing view, baseline unaffected |

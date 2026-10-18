# Self-Service Analytics API (Phase 8)

A read-only FastAPI service over the Phase 5-7 analytics layer (`analytics.*` marts/facts/dimensions
and `monitoring.*` quality tables). No endpoint can mutate data, execute arbitrary SQL, or accept a
client-supplied table/query fragment.

## Architecture

```
app/
  main.py              FastAPI app: route registration, /health, /ready
  config.py             ApiSettings (pagination/date-window defaults) + SUPPORTED_CURRENCIES
  db/session.py          SQLAlchemy engine/session. As of Phase 10, every route below reads
                          through get_read_only_db() (a database-enforced read-only transaction,
                          see docs/security_and_sql_validation.md), not the plain get_db() this
                          doc originally described - that engine still exists, used only by the
                          AI Agent's own audit-log write (Phase 9/10) and by tests.
  api/v1/                 Routes: HTTP handling, query params, status codes, response models
    growth.py, revenue.py, usage.py, customers.py, quality.py
  schemas/                 Pydantic request/response models (one module per domain)
  services/                 Query + light shaping logic (one module per domain)
    _shared.py                date-range/currency/pagination validation, reused by every service
```

**Repository and service layers are deliberately merged into one `services/` module per domain**,
not split further - each query maps 1:1 onto a single mart/fact with no additional business logic
to separate out (the aggregation already happened in dbt). Introducing a distinct repository layer
on top would add indirection without behavior; this is a conscious simplification, not an omission
(see Phase 8 spec: "avoid an overengineered architecture for a single API").

Every query is a fixed, explicit-column SQL string executed via SQLAlchemy's `text()` with bound
parameters (`session.execute(text(sql), {"param": value})`) - filter **values** are always bound
parameters; table and column names are always hardcoded in the query string, never built from
request input. No endpoint accepts a table name, column name, or raw SQL fragment from the client.

## Local startup

```bash
cp .env.example .env
docker compose up -d                      # postgres + analytics-api
curl http://localhost:8000/health
curl http://localhost:8000/ready
open http://localhost:8000/docs           # interactive OpenAPI UI
```

Or locally outside Docker (same pattern as dbt/ingestion - see docs/ingestion.md):

```bash
export DATABASE_URL="$HOST_DATABASE_URL"   # host-reachable port, see .env
uvicorn app.main:app --reload --port 8000
```

## Environment variables

Reuses the existing `DATABASE_URL`/`HOST_DATABASE_URL` convention (app/db/session.py, unchanged).
New in this phase, all optional with documented defaults, `API_`-prefixed (see `app/config.py`):

| Variable | Default | Meaning |
|---|---|---|
| `API_DEFAULT_PAGE_SIZE` | 50 | Page size when `limit` is omitted |
| `API_MAX_PAGE_SIZE` | 200 | Hard cap on `limit` |
| `API_DEFAULT_DATE_WINDOW_DAYS` | 30 | Trailing window when no date filter is given |
| `API_MAX_DATE_WINDOW_DAYS` | 366 | Hard cap on a requested date range |

No secrets; nothing added to `.env.example` beyond documentation, since all of the above have safe
built-in defaults.

**Phase 10 addition**: every free-text filter/path parameter that previously had no length bound
(`acquisition_channel`, `event_name`, `organization_id`, `organization_type`, `customer_id`,
`run_id`) now has one (100-200 characters, enforced by FastAPI's `Query`/`Path` validation, 422 on
violation) - defense-in-depth against pathological input, not an injection fix (every one of these
values was already safely bound-parameterized; see
[security_and_sql_validation.md](security_and_sql_validation.md) §1).

## Health vs. readiness

- **`GET /health`** - pure process liveness. Returns `{"status": "ok"}` unconditionally; never
  queries the database. Answers "is the process up."
- **`GET /ready`** - dependency readiness. Confirms the database is reachable *and* every analytics
  object the API actually queries exists (`to_regclass()` check against 9 key tables/views across
  `analytics`/`monitoring`). Returns 503 with a safe, non-leaking error message if either check
  fails. Answers "can this instance actually serve traffic."

`docker-compose.yml`'s `analytics-api` healthcheck was updated to call `/ready` (previously
`/health`, which did the DB check itself in Phase 1) - this preserves the container's original
healthcheck behavior (unhealthy if the DB is down) under the semantically correct endpoint name.

## Endpoint inventory

All under `/api/v1` except health/readiness.

### Growth
| Endpoint | Grain | Notable filters |
|---|---|---|
| `GET /analytics/growth/daily` | date_day | `start_date`, `end_date` (default trailing 30d, max 366d) |
| `GET /analytics/growth/acquisition-channels` | (channel, currency) | `acquisition_channel` |
| `GET /analytics/growth/trial-conversion` | user | `is_converted`, `is_observation_complete`, pagination |
| `GET /analytics/growth/retention-cohorts` | (cohort_month, period_index) | `cohort_month` |

### Revenue
| Endpoint | Grain | Notable filters |
|---|---|---|
| `GET /analytics/revenue/mrr` | currency (as-of-today snapshot) | `currency` |
| `GET /analytics/revenue/arr` | currency (as-of-today snapshot) | `currency` |
| `GET /analytics/revenue/churn` | organization_type | `organization_type` |
| `GET /analytics/revenue/arpu` | currency (as-of-today snapshot) | `currency` |
| `GET /analytics/revenue/customer-ltv` | (organization_id, currency) | `currency`, pagination |

### Product usage
| Endpoint | Grain | Notable filters |
|---|---|---|
| `GET /analytics/usage/features` | event_name | `event_name` |
| `GET /analytics/usage/daily` | (date, organization, event_name) | date range, `organization_id`, pagination |
| `GET /analytics/usage/api` | (month, organization) | date range, `organization_id`, pagination |

### Customers
| Endpoint | Grain | Notes |
|---|---|---|
| `GET /customers` | organization | `organization_type`, pagination |
| `GET /customers/{customer_id}` | organization | `customer_id` = `organization_id`; 404 if unknown |

### Data quality (Phase 7)
| Endpoint | Notes |
|---|---|
| `GET /quality/latest` | Most recent run + every check's result; 404 if none persisted yet |
| `GET /quality/runs` | Run history, paginated |
| `GET /quality/runs/{run_id}` | One run's full detail; 404 if unknown |

All three return 503 (not a false-healthy empty result) if `monitoring.quality_runs`/
`data_quality_results` don't exist.

## Example requests

```bash
curl "http://localhost:8000/api/v1/analytics/revenue/mrr"
# [{"as_of_date":"2026-10-16","currency":"EUR","active_subscription_count":235,"mrr":"37991.93..."},
#  {"as_of_date":"2026-10-16","currency":"GBP","active_subscription_count":104,"mrr":"17275.85..."},
#  {"as_of_date":"2026-10-16","currency":"USD","active_subscription_count":736,"mrr":"120841.91..."}]

curl "http://localhost:8000/api/v1/customers?limit=3"
# {"items":[{"organization_id":"org_000001","organization_name":"...","organization_type":"startup",
#            "is_enterprise":false,"country_code":"US","created_date":"2026-01-17"}, ...],
#  "meta":{"limit":3,"offset":0,"total":200}}

curl "http://localhost:8000/api/v1/customers/org_000001"
# {"organization_id":"org_000001", ..., "revenue_by_currency":[
#    {"currency":"USD","active_monthly_recurring_revenue":"14.51","realized_ltv_to_date":"31.34",
#     "estimated_ltv":"22219.58..."}],
#  "usage_last_30_days":{"window_days":30,"product_event_count":10,"api_request_count":9}}

curl "http://localhost:8000/api/v1/analytics/revenue/mrr?currency=JPY"
# HTTP 422: {"detail":"unsupported currency 'JPY' - must be one of USD, EUR, GBP"}

curl "http://localhost:8000/api/v1/customers/org_does_not_exist"
# HTTP 404: {"detail":"customer not found"}
```

## Currency semantics

Every monetary field uses `Decimal` (never `float`) in both the database (`numeric` columns) and
the Pydantic response models - avoiding floating-point rounding drift in financial output. **No
endpoint ever sums `SUM()`s or otherwise combines rows across different currencies** - `/mrr`,
`/arr`, `/arpu`, `/acquisition-channels`, and `/customer-ltv` all return one row per currency, and
`/customer-ltv` explicitly carries `currency` on every record. Verified directly:
`tests/integration/test_api_integration.py::test_mrr_endpoint_keeps_currencies_separate` and
`::test_revenue_endpoints_reject_unsupported_currency`.

**MRR/ARR/ARPU are point-in-time snapshots** (`as_of_date` = today), not historical time series -
inherited unchanged from the underlying marts (see `docs/dbt_marts.md` "mart_mrr / mart_arr"). The
API does not, and cannot, present these as a historically accurate series; every response schema's
field description says so explicitly.

`mart_arpu`'s MRR total is intentionally slightly lower than `mart_mrr`'s for the same currency (a
documented ~$382 USD gap in the dev dataset, traced to active subscriptions whose
`organization_key` doesn't resolve - see `docs/dbt_marts.md` "mart_arpu"). The API preserves this
documented discrepancy rather than papering over it; `/arpu` and `/mrr` are not expected to match.

## Customer identity semantics

**`customer_id` in every `/customers` route is `organization_id`, never `user_id`** - matching the
"customer = organization" convention established in Phase 4/5 (`docs/dbt_marts.md` "Customer
identity"). Growth/trial-conversion endpoints that are genuinely person-level (trial conversion,
acquisition) still expose `user_id` where that's the correct grain - the two are never conflated.

## Validation and error semantics

- Date ranges: `end_date >= start_date`, window `<= API_MAX_DATE_WINDOW_DAYS` - 422 otherwise.
- Currency: must be one of `USD`/`EUR`/`GBP` (case-insensitive) - 422 otherwise.
- Pagination: `1 <= limit <= API_MAX_PAGE_SIZE`, `offset >= 0` - 422 (FastAPI's built-in `Query`
  constraint validation) or a domain-level `HTTPException` for the same bounds, depending on which
  layer catches it first.
- Unknown customer/run: 404.
- Missing monitoring tables: 503.
- Database unreachable (`/ready`): 503.
- **No response ever includes a stack trace, SQL text, or a connection string/credential** -
  verified directly: `tests/integration/test_api_integration.py::test_api_errors_never_leak_connection_details`
  and `tests/unit/test_api_unit.py::test_ready_fails_honestly_when_database_unreachable`.

## Running API tests

```bash
pytest tests/unit/test_api_unit.py tests/integration/test_api_integration.py -v
```

Unit tests (`tests/unit/test_api_unit.py`) cover pure validation logic and the /health-vs-/ready
distinction (using a deliberately broken SQLAlchemy engine, never a real connection) - no live
database required. Integration tests (`tests/integration/test_api_integration.py`) run real
queries against the dev database via FastAPI's `TestClient`, skipping cleanly if Postgres isn't
reachable. Both suites are entirely read-only against the shared dataset.

## A real bug this phase found and fixed

Docker Compose verification (`docker compose up -d --build analytics-api`) surfaced a genuine,
pre-existing latent bug: `app/db/session.py`'s `create_engine(DATABASE_URL, ...)` used a bare
`postgresql://` URL. A fresh `pip install` inside the Docker build resolves a newer SQLAlchemy
whose default dialect for that bare scheme is `psycopg` (v3) - which this project never installs
(only `psycopg2-binary`, used directly elsewhere by `ingestion/db.py` and dbt). The container
crash-looped with `ModuleNotFoundError: No module named 'psycopg'` at import time. Fixed by
rewriting the URL to `postgresql+psycopg2://` inside `app/db/session.py` only - the `DATABASE_URL`/
`HOST_DATABASE_URL` environment variables themselves were left untouched, since `psycopg2.connect()`
(used directly by `ingestion/db.py`) has no `+driver` URL syntax and would break if the suffix were
added there instead. Verified: `docker compose ps` shows both containers healthy, `/health` and
`/ready` return 200, and real analytics endpoints return correct data, all from inside the rebuilt
container.

## Known limitations

- No authentication/authorization layer - every endpoint is open, appropriate for this project's
  local-dev scope; a real deployment would need one before this API left a trusted network.
- No response caching - every request re-queries Postgres. At this dataset's scale (hundreds to
  low thousands of rows per mart) this remains, as of the Phase 12 performance investigation, not a
  measured performance problem (`EXPLAIN ANALYZE` on every representative query completes in
  single-digit milliseconds via sequential scans Postgres itself judged optimal at this table size -
  see [performance.md](performance.md)); no cache was added speculatively.
- `/customers/{customer_id}`'s usage summary is a fixed trailing-30-day window, not configurable
  per-request in this phase.
- General natural-language-to-SQL and the full SQL-validation-as-an-active-control layer remain
  explicitly out of scope (the AI Data Analyst Agent itself shipped in Phase 9 - see
  [ai_data_analyst_agent.md](ai_data_analyst_agent.md) - and reuses this API's own query set
  documented above, nothing dynamic.

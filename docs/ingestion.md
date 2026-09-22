# Ingestion (Phase 2)

Synthetic source-system data generation and a production-style, idempotent
ingestion framework that loads it into the `raw` PostgreSQL schema. Builds on
the architecture in [architecture.md](architecture.md).

## Source systems

| System | File | Format | Table |
|---|---|---|---|
| Application DB | `organizations.csv` | CSV | `raw.organizations` |
| Application DB | `users.csv` | CSV | `raw.users` |
| Billing platform | `subscriptions.csv` | CSV | `raw.subscriptions` |
| Billing platform | `invoices.csv` | CSV | `raw.invoices` |
| Product analytics | `product_events.jsonl` | JSON Lines | `raw.product_events` |
| API gateway | `api_usage.parquet` | Parquet | `raw.api_usage` |
| CRM | `crm_accounts.csv` | CSV | `raw.crm_accounts` |
| Marketing platform | `marketing_events.csv` | CSV | `raw.marketing_events` |

Each file is generated and ingested independently, as if it came from a
genuinely separate operational system - no cross-file joins happen before
`raw`. That's dbt staging/intermediate's job (Phase 3+).

## Generating data

```bash
python scripts/generate_data.py --scale small --seed 42
# or: make generate-data
```

Options: `--scale {small|medium|large}`, `--seed <int>`, `--output-dir <path>`
(default `data/generated`), `--clean` (wipe output-dir first).

### Scale profiles

| Scale | users | organizations | subscriptions | invoices | product_events | api_usage | crm_accounts | marketing_events |
|---|---|---|---|---|---|---|---|---|
| small (default) | 1,000 | 200 | 1,500 | 5,000 | 10,000 | 5,000 | ~1,000 | 2,500 |
| medium | 10,000 | 2,000 | 15,000 | 50,000 | 100,000 | 50,000 | ~10,000 | 25,000 |
| large | 100,000 | 20,000 | 150,000 | 500,000 | 1,000,000 | 500,000 | ~100,000 | 250,000 |

Ratios (organizations = 20% of users, subscriptions = 1.5x users, etc.) are
fixed in `scripts/generate_data.py::RATIO_*` so every scale is internally
consistent, not just `large`. CRM accounts are generated as ~5 lifecycle
"touches" per organization (not per-user), reflecting real CRM cardinality
rather than inventing one CRM record per user.

### Determinism

All randomness is drawn from `random.Random(seed)` / `numpy.random.default_rng(seed)`
/ `Faker(...).seed_instance(seed)` instances created once and threaded through
generation in a fixed call order. The same `--scale`/`--seed` always produces
byte-identical CSV/JSONL/Parquet content (verified in
`tests/unit/test_generate_data.py::test_generation_is_deterministic`). Only
`manifest.json`'s own `generation_timestamp` field differs between runs.

### Business correlations (documented assumptions)

These are generated probabilistically, not hardcoded as final answers:

- **Acquisition channel -> conversion**: `referral`/`partnership` have a
  1.5-1.6x "conversion lift" multiplier vs. `paid_social`'s 0.6x, applied to
  churn probability, so channel-level conversion differences emerge from the
  data rather than being computed directly.
- **Org type -> plan mix**: `enterprise` orgs draw from a plan-weight
  distribution skewed toward `business`/`enterprise` plans; `individual` orgs
  skew toward `free`/`starter`.
- **Org type -> API usage**: enterprise-org users have a 50% chance of an
  extra usage record being drawn from the same organization's user pool,
  producing disproportionately more usage per enterprise org.
- **Account age -> status**: signups under 14 days skew heavily `trial`;
  older accounts resolve into `active`/`churned` based on a channel-adjusted
  churn rate.
- **User status -> product events**: `churned` users get an event window
  capped at 180 days post-signup and a lower relative event weight;
  `active`/`trial` users generate events across their whole observable
  history.

### Intentional data-quality issues

Injected deterministically (seeded), at rates configured in
`scripts/generate_data.py::RATES`, and recorded per-run in
`manifest.json.quality_issues_injected`:

| Issue | Where | Rate |
|---|---|---|
| Duplicate records (same business key twice) | every entity | 0.4% |
| Invalid email format | users | 1.5% |
| Null `plan_name` | subscriptions | 2% |
| Messy country code (`USA`/`us`/`United States`/` US`) | orgs, users | 6% |
| Messy currency casing (`usd`, `eur`) | subscriptions, invoices | 6% |
| Leading/trailing whitespace | org name, email | 3% |
| Mixed casing | org name | 2% |
| Missing optional value (null out an optional field) | various | 5% |
| Orphaned foreign key (points at a nonexistent id) | FK columns | 0.6% |
| Invalid status value (`UNKNOWN_STATUS`) | status columns | 0.3% |
| Missing `campaign_id` | marketing_events | 15% |
| Late-arriving / out-of-order events | product_events | ~5% of rows shuffled out of timestamp order |

Rates are intentionally low enough that most records stay valid and usable.

## Raw table design

Raw tables (`app/db/raw_tables.sql`) preserve source values as-is: every
column is `TEXT`, even amounts and timestamps. No type coercion, casing
normalization, or currency/country cleanup happens here - that's dbt
staging's job (Phase 3). Each table's primary key is the source system's own
business identifier (`organization_id`, `event_id`, `request_id`, ...), which
is what makes upsert-based idempotent loading possible.

Every row also carries ingestion metadata: `_ingested_at`, `_source_file`,
`_run_id`.

## Technical validation boundary (Pydantic)

`ingestion/schemas/*.py` validates only what's needed to safely land a row:

- the row's own business/source identifier is present (non-blank)
- for event-like sources (product_events, api_usage, marketing_events), the
  timestamp field is present and parseable - events have no technical
  identity without one
- for other sources, an optional timestamp field is validated for
  parseability *only if present* (missing is fine; garbage is not)

It deliberately does **not** enforce business semantics: `plan_name=None`,
`country_code="USA"`, `currency="usd"` all pass technical validation and
land in `raw` unchanged. dbt staging normalizes those. This separation keeps
ingestion honest about what "valid" means at this layer.

## Loader architecture

```
ingestion/
  pipeline.py       CLI: orchestrates start_run -> loader.load -> finish_run
  registry.py        SourceSpec per source: file, table, PK, columns, technical model
  audit.py            monitoring.ingestion_runs / monitoring.rejected_records helpers
  db.py                psycopg2 connection (HOST_DATABASE_URL/DATABASE_URL)
  logging_config.py     structlog wiring on top of stdlib logging
  loaders/
    base.py              BaseLoader: validate -> dedup -> upsert -> audit, shared by all formats
    csv_loader.py          chunked pandas.read_csv
    jsonl_loader.py         streamed line-by-line, malformed lines isolated
    parquet_loader.py        pyarrow row-group batches
  schemas/
    <source>.py             one Pydantic technical-validation model per source
```

`BaseLoader` owns everything format-agnostic (batching orchestration,
validation, dedup, upsert, audit, structured logs); each format loader only
implements `iter_batches(path) -> Iterator[list[dict]]`.

## Batching

CSV: `pandas.read_csv(..., chunksize=batch_size)`. JSONL: streamed line by
line, buffered into `batch_size`-row lists. Parquet: `pyarrow`'s
`ParquetFile.iter_batches(batch_size=...)`, which reads row-group-aligned
batches. None of the three loaders materializes the whole file in memory,
so `large` (1M+ row) files stay feasible on a normal dev machine. Configure
with `--batch-size` (default 5,000).

## Idempotency & upsert strategy

Each raw table's primary key is the source system's business identifier.
Loading uses `INSERT ... ON CONFLICT (<pk>) DO UPDATE SET <all columns> =
EXCLUDED.<column>` via `psycopg2.extras.execute_values(..., fetch=True)`,
which also returns `(xmax = 0)` per row so the loader can tell inserts from
updates precisely (not just "row landed").

Running the same file twice: run 1 inserts every valid row; run 2 upserts
the same rows and reports `rows_inserted: 0`, `rows_updated: <same count>`.
No duplicate business keys are ever created - verified end-to-end in
`tests/integration/test_idempotency.py` against a live database.

### Duplicates within a single source file

A single `INSERT ... ON CONFLICT` statement cannot affect the same key
twice (Postgres raises "ON CONFLICT DO UPDATE command cannot affect row a
second time"), so duplicates are resolved **before** the SQL statement is
built, per batch:

- **Mutable entities** (have an `updated_at` field: organizations, users,
  subscriptions, crm_accounts): keep the row with the latest `updated_at`.
- **Immutable events/records** (invoices, product_events, api_usage,
  marketing_events): keep the last occurrence in file order.

The losing row is never silently dropped - it's logged to
`monitoring.rejected_records` with reason `"duplicate business key within
source file"`. Duplicates that span batch boundaries (rare, but possible
with a small `--batch-size`) are still handled correctly: each batch is its
own `ON CONFLICT` statement, so a key seen again in a later batch simply
upserts the earlier batch's row.

## Rejected records

`monitoring.rejected_records` captures anything technically un-ingestible,
distinguished from data that's merely messy (which lands fine and gets
cleaned by dbt staging):

- malformed JSON lines (JSONL parse failures, isolated per line - one bad
  line never fails the whole file)
- missing required technical identifier (blank primary key)
- a mandatory timestamp that's missing or unparseable (event-like sources)
- duplicate business key within the file (the losing row, per the policy above)
- a batch-level DB failure (rare; the whole batch's rows are logged with the
  underlying error as the reason)

## Audit metadata

`monitoring.ingestion_runs` gets one row per pipeline execution, created in
`RUNNING` status before any data is touched and updated to a terminal
status when the run finishes:

- `SUCCESS` - no rejected rows
- `PARTIAL_SUCCESS` - some rows rejected, but at least one row landed
- `FAILED` - either nothing could land (all rows rejected) or the run
  couldn't start at all (e.g. missing source file), in which case the audit
  row is written *before* the exception propagates to the CLI

Row counts (`rows_received`/`rows_inserted`/`rows_updated`/`rows_rejected`)
are only added to the in-memory run metrics **after** a batch's transaction
actually commits - if a batch's transaction rolls back, none of its
provisional counts leak into the audit row.

## Transactions

One transaction per batch (`with conn:` around upsert + reject-logging for
that batch). This is a size/latency tradeoff for large files: batch-level
transactions mean a mid-file failure only rolls back its own batch (a few
thousand rows), not everything ingested so far, and the audit row's
`PARTIAL_SUCCESS`/counts stay accurate. Per-source (whole-file) transactions
were considered but rejected: a `large`-scale 1M-row product_events failure
near the end of the file would otherwise discard ~20 minutes of already-good
work.

## Running ingestion

```bash
export HOST_DATABASE_URL=postgresql://analytics:change_me_locally@localhost:5433/growth_platform
python -m ingestion.pipeline --source all
python -m ingestion.pipeline --source users
python -m ingestion.pipeline --source product_events --batch-size 5000
python -m ingestion.pipeline --report              # ingestion observability report
```

Host-side tools use port **5433**, not 5432 - see the comment in
`docker-compose.yml`: many dev machines already run a native Postgres
service bound to 5432, which silently intercepts the container's port
mapping if left at 5432:5432. In-Docker-network traffic (analytics-api ->
postgres) is unaffected; it resolves the `postgres` service name on 5432
regardless of the host mapping.

## Inspecting results

```sql
select * from monitoring.ingestion_runs order by started_at desc limit 20;
select * from monitoring.rejected_records order by rejected_at desc limit 20;
select count(*) from raw.organizations;
```

Or via the CLI: `python -m ingestion.pipeline --report`.

## Performance

Row-by-row `INSERT` would mean one round-trip per row - infeasible at
500k-1M rows. Every loader instead builds one `execute_values(...)` call per
batch (default 5,000 rows), i.e. one round-trip per batch. `execute_values`
was chosen over raw `COPY` because the upsert semantics (`ON CONFLICT DO
UPDATE`, with per-row insert/update attribution via `RETURNING xmax = 0`)
aren't expressible through `COPY`, which only appends.

## Known limitations

- Batch-level (not per-row) failure granularity: if one row's DB write fails
  in a way that isn't a validation problem (rare - most bad rows are caught
  earlier by Pydantic), the whole batch is marked rejected rather than
  isolating just that row.
- No cross-source referential integrity enforcement at ingestion time
  (orphaned FKs are intentionally allowed to land, since raw preserves
  source-system semantics as-is) - reconciliation is dbt's job.

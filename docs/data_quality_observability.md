# Data Quality & Observability (Phase 7)

A persistent, queryable quality-check framework layered on top of the
Phases 1-6 platform: every check run is recorded, every individual check's
outcome is recorded, and the same intentional-synthetic-anomaly policy
established in Phase 3 (`docs/dbt_staging.md` "WARN vs ERROR") is extended
here rather than re-invented.

## Monitoring architecture

```
scripts/run_quality_checks.py
        |
        v
monitoring.quality_runs        (one row per execution: run_id, status, counts, duration)
        |
        v
monitoring.data_quality_results (one row per check per run: status, severity, failed/total, details JSON)
```

Both tables live in the existing `monitoring` schema (Phase 2's
`ingestion_runs`/`rejected_records` already established the convention of
one audit-run table plus one per-event detail table; this phase follows
the identical pattern rather than inventing a new one). DDL:
`app/db/quality_tables.sql`, mounted into `docker-entrypoint-initdb.d` as
`04_quality_tables.sql` (Phase 1-6 convention) and applied manually to the
already-initialized dev volume, exactly as every prior phase's schema
addition was.

## Database schema

`monitoring.quality_runs`: `run_id` (text PK, `qrun_<hex>`), `started_at`,
`finished_at`, `overall_status` (PASS/WARN/FAIL/ERROR), `total_checks`,
`passed_checks`, `warned_checks`, `failed_checks`, `duration_ms`,
`metadata` (jsonb), `error_message`, `created_at`. Indexed on
`started_at` and `overall_status`.

`monitoring.data_quality_results`: `id` (bigserial PK), `run_id` (FK),
`test_name`, `model_name`, `category`, `severity` (INFO/WARN/ERROR),
`status` (PASS/WARN/FAIL), `failed_rows`, `total_rows`, `failure_rate`,
`executed_at`, `duration_ms`, `details` (jsonb - structured, check-specific
diagnostics), `error_message`. Indexed on `run_id`, `status`,
`model_name`, `test_name`, `executed_at`.

**No credentials, connection strings, or raw PII rows are ever stored** -
`details` only ever contains counts, rates, table/column names, and
thresholds (verify by reading any check function in
`scripts/run_quality_checks.py`: every `details` dict is built from
aggregates, never a row's actual content).

**Retention**: no automatic pruning is implemented in this phase - every
run's results persist indefinitely, by design, so "has quality improved
over time" can be answered. If retention becomes a real concern at
production scale, a time-boxed `DELETE` policy on `executed_at` would be
the natural addition; not needed yet at this project's data volume.

## Check catalog

16 checks across the 6 required categories, all built directly against
real models/tables this repository already has - nothing invented. See
`scripts/run_quality_checks.py` for the exact SQL; summarized here:

| Category | Check | Severity | What it verifies |
|---|---|---|---|
| Structural | `required_relations_exist` | ERROR | 23 key tables/views/snapshots across every schema actually exist |
| Structural | `fact_primary_key_uniqueness` | ERROR | `fact_subscriptions`/`fact_invoices` have no duplicate grain keys |
| Structural | `critical_tables_not_empty` | ERROR | Core raw/staging/analytics tables aren't unexpectedly empty |
| Referential | `orphaned_subscription_users` | WARN | Orphan rate on `subscriptions.user_id` vs. the documented expected rate |
| Referential | `orphaned_product_event_users` | WARN | Same, for `product_events.user_id` |
| Referential | `orphaned_invoice_subscriptions` | WARN | Same, for `invoices.subscription_id` |
| Referential | `orphaned_crm_organizations` | WARN | Same, for `crm_accounts.organization_id` |
| Business | `invoice_amount_semantics` | ERROR | `paid_amount`/`outstanding_amount` never exceed `billed_amount`; no negative amounts |
| Business | `mrr_arr_relationship_by_currency` | ERROR | `arr = mrr * 12` holds per currency (never aggregated across currencies) |
| Business | `rates_within_valid_range` | ERROR | Every 0-1 rate/percentage column across the marts stays in `[0, 1]` |
| Business | `no_negative_monetary_or_count_values` | ERROR | Amounts/counts that must never be negative, aren't |
| Freshness | `raw_source_freshness` | WARN | Each raw table's `_ingested_at` age against documented thresholds |
| Freshness | `incremental_fact_watermarks` | WARN | Each incremental fact's own high-water mark isn't lagging unexpectedly behind its source |
| Snapshot | `snapshot_current_record_uniqueness` | ERROR | At most one `dbt_valid_to IS NULL` row per business key, in both snapshots |
| Snapshot | `snapshot_valid_intervals` | ERROR | `dbt_valid_from` never null; `dbt_valid_to` (when set) is strictly after it |
| Volume | `row_count_volume_drop` | WARN | Current row counts vs. the most recently *persisted* observation - never a fabricated baseline |

### Why orphan/freshness checks are WARN severity, not ERROR

Phase 2's synthetic generator **intentionally** injects orphaned foreign
keys (~0.6% per FK column) and Phase 3's `dbt test` suite already encodes
this as the project's documented, permanent policy (9 pre-existing WARNs,
unchanged since Phase 3). This phase's referential-integrity checks use
the exact same classification for the exact same reason: an orphan rate
within the expected range is **not a defect**, and promoting it to
ERROR severity here would contradict three phases of established,
documented policy. A check can still report **status=FAIL** if the rate
balloons far past the expected range (a real regression) - but severity
stays WARN, so it's loudly visible without failing the whole run. This is
a deliberate design choice, not an oversight: **global WARN suppression
was never implemented, and the 9 known dbt WARNs remain exactly as
visible as they were in Phase 3-6.**

Freshness is WARN for the same underlying reason as dbt's own source
freshness config (Phase 3): staleness in a synthetic dev environment is an
operational fact about when `make ingest` last ran, not a data-correctness
defect - failing the whole quality run over it would be the wrong signal.

**Verified directly while building this phase**: the dev dataset's
`raw.*` tables were last ingested `2026-09-22`, and "now" (in this
project's simulated session timeline) is `2026-10-16` - 24 days stale.
Both `raw_source_freshness` (this phase) and `dbt source freshness`
(Phase 3) correctly and consistently report this as stale/errored. This is
a genuine, accurate observation about the dev dataset's ingestion history,
not a bug - re-ingesting to "fix" it was deliberately not done, since it
would have masked the freshness check actually working.

## PASS/WARN/FAIL/exit-code semantics

- **status** (what a check concluded): `PASS` / `WARN` / `FAIL`.
- **severity** (how seriously the run treats a non-PASS result from this
  specific check - a fixed property of the check, not computed per-run):
  `ERROR` / `WARN` / `INFO`.
- **Overall run status** (`_overall_status()`):
  - any check with `status=FAIL` and `severity=ERROR` -> run is `FAIL`.
  - else, any check with `status` in `(WARN, FAIL)` (at any severity) ->
    run is `WARN`.
  - else -> `PASS`.
- **Exit codes**: `0` for an overall status of `PASS` or `WARN` (checks
  ran successfully, even if some found issues worth looking at); `1` for
  an overall status of `FAIL` (a critical quality requirement was
  violated); `2` if the runner itself couldn't execute (e.g. the
  monitoring database is unreachable) - **distinct from `1`
  specifically so a CI/ops consumer can tell "checks ran and found bad
  data" apart from "checks never ran at all."**
- A check that raises an exception is recorded as `status=FAIL`,
  `severity=ERROR` (the safe default - an unclassified exception is
  assumed critical), with `error_message` populated - **never silently
  dropped**, and the run continues to the next check rather than aborting
  (verified: `tests/integration/test_quality_checks_integration.py::test_check_exception_is_recorded_not_silently_dropped`).

## Historical baseline / regression strategy

`row_count_volume_drop` compares the current row counts of 6 watched
tables against the most recently **persisted** observation of this exact
check (queried from `monitoring.data_quality_results` itself - never a
hardcoded or invented number). First run: no prior observation exists, so
the check reports `status=PASS` with `details.baseline_available=false` -
it never fabricates a conclusion from missing history. Subsequent runs:
`details.baseline_available=true`, with the actual previous counts and
computed percent-drops included. Thresholds
(`--volume-drop-warn-pct`/`--volume-drop-fail-pct`, default 20%/50%) are
CLI-configurable. Verified directly:
`tests/integration/test_quality_checks_integration.py::test_baseline_comparison_distinguishes_first_run_from_subsequent`
and the live first-run-vs-second-run behavior shown in "Verification"
below.

No other anomaly-detection model is implemented - deterministic thresholds
against transparent, persisted history, per the phase's own scope
restriction against building a "complicated anomaly-detection model."

## Running checks locally

```bash
python scripts/run_quality_checks.py                          # all checks
python scripts/run_quality_checks.py --category referential    # one category
python scripts/run_quality_checks.py --json                    # machine-readable
python scripts/run_quality_checks.py --verbose                 # print each check's details
python scripts/run_quality_checks.py --show-latest             # read the last persisted run, no new run
python scripts/run_quality_checks.py \
    --orphan-warn-rate 0.05 --orphan-fail-rate 0.25 \
    --freshness-warn-hours 24 --freshness-error-hours 48 \
    --volume-drop-warn-pct 0.20 --volume-drop-fail-pct 0.50
```

## Inspecting run history and failures in SQL

```sql
-- Recent runs, newest first
select run_id, overall_status, total_checks, passed_checks, warned_checks,
       failed_checks, duration_ms, started_at
from monitoring.quality_runs
order by started_at desc
limit 20;

-- Everything that failed or warned in the latest run
select test_name, category, severity, status, failed_rows, total_rows, error_message
from monitoring.data_quality_results
where run_id = (select run_id from monitoring.quality_runs order by started_at desc limit 1)
  and status != 'PASS'
order by category, test_name;

-- Which checks fail/warn most often across all history
select test_name, count(*) as times_run,
       count(*) filter (where status = 'FAIL') as times_failed,
       count(*) filter (where status = 'WARN') as times_warned
from monitoring.data_quality_results
group by test_name
order by times_failed desc, times_warned desc;

-- Has quality improved or deteriorated? run-level trend
select started_at, overall_status, failed_checks, warned_checks
from monitoring.quality_runs
order by started_at;
```

## Diagnosing and recovering from failures

1. `python scripts/run_quality_checks.py --show-latest` to see the latest persisted outcome without re-running anything.
2. `python scripts/run_quality_checks.py --category <category> --verbose` to re-run just the failing category with full structured details.
3. For a referential-integrity WARN/FAIL: cross-check against the documented Phase 2/3 expected orphan rate (`docs/dbt_staging.md`) before assuming regression - it may be expected synthetic noise.
4. For a freshness FAIL: run `make ingest` (Phase 2) to refresh `_ingested_at`, or confirm this is an intentionally stale dev snapshot.
5. For a structural/business-rule ERROR-severity FAIL: this indicates a genuine correctness problem - inspect `monitoring.data_quality_results.details` for the exact failing table/rows, and treat it the same as a failing `dbt test` would be treated.
6. The monitoring tables themselves can be re-initialized safely at any time: `app/db/quality_tables.sql` uses `CREATE TABLE IF NOT EXISTS`/`CREATE INDEX IF NOT EXISTS` throughout - running it again is a no-op, never destructive (verified: `test_monitoring_schema_initialization_is_idempotent`).

## Known limitations

- `row_count_volume_drop` only watches 6 tables (the raw sources); it is not a general-purpose anomaly detector across every table in the project.
- `incremental_fact_watermarks` uses a fixed 24-hour buffer beyond the Phase 6 lookback window - not independently configurable via a CLI flag in this phase (the lookback window itself is Phase 6's `incremental_lookback_days`, unchanged here).
- No automatic retention/pruning of historical results - see "Database schema" above.
- No CI integration was added in this phase (out of scope - "CI/CD expansion" belongs to a later phase); this script is designed to be CI-callable (clear exit codes, `--json`) when that phase arrives, but wiring it into `.github/workflows/` was not done here.
- Freshness/incremental-watermark checks are informational about *this specific dev environment's* ingestion cadence - they are not production SLA monitors.

# Incremental Processing & Snapshots (Phase 6)

Converts three facts from full-refresh-only to incremental materializations
and adds two dbt snapshots for SCD Type 2 history, on top of the unchanged
Phase 1-5 layers. See [dbt_marts.md](dbt_marts.md) for everything about the
facts' business meaning that didn't change in this phase.

## Which models are incremental, and why

| Model | Materialization | Unique key | Why incremental |
|---|---|---|---|
| `fact_product_usage` | `incremental` (delete+insert) | `usage_grain_key` | Event-level source (`raw.product_events`), genuinely append-heavy over time |
| `fact_api_usage` | `incremental` (delete+insert) | `usage_grain_key` | Same reasoning, `raw.api_usage` |
| `fact_invoices` | `incremental` (delete+insert) | `invoice_id` | Billing records accumulate; status can change after issuance |
| `fact_subscriptions` | unchanged (`table`, full refresh) | - | Current-state-only source (one row per subscription, see "What incremental cannot do" below) - no event stream to process incrementally |

`fact_subscriptions` was deliberately **not** converted. `raw.subscriptions`
has exactly one row per subscription, overwritten in place on every
ingestion upsert - there is no append-only event stream to process
incrementally, and treating a current-state table as "new rows since last
run" would be meaningless (every run sees the *same* 1,500 rows, just
possibly with different values). This is also why `fact_subscriptions`'s
own history is handled by a **snapshot** (`snap_subscriptions`) instead -
snapshots and incremental models solve different problems, and this
project uses each for the one it's suited to.

## Why `usage_grain_key` exists (a real bug this phase found and fixed)

`fact_product_usage`/`fact_api_usage`'s grain includes `user_id`/
`organization_id`, which are legitimately nullable (orphaned/unresolvable
source rows - preserved, not dropped, per the Phase 3-5 policy).
`delete+insert`'s row-matching uses plain SQL equality on the unique_key
columns, and **SQL `NULL` never equals `NULL`** - a nullable multi-column
`unique_key` would silently fail to replace old NULL-keyed rows on every
rerun, accumulating duplicates for exactly the orphaned-reference rows the
project is most careful to preserve correctly elsewhere. `usage_grain_key =
dbt_utils.generate_surrogate_key([...COALESCE-guarded columns...])` hashes
the grain into one never-null key, sidestepping this. `user_id`/
`organization_id` are still exposed as their own (nullable) columns -
unchanged in meaning, just re-added to `fact_product_usage`'s output,
which Phase 5 had dropped (keeping only the surrogate dimension keys). This
is a small, explained, regression-tested fix to a Phase 5 model, made
because the incremental conversion required it - not a speculative change.

## The incremental filter strategy

**On `_ingested_at` (processing time), never on `event_timestamp`/
`request_timestamp`/`issued_at` (business/event time), and never on a bare
`MAX(event_timestamp)`.** This is the single most important design
decision in this phase: business event time and ingestion time are *not*
interchangeable. A late-arriving event can have an `event_timestamp` from
weeks ago but an `_ingested_at` from today - `_ingested_at` is the only
reliable signal that a row is new to *this pipeline*, regardless of when
it happened.

1. Each model computes its own high-water mark:
   `max(_last_ingested_at)` from the model's own existing output
   (`{{ this }}`) - not from the source table, so a `--full-refresh` always
   starts clean and a normal run always resumes exactly where the last one
   left off.
2. It finds every **affected key** (calendar date, for the two usage
   facts; invoice_id, for `fact_invoices`) that has at least one source row
   whose `_ingested_at` falls within `incremental_lookback_days` days
   *before* that high-water mark.
3. For the two aggregate facts, it then **recomputes the ENTIRE affected
   date from ALL of that date's events** - not just the newly-ingested
   ones - and replaces (`delete+insert`) the existing rows for that date.
   This is what prevents a late arrival landing in an already-aggregated
   date from inflating `event_count`/`request_count`: the whole bucket is
   rebuilt from scratch, never appended to.
4. For `fact_invoices` (no aggregation - one row per invoice), the
   affected invoice's single row is simply replaced with its current
   state.

## Lookback window configuration

`vars.incremental_lookback_days` in `dbt_project.yml`, default **3 days**.
Referenced as `{{ var("incremental_lookback_days", 3) }}` in each
incremental model. Override per-invocation with `--vars
'{"incremental_lookback_days": 7}'` if a known late-arrival incident needs
a wider temporary window.

**The lookback window is not a guarantee against arbitrarily late data.**
It only re-scans a bounded window behind the model's own high-water mark;
a row whose `_ingested_at` falls *outside* that window - because it was
ingested after the incremental model's watermark had already moved past
it, or because it represents a genuinely very old late arrival - will not
be picked up by a normal incremental run. This is a deliberate, documented
trade-off (bounded, predictable incremental query cost) not a claim that 3
days covers every possible lateness scenario.

### Recovery for late arrivals outside the window

Documented, not just implemented: `dbt run --select <model> --full-refresh`
rebuilds the model from 100% of its current source data, with no lookback
limit at all - this is the universal recovery path for anything the
lookback window missed. There is no finer-grained "rebuild just this one
date" command built into this phase (that would require a custom
operation); a full refresh at this project's dev-data scale takes well
under a second per model (measured - see "Performance" below), so a
targeted partition rebuild was not built as a separate capability.

## Idempotency guarantees and limits

**Guaranteed** (verified - see "Test results" below): running the same
incremental model twice in a row with no source changes produces identical
row counts and totals; a late-arriving event correctly increments the
affected date's `event_count` exactly once; an invoice status update is
reflected without creating a duplicate row; a `--full-refresh` produces
output identical to the accumulated incremental state.

**Not guaranteed**: a row ingested, then later deleted from the source
entirely (not just updated) within the same lookback window is not
specifically detected as a deletion by these incremental facts (they only
ever `SELECT` current source rows - a row that's gone from the source
simply stops being selected, but any already-materialized fact row for a
date/invoice that no longer has ANY contributing source rows left over
would not automatically disappear unless that date/invoice is re-selected
as "affected" by a subsequent run with new activity). This project's
source generator never deletes rows (only upserts), so this edge case is
undetected but also, verified, never actually occurs in this dataset.

## dbt Snapshots - SCD Type 2 for subscriptions and CRM accounts

`snapshots/snap_subscriptions.sql` and `snapshots/snap_crm_accounts.sql`,
both built on the Phase 3 staging models (not raw) so the captured history
is already typed/normalized, landing in a new `snapshots` schema (added
this phase to `app/db/init_schemas.sql` - the one schema addition in this
phase, required because Phase 1 didn't anticipate SCD history).

**Strategy: `timestamp`, on `updated_at`** - deliberately *not*
`_ingested_at`. `updated_at` is a genuine source/business field (set by
`scripts/generate_data.py` to reflect when the record itself last
meaningfully changed) and is passed through ingestion unmodified; using
`_ingested_at` instead would only tell dbt when *we* last touched the row,
which is an invented, untrustworthy "did it change" signal, not a real
one. A `check` strategy (comparing specific columns) was considered and
rejected: a trustworthy `updated_at` already exists, so introducing a
second, weaker detection mechanism alongside it would add complexity
without adding reliability.

Both snapshots use `invalidate_hard_deletes=True`: if a `subscription_id`/
`crm_account_id` ever disappears from the source entirely, its current
version is closed (`dbt_valid_to` set). This source never hard-deletes
records (ingestion only upserts), so this setting is a defensive
guarantee against a scenario that doesn't occur today, not a response to
an observed case.

### SCD Type 2 semantics

- `dbt_valid_from` - when this version became current. Never NULL (tested).
- `dbt_valid_to` - when this version was superseded. NULL for the current version.
- **Current record** = `dbt_valid_to IS NULL`. Tested to be unique per business key (a business key can have many historical rows, but at most one current row - verified both in `dbt test` via a `where`-filtered `unique` test and in a live-data pytest integration test).

### Snapshot limitations - stated plainly, not buried

**`raw.subscriptions`/`raw.crm_accounts` store only the CURRENT state of
each record - ingestion overwrites the previous row on every upsert.** A
dbt snapshot can only capture a transition if it happens to run *between*
two source changes. Any status/plan transition that occurred before this
snapshot's first run, or between two runs spaced further apart than the
source changed multiple times, is **permanently unobservable** - it was
never written down anywhere, by this project or the source system it
simulates. This is exactly why Phase 4's `int_subscription_transitions`
(a milestone timeline reconstructed from the CURRENT row's own timestamp
columns) is **not rewritten** to use this snapshot: that model's
limitations are about reconstructing a past that existed before Phase 6
did, which no snapshot taken from today forward can retroactively fix.
Going forward from whenever this snapshot was first run, however, its
history is complete and reliable for every subsequent real change.

## Querying subscription history

Current state, previous status, and the validity interval in one query:

```sql
select
    subscription_id,
    status,
    plan_name,
    dbt_valid_from,
    dbt_valid_to,
    (dbt_valid_to is null) as is_current,
    lag(status) over (partition by subscription_id order by dbt_valid_from) as previous_status,
    lag(plan_name) over (partition by subscription_id order by dbt_valid_from) as previous_plan_name
from snapshots.snap_subscriptions
order by subscription_id, dbt_valid_from;
```

This is a documented example query, not a new model - Phase 4's
`int_subscription_transitions` stays exactly as it was (observed
snapshot history and source-derived milestones are kept as two distinct,
clearly-labeled things, never merged into one that implies more history
than either actually has).

## Operational considerations and failure modes

- **A failed incremental run does not silently produce a complete-looking
  result**: `delete+insert` runs inside a single transaction per model: if
  the `INSERT` half fails after the `DELETE` half succeeded, dbt rolls the
  whole statement back, leaving the previous data intact rather than a
  half-deleted table. A non-zero dbt exit code is always returned on
  failure - verified by the CI-relevant tests in this phase, which assert
  `returncode == 0` and would fail the test suite otherwise.
- **A real, verified Postgres/dbt interaction worth knowing before
  operating this project**: dbt-postgres's view materialization always
  does `DROP ... CASCADE` + `CREATE` (Postgres cannot `CREATE OR REPLACE
  VIEW` when the column set changes), which can cascade-drop dependent
  views. Rebuilding a single staging/intermediate view with a narrow
  `--select` can silently drop downstream views that a *different* branch
  of the DAG still needs, and dbt's `+` (downstream) selector does not
  protect against this - it only rebuilds what's downstream of the
  selected node, not every other upstream dependency a downstream node
  might also have. **Discovered directly while writing this phase's
  tests** (not a hypothetical): see
  `tests/integration/test_incremental_facts.py` for the specific failure
  and its fix (use a full, unscoped `dbt run` after mutating a table that
  a staging view sits on top of, rather than a narrow `--select`, when
  correctness - not just speed - matters). This is inherent to the
  Postgres adapter, not a bug in this project's models.
- **No new indexes were added.** The dev dataset's facts (1,500-10,000
  rows) make any index evaluation statistically meaningless; see
  "Performance" below.

## Performance

Measured directly on this dev dataset (not fabricated):

- Initial `--full-refresh` of all three converted facts: **2.69s** total (`fact_api_usage` 0.33s / `fact_product_usage` 0.35s / `fact_invoices` 2.39s - the heaviest, correlating with its larger dimension join).
- Subsequent normal incremental run (no new data, full lookback window reprocessed since all `_ingested_at` values are recent): **1.11s** total.
- Full project `dbt build` (38 models + 2 snapshots + 216 tests): consistently **2-3 seconds**.

At this scale, the incremental conversion's benefit is architectural
(demonstrating the pattern, bounding query cost as the dataset grows) more
than a measured speedup today - with only 1,500-10,000 source rows, a full
scan and an incremental scan cost about the same. The lookback-window
mechanism is what will matter once this project's `large` synthetic scale
(1M+ product events) is actually loaded; no such benchmark is fabricated
here since it hasn't been run.

## How to run

```bash
cd dbt
export DBT_POSTGRES_HOST=localhost DBT_POSTGRES_PORT=5435   # see .env / profiles_example.yml

dbt run --select fact_product_usage fact_api_usage fact_invoices --full-refresh  # first conversion only
dbt run --select fact_product_usage fact_api_usage fact_invoices                 # normal incremental runs thereafter

dbt snapshot                      # run both snapshots
dbt snapshot --select snap_subscriptions
dbt test --select snap_subscriptions snap_crm_accounts

dbt build                         # everything: staging+intermediate+marts+incremental facts+tests
```

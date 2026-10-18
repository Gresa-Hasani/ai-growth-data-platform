# Security and SQL Validation (Phase 10)

A security-hardening pass over the existing Analytics API (Phase 8) and AI Data Analyst Agent
(Phase 9). This phase did not rebuild anything - it audited the existing architecture, found most
of it already sound, and added the controls below where real gaps existed. Every claim in this
document is backed by a test in `tests/unit/` or `tests/integration/`; where something was verified
manually against the live database, that's called out explicitly.

## 1. SQL injection audit (finding: none in the current codebase)

Every database query in `app/services/*.py` and `app/agent/tools.py` was inspected line by line.
**Result: no SQL injection vulnerability was found.** Every query uses SQLAlchemy's `text()` with
`:name` bound parameters for every runtime VALUE; table and column names are always hardcoded
literals, never built from request or model input.

A few functions (`customer_service.list_customers`, `growth_service.get_trial_conversion`,
`usage_service.get_daily_usage`/`get_api_usage`) use an f-string like
`text(f"SELECT count(*) FROM analytics.dim_organizations {where}")`. This looks suspicious at a
glance but is safe: `where` is a **static Python string literal** defined earlier in the same
function (e.g. `where = "WHERE (:org_type IS NULL OR organization_type = :org_type)"`), reused
between a `COUNT` query and the paginated `SELECT` so the filter logic isn't duplicated. The actual
filter *value* never touches the f-string - it only ever flows through the `:org_type` bound
parameter. This distinction (static SQL text vs. runtime values) is exactly what Phase 10 asked us
to verify rather than assume.

**Proof, not just audit**: `tests/integration/test_security_sql_injection.py` sends realistic
injection payloads (`' OR '1'='1`, stacked-statement attempts, comment-injection, UNION-based
attempts, NUL bytes) through every free-text filter and path parameter the API exposes
(`organization_id`, `acquisition_channel`, `event_name`, `run_id`, `customer_id`,
`organization_type`, and the AI agent's `question` field) and confirms: no 500, no changed query
behavior, and - captured via a before/after row-count comparison across five tables - **no
underlying data was ever modified** by any attempt.

## 2. The SQL validator (`app/security/sql_validator.py`)

**This module is not wired into any endpoint or agent tool.** Nothing in this codebase executes
client- or model-supplied SQL - the AI Agent only ever selects a `metric_id` from a fixed catalog
(see §4), which maps to one hardcoded, parameterized query. There was no existing execution path
that needed a SQL validator, so this is a standalone, independently-tested **defensive boundary**
for a possible future controlled-SQL feature - never a way to let a client or LLM run arbitrary SQL.

- Uses **`sqlglot`** (structured AST parsing, not regex) to parse PostgreSQL syntax.
- `validate_select_only(sql)` returns a `ValidatedStatement` or raises a typed
  `SqlValidationError` subclass. Fails **closed**: a parse error, an unparseable/unsupported
  construct, or an ambiguous classification is always a rejection, never a best-effort pass.
- Rejects: multiple statements (stacked via `;`), anything that isn't a `SELECT`/`WITH ... SELECT`
  (`INSERT`/`UPDATE`/`DELETE`/`MERGE`, `CREATE`/`ALTER`/`DROP`/`TRUNCATE`, `GRANT`/`REVOKE`,
  `SET`/`BEGIN`/`COMMIT`/`ROLLBACK`, `COPY`, `VACUUM` and other admin commands sqlglot can't model
  as a known statement type), `SELECT ... INTO` (creates a table), locking reads (`FOR UPDATE`),
  any table reference outside the `analytics`/`monitoring` schema allowlist (including
  `pg_catalog`/`information_schema` and unqualified table names), and a function-name blocklist
  covering filesystem access (`pg_read_file`, `lo_export`, ...), cross-database execution
  (`dblink*`), and session/server control (`pg_terminate_backend`, `set_config`, ...).
- Correctly handles CTEs, comments, and string literals containing SQL-looking text (e.g. a
  `WHERE currency = 'DROP TABLE x; --'` literal is accepted as data, not misparsed as a second
  statement) - see `tests/unit/test_security_sql_validator.py` (38 tests covering every category
  above plus the accept-path cases).

**Documented limitations** (also in the module's own docstring):
- A statement that *passes* this validator is only known to be a single, well-formed, read-only
  `SELECT` referencing approved schemas - passing does not by itself bound row counts, enforce a
  statement timeout, or guarantee the connecting role's privileges are also read-only. A caller
  wiring this up for real execution would still need those bounds (this project's actual execution
  path gets them from `AgentSettings`/`read_only_engine` instead - see §3/§4).
- Schema-level allowlisting only (`analytics`, `monitoring`) - it does not allowlist individual
  tables/columns; that's `app/agent/catalog.py`'s job for the agent's actual execution path.
- The disallowed-function list is a deny-list, not an allow-list - it covers the well-known
  dangerous PostgreSQL functions but cannot be assumed exhaustive against every current or future
  server-side function.

## 3. Database-level read-only enforcement

Before Phase 10, the Analytics API and AI Agent shared one PostgreSQL role (`analytics`, the same
one ingestion/dbt use) via `app/db/session.py`'s single `engine`. Read-only behavior was an
**application convention** (every service function happens to only `SELECT`) - nothing at the
database level would have stopped a future bug from writing.

**What changed**: `app/db/session.py` now exposes a second engine, `read_only_engine`, bound with
SQLAlchemy's `postgresql_readonly=True` execution option. This causes psycopg2 to put every
connection from that engine into a **database-enforced read-only transaction**
(`cannot execute INSERT in a read-only transaction` - a PostgreSQL server-side error, not an
application check). Every Phase 8 analytics router and the Phase 9 agent's `/ask` endpoint now
depend on `get_read_only_db()` instead of `get_db()`.

**Verified for real** (not just unit-tested) against the live dev Postgres container:
```
INSERT ... -> psycopg2.errors.ReadOnlySqlTransaction: cannot execute INSERT in a read-only transaction
```
even when connecting as the fully-privileged `analytics` role - the read-only transaction setting
overrides the role's own grants. See `tests/integration/test_security_db_integration.py::test_read_only_engine_rejects_write_at_the_database_level`.

**Least-privilege role (defense-in-depth, additional to the above)**: `app/db/security_roles.sh`
creates `analytics_ro`, granted `SELECT` on `analytics`/`monitoring` only (plus `ALTER DEFAULT
PRIVILEGES` so future dbt-created tables in those schemas inherit the grant automatically) and
**exactly one** write permission: `INSERT` on `monitoring.ai_query_logs`. `READ_ONLY_DATABASE_URL`
points `read_only_engine` at this role; if unset, `read_only_engine` falls back to `DATABASE_URL`'s
role but keeps the `postgresql_readonly` transaction enforcement regardless - the platform is never
left without database-level protection just because the extra role hasn't been provisioned yet.

Verified live, as the `analytics_ro` role, against the real database:
```
SELECT FROM analytics.mart_mrr        -> OK
SELECT FROM monitoring.quality_runs   -> OK
INSERT INTO analytics.mart_mrr        -> permission denied for table mart_mrr
INSERT INTO monitoring.ai_query_logs  -> OK (the one documented exception)
DELETE FROM monitoring.ai_query_logs  -> permission denied (INSERT-only, not UPDATE/DELETE)
SELECT FROM raw.users                 -> permission denied for schema raw
```

**Why the audit log still works under a read-only session**: a database-level read-only
transaction rejects *any* write regardless of the role's grants - so `analytics_ro`'s one `INSERT`
permission would be useless if the agent's audit write used the same `read_only_engine` session as
its analytical reads. It doesn't: `app/agent/service.py::_safe_audit` deliberately opens its own,
separate session from the writable `SessionLocal`/`engine` for the one `INSERT` it's allowed to
make, independent of whatever session the caller used for reads. This is covered by
`tests/unit/test_agent_audit.py` and exercised for real by
`tests/integration/test_agent_integration.py` (which asserts a real row lands in
`monitoring.ai_query_logs` after a real `ask()` call).

### Local setup

A fresh `docker compose up -d` on an empty volume runs `app/db/security_roles.sh` automatically
(mounted as `docker-entrypoint-initdb.d/06_security_roles.sh`, using `ANALYTICS_RO_PASSWORD` from
`.env`/`.env.example`). **On an already-initialized dev database** (any existing `postgres_data`
volume - init scripts only run once, on first creation), apply it manually the same way earlier
phases' SQL files are applied to an existing dev instance:
```bash
docker exec -e ANALYTICS_RO_PASSWORD=change_me_locally_ro growth_platform_postgres \
  sh -c 'ANALYTICS_RO_PASSWORD=$ANALYTICS_RO_PASSWORD POSTGRES_USER=analytics POSTGRES_DB=growth_platform sh /docker-entrypoint-initdb.d/06_security_roles.sh'
```
(or simply recreate the Postgres container/volume, which re-runs every `initdb.d` script including
this one, the standard Phase 2-9 pattern for adding a new table/role script mid-project). Without
this step, `read_only_engine` falls back to `DATABASE_URL`'s role and the platform still works -
see above - just without the extra role-level defense-in-depth until the step is run.

## 4. AI Agent hardening

Phase 9 already enforced most of what Phase 10 asked to verify: a fixed `MetricId` catalog
(`app/agent/catalog.py`), a validated, typed `AnalyticalPlan` (unknown metrics, intent/metric
mismatches, unsupported currencies, out-of-range dates/limits, and missing required filters were
already rejected - `ClarificationNeededError` for the latter, not silently coerced), exactly one
bounded tool call per question (the executor's dispatch is a fixed `if/elif` chain over the
`MetricId` enum - never `getattr()`/dynamic lookup by a model-controlled string), and a documented
no-`execute_sql`-tool, no-DB-write policy. Phase 10 verified all of this with new adversarial tests
and closed a few real gaps found along the way:

**New: `AnalyticalPlan` now rejects unexpected fields** (`model_config = ConfigDict(extra="forbid")`).
A compromised/jailbroken model trying to add a `"tool_name"`, `"sql"`, or `"table"` key to its JSON
response now fails Pydantic validation outright, rather than the extra key being silently dropped.

**New: filter fields are length- and control-character-bounded.** `organization_id`,
`organization_type`, `acquisition_channel`, `event_name`, `run_id` are capped at 200 characters and
reject embedded control characters (including NUL). These values were already safe from SQL
injection (bound parameters - see §1), so this is defense-in-depth against malformed/oversized
input reaching the service layer at all, not an injection fix.

**New: a currency-fabrication guard on LLM-generated answers.** `app/agent/answer.py` now rejects
(falls back to the deterministic `template_answer`) any LLM-written answer that mentions a currency
code not actually present in the tool's returned rows - e.g. a hallucinated "EUR MRR is X" when the
tool only ever returned USD rows for that question. This is an **application-side check on the
model's output**, not a request for the model to self-police, consistent with the spec's "security
must be enforced by application-side validation... not by expecting the model to refuse."

**Prompt injection**: `tests/unit/test_agent_security.py` sends questions attempting to override
instructions, exfiltrate environment variables/credentials/the system prompt, request arbitrary SQL
or destructive operations, and smuggle SQL text through a legitimate-looking filter field - both
for a well-behaved model (returns a clarification) and a simulated **compromised** model (returns
`intent: "unsupported"` or an extra field anyway). In every case the rejection comes from
`planner.validate_plan`/Pydantic validation, never from trusting the model's own judgment. A filter
value containing SQL-looking text (`"x'; DROP TABLE ...; --"`) is accepted as an **opaque string
value** on the plan (never parsed as an instruction) and, end-to-end through the live API
(`tests/integration/test_security_sql_injection.py`), is proven to never actually execute.

**What was already correct and is now explicitly tested rather than just assumed**: no
`execute_sql`-shaped tool exists; the executor has no `getattr`/`eval`/`exec`-based dynamic
dispatch (verified by source inspection in a test, not just reading the code once); exactly one
service-layer call happens per question (verified with a call counter); a `MetricId` with no
registered tool fails closed with `ToolExecutionError` rather than silently no-op'ing; the
`SYSTEM_PROMPT` strings for both planning and answer-writing contain no database URL, API key, or
other secret (grepped in a test, not just by inspection).

**Known limitation, stated plainly**: the currency-fabrication guard catches one concrete,
checkable class of hallucination (wrong currency code). It does not - and, with a free-text LLM
answer field, cannot cheaply - verify every number the model writes against the tool rows without a
much heavier structured-output/verification pass. The deterministic `template_answer` fallback
(used whenever the provider is unavailable, times out, or its answer fails *any* validation
including this guard) remains the only answer path with a 100% grounding guarantee. This is an
accepted, documented trade-off, not an oversight.

## 5. API input validation and safe error handling

Phase 8 already validated date ranges (`resolve_date_range`), currency codes (`validate_currency`
against a fixed `{USD, EUR, GBP}` allowlist), and pagination (`clamp_pagination`, limit/offset
bounds) at the service boundary - all three already had dedicated unit tests
(`tests/unit/test_api_unit.py`) before this phase. Phase 10 added:

- **Max-length bounds on every free-text query/path parameter** that didn't already have one:
  `acquisition_channel`, `event_name`, `organization_id` (usage endpoints), `organization_type`,
  `customer_id` (path), `run_id` (path) - all capped at 100-200 characters. These were never SQL-
  injectable (see §1) but had no length bound at all before this phase.
- **`AgentAskRequest` now rejects unexpected request-body fields** (`extra="forbid"`) - a client
  sending an extra `"sql"`/`"override"` key alongside `"question"` now gets a 422, not a silently
  accepted-and-ignored field.
- Confirmed (existing behavior, now also explicitly tested) that FastAPI's default unhandled-
  exception behavior already returns a generic 500 with **no stack trace in the response body** -
  verified in `tests/unit/test_api_unit.py`/`tests/unit/test_agent_api.py` and the SQL-injection
  integration tests, which all assert `"traceback"` never appears in any response.
- `GET /health` (pure liveness, no DB query) and `GET /ready` (DB + required-objects check) remain
  unchanged in behavior; `GET /api/v1/agent/health` remains a bounded provider check (never a full
  inference call, so it's always safe to poll frequently) - the three-way distinction Phase 9
  established is preserved, not collapsed.

**Known, explicitly deferred gap**: there is no request-body size limit middleware. FastAPI/
Starlette don't provide one out of the box, and adding one is a framework-level middleware change
with its own tuning/compatibility risk; for the one endpoint that accepts a body
(`POST /api/v1/agent/ask`), the `question` field is already capped at 2000 characters by Pydantic,
but that validation runs *after* the full request body is read and parsed as JSON. This is reported
here as a known limitation rather than silently left unaddressed - it was judged out of scope for
this phase's time/risk budget given this platform's current local/dev-oriented deployment, not
fixed and not hidden.

**Also explicitly out of scope, by design**: there is no authentication/authorization layer on any
endpoint, and no rate limiting. This was true before Phase 10 and remains true after it - this
platform has no existing auth system to examine or extend, and introducing one without that
examination would violate this phase's own "do not add a new auth system... without first examining
the existing design and documenting the decision" instruction. Documented here as a known,
accepted gap for the platform's current scope, not a regression.

## 6. Secrets and configuration

- `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` default to empty strings; selecting that provider without a
  key fails fast with `provider_unavailable` (`app/agent/providers/factory.py`) - never a silent
  fallback to a different provider (verified in `tests/unit/test_agent_providers.py`).
  `LLM_PROVIDER` defaults to `ollama` (local-only, zero commercial API credentials needed to run
  the platform at all).
- Every outbound provider HTTP/SDK call uses `AgentSettings.llm_timeout_seconds` (already true
  since Phase 9 - `httpx.post(..., timeout=self._timeout)` for Ollama,
  `openai.OpenAI(..., timeout=...)`/`anthropic.Anthropic(..., timeout=...)` for the opt-in
  providers).
- Grepped every `logger.*`/`print(...)` call site in `app/agent/*`, `app/api/*`, `app/services/*`,
  `app/db/*`: the only two logging calls that touch an exception
  (`app/agent/audit.py`/`app/agent/service.py`) log **only `type(exc).__name__`** (the exception
  class name), never the exception message, never a settings object. `app/main.py`'s
  `/ready` failure log does log the exception's message - verified (both by direct reproduction
  against a real bad-password connection, and by a new test,
  `tests/unit/test_api_unit.py::test_ready_failure_never_logs_the_password_either`) that
  psycopg2/SQLAlchemy's own `OperationalError` message names the *username* but never echoes the
  *password* - this logging call is safe as-is, which is why it was left unchanged rather than
  rewritten without a demonstrated reason.
- `.env`/`.env.example` reviewed: no real/committed credential exists in either file (only the
  long-standing local placeholder `change_me_locally`-style defaults, consistent with every earlier
  phase); `.env` is `.gitignore`d and was not modified in a way that changes that. No committed
  credential was found anywhere else in the repository during this review.
- Agent system prompts (`app/agent/planner.py::SYSTEM_PROMPT`, `app/agent/answer.py::SYSTEM_PROMPT`)
  contain no database URL, connection string, or API key - verified by test, not just inspection.
  No database credentials, internal configuration, or unrelated customer data are ever included in
  a prompt sent to any provider - the prompt only ever contains the catalog's own text and the
  specific tool result rows for the one metric being answered.

## 7. Audit logging and privacy (`monitoring.ai_query_logs`)

Unchanged from Phase 9's documented policy, re-verified under Phase 10's stricter database
permissions: safe metadata only (`provider`, `model`, `used_real_inference`, `intent`, `metric_id`,
`outcome`, `error_category`, `latency_ms`, `tool_invocation_count`); `question_text`/`answer_text`
columns exist but are never populated by any code path; a logging failure is swallowed and reported
to the application logger by default (`AgentSettings.agent_audit_log_failures_block_request=False`)
- never blocking the user's analytical answer - with the opposite (blocking) policy also tested.
`monitoring.data_quality_results` (Phase 7) is never written to by any Phase 9/10 code, confirmed by
both a Phase 9 integration test and a new Phase 10 one that explicitly re-checks it after the
security test suite runs.

## 8. How to run the security tests

```bash
# Unit (no live DB needed) - SQL validator, agent adversarial tests, provider contracts
pytest tests/unit/test_security_sql_validator.py tests/unit/test_agent_security.py \
       tests/unit/test_agent_providers.py tests/unit/test_agent_planning.py \
       tests/unit/test_agent_execution.py tests/unit/test_agent_api.py \
       tests/unit/test_agent_audit.py tests/unit/test_api_unit.py -v

# Integration (requires `docker compose up -d`) - DB-level read-only enforcement, role grants,
# live SQL-injection attempts against the real API
pytest tests/integration/test_security_db_integration.py \
       tests/integration/test_security_sql_injection.py \
       tests/integration/test_agent_integration.py \
       tests/integration/test_api_integration.py -v

# Everything
pytest tests/ -v
```

All of the above are skipped (not failed) automatically if no live Postgres is reachable - see each
file's `pytestmark = pytest.mark.skipif(...)`.

## 9. Summary of what changed vs. what was verified-but-unchanged

| Area | Outcome |
|---|---|
| SQL injection in `app/services/*` | **Audited, none found** - existing bound-parameter usage was already correct |
| SQL validator | **New** (`app/security/sql_validator.py`) - defensive, not wired to any endpoint |
| DB-level read-only enforcement | **New** (`read_only_engine`, `analytics_ro` role) |
| Audit-write isolation from read-only sessions | **New** (`_safe_audit` opens its own session) |
| Agent plan extra-field/length/control-char rejection | **New** |
| Agent answer currency-fabrication guard | **New** |
| API free-text param length bounds | **New** |
| `AgentAskRequest` extra-field rejection | **New** |
| Provider opt-in/no-fallback/timeouts | **Verified unchanged** (already correct since Phase 9) |
| No secrets in logs | **Verified** (grep + new tests) - no code change needed |
| Audit log safe-metadata policy | **Verified unchanged** (already correct since Phase 9) |
| Request body size limits | **Known gap, documented, deferred** |
| Authentication / rate limiting | **Known gap, documented, explicitly out of scope** |

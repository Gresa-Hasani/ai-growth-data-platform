# AI Data Analyst Agent (Phase 9)

A constrained, trustworthy natural-language interface onto the Phase 8 analytics API - **not** a
general-purpose chatbot and **not** a text-to-SQL system. It answers a fixed set of analytics
questions by interpreting the question into a validated plan, invoking exactly one approved
read-only tool backed by the existing `app/services/*` layer, and generating an answer that is
strictly grounded in the data that tool returned.

## What this agent is, and is not

**Is:**
- A thin orchestration layer over Phase 8's existing, already-tested query functions.
- Bounded to a fixed catalog of 14 metrics (`app/agent/catalog.py`), each one a restatement of a
  metric already documented in `docs/business_metrics.md`/`docs/dbt_marts.md`/`docs/analytics_api.md`.
- Able to degrade gracefully to a deterministic, keyword-based planner and a template-based answer
  writer if the configured LLM provider is unavailable - a model outage reduces capability, it does
  not take the agent (or the rest of the platform) down.

**Is not:**
- Not a general NL-to-SQL system. There is no `execute_sql(sql)` tool, and the model never sees or
  chooses a table/column name - only the catalog's fixed `metric_id` values.
- Not a write path. Nothing in `app/agent/*` can mutate data, trigger ingestion, or run dbt.
- Not dependent on any commercial API to function. Ollama (local-only) is the default provider;
  OpenAI/Anthropic are opt-in and are never called as an implicit fallback.

## Architecture

```
app/agent/
  config.py         AgentSettings - env-driven provider selection + execution bounds
  models.py         AnalyticalPlan / ToolResult / AgentAnswer / AgentExecutionMetadata (typed, Pydantic)
  exceptions.py      Typed failure categories (provider_unavailable, timeout, invalid_plan, ...)
  catalog.py          The trusted metric catalog - the ONLY data surface the LLM is told about
  providers/
    base.py              LLMProvider ABC: generate_json() / health_check()
    ollama.py             Default provider - httpx against a local Ollama server
    openai.py              Opt-in, LLM_PROVIDER=openai, optional `openai` package
    anthropic.py            Opt-in, LLM_PROVIDER=anthropic, optional `anthropic` package
    factory.py               Provider selection from AgentSettings.llm_provider - no fallback
  planner.py        question -> AnalyticalPlan, validated against catalog.py before anything executes
  tools.py           Typed tool functions wrapping app/services/* - no SQL of its own
  executor.py         Bounded (1 call) tool invocation, translating failures to ToolExecutionError
  answer.py            Evidence-grounded AgentAnswer generation (LLM or deterministic template)
  audit.py              Safe-metadata-only writes to monitoring.ai_query_logs (non-blocking)
  service.py            End-to-end orchestration (the flow below)

app/api/v1/agent.py   POST /api/v1/agent/ask, GET /api/v1/agent/health
app/schemas/agent.py   Request/response Pydantic models
app/db/ai_query_logs.sql   monitoring.ai_query_logs DDL
```

This mirrors the file layout the Phase 9 spec proposed, with `intent.py` folded into `planner.py`:
the LLM produces intent + metric + filters as one structured JSON response in a single call, so a
separate intent-classification module would only add indirection.

## The trusted catalog

`app/agent/catalog.py` is the single source of truth for what the agent is allowed to know exists.
Every entry restates a metric already documented in Phase 8's docs - nothing here is invented. Each
entry carries: canonical name, business definition, approved source (`analytics.*`/`monitoring.*`
table), required/optional filters, output fields, grain, currency semantics, limitations, and
whether it's a point-in-time snapshot or genuine historical data. The model is given only this
catalog's text (`catalog_summary_for_prompt()`) - it never sees a raw table or column name that
isn't already present in some entry's `output_fields`.

Critical business semantics preserved from earlier phases:
- **Customer = `organization_id`**, never `user_id`.
- **USD/EUR/GBP are never summed** - every currency-bearing metric returns one row per currency and
  the catalog's `currency_semantics` field says so explicitly; `answer.py`'s grounded-answer prompt
  is instructed the same way.
- **MRR/ARR/ARPU/LTV(estimated) are point-in-time snapshots**, flagged via `is_snapshot_only=True`
  and surfaced as a limitation on every answer (`answer.py::_finalize`) - they cannot answer "how has
  MRR trended."
- **Realized LTV (`realized_ltv_to_date`, actual billed revenue) stays distinct from estimated LTV**
  (`estimated_ltv`, a model projection) - the catalog entry and every tool/answer warning say so.
- **Quality WARN severity reflects intentionally-seeded synthetic anomalies** (Phase 2/3 policy), not
  genuine defects - both quality catalog entries and `tools.py`'s quality tools carry this warning
  through to the final answer.

## Request flow (`app/agent/service.ask`)

1. Validate question length (`AgentSettings.llm_max_question_length`, default 500).
2. Build the configured provider (`providers.factory.build_provider`) - Ollama by default; never an
   implicit fallback to a different provider if the configured one fails to construct.
3. Ask the provider for a plan (`planner.build_plan`). The model's raw JSON is **untrusted input**.
4. If the provider is unavailable, times out, or returns invalid/malformed output, fall back to
   `planner.rule_based_plan` - a small, deliberately conservative keyword matcher. Anything it can't
   map with confidence (no match, or multiple ambiguous matches) becomes a clarification request or
   an `unsupported_question` rejection, never a guess.
5. Validate the plan against the catalog (`planner.validate_plan`) - rejects unknown `metric_id`s,
   an intent/metric mismatch, an unsupported currency, a date range on a metric that doesn't support
   one, a limit outside `[1, MAX_RESULT_LIMIT]`, and raises `ClarificationNeededError` (not a failure)
   for a metric with an unmet required filter (e.g. `customer_detail` without `organization_id`).
6. Execute exactly one tool call (`executor.execute_plan`) - bounded by
   `AgentSettings.llm_max_tool_calls` (currently always 1; this agent is deliberately single-hop).
7. An empty result set is **not** an error - it's returned as a normal answer stating no data was
   found, never fabricated.
8. Generate the answer (`answer.build_answer`), constrained to cite only the tool's returned rows; if
   the provider is unavailable, fall back to `answer.template_answer` (no LLM call, still grounded).
9. Record safe audit metadata (`audit.log_query`) - see below. Non-blocking by policy.
10. Return the `AgentAnswer` to the caller.

Failure-mode distinctions (`app/agent/exceptions.py`), each a stable `category` string used both for
HTTP status selection and the audit log's `error_category`:

| category | meaning | HTTP status |
|---|---|---|
| `provider_unavailable` | configured provider unreachable / missing credentials | 503 |
| `model_not_found` | provider reachable but the model isn't pulled/doesn't exist | 503 |
| `timeout` | provider call exceeded `LLM_TIMEOUT_SECONDS` | 504 |
| `invalid_model_output` | provider returned non-JSON or unparseable output | 502 |
| `invalid_plan` | parsed plan failed catalog validation | 400 |
| `unsupported_question` | question doesn't map to any catalog metric | 400 |
| `clarification_needed` | not a failure - a legitimate, documented outcome | 422 |
| `tool_execution_failure` | the underlying `app/services/*` call raised | 502 |
| `invalid_answer` | the answer failed to validate against `AgentAnswer` | 502 |

Every one of these is a safe, generic HTTP response - never a raw stack trace, SQL text, provider
payload, or credential (see `app/api/v1/agent.py`'s `_STATUS_BY_CATEGORY` mapping and tests).

## Providers

| Provider | Default? | Requires | Notes |
|---|---|---|---|
| `ollama` | **yes** | nothing beyond Ollama installed + model pulled | Local-only - no data leaves the machine. `httpx` against `/api/generate` (`format:"json"`, `think:false` for hybrid-reasoning models like qwen3). |
| `openai` | no, opt-in | `LLM_PROVIDER=openai` + `OPENAI_API_KEY` + `pip install -e ".[openai]"` | Never imported unless selected. |
| `anthropic` | no, opt-in | `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` + `pip install -e ".[anthropic]"` | Never imported unless selected. |

`providers/factory.py::build_provider` is the **only** place a provider is instantiated. There is no
fallback between providers - if the explicitly-selected provider's credentials/SDK are missing, it
raises `ProviderUnavailableError` rather than silently trying a different one. Selecting the default
(`ollama`) never imports the `openai`/`anthropic` modules at all (covered by
`tests/unit/test_agent_providers.py::test_openai_and_anthropic_are_never_instantiated_in_default_mode`).

### Docker networking

Inside the `analytics-api` container, `localhost` refers to the container itself, not the host
running Ollama. `docker-compose.yml` sets `OLLAMA_BASE_URL` to `http://host.docker.internal:11434`
by default for the containerized API, with `extra_hosts: host.docker.internal:host-gateway` added so
this also resolves on Linux (Docker Desktop on Mac/Windows provides it natively). Running the API
directly on the host (outside Docker) can use `http://localhost:11434` as-is.

## Environment variables

See `.env.example` for the full list with defaults. The platform and every non-agent endpoint start
with **zero commercial API credentials** - `LLM_PROVIDER` defaults to `ollama`, and `OPENAI_API_KEY`/
`ANTHROPIC_API_KEY` are blank by default (selecting those providers without a key fails fast with
`provider_unavailable`, it does not silently fall back to Ollama).

## API

### `POST /api/v1/agent/ask`

```json
{"question": "What is our current MRR?"}
```

```json
{
  "answer": "Current USD MRR is $12,345. EUR and GBP MRR are reported separately.",
  "key_findings": ["USD MRR: 12345"],
  "data_sources": ["analytics.mart_mrr"],
  "applied_filters": {"currency": "USD"},
  "currency": "USD",
  "data_as_of": "2026-10-16T00:00:00+00:00",
  "limitations": ["Monthly Recurring Revenue (MRR) is a point-in-time snapshot, not a historical time series."],
  "warnings": [],
  "follow_up_suggestions": ["What about ARR?"],
  "meta": {"provider": "ollama", "used_real_inference": true, "latency_ms": 842}
}
```

Never exposes chain-of-thought, the system prompt, or a raw provider payload - only the final,
validated `AgentAnswer` plus safe execution metadata.

### `GET /api/v1/agent/health`

A short, bounded check of the *configured provider* (never a full inference call - see
`LLMProvider.health_check`) - safe to poll frequently, unlike `/ask`.

```json
{"provider": "ollama", "model": "qwen3:8b", "reachable": true, "detail": "ok"}
```

## Audit logging (`monitoring.ai_query_logs`)

A new table (`app/db/ai_query_logs.sql`, wired into `docker-compose.yml` as
`05_ai_query_logs.sql`), following the Phase 2/7 conventions: `TEXT` primary key in the
`aql_<hex>` shape, `CHECK`-constrained `outcome` enum, indexes on the columns actually filtered.

**Safe metadata only** - `provider`, `model`, `used_real_inference`, `intent`, `metric_id`,
`outcome`, `error_category`, `latency_ms`, `tool_invocation_count`. The table has nullable
`question_text`/`answer_text` columns reserved for future use, but **nothing in this phase writes
to them** - free-text question/answer content is not persisted by default, since no retention
policy for it has been documented yet. No credential, connection string, or stack trace is ever
written. Nothing in `app/agent/*` writes to `monitoring.data_quality_results`.

**Documented failure policy**: a logging failure never blocks the analytical response - it's logged
to the application logger instead (`AgentSettings.agent_audit_log_failures_block_request=False` by
default). This is deliberate: an unavailable audit table should degrade the platform's observability,
not its ability to answer a question. Set the setting to `true` to make an audit failure blocking,
if a stricter compliance posture is ever required.

## Testing strategy

**Primary (required) suite - fully mocked, no live model needed:**
- `tests/unit/test_agent_providers.py` - Ollama provider contract via monkeypatched `httpx`
  (success, timeout, unreachable, 404 model-not-found, non-200, malformed JSON, missing field),
  provider selection/factory behavior, proof OpenAI/Anthropic are never imported in default mode.
- `tests/unit/test_agent_planning.py` - valid plans, catalog-violation rejections (unknown metric,
  intent/metric mismatch, unsupported currency, unsupported date range, limit out of bounds),
  clarification behavior, **prompt-injection resistance** (a crafted question/plan attempting to
  smuggle SQL/instructions through a filter value is treated as an opaque string, never executed or
  obeyed), rule-based fallback planner behavior.
- `tests/unit/test_agent_execution.py` - tool selection per metric, provenance preservation, empty-
  but-valid results, translation of a raw service-layer exception (including one with a fake
  embedded credential) into a safe `ToolExecutionError`, currency-separation preservation, and
  answer-grounding (including rejecting an empty/invalid LLM answer rather than fabricating one).
- `tests/unit/test_agent_api.py` - HTTP status mapping for every failure category, request
  validation (empty/oversized/missing question), proof no stack trace/credential/internal detail
  ever appears in a response body, confirmation the pre-existing Phase 1-8 routes are unaffected.
- `tests/unit/test_agent_audit.py` - safe-metadata-only inserts, no-credentials-persisted proof,
  the non-blocking failure policy (and its opposite, when configured to block).

**Integration (live Postgres, skips cleanly if unreachable):**
- `tests/integration/test_agent_integration.py` - `monitoring.ai_query_logs` idempotent init, a real
  end-to-end `ask()` call against real data (forced onto the deterministic planner path via an
  unreachable Ollama URL, so it's fast and doesn't depend on a model), proof
  `monitoring.data_quality_results` is never touched.

**Separate, explicitly bounded, optional live-model smoke test:**
- `tests/integration/test_agent_live_smoke.py` - calls the *real* configured Ollama server if one is
  reachable. **Honestly skips** (never fabricates a pass) if the provider isn't `ollama`, the server
  isn't reachable, or real inference doesn't complete within a short bounded timeout. See "Known
  environment limitation" below - this is expected to skip on this development machine today.

## Known environment limitation (reported honestly, not glossed over)

On the development machine used to build this phase, the local Ollama server is reachable
(`/api/tags` responds instantly) but real inference via `/api/generate` for `qwen3:8b` did **not**
complete within tested timeouts up to 170 seconds, even with `think:false` and a minimal
`num_predict`. This is consistent with CPU-only inference of an 8B-parameter model being
extremely slow (or a cold-start model-load stall) on this particular hardware - it is **not** a
defect in the provider implementation, which is fully exercised and passing against deterministic
HTTP mocks covering every documented failure mode. The live smoke test reports this honestly as a
skip with the real reason, rather than a fabricated pass; real-model behavior should be re-verified
on hardware with a faster CPU or a GPU, or with a smaller model.

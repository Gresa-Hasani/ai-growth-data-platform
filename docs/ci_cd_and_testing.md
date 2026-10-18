# CI/CD and Automated Testing (Phase 11)

A GitHub Actions automation layer over Phases 1-10. No application behavior, API contract, metric
definition, or AI Agent architecture changed in this phase - this is automation and verification
only.

## Workflow structure

Two workflows, both triggered on pull requests, pushes to `main` (the repository's actual default
branch - verified via `git branch -a`/`git remote show`, not assumed), and manual
`workflow_dispatch`:

### `.github/workflows/ci.yml`

| Job | What it does | Needs a database? | Typical time |
|---|---|---|---|
| `lint` | `ruff check .`, import every application package, generate the FastAPI OpenAPI schema, parse every YAML config file | No | ~1 min |
| `unit-tests` | `pytest tests/unit` - every provider/agent/SQL-validator/security test, all mocked | No | ~1 min |
| `docker-validate` | `docker compose config`, build the `analytics-api` image, start the full stack, poll `/health` | Via its own `docker compose up` stack | ~2-3 min |
| `dbt-and-integration` | Disposable Postgres service → init scripts → synthetic data → ingestion → `dbt build` → quality checks → `pytest tests/integration` | Yes (ephemeral, this job only) | ~5-8 min |

All four jobs run **in parallel**, not chained with `needs:` - a failure in the heavy
`dbt-and-integration` job is visible immediately, not hidden behind two minutes of unrelated lint
output. If your team would rather fail fast and save CI minutes on an already-broken PR, add
`needs: [lint, unit-tests]` to the two heavier jobs - this is a one-line, documented tradeoff, not
a limitation of the current design.

### `.github/workflows/security.yml`

| Job | What it does | External dependency |
|---|---|---|
| `dependency-audit` | `pip-audit` against every declared dependency (including the optional `openai`/`anthropic` extras) | Queries the public PyPA advisory database over the network |
| `secrets-and-config-check` | Offline git-history secret scan (`scripts/scan_for_secrets.py`), `.env.example` placeholder check, optional-provider-stays-optional check, every workflow declares `permissions:` | None - fully offline |

Kept as its own workflow (not jobs inside `ci.yml`) so a documentation-only or dbt-only change
still gets a clear, separate security signal without being coupled to the heavier pipeline's
runtime, and so the one job with a real network dependency (`pip-audit`) is easy to spot in the
Actions UI as the one job that could plausibly fail for reasons outside this repository.

## Python and dbt versions

`actions/setup-python@v5` with `python-version: "3.12"` everywhere, matching
`pyproject.toml`'s `requires-python = ">=3.12"` (unchanged from Phase 1-10 - this phase did not
change the supported Python version). dbt version is **not pinned in the workflow** - it installs
whatever `dbt-core>=1.8`/`dbt-postgres>=1.8` resolves to via `pip install -e ".[dev]"`, the same
single source of truth `pyproject.toml` already was before this phase. CI never maintains its own
separate dependency list.

## Unit vs. integration tests

- **`tests/unit/*`** - no database, no live LLM provider. Provider contract tests
  (`test_agent_providers.py`) use monkeypatched `httpx`; agent/planning/execution/security tests
  use a `FakeProvider`; `test_security_sql_validator.py` exercises `app/security/sql_validator.py`
  with no I/O at all. Runs in the `unit-tests` job with no service containers.
- **`tests/integration/*`** - require a live PostgreSQL instance (each file's own
  `pytestmark = pytest.mark.skipif(not _db_available(), ...)` makes this explicit and makes a
  missing database an honest `SKIP`, never a silent pass). Runs in the `dbt-and-integration` job
  against that job's disposable service-container database, never a developer's local database.
- **`tests/integration/test_agent_live_smoke.py`** - the one exception: a bounded, explicitly
  separate real-Ollama smoke test (see `docs/ai_data_analyst_agent.md`). **Not run against a live
  model in CI.** It calls the configured `OLLAMA_BASE_URL`, which the `dbt-and-integration` job
  deliberately points at `http://localhost:1` (unreachable) - the test's own logic then reports an
  honest `SKIP`, matching "no live Ollama service reachable," rather than CI fabricating a pass or
  hanging on a model that was never going to respond. GitHub-hosted runners have no Ollama
  installed and this phase does not add one - see "AI Agent tests in CI" below.

## PostgreSQL initialization in CI

`dbt-and-integration`'s `postgres` service is a bare `postgres:16-alpine` container with only
`POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` set - **no** custom init scripts are mounted into
it directly. This is a deliberate, documented choice, not an oversight: GitHub Actions job-level
`services:` containers start **before** the `actions/checkout` step runs, so they cannot bind-mount
files from the checked-out repository the way `docker-compose.yml` does locally (`volumes:
./app/db/...:/docker-entrypoint-initdb.d/...`) - those files don't exist on the runner's filesystem
yet at service-startup time.

The equivalent, and the actual approach used: after checkout, a dedicated step runs
**`scripts/init_db.sh`** (new in this phase), which applies `app/db/init_schemas.sql` →
`raw_tables.sql` → `monitoring_tables.sql` → `quality_tables.sql` → `ai_query_logs.sql` →
`security_roles.sh`, via `psql`, in the exact same numbered order `docker-compose.yml` mounts them
- respecting the real dependency order (schemas before tables, tables before the `analytics_ro`
role's grants, which need `monitoring.ai_query_logs` to already exist for its one `INSERT` grant).
This script is also directly useful for local development: it's the scripted version of the
`docker exec ... psql ...` workaround `docs/security_and_sql_validation.md` previously documented
by hand for an already-initialized dev Postgres volume.

Synthetic data generation (`scripts/generate_data.py --scale small --seed 42`) and ingestion
(`python -m ingestion.pipeline --source all`) follow, using this project's existing, unmodified
commands (`make generate-data`/`make ingest`'s underlying commands) - CI never invents its own data
pipeline. **Disposable only**: this is a brand-new database created and destroyed within the one CI
job run; no test depends on, and no step ever touches, a developer's local Postgres volume.

## Read-only and audit role verification

`scripts/init_db.sh`'s last step provisions the real `analytics_ro` role via
`app/db/security_roles.sh` - the exact same script `docker-compose.yml` uses locally, run with its
real, intended effect (not stubbed out, not run as superuser-and-called-equivalent). The CI
Postgres credentials (`ci_analytics`/`ci_password`/`growth_platform_ci`) have no special-case
behavior anywhere in the application; the role separation is exercised exactly as the application
uses it in production-shaped operation:

- `tests/integration/test_security_db_integration.py` connects AS `analytics_ro` (via
  `READ_ONLY_DATABASE_URL`, mirrored from `HOST_READ_ONLY_DATABASE_URL` the same way local dev
  does) and proves, against this job's real database: `SELECT` on `analytics`/`monitoring` works,
  `INSERT`/`DELETE` on an ordinary table is denied, `INSERT` on `monitoring.ai_query_logs` (the one
  documented exception) succeeds, `DELETE` on that same table is denied, and `raw.*` is unreadable.
- `tests/integration/test_security_sql_injection.py` runs real injection payloads through the real
  FastAPI app (via `TestClient`, which goes through `app/db/session.py`'s real
  `get_read_only_db()` dependency - the same database-enforced read-only transaction production
  traffic uses) against this same database, and confirms row counts are unchanged afterward.
- `tests/integration/test_agent_integration.py` proves a real `ask()` call writes exactly one real
  row to `monitoring.ai_query_logs` and never touches `monitoring.data_quality_results`.

**Nothing in this suite runs the integration tests as a Postgres superuser and calls that a
verification of least-privilege** - `ci_analytics` itself only has the privileges the official
`postgres` image grants the database's own owning role (not superuser-by-design; it's simply the
single admin-ish role this project's own ingestion/dbt/migrations have always used, unchanged
since Phase 1), and the actual read/audit-write path under test always goes through the separate,
deliberately-restricted `analytics_ro` role. This is the same role/credential-separation the real
application uses locally and in `docker-compose.yml`, not a CI-only stand-in.

## dbt validation and the established baseline

`dbt-and-integration` runs `dbt deps` then `dbt build` against the job's disposable database (after
seeding it with the same synthetic small-scale dataset Phase 2 uses), and parses the build's own
summary line (`Done. PASS=.. WARN=.. ERROR=.. TOTAL=..`) with a dedicated step:

- **`ERROR != 0` always fails the job**, unconditionally - a genuine dbt test failure is never
  treated as acceptable just because intentional warnings already exist elsewhere.
- A result that doesn't match the established baseline (`PASS=247 WARN=9 ERROR=0 TOTAL=256`) is
  reported as a **`::warning::`** annotation, not an automatic job failure - because the baseline
  legitimately changes whenever a model or test is intentionally added, removed, or a synthetic
  anomaly rate is intentionally changed (see `docs/data_quality_observability.md`). The warning
  text says exactly that, and tells the reviewer where to look (the uploaded `dbt_build_output.log`
  artifact) to judge whether a baseline drift was intentional.
- **Updating the baseline**: when a change to `dbt/models/`, `dbt/snapshots/`, or
  `scripts/generate_data.py`'s anomaly-injection rates legitimately changes the expected counts,
  update the four numbers in this step's comparison (`.github/workflows/ci.yml`,
  `dbt-and-integration` job, "Compare dbt build result to the established baseline" step) in the
  same PR, with a one-line note in the PR description explaining why the baseline moved.

Verified locally (see "Local verification" below): a **completely fresh**, disposable database,
seeded with `--scale small --seed 42` and the project's existing ingestion command, reproduces
`PASS=247 WARN=9 ERROR=0 TOTAL=256` exactly - the baseline does not depend on any pre-existing
local state, which is why reproducing it in a disposable CI database is possible at all.

## Docker and API smoke tests

`docker-validate` validates `docker-compose.yml` (`docker compose config`), builds the real
`analytics-api` image from the real `Dockerfile`, brings up the full stack with `docker compose up
-d`, and polls `/health` (pure liveness - see `app/main.py`) with bounded retries.

**Why `/ready` isn't checked in this job**: `docker-validate`'s Postgres starts with only the init
scripts' schemas/tables (via the compose file's real bind mounts, which DO work here - this is a
normal `docker compose up`, not a job-level GH Actions `services:` container, so there's no
checkout-ordering problem) - it never runs `generate_data`/ingestion/`dbt build`, so the
`analytics.*` objects `/ready` requires don't exist yet, and a 503 would be the CORRECT, expected
response, not a smoke-test failure. `/ready`-against-real-data is instead verified in the
`dbt-and-integration` job, where `tests/integration/test_api_integration.py::test_ready_succeeds_against_real_database`
exercises the identical `/ready` code path (via FastAPI's `TestClient`, in-process - not a second
container) against that job's fully-built, real data. This is a deliberate split across two jobs
using two different mechanisms, not a gap.

## AI Agent tests in CI

The normal CI pipeline's result never depends on live Ollama inference or a commercial LLM API
being reachable:

- `dbt-and-integration` sets `LLM_PROVIDER=ollama` and `OLLAMA_BASE_URL=http://localhost:1`
  (deliberately unreachable) - every agent integration test exercises the documented
  provider-unavailable → deterministic rule-based planner / template-answer-writer fallback path,
  the same graceful-degradation behavior a developer sees if Ollama simply isn't running locally.
- `unit-tests` never touches a network at all - every provider test uses monkeypatched `httpx`.
- `tests/integration/test_agent_live_smoke.py` is the one test file that *would* call a real
  Ollama server if `LLM_PROVIDER=ollama` and that URL were reachable - in CI it is not, so this
  test reports `SKIP` with an honest reason string, never a fabricated pass. No separate
  live-Ollama GitHub Actions workflow was added in this phase: GitHub-hosted runners have no GPU
  and no Ollama installation, and installing + pulling an 8B-parameter model on every run would be
  slow and unreliable rather than a meaningful signal - this is a deliberate, documented scope
  decision, not an oversight. A self-hosted runner with a GPU could run this test for real in the
  future via a separate, manually-triggered `workflow_dispatch` workflow; none exists today.

## Security and dependency scanning

- **`pip-audit`** (pinned to `2.9.0`) scans every declared dependency, including the `openai`/
  `anthropic` optional extras (so their transitive dependencies are covered even though the default
  Ollama-only install never pulls them in). Fails the job (`--strict`) on any finding - never
  configured to always succeed. This is the one job in either workflow with a real external
  dependency (the public PyPA advisory database over the network); if it ever fails because that
  service is unreachable rather than because of a genuine finding, the job log makes the distinction (a network/connection error looks nothing like a vulnerability report).
- **`scripts/scan_for_secrets.py`** (new in this phase) - a small, fully offline, dependency-free
  scanner over every added line in the full git history: AWS access key IDs, PEM private key
  headers, OpenAI/Anthropic/GitHub/Slack token prefixes, and a `postgresql://` URL whose password
  isn't one of this project's documented placeholder/test values. **Secret-scanning limitation,
  stated plainly**: the DSN-password check is a small allowlist of known-fake words (`analytics`,
  `nopass`, `secret`, ...), not an entropy analysis - it will not catch a real secret that happens
  to collide with one of those exact words, and it is not a general-purpose scanner like gitleaks/
  trufflehog. It was built this way deliberately: fully offline, no external action/license
  dependency, and tuned specifically to this repository's own test fixtures to avoid false-positive
  noise on every run. If a real credential is ever found, the finding is reported as
  `commit file:line - possible <kind>` only - the matched line's actual content is never printed,
  so a genuine secret is never duplicated into the (public) Actions log.
- **If a secret is found**: rotate it immediately at the provider first. Removing it from a new
  commit is not sufficient - it remains in git history and must be purged (e.g.
  `git filter-repo`/BFG) or the repository re-created from a clean history. This document does not
  walk through that procedure in detail since it is a one-time incident response action, not a
  routine CI step.
- `.env.example` is checked for placeholder-only values; a check confirms `openai`/`anthropic`
  remain under `[project.optional-dependencies]`, never core dependencies; a check confirms every
  workflow file declares an explicit `permissions:` block.

## Reproducing CI locally

```bash
# lint job
ruff check .

# unit-tests job
pytest tests/unit -v

# dbt-and-integration job, against a DISPOSABLE Postgres you start yourself
# (never your existing dev container/volume):
docker run -d --name ci_test_postgres \
  -e POSTGRES_USER=ci_analytics -e POSTGRES_PASSWORD=ci_password -e POSTGRES_DB=growth_platform_ci \
  -p 5555:5432 postgres:16-alpine

export POSTGRES_USER=ci_analytics POSTGRES_PASSWORD=ci_password POSTGRES_DB=growth_platform_ci
export ANALYTICS_RO_PASSWORD=ci_ro_password PGHOST=localhost PGPORT=5555 PGPASSWORD=ci_password
export HOST_DATABASE_URL=postgresql://ci_analytics:ci_password@localhost:5555/growth_platform_ci
export DATABASE_URL=$HOST_DATABASE_URL
export HOST_READ_ONLY_DATABASE_URL=postgresql://analytics_ro:ci_ro_password@localhost:5555/growth_platform_ci
export READ_ONLY_DATABASE_URL=$HOST_READ_ONLY_DATABASE_URL
export DBT_POSTGRES_HOST=localhost DBT_POSTGRES_PORT=5555
export LLM_PROVIDER=ollama OLLAMA_BASE_URL=http://localhost:1

./scripts/init_db.sh
python scripts/generate_data.py --scale small --seed 42
python -m ingestion.pipeline --source all
mkdir -p ~/.dbt && cp dbt/profiles_example.yml ~/.dbt/profiles.yml
cd dbt && dbt deps && dbt build && cd ..
python scripts/run_quality_checks.py
pytest tests/integration -v

docker rm -f ci_test_postgres   # tear down - never touches your real dev database

# docker-validate job
docker compose config --quiet
docker compose build analytics-api
docker compose up -d && curl http://localhost:8000/health && docker compose down -v

# security.yml
pip-audit --strict
python scripts/scan_for_secrets.py
```

## Diagnosing common CI failures

| Symptom | Likely cause |
|---|---|
| `lint` fails on an import error | A new module has a hard dependency that isn't declared in `pyproject.toml`, or a circular import |
| `unit-tests` fails on a provider test | A provider's HTTP contract assumption (`app/agent/providers/*.py`) changed without updating its monkeypatched test |
| `dbt-and-integration` fails at "Initialize schemas..." | `app/db/security_roles.sh` or one of the `.sql` files has a syntax error, or the service container isn't actually healthy yet (check the health-check step's output) |
| `dbt-and-integration` fails at `dbt build` with a genuine `ERROR` | A real dbt test regression - never dismiss this as "probably one of the 9 warnings" |
| `dbt-and-integration`'s baseline step prints `::warning::` | Expected PASS/WARN/TOTAL differ from 247/9/256 - check whether this PR intentionally changed a model/test/anomaly rate; if so, update the baseline numbers in the same PR (see above) |
| `dbt-and-integration` integration tests fail on quality-run-related assertions | `scripts/run_quality_checks.py` didn't run (or failed) before pytest in this job - quality-run-dependent tests need at least one persisted run to read |
| `docker-validate` fails waiting for Postgres health | Check the "Dump container logs on failure" step's output, uploaded nowhere further - it's printed directly in the job log |
| `security.yml`'s `dependency-audit` fails | Read the pip-audit output directly - it names the exact package/CVE/fix version; this is never a flaky/always-green check |
| `security.yml`'s secret scan fails | See "If a secret is found" above - do not treat this as a false positive without actually checking what matched (though the match itself is never printed to the log) |

## Known limitations and manual verification still required

- **No real-Ollama GitHub Actions job.** See "AI Agent tests in CI" above - this is a deliberate
  scope decision for this phase, documented rather than silently absent.
- **The `dbt-and-integration` job's Postgres role separation was verified as this job's own
  `ci_analytics` admin-ish role vs. its own `analytics_ro` role** - both freshly created for that
  one job run. It was not verified against a GitHub-managed "least privilege for CI service
  accounts" concept, because none exists for a job-level service container; the role separation
  under test is the application's own, not GitHub's.
- **Coverage reporting** (`pytest-cov`, already an existing dev dependency) was not wired into a
  mandatory coverage threshold in this phase - the spec for this phase explicitly warns against an
  arbitrary threshold that fails unrelated legacy tests, and no such threshold was established in
  an earlier phase to preserve. `pytest-cov` remains available for anyone who wants to run
  `pytest --cov=app` locally; CI does not currently publish a coverage artifact. This can be added
  later as its own small, explicit change if the team wants it.
- **Workflow YAML was validated with PyYAML's parser locally** (no network access to GitHub Actions
  itself from this development environment) - this confirms the files are syntactically valid YAML
  and that the jobs/steps parse into the expected structure, but it is **not** the same as an
  actual GitHub Actions run. No claim is made in this document, or in the Phase 11 final report,
  that these workflows have executed successfully on GitHub's infrastructure - that can only be
  confirmed after this branch is pushed and a workflow run completes there.

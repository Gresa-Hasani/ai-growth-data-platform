.PHONY: setup generate-data ingest ingest-users ingestion-report test-ingestion dbt-deps dbt-debug dbt-freshness dbt-run-staging dbt-test-staging dbt-build-staging dbt-run-intermediate dbt-test-intermediate dbt-build-intermediate dbt-run-marts dbt-test-marts dbt-build-marts dbt-run-incremental dbt-full-refresh-incremental dbt-snapshot dbt-test-snapshots dbt-run dbt-test dbt-docs quality quality-latest quality-json test-quality pipeline api test-api test docs clean

setup: ## Install Python deps and bring up Postgres + API containers
	pip install -e ".[dev]"
	docker compose up -d

generate-data: ## Generate synthetic source data (small scale, seed 42) (Phase 2)
	python scripts/generate_data.py --scale small --seed 42

ingest: ## Load all generated source data into the raw schema (Phase 2)
	python -m ingestion.pipeline --source all

ingest-users: ## Load just the users source (Phase 2)
	python -m ingestion.pipeline --source users

ingestion-report: ## Print the ingestion quality/observability report (Phase 2)
	python -m ingestion.pipeline --report

test-ingestion: ## Run only the ingestion-related test suite (Phase 2)
	pytest tests/unit/test_generate_data.py tests/unit/test_loaders.py tests/integration/test_idempotency.py tests/integration/test_failures.py -v

dbt-deps: ## Install dbt packages (dbt-utils) (Phase 3)
	cd dbt && dbt deps

dbt-debug: ## Validate dbt profile/connection (Phase 3)
	cd dbt && dbt debug

dbt-freshness: ## Check source freshness against _ingested_at (Phase 3)
	cd dbt && dbt source freshness

dbt-run-staging: ## Build only the staging models (Phase 3)
	cd dbt && dbt run --select staging

dbt-test-staging: ## Run only the staging tests (Phase 3)
	cd dbt && dbt test --select staging

dbt-build-staging: ## Run + test the staging layer together (Phase 3)
	cd dbt && dbt build --select staging

dbt-run-intermediate: ## Build only the intermediate models (Phase 4)
	cd dbt && dbt run --select intermediate

dbt-test-intermediate: ## Run only the intermediate tests (Phase 4)
	cd dbt && dbt test --select intermediate

dbt-build-intermediate: ## Run + test the intermediate layer together (Phase 4)
	cd dbt && dbt build --select intermediate

dbt-run-marts: ## Build dimensions, facts, and growth/revenue marts (Phase 5)
	cd dbt && dbt run --select tag:dimensions tag:facts tag:growth tag:revenue

dbt-test-marts: ## Test dimensions, facts, and growth/revenue marts (Phase 5)
	cd dbt && dbt test --select tag:dimensions tag:facts tag:growth tag:revenue

dbt-build-marts: ## Run + test dimensions/facts/marts together (Phase 5)
	cd dbt && dbt build --select tag:dimensions tag:facts tag:growth tag:revenue

dbt-run-incremental: ## Normal incremental run of the 3 converted facts (Phase 6)
	cd dbt && dbt run --select fact_product_usage fact_api_usage fact_invoices

dbt-full-refresh-incremental: ## Full-refresh rebuild of the 3 converted facts (Phase 6)
	cd dbt && dbt run --select fact_product_usage fact_api_usage fact_invoices --full-refresh

dbt-snapshot: ## Run both SCD Type 2 snapshots (Phase 6)
	cd dbt && dbt snapshot

dbt-test-snapshots: ## Test both snapshots (Phase 6)
	cd dbt && dbt test --select snap_subscriptions snap_crm_accounts

dbt-run: ## Build staging/intermediate/mart models (Phase 3+)
	cd dbt && dbt run

dbt-test: ## Run dbt tests (Phase 3+)
	cd dbt && dbt test

quality: ## Run the full data quality check suite (Phase 7)
	python scripts/run_quality_checks.py

quality-latest: ## Show the most recently persisted quality run (Phase 7)
	python scripts/run_quality_checks.py --show-latest

quality-json: ## Run quality checks and emit machine-readable JSON (Phase 7)
	python scripts/run_quality_checks.py --json

test-quality: ## Run only the quality-framework test suite (Phase 7)
	pytest tests/unit/test_quality_checks_unit.py tests/integration/test_quality_checks_integration.py -v

pipeline: ## Run the full orchestrated pipeline end-to-end (Phase 24)
	python scripts/run_pipeline.py

api: ## Run the FastAPI service locally (outside Docker)
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test-api: ## Run only the analytics API test suite (Phase 8)
	pytest tests/unit/test_api_unit.py tests/integration/test_api_integration.py -v

test: ## Run the Python test suite
	pytest

docs: dbt-docs ## Alias for dbt-docs

dbt-docs: ## Generate and serve dbt docs (Phase 3+)
	cd dbt && dbt docs generate && dbt docs serve

clean: ## Tear down containers and remove local volumes
	docker compose down -v

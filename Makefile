.PHONY: setup generate-data ingest ingest-users ingestion-report test-ingestion dbt-run dbt-test quality pipeline api test docs clean

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

dbt-run: ## Build staging/intermediate/mart models (Phase 3+)
	cd dbt && dbt run

dbt-test: ## Run dbt tests (Phase 3+)
	cd dbt && dbt test

quality: ## Run standalone data quality checks (Phase 7)
	python scripts/run_quality_checks.py

pipeline: ## Run the full orchestrated pipeline end-to-end (Phase 24)
	python scripts/run_pipeline.py

api: ## Run the FastAPI service locally (outside Docker)
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test: ## Run the Python test suite
	pytest

docs: ## Generate and serve dbt docs (Phase 17+)
	cd dbt && dbt docs generate && dbt docs serve

clean: ## Tear down containers and remove local volumes
	docker compose down -v

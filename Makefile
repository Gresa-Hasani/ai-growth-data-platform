.PHONY: setup generate-data ingest dbt-run dbt-test quality pipeline api test docs clean

setup: ## Install Python deps and bring up Postgres + API containers
	pip install -e ".[dev]"
	docker compose up -d

generate-data: ## Generate synthetic source data (Phase 2)
	python scripts/generate_data.py

ingest: ## Load generated source data into the raw schema (Phase 2)
	python -m ingestion.pipeline

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

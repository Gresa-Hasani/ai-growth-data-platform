-- Phase 1: create the schema layout the whole platform builds on.
-- raw          -> landing zone for ingested source data (Python loaders write here)
-- staging      -> dbt staging models (1:1 cleaned views of raw sources)
-- intermediate -> dbt intermediate models (reusable business transforms)
-- analytics    -> dbt marts (dims/facts, growth/revenue marts) consumed by the API
-- monitoring   -> ingestion audit log, data quality results, AI agent query logs

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS intermediate;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS monitoring;

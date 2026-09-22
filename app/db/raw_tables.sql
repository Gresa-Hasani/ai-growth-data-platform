-- Phase 2: raw landing tables, one per source file.
-- These preserve original source-system values as-is (messy strings, mixed casing,
-- null plan names, etc). Cleaning/normalization is dbt staging's job (Phase 3), not this file.
--
-- Every table carries ingestion-level technical metadata:
--   _ingested_at  -> when this row was written/updated by the ingestion pipeline
--   _source_file  -> the source file this row came from (for traceability)
--   _run_id       -> the ingestion_runs.run_id that wrote/last touched this row
--
-- The primary key on each table is the source system's business/technical identifier.
-- This is what makes ingestion idempotent: re-running loads the same file and upserts
-- (INSERT ... ON CONFLICT) onto the same keys instead of duplicating rows.

CREATE TABLE IF NOT EXISTS raw.organizations (
    organization_id      TEXT PRIMARY KEY,
    organization_name    TEXT,
    domain                TEXT,
    country_code          TEXT,
    organization_type     TEXT,
    created_at             TEXT,
    updated_at             TEXT,
    _ingested_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    _source_file           TEXT,
    _run_id                TEXT
);

CREATE TABLE IF NOT EXISTS raw.users (
    user_id               TEXT PRIMARY KEY,
    organization_id       TEXT,
    email                  TEXT,
    country_code           TEXT,
    signup_at              TEXT,
    acquisition_channel     TEXT,
    status                  TEXT,
    created_at              TEXT,
    updated_at              TEXT,
    _ingested_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    _source_file            TEXT,
    _run_id                 TEXT
);

CREATE TABLE IF NOT EXISTS raw.subscriptions (
    subscription_id        TEXT PRIMARY KEY,
    user_id                 TEXT,
    organization_id         TEXT,
    plan_name                TEXT,
    billing_interval          TEXT,
    status                    TEXT,
    currency                  TEXT,
    amount                    TEXT,
    started_at                TEXT,
    trial_started_at          TEXT,
    trial_ended_at             TEXT,
    cancelled_at                TEXT,
    current_period_start        TEXT,
    current_period_end          TEXT,
    updated_at                   TEXT,
    _ingested_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    _source_file                 TEXT,
    _run_id                      TEXT
);

CREATE TABLE IF NOT EXISTS raw.invoices (
    invoice_id             TEXT PRIMARY KEY,
    subscription_id         TEXT,
    customer_id              TEXT,
    currency                  TEXT,
    subtotal                  TEXT,
    tax                        TEXT,
    total                       TEXT,
    status                      TEXT,
    issued_at                   TEXT,
    paid_at                      TEXT,
    _ingested_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    _source_file                 TEXT,
    _run_id                      TEXT
);

CREATE TABLE IF NOT EXISTS raw.product_events (
    event_id                TEXT PRIMARY KEY,
    user_id                  TEXT,
    organization_id           TEXT,
    event_name                 TEXT,
    event_timestamp             TEXT,
    session_id                   TEXT,
    properties                    JSONB,
    _ingested_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    _source_file                  TEXT,
    _run_id                       TEXT
);

CREATE TABLE IF NOT EXISTS raw.api_usage (
    request_id               TEXT PRIMARY KEY,
    user_id                   TEXT,
    organization_id            TEXT,
    endpoint                    TEXT,
    model_family                 TEXT,
    request_timestamp             TEXT,
    latency_ms                     TEXT,
    input_units                     TEXT,
    output_units                     TEXT,
    status_code                       TEXT,
    estimated_cost                     TEXT,
    _ingested_at                        TIMESTAMPTZ NOT NULL DEFAULT now(),
    _source_file                        TEXT,
    _run_id                             TEXT
);

CREATE TABLE IF NOT EXISTS raw.crm_accounts (
    crm_account_id            TEXT PRIMARY KEY,
    organization_id            TEXT,
    account_status               TEXT,
    segment                        TEXT,
    owner_team                      TEXT,
    lead_source                      TEXT,
    created_at                        TEXT,
    updated_at                         TEXT,
    _ingested_at                        TIMESTAMPTZ NOT NULL DEFAULT now(),
    _source_file                        TEXT,
    _run_id                             TEXT
);

CREATE TABLE IF NOT EXISTS raw.marketing_events (
    marketing_event_id        TEXT PRIMARY KEY,
    user_id                     TEXT,
    anonymous_id                  TEXT,
    campaign_id                     TEXT,
    channel                           TEXT,
    event_type                          TEXT,
    event_timestamp                       TEXT,
    utm_source                              TEXT,
    utm_medium                                TEXT,
    utm_campaign                                TEXT,
    _ingested_at                                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    _source_file                                  TEXT,
    _run_id                                        TEXT
);

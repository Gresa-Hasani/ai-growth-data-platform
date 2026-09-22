-- Phase 2: ingestion observability tables.

CREATE TABLE IF NOT EXISTS monitoring.ingestion_runs (
    run_id             TEXT PRIMARY KEY,
    pipeline_name       TEXT NOT NULL,
    source_name          TEXT NOT NULL,
    source_file           TEXT,
    started_at             TIMESTAMPTZ NOT NULL,
    finished_at             TIMESTAMPTZ,
    status                   TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'PARTIAL_SUCCESS', 'FAILED')),
    rows_received             INTEGER NOT NULL DEFAULT 0,
    rows_inserted              INTEGER NOT NULL DEFAULT 0,
    rows_updated                INTEGER NOT NULL DEFAULT 0,
    rows_rejected                 INTEGER NOT NULL DEFAULT 0,
    error_message                  TEXT,
    created_at                       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ingestion_runs_source_name ON monitoring.ingestion_runs (source_name);
CREATE INDEX IF NOT EXISTS ix_ingestion_runs_started_at ON monitoring.ingestion_runs (started_at);

CREATE TABLE IF NOT EXISTS monitoring.rejected_records (
    id                        BIGSERIAL PRIMARY KEY,
    run_id                     TEXT NOT NULL REFERENCES monitoring.ingestion_runs (run_id),
    source_name                 TEXT NOT NULL,
    source_record_identifier      TEXT,
    reason                          TEXT NOT NULL,
    raw_payload                      JSONB,
    rejected_at                        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_rejected_records_run_id ON monitoring.rejected_records (run_id);
CREATE INDEX IF NOT EXISTS ix_rejected_records_source_name ON monitoring.rejected_records (source_name);

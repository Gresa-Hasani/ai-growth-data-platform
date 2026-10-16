-- Phase 7: data quality / observability tables. Follows the same
-- conventions as monitoring_tables.sql (Phase 2): TEXT primary keys in
-- the "<prefix>_<hex>" shape, CHECK-constrained enum columns, indexes on
-- the columns actual queries filter by.

CREATE TABLE IF NOT EXISTS monitoring.quality_runs (
    run_id            TEXT PRIMARY KEY,
    started_at         TIMESTAMPTZ NOT NULL,
    finished_at         TIMESTAMPTZ,
    overall_status        TEXT CHECK (overall_status IN ('PASS', 'WARN', 'FAIL', 'ERROR')),
    total_checks           INTEGER NOT NULL DEFAULT 0,
    passed_checks            INTEGER NOT NULL DEFAULT 0,
    warned_checks              INTEGER NOT NULL DEFAULT 0,
    failed_checks                INTEGER NOT NULL DEFAULT 0,
    duration_ms                    INTEGER,
    metadata                          JSONB,
    error_message                      TEXT,
    created_at                           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_quality_runs_started_at ON monitoring.quality_runs (started_at);
CREATE INDEX IF NOT EXISTS ix_quality_runs_overall_status ON monitoring.quality_runs (overall_status);

CREATE TABLE IF NOT EXISTS monitoring.data_quality_results (
    id                BIGSERIAL PRIMARY KEY,
    run_id             TEXT NOT NULL REFERENCES monitoring.quality_runs (run_id),
    test_name            TEXT NOT NULL,
    model_name             TEXT,
    category                 TEXT,
    severity                   TEXT NOT NULL CHECK (severity IN ('INFO', 'WARN', 'ERROR')),
    status                       TEXT NOT NULL CHECK (status IN ('PASS', 'WARN', 'FAIL')),
    failed_rows                    INTEGER,
    total_rows                       INTEGER,
    failure_rate                       NUMERIC,
    executed_at                          TIMESTAMPTZ NOT NULL DEFAULT now(),
    duration_ms                            INTEGER,
    details                                  JSONB,
    error_message                              TEXT
);

CREATE INDEX IF NOT EXISTS ix_dqr_run_id ON monitoring.data_quality_results (run_id);
CREATE INDEX IF NOT EXISTS ix_dqr_status ON monitoring.data_quality_results (status);
CREATE INDEX IF NOT EXISTS ix_dqr_model_name ON monitoring.data_quality_results (model_name);
CREATE INDEX IF NOT EXISTS ix_dqr_test_name ON monitoring.data_quality_results (test_name);
CREATE INDEX IF NOT EXISTS ix_dqr_executed_at ON monitoring.data_quality_results (executed_at);

-- Phase 9: AI Data Analyst Agent audit log. Follows the same conventions as
-- quality_tables.sql (Phase 7): TEXT primary key in the "<prefix>_<hex>"
-- shape, CHECK-constrained enum columns, indexes on columns actual queries
-- filter by.
--
-- Deliberately SAFE METADATA ONLY: no credentials, no raw connection
-- strings, no stack traces. question_text/answer_text are nullable and, by
-- documented policy (AgentSettings - see app/agent/audit.py), are NOT
-- populated by default - only the metadata below is. This table is never
-- written to by anything other than app/agent/audit.py, and nothing in
-- app/agent/* ever writes to monitoring.data_quality_results.

CREATE TABLE IF NOT EXISTS monitoring.ai_query_logs (
    query_log_id      TEXT PRIMARY KEY,
    requested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    provider               TEXT NOT NULL,
    model                    TEXT,
    used_real_inference        BOOLEAN NOT NULL,
    intent                        TEXT,
    metric_id                       TEXT,
    outcome                            TEXT NOT NULL CHECK (outcome IN ('success', 'clarification_needed', 'error')),
    error_category                        TEXT,
    latency_ms                               INTEGER,
    tool_invocation_count                       INTEGER NOT NULL DEFAULT 0,
    question_text                                  TEXT,
    answer_text                                       TEXT
);

CREATE INDEX IF NOT EXISTS ix_ai_query_logs_requested_at ON monitoring.ai_query_logs (requested_at);
CREATE INDEX IF NOT EXISTS ix_ai_query_logs_outcome ON monitoring.ai_query_logs (outcome);
CREATE INDEX IF NOT EXISTS ix_ai_query_logs_error_category ON monitoring.ai_query_logs (error_category);

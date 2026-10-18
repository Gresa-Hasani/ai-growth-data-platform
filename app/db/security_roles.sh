#!/bin/sh
# Phase 10: creates the least-privilege `analytics_ro` role the Analytics
# API and AI Agent use for every read request (see app/db/session.py's
# read_only_engine and docs/security_and_sql_validation.md).
#
# A plain .sql file mounted into docker-entrypoint-initdb.d cannot read
# environment variables, so this role (whose password comes from
# ANALYTICS_RO_PASSWORD) is created via a shell script instead, following
# the official postgres image's documented convention for scripts in that
# directory. Idempotent: safe to re-run against an already-initialized
# database (which docker-entrypoint-initdb.d scripts do NOT do
# automatically on a pre-existing data volume - see
# docs/security_and_sql_validation.md "Local setup" for the manual command
# to apply it to an already-running dev container).
set -e

: "${ANALYTICS_RO_PASSWORD:?ANALYTICS_RO_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'analytics_ro') THEN
        CREATE ROLE analytics_ro LOGIN PASSWORD '${ANALYTICS_RO_PASSWORD}';
    ELSE
        ALTER ROLE analytics_ro WITH LOGIN PASSWORD '${ANALYTICS_RO_PASSWORD}';
    END IF;
END
\$\$;

GRANT CONNECT ON DATABASE "$POSTGRES_DB" TO analytics_ro;
GRANT USAGE ON SCHEMA analytics TO analytics_ro;
GRANT USAGE ON SCHEMA monitoring TO analytics_ro;

-- Existing objects at the time this script runs (covers both a fresh
-- volume, where these schemas are still empty until dbt/the quality
-- scripts create their objects, and an already-populated dev database).
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO analytics_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA monitoring TO analytics_ro;

-- Future objects: dbt/ingestion/the quality runner all connect as
-- "$POSTGRES_USER" (the role executing this ALTER DEFAULT PRIVILEGES), so
-- every table they create in these two schemas from now on automatically
-- grants analytics_ro SELECT too, with no further manual step needed.
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO analytics_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA monitoring GRANT SELECT ON TABLES TO analytics_ro;

-- The ONE documented exception to "SELECT only": the AI agent's audit log.
-- No other table, in any schema, is writable by analytics_ro.
GRANT INSERT ON monitoring.ai_query_logs TO analytics_ro;
EOSQL

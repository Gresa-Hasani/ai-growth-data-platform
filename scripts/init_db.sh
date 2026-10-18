#!/usr/bin/env bash
# Applies every app/db/*.sql file plus app/db/security_roles.sh, in the
# exact same order docker-compose.yml mounts them into
# docker-entrypoint-initdb.d, against ANY already-running PostgreSQL
# instance - a disposable CI service container (see
# .github/workflows/ci.yml) or an existing local dev container whose data
# volume predates one of these files (docker-entrypoint-initdb.d only runs
# automatically on a volume's FIRST initialization - see
# docs/security_and_sql_validation.md "Local setup", which this script
# replaces the hand-typed `docker exec ...` workaround documented there).
#
# Inputs (environment variables, psql/libpq conventions):
#   PGHOST, PGPORT, PGPASSWORD  - how to reach Postgres
#   POSTGRES_USER, POSTGRES_DB  - the admin role/database to apply these against
#   ANALYTICS_RO_PASSWORD      - password for the analytics_ro role (security_roles.sh)
#
# Output: exits non-zero on the first failing script, printing which file
# failed and psql's own error - never continues past a failure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_DIR="$SCRIPT_DIR/app/db"

: "${POSTGRES_USER:?POSTGRES_USER must be set}"
: "${POSTGRES_DB:?POSTGRES_DB must be set}"
: "${ANALYTICS_RO_PASSWORD:?ANALYTICS_RO_PASSWORD must be set}"

for f in init_schemas.sql raw_tables.sql monitoring_tables.sql quality_tables.sql ai_query_logs.sql; do
    echo "==> applying $f"
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f "$DB_DIR/$f"
done

echo "==> applying security_roles.sh (creates the least-privilege analytics_ro role)"
bash "$DB_DIR/security_roles.sh"

echo "All init scripts applied successfully."

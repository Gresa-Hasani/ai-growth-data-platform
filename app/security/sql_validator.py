"""A defensive SQL validation boundary - NOT currently wired into any public
endpoint or AI Agent tool.

Phase 9's AI Data Analyst Agent never generates or executes SQL: it selects
a `metric_id` from a fixed catalog (app/agent/catalog.py), and that maps to
exactly one typed tool function (app/agent/tools.py) which calls an
existing, hardcoded, parameterized query in app/services/*.py. There is no
`execute_sql(sql)` tool and no code path that hands a client- or
model-supplied SQL string to this module today.

This module exists as a prepared, independently-tested defensive boundary
for a possible FUTURE controlled SQL-execution feature (or for validating
SQL from an already-trusted, explicitly-approved internal workflow) - it
must never be wired up as a way for a client or an LLM to run arbitrary
SQL. If that day comes, `validate_select_only()` is the gate a caller would
put in front of execution; today, nothing calls it outside this module's
own tests.

Design notes / limitations (documented, not glossed over):
- Uses `sqlglot` to parse PostgreSQL SQL into a structured AST - never a
  regex-only check. Comments, string literals, and nested expressions are
  handled by the parser, not by pattern-matching the raw text.
- Fails CLOSED: any parse error, any unsupported/ambiguous construct, is
  rejected rather than passed through.
- A statement that PASSES this validator is only known to be a single,
  syntactically well-formed, read-only `SELECT`/`WITH ... SELECT` referencing
  only allowlisted schemas and approved functions. Passing validation does
  NOT by itself make a query safe to run with elevated privileges, without
  a row limit, or without a statement timeout - callers remain responsible
  for execution-time bounds (see app/agent/config.py's
  llm_max_tool_calls/timeout pattern for the bounds this project actually
  uses on its real execution path).
- Table/column allowlisting here is at the SCHEMA level (`analytics`,
  `monitoring`) - it does not allowlist individual tables/columns. A finer
  per-table allowlist would duplicate app/agent/catalog.py's job; this
  module's job is structural statement safety, not business-metric scoping.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

DIALECT = "postgres"

# Only these schemas may ever be referenced by a validated statement - the
# same schemas the Analytics API and AI Agent are actually allowed to read
# (see app/main.py's _REQUIRED_ANALYTICS_RELATIONS and app/agent/catalog.py).
# System catalogs (pg_catalog, information_schema, pg_*) are deliberately
# excluded - nothing in this application has a legitimate need to query them
# at request time.
ALLOWED_SCHEMAS = frozenset({"analytics", "monitoring"})

# Function names that can modify data, read/write the filesystem, execute
# programs, change session/server behavior, or leak information about the
# server - rejected regardless of what schema (if any) they're called in.
# Matched case-insensitively against the bare function name.
DISALLOWED_FUNCTIONS = frozenset(
    {
        # filesystem / program execution / large objects
        "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_ls_logdir",
        "pg_ls_waldir", "pg_stat_file", "lo_import", "lo_export",
        "lo_read", "lo_write", "lo_creat", "lo_create", "lo_unlink",
        # cross-database / remote execution
        "dblink", "dblink_exec", "dblink_connect",
        # session / server state changes
        "set_config", "pg_reload_conf", "pg_rotate_logfile",
        "pg_terminate_backend", "pg_cancel_backend",
        "pg_create_restore_point", "pg_switch_wal", "pg_start_backup",
        "pg_stop_backup",
        # arbitrary code execution via procedural languages
        "pg_execute_server_program",
    }
)


class StatementCategory(str, Enum):
    SELECT_ONLY = "select_only"
    WRITE = "write"
    SCHEMA_CHANGE = "schema_change"
    PERMISSION_CHANGE = "permission_change"
    TRANSACTION_CONTROL = "transaction_control"
    ADMIN = "admin"
    UNKNOWN = "unknown"


# sqlglot expression types that are unambiguously not a read-only SELECT,
# mapped to a safe, specific rejection category for the error message.
_REJECTED_STATEMENT_TYPES: dict[type, StatementCategory] = {
    exp.Insert: StatementCategory.WRITE,
    exp.Update: StatementCategory.WRITE,
    exp.Delete: StatementCategory.WRITE,
    exp.Merge: StatementCategory.WRITE,
    exp.Create: StatementCategory.SCHEMA_CHANGE,
    exp.Alter: StatementCategory.SCHEMA_CHANGE,
    exp.Drop: StatementCategory.SCHEMA_CHANGE,
    exp.TruncateTable: StatementCategory.SCHEMA_CHANGE,
    exp.Grant: StatementCategory.PERMISSION_CHANGE,
    exp.Transaction: StatementCategory.TRANSACTION_CONTROL,
    exp.Commit: StatementCategory.TRANSACTION_CONTROL,
    exp.Rollback: StatementCategory.TRANSACTION_CONTROL,
    exp.Set: StatementCategory.ADMIN,
    exp.Command: StatementCategory.ADMIN,  # sqlglot's catch-all for anything it can't model (VACUUM, COPY-as-admin, etc.)
    exp.Copy: StatementCategory.ADMIN,
}


class SqlValidationError(Exception):
    """Base class. `category` is a safe, stable string suitable for logs/
    error responses - never the raw parser exception message, which could
    echo back attacker-supplied SQL text in a way that isn't necessarily
    safe to render verbatim everywhere."""

    category: str = "sql_rejected"


class MalformedSqlError(SqlValidationError):
    category = "malformed_sql"


class MultipleStatementsError(SqlValidationError):
    category = "multiple_statements"


class UnsupportedStatementError(SqlValidationError):
    category = "unsupported_statement"


class DisallowedSchemaError(SqlValidationError):
    category = "disallowed_schema"


class DisallowedFunctionError(SqlValidationError):
    category = "disallowed_function"


class DisallowedConstructError(SqlValidationError):
    category = "disallowed_construct"


@dataclass(frozen=True)
class ValidatedStatement:
    """The result of a successful validation - the normalized SQL plus what
    the validator observed, for a caller that wants to log/audit it."""

    sql: str
    dialect: str = field(default=DIALECT)
    referenced_tables: tuple[str, ...] = field(default_factory=tuple)


def classify_statement(sql: str) -> StatementCategory:
    """Best-effort classification for diagnostics/logging - never used on
    its own to authorize execution. Returns UNKNOWN on any parse failure,
    never raises. Use `validate_select_only` to actually gate execution."""
    try:
        statements = sqlglot.parse(sql, read=DIALECT)
    except (ParseError, TokenError):
        return StatementCategory.UNKNOWN
    if len(statements) != 1 or statements[0] is None:
        return StatementCategory.UNKNOWN
    node = statements[0]
    if isinstance(node, exp.Select):
        return StatementCategory.SELECT_ONLY
    for node_type, category in _REJECTED_STATEMENT_TYPES.items():
        if isinstance(node, node_type):
            return category
    return StatementCategory.UNKNOWN


def validate_select_only(sql: str) -> ValidatedStatement:
    """Validates that `sql` is exactly one syntactically well-formed,
    read-only SELECT (optionally with CTEs) that only references
    ALLOWED_SCHEMAS and does not call a disallowed function. Raises a typed
    SqlValidationError subclass on any violation - never returns a
    "best-effort" or partially-validated result. Fails closed on anything
    the parser cannot confidently classify.
    """
    if not sql or not sql.strip():
        raise MalformedSqlError("SQL statement must not be empty")

    try:
        statements = sqlglot.parse(sql, read=DIALECT, error_level=sqlglot.ErrorLevel.RAISE)
    except (ParseError, TokenError) as exc:
        raise MalformedSqlError("SQL could not be parsed as valid PostgreSQL syntax") from exc

    # sqlglot.parse splits on top-level `;` - more than one non-empty
    # statement means a stacked/batched query, rejected outright regardless
    # of what each individual statement looks like.
    non_empty_statements = [s for s in statements if s is not None]
    if len(non_empty_statements) != 1:
        raise MultipleStatementsError(
            f"expected exactly one SQL statement, found {len(non_empty_statements)}"
        )

    node = non_empty_statements[0]

    if not isinstance(node, exp.Select):
        category = next(
            (cat for node_type, cat in _REJECTED_STATEMENT_TYPES.items() if isinstance(node, node_type)),
            StatementCategory.UNKNOWN,
        )
        raise UnsupportedStatementError(
            f"only read-only SELECT statements are permitted (rejected: {category.value})"
        )

    # `SELECT ... INTO <table>` creates a table - structurally a SELECT to
    # the parser, but schema-changing in effect. Must be rejected explicitly.
    if node.args.get("into") is not None:
        raise DisallowedConstructError("'SELECT ... INTO' is not permitted (creates a table)")

    # Locking clauses (FOR UPDATE / FOR SHARE / FOR NO KEY UPDATE) take row
    # locks and can interact with write transactions elsewhere - out of
    # scope for a read-only validation boundary.
    if node.args.get("locks"):
        raise DisallowedConstructError("locking reads (FOR UPDATE/FOR SHARE) are not permitted")

    # CTE names (e.g. the "recent" in `WITH recent AS (...) SELECT ... FROM
    # recent`) are not real tables and are never schema-qualified - exclude
    # them from the schema-allowlist check below. The CTE's own body is
    # still walked by find_all() like any other subquery, so a real table
    # referenced *inside* a CTE is still validated normally.
    cte_names = {cte.alias.lower() for cte in node.find_all(exp.CTE) if cte.alias}

    referenced_tables: list[str] = []
    for table in node.find_all(exp.Table):
        if not table.db and table.name.lower() in cte_names:
            continue
        schema = (table.db or "").lower()
        qualified_name = f"{schema}.{table.name}" if schema else table.name
        if not schema:
            raise DisallowedSchemaError(
                f"table reference '{table.name}' must be schema-qualified (e.g. 'analytics.{table.name}')"
            )
        if schema not in ALLOWED_SCHEMAS:
            raise DisallowedSchemaError(f"schema '{schema}' is not in the approved allowlist {sorted(ALLOWED_SCHEMAS)}")
        referenced_tables.append(qualified_name)

    for func in node.find_all(exp.Func):
        func_name = (func.name or "").lower() if isinstance(func, exp.Anonymous) else (func.sql_name() or "").lower()
        if func_name in DISALLOWED_FUNCTIONS:
            raise DisallowedFunctionError(f"function '{func_name}' is not permitted")

    return ValidatedStatement(sql=node.sql(dialect=DIALECT), referenced_tables=tuple(referenced_tables))

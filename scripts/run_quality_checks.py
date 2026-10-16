"""Data quality check runner (Phase 7).

Usage:
    python scripts/run_quality_checks.py
    python scripts/run_quality_checks.py --category referential
    python scripts/run_quality_checks.py --json
    python scripts/run_quality_checks.py --show-latest
    python scripts/run_quality_checks.py --verbose

Every check's outcome is persisted to monitoring.data_quality_results,
grouped under one monitoring.quality_runs row per execution - see
docs/data_quality_observability.md for the full architecture, the check
catalog's rationale, and the PASS/WARN/FAIL/exit-code semantics this module
implements.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg2
import psycopg2.extras

from ingestion.db import get_connection
from ingestion.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Status / severity semantics (see docs/data_quality_observability.md
# "PASS/WARN/FAIL semantics" for the full rationale)
# ---------------------------------------------------------------------------
# status   - what a check itself concluded about the data: PASS, WARN, FAIL.
# severity - a fixed property of the check (not computed per-run): how
#            seriously the *run* should treat a non-PASS result from it.
#              ERROR: a FAIL here fails the whole run (exit code 1).
#              WARN:  a WARN/FAIL here is surfaced but never fails the run
#                     by itself - this is where Phase 2/3's documented,
#                     intentional synthetic-data anomalies live.
#              INFO:  purely informational; never affects run status.
# A check raising an exception is recorded as status=FAIL with
# error_message populated - it is never silently dropped.

EXIT_PASS_OR_WARN = 0
EXIT_FAIL = 1
EXIT_RUNNER_ERROR = 2


@dataclass
class CheckResult:
    test_name: str
    model_name: str | None
    category: str
    severity: str  # INFO | WARN | ERROR
    status: str  # PASS | WARN | FAIL
    failed_rows: int | None = None
    total_rows: int | None = None
    failure_rate: float | None = None
    details: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    duration_ms: int | None = None


# ---------------------------------------------------------------------------
# Config (CLI-overridable thresholds - see docs for the documented defaults)
# ---------------------------------------------------------------------------


@dataclass
class QualityCheckConfig:
    freshness_warn_hours: int = 24
    freshness_error_hours: int = 48
    # Orphan rates: Phase 2/3 intentionally inject ~0.6% orphaned FKs.
    # warn above the expected rate, fail only if it balloons far beyond it
    # (a real regression, not synthetic noise).
    orphan_warn_rate: float = 0.02
    orphan_fail_rate: float = 0.15
    volume_drop_warn_pct: float = 0.20
    volume_drop_fail_pct: float = 0.50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _relation_exists(cur, schema: str, name: str) -> bool:
    cur.execute(
        "SELECT to_regclass(%s) IS NOT NULL",
        (f"{schema}.{name}",),
    )
    return bool(cur.fetchone()[0])


def _scalar(cur, sql: str, params: tuple = ()) -> Any:
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def _previous_result_details(cur, test_name: str) -> dict | None:
    """Most recent prior persisted result for this test_name, for baseline
    comparisons - returns None (not a fabricated baseline) if none exists."""
    cur.execute(
        """
        SELECT details FROM monitoring.data_quality_results
        WHERE test_name = %s
        ORDER BY executed_at DESC
        LIMIT 1
        """,
        (test_name,),
    )
    row = cur.fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# A. Structural integrity
# ---------------------------------------------------------------------------


def check_required_relations_exist(conn, config: QualityCheckConfig) -> CheckResult:
    required = [
        ("raw", "users"), ("raw", "organizations"), ("raw", "subscriptions"),
        ("raw", "invoices"), ("raw", "product_events"), ("raw", "api_usage"),
        ("raw", "crm_accounts"), ("raw", "marketing_events"),
        ("staging", "stg_app__users"), ("staging", "stg_billing__subscriptions"),
        ("intermediate", "int_customer_revenue"),
        ("analytics", "dim_users"), ("analytics", "dim_organizations"),
        ("analytics", "fact_subscriptions"), ("analytics", "fact_invoices"),
        ("analytics", "fact_product_usage"), ("analytics", "fact_api_usage"),
        ("analytics", "mart_mrr"), ("analytics", "mart_customer_churn"),
        ("snapshots", "snap_subscriptions"), ("snapshots", "snap_crm_accounts"),
        ("monitoring", "ingestion_runs"), ("monitoring", "quality_runs"),
    ]
    with conn.cursor() as cur:
        missing = [f"{s}.{n}" for s, n in required if not _relation_exists(cur, s, n)]
    status = "FAIL" if missing else "PASS"
    return CheckResult(
        test_name="required_relations_exist",
        model_name=None,
        category="structural",
        severity="ERROR",
        status=status,
        failed_rows=len(missing),
        total_rows=len(required),
        failure_rate=len(missing) / len(required) if required else None,
        details={"missing": missing, "checked": len(required)},
    )


def check_fact_primary_key_uniqueness(conn, config: QualityCheckConfig) -> CheckResult:
    targets = [
        ("analytics.fact_subscriptions", "subscription_id"),
        ("analytics.fact_invoices", "invoice_id"),
    ]
    duplicates: dict[str, int] = {}
    with conn.cursor() as cur:
        for table, key in targets:
            cur.execute(
                f"SELECT count(*) FROM (SELECT {key} FROM {table} GROUP BY {key} HAVING count(*) > 1) x"
            )
            n = cur.fetchone()[0]
            if n:
                duplicates[table] = n
    status = "FAIL" if duplicates else "PASS"
    return CheckResult(
        test_name="fact_primary_key_uniqueness",
        model_name="fact_subscriptions,fact_invoices",
        category="structural",
        severity="ERROR",
        status=status,
        failed_rows=sum(duplicates.values()),
        details={"duplicate_keys_by_table": duplicates},
    )


def check_critical_tables_not_empty(conn, config: QualityCheckConfig) -> CheckResult:
    tables = [
        "raw.users", "raw.organizations", "raw.subscriptions", "raw.invoices",
        "staging.stg_app__users", "analytics.dim_users", "analytics.fact_invoices",
    ]
    empty: list[str] = []
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in tables:
            cur.execute(f"SELECT count(*) FROM {table}")
            n = cur.fetchone()[0]
            counts[table] = n
            if n == 0:
                empty.append(table)
    status = "FAIL" if empty else "PASS"
    return CheckResult(
        test_name="critical_tables_not_empty",
        model_name=None,
        category="structural",
        severity="ERROR",
        status=status,
        failed_rows=len(empty),
        total_rows=len(tables),
        details={"row_counts": counts, "unexpectedly_empty": empty},
    )


# ---------------------------------------------------------------------------
# B. Referential integrity
# ---------------------------------------------------------------------------


def _orphan_rate_check(
    conn, config: QualityCheckConfig, *, test_name: str, model_name: str,
    child_table: str, fk_column: str, parent_table: str, parent_key: str,
) -> CheckResult:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT count(*) FILTER (WHERE child.{fk_column} is not null and parent.{parent_key} is null),
                   count(*)
            FROM {child_table} child
            LEFT JOIN {parent_table} parent ON child.{fk_column} = parent.{parent_key}
            """
        )
        orphans, total = cur.fetchone()
    rate = (orphans / total) if total else 0.0
    if rate > config.orphan_fail_rate:
        status = "FAIL"
    elif rate > config.orphan_warn_rate:
        status = "WARN"
    else:
        status = "PASS"
    return CheckResult(
        test_name=test_name,
        model_name=model_name,
        category="referential",
        # WARN severity: Phase 2/3 intentionally inject orphaned FKs at a
        # low rate - this is documented, expected synthetic behavior, not
        # a production defect. A FAIL status (rate blew past orphan_fail_rate)
        # still surfaces loudly, it just doesn't fail the overall run unless
        # promoted - see docs for the rationale.
        severity="WARN",
        status=status,
        failed_rows=orphans,
        total_rows=total,
        failure_rate=round(rate, 4),
        details={
            "orphan_warn_rate_threshold": config.orphan_warn_rate,
            "orphan_fail_rate_threshold": config.orphan_fail_rate,
            "note": "Low orphan rates are an intentional Phase 2 synthetic-data behavior, not a defect.",
        },
    )


def check_orphaned_subscription_users(conn, config: QualityCheckConfig) -> CheckResult:
    return _orphan_rate_check(
        conn, config,
        test_name="orphaned_subscription_users",
        model_name="stg_billing__subscriptions",
        child_table="staging.stg_billing__subscriptions", fk_column="user_id",
        parent_table="staging.stg_app__users", parent_key="user_id",
    )


def check_orphaned_product_event_users(conn, config: QualityCheckConfig) -> CheckResult:
    return _orphan_rate_check(
        conn, config,
        test_name="orphaned_product_event_users",
        model_name="stg_product__events",
        child_table="staging.stg_product__events", fk_column="user_id",
        parent_table="staging.stg_app__users", parent_key="user_id",
    )


def check_orphaned_invoice_subscriptions(conn, config: QualityCheckConfig) -> CheckResult:
    return _orphan_rate_check(
        conn, config,
        test_name="orphaned_invoice_subscriptions",
        model_name="stg_billing__invoices",
        child_table="staging.stg_billing__invoices", fk_column="subscription_id",
        parent_table="staging.stg_billing__subscriptions", parent_key="subscription_id",
    )


def check_orphaned_crm_organizations(conn, config: QualityCheckConfig) -> CheckResult:
    return _orphan_rate_check(
        conn, config,
        test_name="orphaned_crm_organizations",
        model_name="stg_crm__accounts",
        child_table="staging.stg_crm__accounts", fk_column="organization_id",
        parent_table="staging.stg_app__organizations", parent_key="organization_id",
    )


# ---------------------------------------------------------------------------
# C. Business rules
# ---------------------------------------------------------------------------


def check_invoice_amount_semantics(conn, config: QualityCheckConfig) -> CheckResult:
    """paid_amount/outstanding_amount must never exceed billed_amount, and
    no monetary amount may be negative - checked within each currency
    separately (never summed across currencies, though this check is
    row-level so cross-currency mixing isn't possible here anyway)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*), count(*) FILTER (
                WHERE paid_amount > billed_amount + 0.01
                   OR outstanding_amount > billed_amount + 0.01
                   OR billed_amount < 0 OR paid_amount < 0 OR outstanding_amount < 0
            )
            FROM analytics.fact_invoices
            """
        )
        total, failed = cur.fetchone()
    status = "FAIL" if failed else "PASS"
    return CheckResult(
        test_name="invoice_amount_semantics",
        model_name="fact_invoices",
        category="business",
        severity="ERROR",
        status=status,
        failed_rows=failed,
        total_rows=total,
        failure_rate=round(failed / total, 4) if total else None,
    )


def check_mrr_arr_relationship_by_currency(conn, config: QualityCheckConfig) -> CheckResult:
    """arr must equal mrr * 12 for every currency - never aggregated
    across currencies (the join itself is on currency, so a mismatch here
    is a real logic drift, not a cross-currency mixing bug)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT mart_mrr.currency, mart_mrr.mrr, mart_arr.arr
            FROM analytics.mart_mrr
            JOIN analytics.mart_arr ON mart_mrr.currency = mart_arr.currency
            WHERE round(mart_mrr.mrr * 12, 2) != round(mart_arr.arr, 2)
            """
        )
        mismatches = cur.fetchall()
        cur.execute("SELECT count(*) FROM analytics.mart_mrr")
        total = cur.fetchone()[0]
    status = "FAIL" if mismatches else "PASS"
    return CheckResult(
        test_name="mrr_arr_relationship_by_currency",
        model_name="mart_mrr,mart_arr",
        category="business",
        severity="ERROR",
        status=status,
        failed_rows=len(mismatches),
        total_rows=total,
        details={"mismatched_currencies": [row[0] for row in mismatches]},
    )


def check_rates_within_valid_range(conn, config: QualityCheckConfig) -> CheckResult:
    """Every 0-1 fraction-typed column across the marts must stay in [0, 1]."""
    checks = [
        ("analytics.mart_customer_churn", "churn_rate"),
        ("analytics.mart_revenue_churn", "gross_revenue_churn_rate"),
        ("analytics.mart_retention_cohorts", "retention_rate"),
        ("analytics.mart_feature_adoption", "adoption_rate"),
        ("analytics.mart_acquisition_channels", "trial_to_paid_conversion_rate"),
        ("analytics.fact_api_usage", "error_rate"),
    ]
    violations: dict[str, int] = {}
    with conn.cursor() as cur:
        for table, column in checks:
            cur.execute(
                f"SELECT count(*) FROM {table} WHERE {column} is not null AND ({column} < 0 OR {column} > 1)"
            )
            n = cur.fetchone()[0]
            if n:
                violations[f"{table}.{column}"] = n
    status = "FAIL" if violations else "PASS"
    return CheckResult(
        test_name="rates_within_valid_range",
        model_name=None,
        category="business",
        severity="ERROR",
        status=status,
        failed_rows=sum(violations.values()),
        details={"violations_by_column": violations},
    )


def check_no_negative_monetary_or_count_values(conn, config: QualityCheckConfig) -> CheckResult:
    checks = [
        ("analytics.fact_subscriptions", "subscription_amount"),
        ("analytics.fact_subscriptions", "monthly_equivalent_amount"),
        ("analytics.fact_api_usage", "request_count"),
        ("analytics.fact_product_usage", "event_count"),
    ]
    violations: dict[str, int] = {}
    with conn.cursor() as cur:
        for table, column in checks:
            cur.execute(f"SELECT count(*) FROM {table} WHERE {column} is not null AND {column} < 0")
            n = cur.fetchone()[0]
            if n:
                violations[f"{table}.{column}"] = n
    status = "FAIL" if violations else "PASS"
    return CheckResult(
        test_name="no_negative_monetary_or_count_values",
        model_name=None,
        category="business",
        severity="ERROR",
        status=status,
        failed_rows=sum(violations.values()),
        details={"violations_by_column": violations},
    )


# ---------------------------------------------------------------------------
# D. Freshness and incremental processing
# ---------------------------------------------------------------------------


def check_raw_source_freshness(conn, config: QualityCheckConfig) -> CheckResult:
    tables = [
        "raw.users", "raw.organizations", "raw.subscriptions", "raw.invoices",
        "raw.product_events", "raw.api_usage", "raw.crm_accounts", "raw.marketing_events",
    ]
    stale_warn: list[str] = []
    stale_fail: list[str] = []
    ages: dict[str, float | None] = {}
    with conn.cursor() as cur:
        for table in tables:
            cur.execute(f"SELECT extract(epoch FROM now() - max(_ingested_at)) / 3600.0 FROM {table}")
            age_hours = cur.fetchone()[0]
            ages[table] = float(age_hours) if age_hours is not None else None
            if age_hours is None:
                continue  # empty table - covered by check_critical_tables_not_empty, not freshness
            if age_hours > config.freshness_error_hours:
                stale_fail.append(table)
            elif age_hours > config.freshness_warn_hours:
                stale_warn.append(table)
    status = "FAIL" if stale_fail else ("WARN" if stale_warn else "PASS")
    return CheckResult(
        test_name="raw_source_freshness",
        model_name=None,
        category="freshness",
        severity="WARN",  # mirrors dbt source freshness's own severity policy
        status=status,
        failed_rows=len(stale_fail) + len(stale_warn),
        total_rows=len(tables),
        details={
            "age_hours_by_table": ages,
            "warn_threshold_hours": config.freshness_warn_hours,
            "error_threshold_hours": config.freshness_error_hours,
            "stale_warn": stale_warn,
            "stale_fail": stale_fail,
        },
    )


def check_incremental_fact_watermarks(conn, config: QualityCheckConfig) -> CheckResult:
    """Each incremental fact's own high-water mark (_last_ingested_at)
    should not lag far behind its raw source's latest _ingested_at -  a
    large gap suggests the incremental model hasn't been run recently,
    not a correctness bug (see docs/incremental_processing.md)."""
    pairs = [
        ("analytics.fact_product_usage", "raw.product_events"),
        ("analytics.fact_api_usage", "raw.api_usage"),
        ("analytics.fact_invoices", "raw.invoices"),
    ]
    lag_hours: dict[str, float | None] = {}
    lagging: list[str] = []
    # A watermark more than (lookback + this buffer) behind the source is
    # worth flagging - the lookback window itself is expected/by design.
    buffer_hours = 24
    with conn.cursor() as cur:
        for fact_table, raw_table in pairs:
            cur.execute(
                f"""
                SELECT extract(epoch FROM
                    (SELECT max(_ingested_at) FROM {raw_table})
                    - (SELECT max(_last_ingested_at) FROM {fact_table})
                ) / 3600.0
                """
            )
            lag = cur.fetchone()[0]
            lag_hours[fact_table] = float(lag) if lag is not None else None
            if lag is not None and lag > buffer_hours:
                lagging.append(fact_table)
    status = "WARN" if lagging else "PASS"
    return CheckResult(
        test_name="incremental_fact_watermarks",
        model_name="fact_product_usage,fact_api_usage,fact_invoices",
        category="freshness",
        severity="WARN",
        status=status,
        failed_rows=len(lagging),
        total_rows=len(pairs),
        details={"lag_hours_by_fact": lag_hours, "buffer_hours": buffer_hours, "lagging": lagging},
    )


# ---------------------------------------------------------------------------
# E. Snapshot integrity
# ---------------------------------------------------------------------------


def check_snapshot_current_record_uniqueness(conn, config: QualityCheckConfig) -> CheckResult:
    snapshots = [
        ("snapshots.snap_subscriptions", "subscription_id"),
        ("snapshots.snap_crm_accounts", "crm_account_id"),
    ]
    violations: dict[str, int] = {}
    with conn.cursor() as cur:
        for table, key in snapshots:
            cur.execute(
                f"""
                SELECT count(*) FROM (
                    SELECT {key} FROM {table} WHERE dbt_valid_to IS NULL GROUP BY {key} HAVING count(*) > 1
                ) x
                """
            )
            n = cur.fetchone()[0]
            if n:
                violations[table] = n
    status = "FAIL" if violations else "PASS"
    return CheckResult(
        test_name="snapshot_current_record_uniqueness",
        model_name="snap_subscriptions,snap_crm_accounts",
        category="snapshot",
        severity="ERROR",
        status=status,
        failed_rows=sum(violations.values()),
        details={"duplicate_current_records_by_snapshot": violations},
    )


def check_snapshot_valid_intervals(conn, config: QualityCheckConfig) -> CheckResult:
    """dbt_valid_from must never be null, and a closed row's dbt_valid_to
    must be strictly after dbt_valid_from. Does NOT claim this snapshot
    can recover any change that happened before its first run - see
    docs/incremental_processing.md "Snapshot limitations"."""
    snapshots = ["snapshots.snap_subscriptions", "snapshots.snap_crm_accounts"]
    violations: dict[str, int] = {}
    total = 0
    with conn.cursor() as cur:
        for table in snapshots:
            cur.execute(
                f"""
                SELECT count(*) FROM {table}
                WHERE dbt_valid_from IS NULL
                   OR (dbt_valid_to IS NOT NULL AND dbt_valid_to <= dbt_valid_from)
                """
            )
            n = cur.fetchone()[0]
            if n:
                violations[table] = n
            cur.execute(f"SELECT count(*) FROM {table}")
            total += cur.fetchone()[0]
    status = "FAIL" if violations else "PASS"
    return CheckResult(
        test_name="snapshot_valid_intervals",
        model_name="snap_subscriptions,snap_crm_accounts",
        category="snapshot",
        severity="ERROR",
        status=status,
        failed_rows=sum(violations.values()),
        total_rows=total,
        details={"malformed_intervals_by_snapshot": violations},
    )


# ---------------------------------------------------------------------------
# F. Volume and anomaly detection
# ---------------------------------------------------------------------------


def check_row_count_volume_drop(conn, config: QualityCheckConfig) -> CheckResult:
    """Compares each watched table's current row count against the most
    recently PERSISTED observation of this same check - never an invented
    baseline. If no prior run exists, reports PASS with
    baseline_available=False rather than guessing."""
    tables = [
        "raw.users", "raw.organizations", "raw.subscriptions", "raw.invoices",
        "raw.product_events", "raw.api_usage",
    ]
    current_counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in tables:
            cur.execute(f"SELECT count(*) FROM {table}")
            current_counts[table] = cur.fetchone()[0]

        previous = _previous_result_details(cur, "row_count_volume_drop")

    if not previous or "row_counts" not in previous:
        return CheckResult(
            test_name="row_count_volume_drop",
            model_name=None,
            category="volume",
            severity="WARN",
            status="PASS",
            details={"row_counts": current_counts, "baseline_available": False},
        )

    previous_counts = previous["row_counts"]
    drops_warn: dict[str, float] = {}
    drops_fail: dict[str, float] = {}
    for table, current_n in current_counts.items():
        prev_n = previous_counts.get(table)
        if not prev_n:
            continue
        pct_drop = (prev_n - current_n) / prev_n
        if pct_drop > config.volume_drop_fail_pct:
            drops_fail[table] = round(pct_drop, 4)
        elif pct_drop > config.volume_drop_warn_pct:
            drops_warn[table] = round(pct_drop, 4)

    status = "FAIL" if drops_fail else ("WARN" if drops_warn else "PASS")
    return CheckResult(
        test_name="row_count_volume_drop",
        model_name=None,
        category="volume",
        severity="WARN",
        status=status,
        details={
            "row_counts": current_counts,
            "baseline_available": True,
            "previous_row_counts": previous_counts,
            "drops_warn": drops_warn,
            "drops_fail": drops_fail,
            "warn_threshold_pct": config.volume_drop_warn_pct,
            "fail_threshold_pct": config.volume_drop_fail_pct,
        },
    )


CHECK_CATALOG: list[tuple[str, Callable[[Any, QualityCheckConfig], CheckResult]]] = [
    ("structural", check_required_relations_exist),
    ("structural", check_fact_primary_key_uniqueness),
    ("structural", check_critical_tables_not_empty),
    ("referential", check_orphaned_subscription_users),
    ("referential", check_orphaned_product_event_users),
    ("referential", check_orphaned_invoice_subscriptions),
    ("referential", check_orphaned_crm_organizations),
    ("business", check_invoice_amount_semantics),
    ("business", check_mrr_arr_relationship_by_currency),
    ("business", check_rates_within_valid_range),
    ("business", check_no_negative_monetary_or_count_values),
    ("freshness", check_raw_source_freshness),
    ("freshness", check_incremental_fact_watermarks),
    ("snapshot", check_snapshot_current_record_uniqueness),
    ("snapshot", check_snapshot_valid_intervals),
    ("volume", check_row_count_volume_drop),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def new_run_id() -> str:
    return f"qrun_{uuid.uuid4().hex[:16]}"


def run_checks(
    conn,
    category: str | None = None,
    config: QualityCheckConfig | None = None,
) -> tuple[str, list[CheckResult], str]:
    """Executes the catalog (optionally filtered by category), persisting
    every result as it completes. Returns (run_id, results, overall_status).
    """
    config = config or QualityCheckConfig()
    run_id = new_run_id()
    started_at = datetime.now(UTC)

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO monitoring.quality_runs (run_id, started_at, overall_status) VALUES (%s, %s, 'FAIL')",
            (run_id, started_at),
        )
    conn.commit()

    results: list[CheckResult] = []
    for check_category, check_fn in CHECK_CATALOG:
        if category and category != "all" and category != check_category:
            continue

        start = time.monotonic()
        try:
            result = check_fn(conn, config)
            result.category = check_category
        except Exception as exc:  # noqa: BLE001 - must never silently disappear
            conn.rollback()
            result = CheckResult(
                test_name=check_fn.__name__,
                model_name=None,
                category=check_category,
                severity="ERROR",
                status="FAIL",
                error_message=str(exc),
                details={"exception_type": type(exc).__name__},
            )
            logger.error("quality_check_raised", test_name=result.test_name, error=str(exc))
        result.duration_ms = round((time.monotonic() - start) * 1000)
        results.append(result)

        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO monitoring.data_quality_results (
                        run_id, test_name, model_name, category, severity, status,
                        failed_rows, total_rows, failure_rate, duration_ms, details, error_message
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id, result.test_name, result.model_name, result.category,
                        result.severity, result.status, result.failed_rows, result.total_rows,
                        result.failure_rate, result.duration_ms,
                        psycopg2.extras.Json(result.details, dumps=lambda v: json.dumps(v, default=str)),
                        result.error_message,
                    ),
                )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            logger.error("quality_result_persist_failed", test_name=result.test_name, error=str(exc))

        logger.info(
            "quality_check_complete",
            run_id=run_id, test_name=result.test_name, status=result.status,
            severity=result.severity, duration_ms=result.duration_ms,
        )

    overall_status = _overall_status(results)
    finished_at = datetime.now(UTC)
    duration_ms = round((finished_at - started_at).total_seconds() * 1000)

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE monitoring.quality_runs
            SET finished_at = %s, overall_status = %s, total_checks = %s,
                passed_checks = %s, warned_checks = %s, failed_checks = %s, duration_ms = %s
            WHERE run_id = %s
            """,
            (
                finished_at, overall_status, len(results),
                sum(1 for r in results if r.status == "PASS"),
                sum(1 for r in results if r.status == "WARN"),
                sum(1 for r in results if r.status == "FAIL"),
                duration_ms, run_id,
            ),
        )
    conn.commit()

    return run_id, results, overall_status


def _overall_status(results: list[CheckResult]) -> str:
    """A FAIL at severity=ERROR fails the whole run. Anything else that
    isn't a clean PASS (a WARN, or a FAIL at severity WARN/INFO - e.g. an
    expected, documented synthetic-data anomaly) downgrades the run to
    WARN without failing it. See docs/data_quality_observability.md."""
    if any(r.status == "FAIL" and r.severity == "ERROR" for r in results):
        return "FAIL"
    if any(r.status in ("WARN", "FAIL") for r in results):
        return "WARN"
    return "PASS"


def exit_code_for(overall_status: str) -> int:
    if overall_status == "FAIL":
        return EXIT_FAIL
    return EXIT_PASS_OR_WARN


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------


def print_summary(run_id: str, results: list[CheckResult], overall_status: str, verbose: bool) -> None:
    print(f"\nQuality run {run_id} - overall status: {overall_status}\n")
    print(f"{'STATUS':<6} {'SEVERITY':<8} {'CATEGORY':<12} {'TEST':<42} {'FAILED/TOTAL':<14} DURATION")
    for r in results:
        failed_total = (
            f"{r.failed_rows}/{r.total_rows}" if r.total_rows is not None else (str(r.failed_rows) if r.failed_rows is not None else "-")
        )
        print(f"{r.status:<6} {r.severity:<8} {r.category:<12} {r.test_name:<42} {failed_total:<14} {r.duration_ms}ms")
        if r.error_message:
            print(f"       error: {r.error_message}")
        if verbose and r.details:
            print(f"       details: {json.dumps(r.details, default=str)}")

    passed = sum(1 for r in results if r.status == "PASS")
    warned = sum(1 for r in results if r.status == "WARN")
    failed = sum(1 for r in results if r.status == "FAIL")
    print(f"\n{len(results)} checks: {passed} passed, {warned} warned, {failed} failed\n")


def print_latest_run(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT run_id, started_at, finished_at, overall_status, total_checks,
                   passed_checks, warned_checks, failed_checks, duration_ms
            FROM monitoring.quality_runs
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if not row:
            print("No quality runs have been persisted yet.")
            return EXIT_PASS_OR_WARN

        run_id = row[0]
        print(
            f"Latest run {run_id}: {row[3]} ({row[5]} passed, {row[6]} warned, {row[7]} failed "
            f"of {row[4]} checks) started {row[1]}, duration {row[8]}ms"
        )

        cur.execute(
            """
            SELECT test_name, category, severity, status, failed_rows, total_rows, error_message
            FROM monitoring.data_quality_results
            WHERE run_id = %s
            ORDER BY category, test_name
            """,
            (run_id,),
        )
        for test_name, cat, severity, status, failed, total, err in cur.fetchall():
            ft = f"{failed}/{total}" if total is not None else (str(failed) if failed is not None else "-")
            print(f"  [{status:<4}] {severity:<5} {cat:<12} {test_name:<42} {ft}" + (f"  ERROR: {err}" if err else ""))
    return EXIT_PASS_OR_WARN


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 7 data quality check suite.")
    parser.add_argument(
        "--category",
        choices=["all", "structural", "referential", "business", "freshness", "snapshot", "volume"],
        default="all",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a table.")
    parser.add_argument("--verbose", action="store_true", help="Print each check's structured details.")
    parser.add_argument("--show-latest", action="store_true", help="Print the most recent persisted run and exit.")
    parser.add_argument("--freshness-warn-hours", type=int, default=24)
    parser.add_argument("--freshness-error-hours", type=int, default=48)
    parser.add_argument("--orphan-warn-rate", type=float, default=0.02)
    parser.add_argument("--orphan-fail-rate", type=float, default=0.15)
    parser.add_argument("--volume-drop-warn-pct", type=float, default=0.20)
    parser.add_argument("--volume-drop-fail-pct", type=float, default=0.50)
    args = parser.parse_args()

    try:
        with get_connection() as conn:
            if args.show_latest:
                return print_latest_run(conn)

            config = QualityCheckConfig(
                freshness_warn_hours=args.freshness_warn_hours,
                freshness_error_hours=args.freshness_error_hours,
                orphan_warn_rate=args.orphan_warn_rate,
                orphan_fail_rate=args.orphan_fail_rate,
                volume_drop_warn_pct=args.volume_drop_warn_pct,
                volume_drop_fail_pct=args.volume_drop_fail_pct,
            )
            run_id, results, overall_status = run_checks(conn, category=args.category, config=config)
    except psycopg2.OperationalError as exc:
        print(f"ERROR: could not connect to the monitoring database: {exc}", file=sys.stderr)
        return EXIT_RUNNER_ERROR

    if args.json:
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "overall_status": overall_status,
                    "results": [
                        {
                            "test_name": r.test_name,
                            "model_name": r.model_name,
                            "category": r.category,
                            "severity": r.severity,
                            "status": r.status,
                            "failed_rows": r.failed_rows,
                            "total_rows": r.total_rows,
                            "failure_rate": r.failure_rate,
                            "duration_ms": r.duration_ms,
                            "details": r.details,
                            "error_message": r.error_message,
                        }
                        for r in results
                    ],
                },
                indent=2,
                default=str,
            )
        )
    else:
        print_summary(run_id, results, overall_status, verbose=args.verbose)

    return exit_code_for(overall_status)


if __name__ == "__main__":
    sys.exit(main())

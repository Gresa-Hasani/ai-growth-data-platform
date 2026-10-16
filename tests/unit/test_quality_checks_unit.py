"""Unit tests for the Phase 7 quality-check runner's pure logic: status/
severity -> overall-run-status mapping, exit codes, run_id uniqueness, and
honest handling of a monitoring-database connection failure. None of these
require a live database.
"""
from __future__ import annotations

from unittest.mock import patch

import psycopg2
import pytest

from scripts.run_quality_checks import (
    EXIT_FAIL,
    EXIT_PASS_OR_WARN,
    EXIT_RUNNER_ERROR,
    CheckResult,
    _overall_status,
    exit_code_for,
    main,
    new_run_id,
)


def _result(status: str, severity: str) -> CheckResult:
    return CheckResult(test_name="t", model_name=None, category="structural", severity=severity, status=status)


def test_overall_status_all_pass_is_pass():
    results = [_result("PASS", "ERROR"), _result("PASS", "WARN")]
    assert _overall_status(results) == "PASS"


def test_overall_status_warn_at_warn_severity_does_not_fail():
    results = [_result("PASS", "ERROR"), _result("WARN", "WARN")]
    assert _overall_status(results) == "WARN"


def test_overall_status_fail_at_warn_severity_downgrades_to_warn_not_fail():
    """An expected, documented synthetic-data anomaly (e.g. orphan rate
    breaching its fail threshold) is still only WARN severity by design -
    it must never fail the whole run on its own."""
    results = [_result("PASS", "ERROR"), _result("FAIL", "WARN")]
    assert _overall_status(results) == "WARN"


def test_overall_status_fail_at_error_severity_fails_the_run():
    results = [_result("PASS", "WARN"), _result("FAIL", "ERROR")]
    assert _overall_status(results) == "FAIL"


def test_overall_status_empty_results_is_pass():
    assert _overall_status([]) == "PASS"


@pytest.mark.parametrize(
    "status,expected_exit",
    [("PASS", EXIT_PASS_OR_WARN), ("WARN", EXIT_PASS_OR_WARN), ("FAIL", EXIT_FAIL)],
)
def test_exit_code_mapping(status, expected_exit):
    assert exit_code_for(status) == expected_exit


def test_run_id_is_unique_and_well_formed():
    ids = {new_run_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(i.startswith("qrun_") for i in ids)


def test_monitoring_database_connection_failure_is_handled_honestly():
    """main() must return a distinct, nonzero exit code (not pretend
    checks ran, not crash with an unhandled traceback) when the
    monitoring database itself is unreachable."""
    with patch("scripts.run_quality_checks.get_connection") as mock_get_connection:
        mock_get_connection.side_effect = psycopg2.OperationalError("could not connect")
        with patch("sys.argv", ["run_quality_checks.py"]):
            exit_code = main()
    assert exit_code == EXIT_RUNNER_ERROR
    assert EXIT_RUNNER_ERROR != EXIT_FAIL  # distinct from "checks ran and found bad data"

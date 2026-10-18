"""Execution/answer tests - no live database. The DB `Session` is a stand-in
object; each test monkeypatches the specific app.services.* function the
executor is expected to call, so these tests prove (a) correct tool
selection per metric_id, (b) provenance preservation, (c) empty-but-valid
results are handled without raising, (d) tool failures are translated to
ToolExecutionError (never a raw exception), and (e) the answer layer only
ever cites data present in the tool result.
"""
from __future__ import annotations

import pytest

from app.agent import answer as answer_mod
from app.agent.catalog import get_entry
from app.agent.exceptions import InvalidAnswerError, ToolExecutionError
from app.agent.executor import execute_plan
from app.agent.models import AnalyticalPlan, Intent, MetricId
from app.agent.providers.base import ProviderHealth


class _FakeSession:
    """Never actually used for a real query - every test monkeypatches the
    service-layer function the executor would call it with."""


def test_executor_selects_revenue_tool_for_mrr(monkeypatch):
    from app.services import revenue_service

    monkeypatch.setattr(revenue_service, "get_mrr", lambda db, currency: [{"currency": "USD", "mrr": 1000}])
    plan = AnalyticalPlan(intent=Intent.REVENUE, metric_id=MetricId.MRR, currency="USD")
    result = execute_plan(_FakeSession(), plan)
    assert result.provenance.source_model == "analytics.mart_mrr"
    assert result.rows == [{"currency": "USD", "mrr": 1000}]
    assert result.is_empty is False


def test_executor_selects_growth_tool_for_daily_growth(monkeypatch):
    from app.services import growth_service

    monkeypatch.setattr(growth_service, "get_daily_growth", lambda db, start, end: [{"date_day": "2026-01-01"}])
    plan = AnalyticalPlan(intent=Intent.GROWTH, metric_id=MetricId.DAILY_GROWTH)
    result = execute_plan(_FakeSession(), plan)
    assert result.provenance.source_model == "analytics.mart_daily_growth"


def test_executor_handles_empty_but_valid_result_without_raising(monkeypatch):
    from app.services import revenue_service

    monkeypatch.setattr(revenue_service, "get_mrr", lambda db, currency: [])
    plan = AnalyticalPlan(intent=Intent.REVENUE, metric_id=MetricId.MRR, currency="EUR")
    result = execute_plan(_FakeSession(), plan)
    assert result.is_empty is True
    assert result.rows == []


def test_executor_translates_a_service_layer_exception_into_tool_execution_error(monkeypatch):
    from app.services import revenue_service

    def boom(db, currency):
        raise RuntimeError("psycopg2.OperationalError: connection to server failed, password=supersecret")

    monkeypatch.setattr(revenue_service, "get_mrr", boom)
    plan = AnalyticalPlan(intent=Intent.REVENUE, metric_id=MetricId.MRR)
    with pytest.raises(ToolExecutionError) as exc_info:
        execute_plan(_FakeSession(), plan)
    assert "supersecret" not in str(exc_info.value)


def test_executor_customer_detail_requires_organization_id():
    plan = AnalyticalPlan(intent=Intent.CUSTOMER, metric_id=MetricId.CUSTOMER_DETAIL, organization_id=None)
    with pytest.raises(ToolExecutionError):
        execute_plan(_FakeSession(), plan)


def test_executor_quality_run_detail_requires_run_id():
    plan = AnalyticalPlan(intent=Intent.QUALITY, metric_id=MetricId.QUALITY_RUN_DETAIL, run_id=None)
    with pytest.raises(ToolExecutionError):
        execute_plan(_FakeSession(), plan)


def test_executor_preserves_currency_separation_never_sums_rows(monkeypatch):
    from app.services import revenue_service

    monkeypatch.setattr(
        revenue_service, "get_mrr", lambda db, currency: [
            {"currency": "USD", "mrr": 1000}, {"currency": "EUR", "mrr": 800},
        ]
    )
    plan = AnalyticalPlan(intent=Intent.REVENUE, metric_id=MetricId.MRR)
    result = execute_plan(_FakeSession(), plan)
    # Both currency rows preserved distinctly - nothing here ever merges them.
    assert {r["currency"] for r in result.rows} == {"USD", "EUR"}


class FakeAnswerProvider:
    name = "fake"

    def __init__(self, response: dict):
        self._response = response

    def generate_json(self, system_prompt, user_prompt):
        return self._response

    def health_check(self):
        return ProviderHealth(provider=self.name, model=None, reachable=True)


def test_template_answer_discloses_point_in_time_snapshot_limitation(monkeypatch):
    from app.services import revenue_service

    monkeypatch.setattr(revenue_service, "get_mrr", lambda db, currency: [{"currency": "USD", "mrr": 5000}])
    plan = AnalyticalPlan(intent=Intent.REVENUE, metric_id=MetricId.MRR, currency="USD")
    entry = get_entry(MetricId.MRR)
    result = execute_plan(_FakeSession(), plan)
    answer = answer_mod.template_answer(plan, entry, result)
    assert any("point-in-time" in limitation.lower() for limitation in answer.limitations)
    assert answer.currency == "USD"
    assert answer.data_sources == ["analytics.mart_mrr"]


def test_template_answer_on_empty_result_says_no_data_not_a_fabricated_number():
    entry = get_entry(MetricId.MRR)
    plan = AnalyticalPlan(intent=Intent.REVENUE, metric_id=MetricId.MRR)
    from app.agent.models import ToolProvenance, ToolResult

    empty_result = ToolResult(
        provenance=ToolProvenance(tool_name="get_revenue_metrics", source_model="analytics.mart_mrr", row_count=0),
        rows=[], is_empty=True,
    )
    answer = answer_mod.template_answer(plan, entry, empty_result)
    assert "no data" in answer.answer.lower()
    assert answer.key_findings == []


def test_llm_answer_grounded_in_tool_rows_is_accepted(monkeypatch):
    from app.services import revenue_service

    monkeypatch.setattr(revenue_service, "get_mrr", lambda db, currency: [{"currency": "USD", "mrr": 12345}])
    plan = AnalyticalPlan(intent=Intent.REVENUE, metric_id=MetricId.MRR, currency="USD")
    entry = get_entry(MetricId.MRR)
    result = execute_plan(_FakeSession(), plan)

    provider = FakeAnswerProvider({"answer": "Current USD MRR is $12,345.", "key_findings": ["USD MRR: 12345"]})
    answer = answer_mod.build_answer(provider, plan, entry, result)
    assert "12,345" in answer.answer
    assert answer.currency == "USD"


def test_llm_answer_with_empty_text_is_rejected_not_fabricated():
    plan = AnalyticalPlan(intent=Intent.REVENUE, metric_id=MetricId.MRR)
    entry = get_entry(MetricId.MRR)
    from app.agent.models import ToolProvenance, ToolResult

    empty_result = ToolResult(
        provenance=ToolProvenance(tool_name="get_revenue_metrics", source_model="analytics.mart_mrr", row_count=0),
        rows=[], is_empty=True,
    )
    provider = FakeAnswerProvider({"answer": ""})
    with pytest.raises(InvalidAnswerError):
        answer_mod.build_answer(provider, plan, entry, empty_result)

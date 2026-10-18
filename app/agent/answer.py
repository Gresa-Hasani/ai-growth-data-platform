"""Evidence-grounded answer generation. The LLM is given ONLY the tool's
returned rows (as JSON) plus the catalog entry's text - it is instructed to
cite exclusively that data. If the provider is unavailable, `template_answer`
produces a deterministic, still-evidence-grounded answer without any LLM
call, so a provider outage degrades gracefully rather than blocking the
whole response.
"""
from __future__ import annotations

import json

from app.agent.catalog import CatalogEntry
from app.agent.exceptions import InvalidAnswerError
from app.agent.models import AgentAnswer, AnalyticalPlan, ToolResult
from app.agent.providers.base import LLMProvider

SYSTEM_PROMPT = """You are an analytics answer writer. You will be given a \
metric's definition/limitations and the EXACT rows a trusted tool returned. \
You must answer the user's question using ONLY the numbers in those rows - \
never invent, estimate, or recall a number from outside the provided data. \
If the rows are empty, say so plainly rather than guessing. Respond with ONE \
JSON object matching exactly this shape and nothing else:

{
  "answer": "<2-4 sentence plain-language answer>",
  "key_findings": ["<short bullet>", ...],
  "limitations": ["<carry over any limitations from the metric definition that apply>"],
  "warnings": ["<carry over any warnings given below, verbatim or close to it>"],
  "follow_up_suggestions": ["<at most 2 short natural follow-up questions>"]
}
"""


def build_answer(provider: LLMProvider | None, plan: AnalyticalPlan, entry: CatalogEntry, tool_result: ToolResult) -> AgentAnswer:
    if provider is None:
        return template_answer(plan, entry, tool_result)

    user_prompt = (
        f"Metric: {entry.name}\nDefinition: {entry.definition}\n"
        f"Limitations: {entry.limitations}\nCurrency semantics: {entry.currency_semantics}\n"
        f"Tool warnings: {tool_result.warnings}\n"
        f"Rows ({tool_result.provenance.row_count} total): {json.dumps(tool_result.rows, default=str)}\n"
        "Question context: answer strictly from these rows."
    )
    raw = provider.generate_json(SYSTEM_PROMPT, user_prompt)
    try:
        partial = AgentAnswer.model_validate(
            {
                "answer": raw.get("answer", ""),
                "key_findings": raw.get("key_findings", []),
                "limitations": raw.get("limitations", []),
                "warnings": raw.get("warnings", tool_result.warnings),
                "follow_up_suggestions": raw.get("follow_up_suggestions", []),
            }
        )
    except Exception as exc:
        raise InvalidAnswerError(f"model's answer did not match the AgentAnswer schema: {exc}") from exc

    if not partial.answer.strip():
        raise InvalidAnswerError("model returned an empty answer")

    return _finalize(partial, plan, entry, tool_result)


def template_answer(plan: AnalyticalPlan, entry: CatalogEntry, tool_result: ToolResult) -> AgentAnswer:
    if tool_result.is_empty:
        answer_text = f"No data was found for '{entry.name}' with the given filters."
        key_findings: list[str] = []
    else:
        answer_text = (
            f"Retrieved {tool_result.provenance.row_count} row(s) for '{entry.name}' "
            f"from {tool_result.provenance.source_model}."
        )
        key_findings = [json.dumps(row, default=str) for row in tool_result.rows[:5]]

    base = AgentAnswer(
        answer=answer_text,
        key_findings=key_findings,
        limitations=[entry.limitations],
        warnings=list(tool_result.warnings),
        follow_up_suggestions=[],
    )
    return _finalize(base, plan, entry, tool_result)


def _finalize(answer: AgentAnswer, plan: AnalyticalPlan, entry: CatalogEntry, tool_result: ToolResult) -> AgentAnswer:
    answer.data_sources = [tool_result.provenance.source_model]
    answer.data_as_of = tool_result.provenance.data_as_of
    answer.currency = plan.currency
    answer.applied_filters = {
        k: v
        for k, v in {
            "start_date": str(plan.start_date) if plan.start_date else None,
            "end_date": str(plan.end_date) if plan.end_date else None,
            "currency": plan.currency,
            "organization_id": plan.organization_id,
            "organization_type": plan.organization_type,
            "acquisition_channel": plan.acquisition_channel,
            "event_name": plan.event_name,
            "run_id": plan.run_id,
        }.items()
        if v is not None
    }
    if entry.is_snapshot_only and "point-in-time" not in " ".join(answer.limitations).lower():
        answer.limitations.append(f"{entry.name} is a point-in-time snapshot, not a historical time series.")
    return answer

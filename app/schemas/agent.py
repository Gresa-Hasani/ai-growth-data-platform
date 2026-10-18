"""Request/response models for the AI Data Analyst Agent endpoints. Never
exposes chain-of-thought, system prompts, or raw provider payloads - only
the final AgentAnswer shape plus safe, already-sanitized metadata."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentAskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="A natural-language analytics question.")


class AgentAskResponseMeta(BaseModel):
    provider: str
    used_real_inference: bool
    latency_ms: int


class AgentAskResponse(BaseModel):
    answer: str
    key_findings: list[str]
    data_sources: list[str]
    applied_filters: dict[str, Any]
    currency: str | None
    data_as_of: str | None
    limitations: list[str]
    warnings: list[str]
    follow_up_suggestions: list[str]
    meta: AgentAskResponseMeta


class AgentHealthResponse(BaseModel):
    provider: str
    model: str | None
    reachable: bool
    detail: str | None

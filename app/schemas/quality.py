"""Quality summary response models, reading from the Phase 7 monitoring
tables. Never exposes `details`/raw check payloads beyond what's already
safe-by-construction in monitoring.data_quality_results (see
docs/data_quality_observability.md - that table itself never stores
credentials or row-level PII, only aggregates)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class QualityCheckResultSummary(BaseModel):
    test_name: str
    model_name: str | None
    category: str | None
    severity: str
    status: str
    failed_rows: int | None
    total_rows: int | None
    failure_rate: float | None
    error_message: str | None


class QualityRunSummary(BaseModel):
    run_id: str
    started_at: datetime
    finished_at: datetime | None
    overall_status: str | None
    total_checks: int
    passed_checks: int
    warned_checks: int
    failed_checks: int
    duration_ms: int | None


class QualityRunDetail(QualityRunSummary):
    results: list[QualityCheckResultSummary]

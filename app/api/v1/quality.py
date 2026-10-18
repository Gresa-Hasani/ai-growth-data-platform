from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_read_only_db
from app.schemas.common import Page, PageMeta
from app.schemas.quality import QualityRunDetail, QualityRunSummary
from app.services import quality_service

router = APIRouter(prefix="/api/v1/quality", tags=["quality"])


def _require_monitoring_tables(db: Session) -> None:
    if not quality_service.monitoring_tables_available(db):
        raise HTTPException(
            status_code=503,
            detail="quality monitoring tables are not available - run Phase 7's app/db/quality_tables.sql",
        )


@router.get(
    "/latest",
    response_model=QualityRunDetail,
    summary="Most recently persisted quality-check run, with every check's result",
    responses={404: {"description": "No quality run has ever been persisted."}},
)
def latest_run(db: Session = Depends(get_read_only_db)) -> dict:
    _require_monitoring_tables(db)
    detail = quality_service.get_latest_run(db)
    if detail is None:
        raise HTTPException(status_code=404, detail="no quality runs have been persisted yet")
    return detail


@router.get(
    "/runs",
    response_model=Page[QualityRunSummary],
    summary="Quality-check run history",
)
def list_runs(
    limit: int = Query(default=settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_read_only_db),
) -> dict:
    _require_monitoring_tables(db)
    items, total = quality_service.list_runs(db, limit, offset)
    return {"items": items, "meta": PageMeta(limit=limit, offset=offset, total=total)}


@router.get(
    "/runs/{run_id}",
    response_model=QualityRunDetail,
    summary="One quality run's full detail, including every individual check's result",
    responses={404: {"description": "No run with this run_id exists."}},
)
def get_run(run_id: str = Path(..., max_length=100), db: Session = Depends(get_read_only_db)) -> dict:
    _require_monitoring_tables(db)
    detail = quality_service.get_run_detail(db, run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="run not found")
    return detail

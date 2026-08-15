"""
Core research endpoints: create a research run and fetch its status/detail.

This module is intentionally thin — it validates input, talks to the
repository for persistence, and schedules `run_background_research`
via Celery as a background task. It never touches LangGraph directly; all agent
orchestration logic lives in `app/services/research_service.py`.
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

# --- Clean Rate Limiter Import ---
from fastapi_limiter.depends import RateLimiter
# ---------------------------------

from app.api.deps import get_current_user
from app.core.logging import get_logger
from app.db.database import get_db
from app.db.repository import ResearchRepository
from app.models.user import User
from app.schemas.research import (
    ResearchCreateRequest,
    ResearchCreateResponse,
    ResearchDetailResponse,
    ResearchReport,
)
from app.services.research_service import ResearchService, get_research_service
from app.tasks import run_background_research

logger = get_logger(__name__)
router = APIRouter(prefix="/research", tags=["research"])


@router.post("", response_model=ResearchCreateResponse, status_code=201, dependencies=[Depends(RateLimiter(times=3, seconds=60))])
def create_research(
    request: ResearchCreateRequest,
    db: Session = Depends(get_db),
    research_service: ResearchService = Depends(get_research_service),
    current_user: User = Depends(get_current_user),
) -> ResearchCreateResponse:
    """
    Create a new research request and dispatch it to the Celery background worker.
    """
    repo = ResearchRepository(db)
    record = repo.create(query=request.query, user_id=current_user.id)

    logger.info(
        "Created research request id=%s user_id=%s query=%r", record.id, current_user.id, request.query
    )

    # Celery task trigger karein
    run_background_research.delay(record.id, request.query)

    return ResearchCreateResponse(id=record.id, status=record.status.value, query=record.query)


@router.get("/{research_id}", response_model=ResearchDetailResponse)
def get_research(
    research_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchDetailResponse:
    """
    Fetch the current status, progress, and full report for a research run.
    """
    repo = ResearchRepository(db)
    record = repo.get(research_id)
    if not record or record.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Research request not found.")

    progress = json.loads(record.progress_json or "[]")
    report = None
    if record.report_json:
        try:
            report = ResearchReport.model_validate(json.loads(record.report_json))
        except Exception as exc:
            logger.error("Failed to parse stored report for %s: %s", research_id, exc)

    return ResearchDetailResponse(
        id=record.id,
        query=record.query,
        status=record.status.value,
        progress=progress,
        report=report,
        error_message=record.error_message,
        duration_seconds=record.duration_seconds,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
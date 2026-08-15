"""
Research history endpoints: list past research runs and delete one.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.logging import get_logger
from app.db.database import get_db
from app.db.repository import ResearchRepository
from app.models.user import User
from app.schemas.research import ResearchHistoryItem, ResearchHistoryResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/research", tags=["history"])


def _summary_from_report_json(report_json: str | None) -> str | None:
    if not report_json:
        return None
    try:
        data = json.loads(report_json)
        summary = data.get("executive_summary", "")
        return (summary[:200] + "...") if len(summary) > 200 else summary
    except Exception:
        return None


@router.get("", response_model=ResearchHistoryResponse)
def list_research_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ResearchHistoryResponse:
    repo = ResearchRepository(db)
    records = repo.list_all(limit=limit, offset=offset, user_id=current_user.id)
    total = repo.count(user_id=current_user.id)

    items = [
        ResearchHistoryItem(
            id=r.id,
            query=r.query,
            status=r.status.value,
            sources_count=r.sources_count,
            created_at=r.created_at,
            summary=_summary_from_report_json(r.report_json),
        )
        for r in records
    ]
    return ResearchHistoryResponse(items=items, total=total)


@router.delete("/{research_id}", status_code=204)
def delete_research(
    research_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    repo = ResearchRepository(db)
    record = repo.get(research_id)
    # Same 404-not-403 rationale as get_research: don't confirm existence
    # of a record that isn't this user's.
    if not record or record.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Research request not found.")

    repo.delete(research_id)
    logger.info("Deleted research request id=%s user_id=%s", research_id, current_user.id)

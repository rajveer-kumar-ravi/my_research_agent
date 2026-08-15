"""
Repository layer: all direct DB access for research records goes through
here. Keeps SQLAlchemy specifics out of services/API code.
"""
import json
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.research import ResearchRecord, ResearchStatus
from app.schemas.research import ProgressStage


class ResearchRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, query: str, user_id: str | None = None) -> ResearchRecord:
        record = ResearchRecord(
            query=query, status=ResearchStatus.PENDING, progress_json="[]", user_id=user_id
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def get(self, research_id: str) -> Optional[ResearchRecord]:
        return self.db.get(ResearchRecord, research_id)

    def list_all(
        self, limit: int = 50, offset: int = 0, user_id: str | None = None
    ) -> List[ResearchRecord]:
        stmt = select(ResearchRecord).order_by(ResearchRecord.created_at.desc())
        if user_id is not None:
            stmt = stmt.where(ResearchRecord.user_id == user_id)
        stmt = stmt.limit(limit).offset(offset)
        return list(self.db.execute(stmt).scalars().all())

    def count(self, user_id: str | None = None) -> int:
        query = self.db.query(ResearchRecord)
        if user_id is not None:
            query = query.filter(ResearchRecord.user_id == user_id)
        return query.count()

    def update_status(
        self, research_id: str, status: ResearchStatus, error_message: Optional[str] = None
    ) -> Optional[ResearchRecord]:
        record = self.get(research_id)
        if not record:
            return None
        record.status = status
        if error_message is not None:
            record.error_message = error_message
        self.db.commit()
        self.db.refresh(record)
        return record

    def update_progress(self, research_id: str, progress: List[ProgressStage]) -> None:
        record = self.get(research_id)
        if not record:
            return
        record.progress_json = json.dumps([p.model_dump() for p in progress])
        self.db.commit()

    def save_report(
        self,
        research_id: str,
        report_json: str,
        sources_count: int,
        duration_seconds: float,
    ) -> Optional[ResearchRecord]:
        record = self.get(research_id)
        if not record:
            return None
        record.report_json = report_json
        record.sources_count = sources_count
        record.duration_seconds = duration_seconds
        record.status = ResearchStatus.COMPLETED
        self.db.commit()
        self.db.refresh(record)
        return record

    def delete(self, research_id: str) -> bool:
        record = self.get(research_id)
        if not record:
            return False
        self.db.delete(record)
        self.db.commit()
        return True

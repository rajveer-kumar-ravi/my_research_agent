"""
ORM model for a research request/report.

We store the full report and metadata as JSON text columns rather than
normalizing into many tables — the report is always read/written as a
whole document, so this keeps the schema simple without sacrificing
queryability of the fields that matter (status, query, timestamps).
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ResearchStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchRecord(Base):
    __tablename__ = "research_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ResearchStatus] = mapped_column(
        Enum(ResearchStatus), default=ResearchStatus.PENDING, nullable=False
    )

    # Nullable so pre-authentication rows (created before this column
    # existed) remain valid; owned research always has this set going
    # forward. See db/database.py's startup migration for how this column
    # is safely added to an existing database file.
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True, index=True
    )

    # Progress tracking (JSON-encoded list of {stage, status} dicts)
    progress_json: Mapped[str] = mapped_column(Text, default="[]")

    # Final report, stored as JSON-encoded structured data (see schemas.research.ResearchReport)
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Sources actually used, JSON-encoded list, kept separately for fast history previews
    sources_count: Mapped[int] = mapped_column(Integer, default=0)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

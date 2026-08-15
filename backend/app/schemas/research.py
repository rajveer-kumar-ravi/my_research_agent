"""
Pydantic schemas: the contract between the API and the outside world.

Kept separate from SQLAlchemy models (app/models) so the persistence layer
and the API layer can evolve independently.
"""
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class ResearchStatusEnum(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------- Requests ----------

class ResearchCreateRequest(BaseModel):
    query: str = Field(..., description="The research question to investigate")

    @field_validator("query")
    @classmethod
    def query_must_be_meaningful(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Query must not be empty.")
        if len(v) < 8:
            raise ValueError("Query is too short to research meaningfully (min 8 characters).")
        if len(v) > 2000:
            raise ValueError("Query is too long (max 2000 characters).")
        return v


# ---------- Progress ----------

class ProgressStage(BaseModel):
    stage: str
    status: str  # "pending" | "in_progress" | "completed" | "failed"
    detail: Optional[str] = None


# ---------- Evidence / report substructures ----------

class SourceQuality(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Source(BaseModel):
    source_url: str
    title: str
    source_domain: str
    publication_date: Optional[str] = None
    relevance_score: float = 0.0
    quality: SourceQuality = SourceQuality.MEDIUM


class Claim(BaseModel):
    text: str
    supporting_source_urls: List[str] = Field(default_factory=list)
    confidence: float = 0.0


class Conflict(BaseModel):
    topic: str
    description: str
    conflicting_sources: List[str] = Field(default_factory=list)


class ComparisonRow(BaseModel):
    method: str
    advantages: str
    disadvantages: str
    best_use_case: str


class ResearchReport(BaseModel):
    executive_summary: str
    key_findings: List[str] = Field(default_factory=list)
    detailed_analysis: str
    comparison_table: List[ComparisonRow] = Field(default_factory=list)
    claims: List[Claim] = Field(default_factory=list)
    conflicts: List[Conflict] = Field(default_factory=list)
    sources: List[Source] = Field(default_factory=list)
    evidence_sufficient: bool = True
    insufficient_evidence_note: Optional[str] = None


# ---------- Responses ----------

class ResearchCreateResponse(BaseModel):
    id: str
    status: ResearchStatusEnum
    query: str


class ResearchStatusResponse(BaseModel):
    id: str
    query: str
    status: ResearchStatusEnum
    progress: List[ProgressStage] = Field(default_factory=list)
    error_message: Optional[str] = None


class ResearchDetailResponse(BaseModel):
    id: str
    query: str
    status: ResearchStatusEnum
    progress: List[ProgressStage] = Field(default_factory=list)
    report: Optional[ResearchReport] = None
    error_message: Optional[str] = None
    duration_seconds: Optional[float] = None
    created_at: datetime
    updated_at: datetime


class ResearchHistoryItem(BaseModel):
    id: str
    query: str
    status: ResearchStatusEnum
    sources_count: int
    created_at: datetime
    summary: Optional[str] = None


class ResearchHistoryResponse(BaseModel):
    items: List[ResearchHistoryItem]
    total: int


class HealthResponse(BaseModel):
    status: str
    gemini_configured: bool
    search_configured: bool

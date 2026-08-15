"""
Health check endpoint. Reports whether the required external API keys are
configured, without ever exposing the keys themselves.
"""
from fastapi import APIRouter

from app.core.config import get_settings
from app.schemas.research import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        gemini_configured=settings.is_gemini_configured,
        search_configured=settings.is_search_configured,
    )

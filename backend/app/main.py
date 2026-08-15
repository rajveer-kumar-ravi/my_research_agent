"""
FastAPI application entrypoint.

Run locally with:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# --- Sentry Imports ---
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.celery import CeleryIntegration
# ----------------------

# --- Rate Limiting Imports ---
import redis.asyncio as redis
from fastapi_limiter import FastAPILimiter
# -----------------------------

from app.api import auth, health, history, research
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.database import init_db
from prometheus_fastapi_instrumentator import Instrumentator

# --- Sentry Initialize (Environment check ke sath) ---
settings_initial = get_settings()
if getattr(settings_initial, "sentry_dsn", None):
    sentry_sdk.init(
        dsn=settings_initial.sentry_dsn,
        integrations=[FastApiIntegration(), CeleryIntegration()],
        traces_sample_rate=1.0,
        environment="production",
    )
# ---------------------------------------------------

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Intelligent Web Research Agent API...")
    init_db()
    settings = get_settings()
    
    # --- Rate Limiter Initialize Karein ---
    redis_connection = None
    try:
        redis_connection = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        await redis_connection.ping()
        await FastAPILimiter.init(redis_connection)
        logger.info("FastAPI Rate Limiter successfully initialized with Redis.")
    except Exception as e:
        logger.warning("Redis not available (%s) — running in offline/test mode without Rate Limiter.", e)
        redis_connection = None
        
        class FakeRedis:
            async def evalsha(self, *args, **kwargs):
                return 0
            async def eval(self, *args, **kwargs):
                return 0
            async def execute_command(self, *args, **kwargs):
                return 0

        FastAPILimiter.redis = FakeRedis()
        
        async def fake_identifier(request: Request):
            return "test-rate-key"
            
        FastAPILimiter.identifier = fake_identifier
        FastAPILimiter.http_callback = lambda request, pexpire: None
    
    if not settings.is_gemini_configured:
        logger.warning("GEMINI_API_KEY is not configured — research requests will fail until set.")
    if not settings.is_search_configured:
        logger.warning("SEARCH_API_KEY is not configured — research requests will fail until set.")
    if not settings.is_google_oauth_configured:
        logger.warning("GOOGLE_CLIENT_ID/SECRET not configured — 'Continue with Google' will be unavailable.")
    
    yield
    
    logger.info("Shutting down Intelligent Web Research Agent API...")
    if redis_connection:
        await redis_connection.close()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Intelligent Web Research Agent",
        description="AI-powered web research and evidence synthesis API.",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "An internal error occurred. Please try again."},
        )

    app.include_router(auth.router, prefix="/api")
    app.include_router(health.router, prefix="/api")
    app.include_router(research.router, prefix="/api")
    app.include_router(history.router, prefix="/api")

    Instrumentator().instrument(app).expose(app)

    frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    if frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
        logger.info("Serving frontend static files from %s", frontend_dir)
        
    return app


app = create_app()
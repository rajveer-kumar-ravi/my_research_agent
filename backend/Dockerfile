# syntax=docker/dockerfile:1
#
# Intelligent Web Research Agent — backend image.
#
# This single image runs the FastAPI backend AND serves the vanilla-JS
# frontend from the same process (app/main.py already mounts ../frontend
# as static files when present — see Batch 4/5). Running one container for
# both keeps the deployment simple and avoids an unnecessary second
# nginx/static-file service for a project this size.
#
# Build context is the REPOSITORY ROOT (not backend/), so this Dockerfile
# can COPY both backend/ and frontend/ into the image at sibling paths,
# matching what app/main.py expects on disk.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is required for the HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Install Python dependencies first (better Docker layer caching: this
# layer is only rebuilt when requirements.txt changes, not on every code
# edit). ---
COPY backend/requirements.txt backend/requirements.txt

# CRITICAL — CPU-only PyTorch:
# sentence-transformers depends on torch. Installing it BEFORE
# requirements.txt, from PyTorch's dedicated CPU wheel index, means pip
# resolves sentence-transformers' torch requirement against the
# already-installed CPU build instead of pulling the full ~5GB CUDA build.
# This app never uses a GPU, so the CUDA build would only bloat the image
# and slow every build/pull for no benefit.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

RUN pip install -r backend/requirements.txt

# --- Application code ---
# Both backend/ and frontend/ are copied so app/main.py's existing static
# file mount resolves correctly on disk, with zero code changes required:
#   app/main.py lives at /app/backend/app/main.py
#   Path(__file__).resolve().parents[2] == /app
#   -> frontend/ is found at /app/frontend
COPY backend/ backend/
COPY frontend/ frontend/

# --- Run as a non-root user ---
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
USER appuser

WORKDIR /app/backend
EXPOSE 8000

# Matches GET /api/health from app/api/health.py (Batch 4). Container is
# reported "unhealthy" if the API stops responding.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

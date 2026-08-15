"""
Tests for the FastAPI API layer (app/api/*).

Uses a real FastAPI TestClient against a real (temporary, isolated) SQLite
database, with `ResearchService` swapped for a fake via FastAPI's
dependency override system — the agent pipeline itself is already covered
end-to-end in test_agent.py, so here we only verify the HTTP contract:
status codes, request validation, and response shapes.
"""
import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


class FakeResearchService:
    """Records calls instead of actually running the agent pipeline."""

    def __init__(self):
        self.calls = []

    async def run_research(self, research_id: str, query: str) -> None:
        self.calls.append((research_id, query))


@pytest.fixture(scope="module")
def client():
    """
    One shared app + temp SQLite DB for the whole module.
    """
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    from app.core.config import get_settings

    get_settings.cache_clear()

    from app.main import create_app
    from app.services.research_service import get_research_service

    app = create_app()
    fake_service = FakeResearchService()
    app.dependency_overrides[get_research_service] = lambda: fake_service

    with TestClient(app) as test_client:
        test_client.fake_service = fake_service
        test_client.post(
            "/api/auth/register",
            json={
                "full_name": "Test User",
                "email": "test-api-user@example.com",
                "password": "TestPassword123",
                "confirm_password": "TestPassword123",
            },
        )
        yield test_client

    os.close(db_fd)
    os.remove(db_path)
    get_settings.cache_clear()


# ---------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------

def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "gemini_configured" in body
    assert "search_configured" in body


# ---------------------------------------------------------------------
# Create research
# ---------------------------------------------------------------------

@patch("app.api.research.run_background_research.delay")
def test_create_research_success(mock_delay, client):
    response = client.post("/api/research", json={"query": "Compare RAG evaluation methods"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["query"] == "Compare RAG evaluation methods"
    assert body["id"]

    # Celery task delay call verify karein
    mock_delay.assert_called_once()


def test_create_research_rejects_empty_query(client):
    response = client.post("/api/research", json={"query": ""})
    assert response.status_code == 422


def test_create_research_rejects_too_short_query(client):
    response = client.post("/api/research", json={"query": "short"})
    assert response.status_code == 422


def test_create_research_rejects_missing_query_field(client):
    response = client.post("/api/research", json={})
    assert response.status_code == 422


# ---------------------------------------------------------------------
# Get research detail/status
# ---------------------------------------------------------------------

def test_get_research_not_found(client):
    response = client.get("/api/research/does-not-exist")
    assert response.status_code == 404


@patch("app.api.research.run_background_research.delay")
def test_get_research_after_create(mock_delay, client):
    create_response = client.post("/api/research", json={"query": "What is retrieval augmented generation?"})
    research_id = create_response.json()["id"]

    response = client.get(f"/api/research/{research_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == research_id
    assert body["status"] == "pending"
    assert body["progress"] == []
    assert body["report"] is None


# ---------------------------------------------------------------------
# History list + delete
# ---------------------------------------------------------------------

@patch("app.api.research.run_background_research.delay")
def test_history_list_reflects_created_research(mock_delay, client):
    client.post("/api/research", json={"query": "First research question here"})
    client.post("/api/research", json={"query": "Second research question here"})

    response = client.get("/api/research")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 2
    assert len(body["items"]) >= 2


@patch("app.api.research.run_background_research.delay")
def test_history_list_respects_limit(mock_delay, client):
    for i in range(3):
        client.post("/api/research", json={"query": f"Research question number {i} here"})

    response = client.get("/api/research?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2


@patch("app.api.research.run_background_research.delay")
def test_delete_research(mock_delay, client):
    create_response = client.post("/api/research", json={"query": "Research question to delete"})
    research_id = create_response.json()["id"]

    delete_response = client.delete(f"/api/research/{research_id}")
    assert delete_response.status_code == 204

    get_response = client.get(f"/api/research/{research_id}")
    assert get_response.status_code == 404


def test_delete_nonexistent_research_returns_404(client):
    response = client.delete("/api/research/does-not-exist")
    assert response.status_code == 404
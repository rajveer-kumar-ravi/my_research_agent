"""
Authentication tests.

Uses a real FastAPI TestClient against a temp SQLite DB (same pattern as
test_api.py). Google's OAuth verification is mocked at the
auth_service.exchange_code_and_verify_identity boundary — no real network
call to Google is ever made in this test suite.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    """
    Fresh app + fresh temp DB per test — auth tests need full isolation
    from each other (unlike test_api.py, which shares one logged-in user
    for its whole module).

    NOTE: app.db.database's `engine`/`SessionLocal` are module-level
    singletons bound the FIRST time that module is imported in this
    process. Simply setting a new DATABASE_URL env var per test (as
    test_api.py's module-scoped fixture does once) does NOT rebind them on
    later tests, since the module is already cached in sys.modules — the
    engine would silently keep pointing at the first test's (now-deleted)
    temp file. To get real per-test isolation, we explicitly rebind
    `engine`/`SessionLocal` on the already-imported module before each
    test, rather than relying on import-time binding.
    """
    db_fd, db_path = tempfile.mkstemp(suffix=".db")

    from app.core.config import get_settings

    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    import app.db.database as db_module
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    new_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    db_module.engine = new_engine
    db_module.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=new_engine)

    from app.models import research, session, user  # noqa: F401 — register all models

    db_module.Base.metadata.create_all(bind=new_engine)

    from app.main import create_app

    app = create_app()

    with TestClient(app) as test_client:
        yield test_client

    os.close(db_fd)
    os.remove(db_path)
    get_settings.cache_clear()


def _register(client, email="alice@example.com", password="StrongPass123", name="Alice Smith"):
    return client.post(
        "/api/auth/register",
        json={"full_name": name, "email": email, "password": password, "confirm_password": password},
    )


# ---------------------------------------------------------------------
# 1-5: Registration
# ---------------------------------------------------------------------

def test_register_new_user(client):
    response = _register(client)
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "alice@example.com"
    assert body["full_name"] == "Alice Smith"
    assert "password" not in body
    assert "password_hash" not in body
    # Session cookie must be set — user is authenticated immediately.
    assert "session_token" in response.cookies


def test_register_duplicate_email_rejected(client):
    _register(client)
    response = _register(client)
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"].lower()


def test_register_invalid_email_rejected(client):
    response = client.post(
        "/api/auth/register",
        json={
            "full_name": "Bob",
            "email": "not-an-email",
            "password": "StrongPass123",
            "confirm_password": "StrongPass123",
        },
    )
    assert response.status_code == 422


def test_register_password_mismatch_rejected(client):
    response = client.post(
        "/api/auth/register",
        json={
            "full_name": "Bob",
            "email": "bob@example.com",
            "password": "StrongPass123",
            "confirm_password": "DifferentPass456",
        },
    )
    assert response.status_code == 422


def test_password_is_hashed_never_stored_plaintext(client):
    _register(client, email="hashcheck@example.com", password="StrongPass123")

    from app.db.database import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "hashcheck@example.com").first()
        assert user is not None
        assert user.password_hash is not None
        assert user.password_hash != "StrongPass123"
        assert user.password_hash.startswith("$argon2")
    finally:
        db.close()


# ---------------------------------------------------------------------
# 6-7: Sign in
# ---------------------------------------------------------------------

def test_sign_in_success(client):
    _register(client, email="signin@example.com", password="StrongPass123")
    client.post("/api/auth/logout")

    response = client.post("/api/auth/login", json={"email": "signin@example.com", "password": "StrongPass123"})
    assert response.status_code == 200
    assert response.json()["email"] == "signin@example.com"
    assert "session_token" in response.cookies


def test_sign_in_invalid_credentials_generic_error(client):
    _register(client, email="signin2@example.com", password="StrongPass123")
    client.post("/api/auth/logout")

    wrong_password = client.post(
        "/api/auth/login", json={"email": "signin2@example.com", "password": "WrongPassword1"}
    )
    nonexistent_email = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "WrongPassword1"}
    )

    assert wrong_password.status_code == 401
    assert nonexistent_email.status_code == 401
    # Same generic message either way — never reveals whether the email exists.
    assert wrong_password.json()["detail"] == nonexistent_email.json()["detail"]
    assert wrong_password.json()["detail"] == "Invalid email or password."


# ---------------------------------------------------------------------
# 8-10: Session validity
# ---------------------------------------------------------------------

def test_session_endpoint_missing_session(client):
    response = client.get("/api/auth/session")
    assert response.status_code == 200
    assert response.json() == {"authenticated": False, "user": None}


def test_session_endpoint_valid_session(client):
    _register(client, email="session@example.com")
    response = client.get("/api/auth/session")
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == "session@example.com"


def test_session_expired_is_treated_as_invalid(client):
    _register(client, email="expired@example.com")

    from app.db.database import SessionLocal
    from app.models.session import AuthSession

    db = SessionLocal()
    try:
        # Force every session row for this test into the past.
        for row in db.query(AuthSession).all():
            row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()
    finally:
        db.close()

    response = client.get("/api/auth/session")
    assert response.status_code == 200
    assert response.json()["authenticated"] is False


# ---------------------------------------------------------------------
# 11: Logout
# ---------------------------------------------------------------------

def test_logout_invalidates_session(client):
    _register(client, email="logout@example.com")
    assert client.get("/api/auth/session").json()["authenticated"] is True

    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 204

    assert client.get("/api/auth/session").json()["authenticated"] is False


# ---------------------------------------------------------------------
# 12-13: Protected research endpoints
# ---------------------------------------------------------------------

def test_protected_endpoint_without_session_rejected(client):
    response = client.post("/api/research", json={"query": "Some research question here"})
    assert response.status_code == 401


def test_protected_endpoint_with_valid_session_allowed(client):
    _register(client, email="protected@example.com")

    from app.services.research_service import get_research_service

    class FakeResearchService:
        async def run_research(self, research_id, query):
            pass

    client.app.dependency_overrides[get_research_service] = lambda: FakeResearchService()

    response = client.post("/api/research", json={"query": "Some research question here"})
    assert response.status_code == 201


# ---------------------------------------------------------------------
# 14: 30-day expiration window
# ---------------------------------------------------------------------

def test_session_expires_at_is_thirty_days_out(client):
    _register(client, email="thirtyday@example.com")

    from app.db.database import SessionLocal
    from app.models.session import AuthSession

    db = SessionLocal()
    try:
        row = db.query(AuthSession).order_by(AuthSession.created_at.desc()).first()
        assert row is not None
        delta = row.expires_at.replace(tzinfo=timezone.utc) - row.created_at.replace(tzinfo=timezone.utc)
        # Allow a small tolerance for test execution time.
        assert timedelta(days=29, hours=23) < delta < timedelta(days=30, hours=1)
    finally:
        db.close()


# ---------------------------------------------------------------------
# 15-19: Google OAuth (verification mocked — no real network call)
# ---------------------------------------------------------------------

def _mock_google_claims(email="googleuser@example.com", sub="google-sub-123", name="Google User"):
    return {"sub": sub, "email": email, "email_verified": True, "name": name, "iss": "accounts.google.com"}


def test_google_new_user_creates_account(client, monkeypatch):
    from app.api import auth as auth_module

    monkeypatch.setattr(
        auth_module.auth_service, "exchange_code_and_verify_identity", lambda code: _mock_google_claims()
    )

    # Simulate the state cookie the /google redirect step would have set.
    client.cookies.set("oauth_state", "test-state-123")
    response = client.get(
        "/api/auth/google/callback", params={"code": "fake-code", "state": "test-state-123"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "session_token" in response.cookies

    session_check = client.get("/api/auth/session")
    assert session_check.json()["authenticated"] is True
    assert session_check.json()["user"]["email"] == "googleuser@example.com"


def test_google_existing_user_signs_in_not_duplicated(client, monkeypatch):
    from app.api import auth as auth_module

    monkeypatch.setattr(
        auth_module.auth_service, "exchange_code_and_verify_identity", lambda code: _mock_google_claims()
    )

    client.cookies.set("oauth_state", "state-1")
    client.get("/api/auth/google/callback", params={"code": "c1", "state": "state-1"}, follow_redirects=False)
    first_user_id = client.get("/api/auth/session").json()["user"]["id"]

    client.post("/api/auth/logout")

    client.cookies.set("oauth_state", "state-2")
    client.get("/api/auth/google/callback", params={"code": "c2", "state": "state-2"}, follow_redirects=False)
    second_user_id = client.get("/api/auth/session").json()["user"]["id"]

    assert first_user_id == second_user_id  # no duplicate account created

    from app.db.database import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        assert db.query(User).filter(User.email == "googleuser@example.com").count() == 1
    finally:
        db.close()


def test_google_callback_rejects_missing_or_mismatched_state(client, monkeypatch):
    from app.api import auth as auth_module

    monkeypatch.setattr(
        auth_module.auth_service, "exchange_code_and_verify_identity", lambda code: _mock_google_claims()
    )

    client.cookies.set("oauth_state", "expected-state")
    response = client.get(
        "/api/auth/google/callback",
        params={"code": "fake-code", "state": "WRONG-state"},
        follow_redirects=False,
    )
    # Rejected before ever calling Google verification — redirected back to login with an error.
    assert response.status_code in (302, 307)
    assert "login.html" in response.headers["location"]
    assert "session_token" not in response.cookies


def test_google_callback_handles_invalid_identity_verification(client, monkeypatch):
    from app.api import auth as auth_module
    from app.services.auth_service import AuthError

    def _raise(code):
        raise AuthError("Could not verify Google identity. Please try again.")

    monkeypatch.setattr(auth_module.auth_service, "exchange_code_and_verify_identity", _raise)

    client.cookies.set("oauth_state", "state-x")
    response = client.get(
        "/api/auth/google/callback", params={"code": "bad-code", "state": "state-x"}, follow_redirects=False
    )
    assert response.status_code in (302, 307)
    assert "login.html" in response.headers["location"]


def test_google_user_receives_thirty_day_session(client, monkeypatch):
    from app.api import auth as auth_module

    monkeypatch.setattr(
        auth_module.auth_service, "exchange_code_and_verify_identity", lambda code: _mock_google_claims()
    )
    client.cookies.set("oauth_state", "state-30d")
    client.get("/api/auth/google/callback", params={"code": "c", "state": "state-30d"}, follow_redirects=False)

    from app.db.database import SessionLocal
    from app.models.session import AuthSession

    db = SessionLocal()
    try:
        row = db.query(AuthSession).order_by(AuthSession.created_at.desc()).first()
        delta = row.expires_at.replace(tzinfo=timezone.utc) - row.created_at.replace(tzinfo=timezone.utc)
        assert timedelta(days=29, hours=23) < delta < timedelta(days=30, hours=1)
    finally:
        db.close()


def test_google_user_logout_works(client, monkeypatch):
    from app.api import auth as auth_module

    monkeypatch.setattr(
        auth_module.auth_service, "exchange_code_and_verify_identity", lambda code: _mock_google_claims()
    )
    client.cookies.set("oauth_state", "state-logout")
    client.get(
        "/api/auth/google/callback", params={"code": "c", "state": "state-logout"}, follow_redirects=False
    )
    assert client.get("/api/auth/session").json()["authenticated"] is True

    client.post("/api/auth/logout")
    assert client.get("/api/auth/session").json()["authenticated"] is False


# ---------------------------------------------------------------------
# 21: Cross-user isolation
# ---------------------------------------------------------------------

def test_user_a_cannot_access_user_b_research(client):
    from app.services.research_service import get_research_service

    class FakeResearchService:
        async def run_research(self, research_id, query):
            pass

    client.app.dependency_overrides[get_research_service] = lambda: FakeResearchService()

    # User A creates a research record.
    _register(client, email="user-a@example.com", password="StrongPass123")
    create_response = client.post("/api/research", json={"query": "User A's private research question"})
    research_id = create_response.json()["id"]

    # User A can see it.
    assert client.get(f"/api/research/{research_id}").status_code == 200

    # User B logs in (separate account) and must NOT be able to see it.
    client.post("/api/auth/logout")
    _register(client, email="user-b@example.com", password="StrongPass123")

    response = client.get(f"/api/research/{research_id}")
    assert response.status_code == 404  # not 403 — existence is not confirmed either

    # User B's history must not include User A's record.
    history_response = client.get("/api/research")
    ids_visible_to_b = [item["id"] for item in history_response.json()["items"]]
    assert research_id not in ids_visible_to_b

    # User B cannot delete User A's record either.
    delete_response = client.delete(f"/api/research/{research_id}")
    assert delete_response.status_code == 404


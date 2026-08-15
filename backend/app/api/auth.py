"""
Authentication endpoints.

Uses secure HttpOnly cookies for the session token — never returns the
token in a JSON body, and the frontend never reads or sets it directly.
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_optional_user
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, SessionResponse, UserPublic
from app.services import auth_service
from app.services.auth_service import AuthError

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_OAUTH_STATE_COOKIE = "oauth_state"


def _set_session_cookie(response: Response, raw_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        max_age=settings.auth_session_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(key=settings.session_cookie_name, path="/")


@router.post("/register", response_model=UserPublic, status_code=201)
def register(payload: RegisterRequest, response: Response, db: Session = Depends(get_db)) -> UserPublic:
    """Create an account and immediately sign the user in (no separate login step)."""
    try:
        user = auth_service.register_user(db, payload.full_name, payload.email, payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    raw_token = auth_service.create_session(db, user)
    _set_session_cookie(response, raw_token)
    return UserPublic(id=user.id, full_name=user.full_name, email=user.email, created_at=user.created_at)


@router.post("/login", response_model=UserPublic)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> UserPublic:
    try:
        user = auth_service.authenticate_user(db, payload.email, payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    raw_token = auth_service.create_session(db, user)
    _set_session_cookie(response, raw_token)
    return UserPublic(id=user.id, full_name=user.full_name, email=user.email, created_at=user.created_at)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    settings = get_settings()
    raw_token = request.cookies.get(settings.session_cookie_name)
    auth_service.invalidate_session(db, raw_token)
    _clear_session_cookie(response)


@router.get("/session", response_model=SessionResponse)
def get_session(current_user: User | None = Depends(get_optional_user)) -> SessionResponse:
    """Tells the frontend whether the current session cookie is valid."""
    if not current_user:
        return SessionResponse(authenticated=False, user=None)
    return SessionResponse(
        authenticated=True,
        user=UserPublic(
            id=current_user.id,
            full_name=current_user.full_name,
            email=current_user.email,
            created_at=current_user.created_at,
        ),
    )


# ---------------------------------------------------------------------
# Google OAuth 2.0 (authorization code flow)
# ---------------------------------------------------------------------

@router.get("/google")
def google_login(response: Response) -> RedirectResponse:
    """Redirects the browser to Google's consent screen."""
    settings = get_settings()
    if not settings.is_google_oauth_configured:
        raise HTTPException(
            status_code=503,
            detail="Google sign-in is not configured on this server (GOOGLE_CLIENT_ID/SECRET missing).",
        )

    state = secrets.token_urlsafe(24)
    redirect = RedirectResponse(url=auth_service.build_google_auth_url(state))
    # Short-lived, HttpOnly cookie holding the expected state value — this
    # is the CSRF protection: the callback below must see the SAME value
    # come back from Google, proving the request round-trip wasn't forged.
    redirect.set_cookie(
        key=_OAUTH_STATE_COOKIE,
        value=state,
        max_age=600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return redirect


@router.get("/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    expected_state = request.cookies.get(_OAUTH_STATE_COOKIE)

    if not code or not state or not expected_state or not secrets.compare_digest(state, expected_state):
        logger.warning("Google OAuth callback rejected: missing/mismatched state.")
        return RedirectResponse(url="/login.html?error=google_oauth_failed")

    try:
        claims = auth_service.exchange_code_and_verify_identity(code)
        user = auth_service.find_or_create_google_user(db, claims)
    except AuthError as exc:
        logger.warning("Google OAuth callback failed: %s", exc)
        return RedirectResponse(url="/login.html?error=google_oauth_failed")

    raw_token = auth_service.create_session(db, user)
    redirect = RedirectResponse(url="/index.html")
    _set_session_cookie(redirect, raw_token)
    redirect.delete_cookie(key=_OAUTH_STATE_COOKIE, path="/")
    return redirect

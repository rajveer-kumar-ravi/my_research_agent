"""
Shared FastAPI dependencies for authentication.

Every protected route depends on `get_current_user` — the user is always
derived from the server-validated session cookie, never from anything the
frontend claims (no trusting a user_id in the request body/query string).
"""
from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.database import get_db
from app.models.user import User
from app.services import auth_service


def _build_current_user_dependency():
    """
    The session cookie's NAME is configurable (SESSION_COOKIE_NAME), so we
    build the actual dependency function at import time using the
    configured name, rather than hardcoding it in a Cookie() default.
    """
    cookie_name = get_settings().session_cookie_name

    def _dependency(
        db: Session = Depends(get_db),
        raw_token: str | None = Cookie(default=None, alias=cookie_name),
    ) -> User:
        user = auth_service.validate_session(db, raw_token)
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required.")
        return user

    return _dependency


def _build_optional_user_dependency():
    cookie_name = get_settings().session_cookie_name

    def _dependency(
        db: Session = Depends(get_db),
        raw_token: str | None = Cookie(default=None, alias=cookie_name),
    ) -> User | None:
        return auth_service.validate_session(db, raw_token)

    return _dependency


get_current_user = _build_current_user_dependency()
get_optional_user = _build_optional_user_dependency()

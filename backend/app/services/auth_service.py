"""
Authentication service.

Centralizes everything auth-related: password hashing/verification,
session token generation/validation, and the Google OAuth 2.0 /
OpenID Connect authorization-code flow. API routes never touch password
hashes, raw tokens, or Google's endpoints directly — they only call
methods on this service.

Security notes:
  - Passwords are hashed with Argon2id (argon2-cffi), never stored or
    logged in plaintext.
  - Session tokens are cryptographically random (secrets.token_urlsafe);
    only a SHA-256 hash of the token is ever persisted.
  - Google identity is established ONLY via backend-side verification of
    a token Google itself issued (authorization-code exchange +
    signature/issuer/audience/expiry verification) — the frontend never
    supplies an email/identity that the backend trusts directly.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.session import AuthSession
from app.models.user import User

logger = get_logger(__name__)

_hasher = PasswordHasher()

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


class AuthError(Exception):
    """Raised for any authentication failure that should surface to the user."""


# ---------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        # Malformed hash or similar — never let this crash into a 500 that
        # might leak internals; treat as a failed verification.
        return False


# ---------------------------------------------------------------------
# User lookup / registration
# ---------------------------------------------------------------------

def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email.lower().strip()).first()


def get_user_by_id(db: Session, user_id: str) -> Optional[User]:
    return db.get(User, user_id)


def register_user(db: Session, full_name: str, email: str, password: str) -> User:
    """Create a new password-based account. Raises AuthError on duplicate email."""
    email = email.lower().strip()
    if get_user_by_email(db, email):
        # Deliberately generic — see login's identical wording rationale below.
        raise AuthError("An account with this email already exists.")

    user = User(full_name=full_name.strip(), email=email, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("Registered new user id=%s", user.id)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User:
    """
    Verify email/password credentials. Raises AuthError with a single
    generic message on any failure (wrong email, wrong password, or a
    Google-only account with no password) — never reveals which case it
    was, to avoid confirming whether an email is registered.
    """
    user = get_user_by_email(db, email)
    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        raise AuthError("Invalid email or password.")
    return user


# ---------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------

def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_session(db: Session, user: User) -> str:
    """Create a new session for `user` and return the RAW token (cookie value)."""
    settings = get_settings()
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.auth_session_days)

    session_row = AuthSession(token_hash=_hash_token(raw_token), user_id=user.id, expires_at=expires_at)
    db.add(session_row)
    db.commit()
    return raw_token


def validate_session(db: Session, raw_token: Optional[str]) -> Optional[User]:
    """Return the User for a valid, unexpired session token, else None."""
    if not raw_token:
        return None

    session_row = (
        db.query(AuthSession).filter(AuthSession.token_hash == _hash_token(raw_token)).first()
    )
    if not session_row:
        return None

    expires_at = session_row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return None

    return get_user_by_id(db, session_row.user_id)


def invalidate_session(db: Session, raw_token: Optional[str]) -> None:
    """Delete the session row for this token, if any. Safe to call with None/garbage."""
    if not raw_token:
        return
    db.query(AuthSession).filter(AuthSession.token_hash == _hash_token(raw_token)).delete()
    db.commit()


# ---------------------------------------------------------------------
# Google OAuth 2.0 / OpenID Connect (authorization code flow)
# ---------------------------------------------------------------------

def build_google_auth_url(state: str) -> str:
    """Build the URL to redirect the browser to for Google's consent screen."""
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"


def exchange_code_and_verify_identity(code: str) -> dict:
    """
    Exchange the authorization code for tokens, then verify the returned
    ID token's signature, issuer, audience, and expiry against Google's
    public keys. Returns the verified claims dict (sub, email, name, ...).
    Raises AuthError on any failure — this is the ONLY place Google's
    identity assertion is trusted, and only after full verification.
    """
    settings = get_settings()

    try:
        response = httpx.post(
            GOOGLE_TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Google token exchange failed: %s", exc)
        raise AuthError("Google authentication failed. Please try again.") from exc

    token_data = response.json()
    id_token_raw = token_data.get("id_token")
    if not id_token_raw:
        raise AuthError("Google authentication failed. Please try again.")

    # Verify signature, issuer, audience, and expiry against Google's own
    # public keys — this is what makes the identity trustworthy, not the
    # mere presence of an id_token.
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token

        claims = google_id_token.verify_oauth2_token(
            id_token_raw, google_requests.Request(), settings.google_client_id
        )
    except Exception as exc:
        logger.error("Google ID token verification failed: %s", exc)
        raise AuthError("Could not verify Google identity. Please try again.") from exc

    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise AuthError("Could not verify Google identity. Please try again.")
    if not claims.get("email_verified", False):
        raise AuthError("Your Google email is not verified. Please verify it with Google first.")

    return claims


def find_or_create_google_user(db: Session, claims: dict) -> User:
    """
    Resolve a verified Google identity to a local User, creating one if
    needed.

    Account-linking policy: Google has already cryptographically verified
    ownership of this email (email_verified=True on a signature-checked
    ID token), which is a fundamentally stronger guarantee than an email
    string supplied by a browser — so it's safe to LINK a Google identity
    to an existing password-based account with the same verified email,
    rather than creating a second, disconnected account. If no account
    exists at all, one is created automatically (Google-only, no password).
    """
    google_id = claims["sub"]
    email = claims["email"].lower().strip()
    name = claims.get("name") or email.split("@")[0]

    # 1. Already linked — the common case for a returning Google user.
    user = db.query(User).filter(User.google_id == google_id).first()
    if user:
        return user

    # 2. An existing password account with this verified email — link it.
    user = get_user_by_email(db, email)
    if user:
        user.google_id = google_id
        db.commit()
        db.refresh(user)
        logger.info("Linked Google identity to existing account id=%s", user.id)
        return user

    # 3. Brand new user — Google-only account, no password.
    user = User(full_name=name, email=email, google_id=google_id, password_hash=None)
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("Created new Google-linked user id=%s", user.id)
    return user

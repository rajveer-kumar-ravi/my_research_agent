"""
ORM model for application users.

Supports three account shapes:
  - password-only (password_hash set, google_id NULL)
  - Google-only (google_id set, password_hash NULL)
  - linked (both set — a password account that later verified the same
    email via Google; see auth_service.find_or_create_google_user for the
    linking policy).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    # Nullable: a Google-only account has no password.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Nullable + unique: only present once a user has authenticated via
    # Google (either at registration or via later account linking).
    google_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

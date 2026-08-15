"""
Database engine and session factory.

Uses SQLite for local development. `check_same_thread=False` is required
because FastAPI's background tasks and request handlers may access the
connection from different threads; sessions are still created per-request/
per-task, so this is safe for our usage pattern.
"""
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# --- CLOUD DEPLOYMENT FIX (Railway/Heroku) ---
# Cloud providers often supply URLs starting with 'postgres://'
# SQLAlchemy 1.4+ strictly requires 'postgresql://'
db_url = settings.database_url
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
# ---------------------------------------------

connect_args = {"check_same_thread": False} if "sqlite" in db_url else {}

engine = create_engine(db_url, connect_args=connect_args, echo=False)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """
    Create all tables. Safe to call multiple times.

    This project has no Alembic migration tooling — `create_all()` only
    creates tables that don't exist yet, it does NOT alter existing ones.
    Since authentication was added after the initial schema shipped, a
    user with an existing research_agent.db would be missing the new
    `user_id` column on `research_records`. `_ensure_columns_exist` below
    is a minimal, additive-only migration (safe on SQLite: `ALTER TABLE
    ... ADD COLUMN` for a nullable column) so existing local databases
    keep working without the user having to delete their data.
    """
    from app.models import research, session, user  # noqa: F401  (register all models)

    Base.metadata.create_all(bind=engine)
    _ensure_columns_exist()


def _ensure_columns_exist() -> None:
    """Add any columns that a pre-auth database file is missing."""
    if "sqlite" not in settings.database_url:
        return  # Non-SQLite deployments are expected to manage their own migrations.

    with engine.connect() as conn:
        existing_columns = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(research_records)").fetchall()
        }
        if "user_id" not in existing_columns:
            conn.exec_driver_sql("ALTER TABLE research_records ADD COLUMN user_id VARCHAR(36)")
            conn.commit()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session() -> Generator[Session, None, None]:
    """Context manager for use inside background tasks (non-FastAPI-DI context)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
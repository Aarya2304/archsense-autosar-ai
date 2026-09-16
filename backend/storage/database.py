"""SQLite schema + session management (SQLAlchemy 2.x)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import DB_PATH


class Base(DeclarativeBase):
    pass


def _fk_pragma(dbapi_conn, _rec):  # pragma: no cover - sqlite hook
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def make_engine(db_path: Path | None = None):
    """Create a SQLite engine with FK enforcement enabled."""
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", future=True)
    event.listen(engine, "connect", _fk_pragma)
    return engine


def make_session_factory(engine=None) -> sessionmaker:
    engine = engine or make_engine()
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_schema(engine) -> None:
    """Create all tables (idempotent)."""
    import backend.storage.models as _models  # noqa: F401 - register tables

    Base.metadata.create_all(engine)


def get_session(engine) -> Session:
    return Session(engine, expire_on_commit=False)

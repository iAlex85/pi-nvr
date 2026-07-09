"""
SQLite database engine + session management.

SQLite is intentional: zero external services, trivial backup (copy one
file), and more than fast enough for this workload (a handful of cameras,
a few writes per second at most for motion events).
"""
from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.config import get_config

_engine = None
_SessionLocal: sessionmaker | None = None


def init_db() -> None:
    """Create the engine, ensure the parent directory exists, and create
    tables. Safe to call multiple times (idempotent)."""
    global _engine, _SessionLocal
    cfg = get_config()
    db_path = Path(cfg.get("database.path", "database/pi-nvr.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    _engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )

    # SQLite performs much better under concurrent readers/writers with WAL.
    with _engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")

    _SessionLocal = sessionmaker(
        bind=_engine, autoflush=False, autocommit=False, future=True
    )

    from app import models  # noqa: F401  (register models with Base.metadata)
    models.Base.metadata.create_all(bind=_engine)


def get_engine():
    if _engine is None:
        init_db()
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager for scripts/background tasks:
    `with session_scope() as db: ...`
    """
    if _SessionLocal is None:
        init_db()
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: `db: Session = Depends(get_db)`"""
    if _SessionLocal is None:
        init_db()
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

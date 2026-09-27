"""PostgreSQL engine, session management, and startup health checks."""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config import settings

LOGGER = logging.getLogger("onboard.database")

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        db_url = settings.database_url
        if not db_url:
            raise RuntimeError(
                "DATABASE_URL environment variable is not set. "
                "Set it to a valid PostgreSQL connection string: "
                "postgresql://user:password@host:5432/dbname"
            )
        _engine = create_engine(
            db_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            echo=False,
        )
        LOGGER.info("database engine created url=%s", db_url.split("@")[-1])
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)
    return _SessionLocal


@contextmanager
def get_session() -> Generator[Session, None, None]:
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_health() -> bool:
    """Return True if the database responds to a simple query."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        LOGGER.error("database health check failed: %s", exc)
        return False


def mark_stale_jobs() -> None:
    """Mark any jobs that were left in queued/processing state as failed (server restart)."""
    from backend.models import Job, Repository
    with get_session() as session:
        stale_jobs = session.query(Job).filter(Job.status.in_(["queued", "processing"])).all()
        for job in stale_jobs:
            job.status = "failed"
            job.error = "Server restarted while job was active"
        stale_repos = session.query(Repository).filter(Repository.status.in_(["queued", "processing"])).all()
        for repo in stale_repos:
            repo.status = "failed"
            repo.error = "Server restarted while job was active"
        if stale_jobs or stale_repos:
            LOGGER.info("marked %d stale jobs and %d stale repositories as failed", len(stale_jobs), len(stale_repos))

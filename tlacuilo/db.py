"""Job metadata — the only table, and it holds no content.

Columns are ids, hashes, sizes, page counts, timings, engine names, status and the
sensitivity label. Rows expire after JOB_TTL_SECONDS and are purged opportunistically.
M0 creates the table at startup (one table, additive); Alembic arrives with M1."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import JSON, DateTime, Integer, String, create_engine, delete, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool

from .settings import get_settings


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed
    mode: Mapped[str] = mapped_column(String(8), default="sync")
    doc_type: Mapped[str | None] = mapped_column(String(32))
    sensitivity: Mapped[str] = mapped_column(String(16))
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    pages: Mapped[int | None] = mapped_column(Integer)
    engines: Mapped[dict | None] = mapped_column(JSON)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC), onupdate=lambda: dt.datetime.now(dt.UTC)
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    def public(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "mode": self.mode,
            "documentType": self.doc_type,
            "sensitivity": self.sensitivity,
            "sha256": self.sha256,
            "sizeBytes": self.size_bytes,
            "pages": self.pages,
            "engines": self.engines,
            "durationMs": self.duration_ms,
            "errorCode": self.error_code,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "expiresAt": self.expires_at.isoformat() if self.expires_at else None,
        }


_engine = None


def _normalise(url: str) -> str:
    # The Enclii addon writes postgres://; SQLAlchemy 2 wants the driver named.
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def get_engine():
    global _engine
    if _engine is None:
        url = get_settings().database_url
        if url:
            _engine = create_engine(_normalise(url), pool_pre_ping=True, future=True)
        else:  # tests / local: an in-memory store with the same schema
            _engine = create_engine(
                "sqlite+pysqlite:///:memory:",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
                future=True,
            )
    return _engine


def init_db() -> None:
    Base.metadata.create_all(get_engine())


def session() -> Session:
    return Session(get_engine(), expire_on_commit=False)


def ping() -> bool:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return True


def purge_expired(s: Session) -> int:
    now = dt.datetime.now(dt.UTC)
    result = s.execute(delete(Job).where(Job.expires_at < now))
    s.commit()
    return result.rowcount or 0


def new_job(s: Session, **fields) -> Job:
    ttl = get_settings().job_ttl_seconds
    job = Job(expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=ttl), **fields)
    s.add(job)
    s.commit()
    return job

"""SQLAlchemy ORM models — single source of truth for all database tables."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    default_branch: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    jobs: Mapped[list[Job]] = relationship("Job", back_populates="repository", cascade="all, delete-orphan")
    files: Mapped[list[File]] = relationship("File", back_populates="repository", cascade="all, delete-orphan")
    symbols: Mapped[list[Symbol]] = relationship("Symbol", back_populates="repository", cascade="all, delete-orphan")
    relationships: Mapped[list[Relationship]] = relationship("Relationship", back_populates="repository", cascade="all, delete-orphan")
    api_routes: Mapped[list[APIRoute]] = relationship("APIRoute", back_populates="repository", cascade="all, delete-orphan")
    chunks: Mapped[list[Chunk]] = relationship("Chunk", back_populates="repository", cascade="all, delete-orphan")
    guides: Mapped[list[Guide]] = relationship("Guide", back_populates="repository", cascade="all, delete-orphan")
    conversations: Mapped[list[Conversation]] = relationship("Conversation", back_populates="repository", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_repo_created", "repository_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    files_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    symbols_extracted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunks_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    repository: Mapped[Repository] = relationship("Repository", back_populates="jobs")


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("repository_id", "path", name="uq_files_repo_path"),
        Index("ix_files_repo_kind", "repository_id", "kind"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    path: Mapped[str] = mapped_column(String(2048), nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    language: Mapped[str | None] = mapped_column(String(50))
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    extra_meta: Mapped[dict | None] = mapped_column(JSONB)

    repository: Mapped[Repository] = relationship("Repository", back_populates="files")
    symbols: Mapped[list[Symbol]] = relationship("Symbol", back_populates="file", cascade="all, delete-orphan")
    chunks: Mapped[list[Chunk]] = relationship("Chunk", back_populates="file", cascade="all, delete-orphan")


class Symbol(Base):
    __tablename__ = "symbols"
    __table_args__ = (
        UniqueConstraint("repository_id", "symbol_key", name="uq_symbols_repo_key"),
        Index("ix_symbols_repo_name", "repository_id", "name"),
        Index("ix_symbols_repo_file", "repository_id", "file_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    file_id: Mapped[str] = mapped_column(String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False)
    symbol_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    qualified_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False, default="")
    docstring: Mapped[str] = mapped_column(Text, nullable=False, default="")

    repository: Mapped[Repository] = relationship("Repository", back_populates="symbols")
    file: Mapped[File] = relationship("File", back_populates="symbols")


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (
        Index("ix_relationships_source", "repository_id", "source_type", "source_key"),
        Index("ix_relationships_target", "repository_id", "target_type", "target_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    relation: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    line_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    repository: Mapped[Repository] = relationship("Repository", back_populates="relationships")


class APIRoute(Base):
    __tablename__ = "api_routes"
    __table_args__ = (Index("ix_api_routes_repo_path", "repository_id", "path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(2048), nullable=False)
    file_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    handler: Mapped[str] = mapped_column(String(512), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    framework: Mapped[str | None] = mapped_column(String(100))

    repository: Mapped[Repository] = relationship("Repository", back_populates="api_routes")


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_repo", "repository_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    file_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("files.id", ondelete="CASCADE"))
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_type: Mapped[str] = mapped_column(String(50), nullable=False, default="text")
    file_path: Mapped[str | None] = mapped_column(String(2048))
    start_line: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    language: Mapped[str | None] = mapped_column(String(50))
    symbol_name: Mapped[str | None] = mapped_column(String(512))
    extra_meta: Mapped[dict | None] = mapped_column(JSONB)

    repository: Mapped[Repository] = relationship("Repository", back_populates="chunks")
    file: Mapped[File | None] = relationship("File", back_populates="chunks")
    embedding: Mapped[Embedding | None] = relationship("Embedding", back_populates="chunk", uselist=False, cascade="all, delete-orphan")


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (Index("ix_embeddings_repo", "repository_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    chunk_id: Mapped[str] = mapped_column(String(36), ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False, unique=True)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(384), nullable=False)

    chunk: Mapped[Chunk] = relationship("Chunk", back_populates="embedding")


class Guide(Base):
    __tablename__ = "guides"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, unique=True)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    content_json: Mapped[dict | None] = mapped_column(JSONB)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    repository: Mapped[Repository] = relationship("Repository", back_populates="guides")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repository_id: Mapped[str] = mapped_column(String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    repository: Mapped[Repository] = relationship("Repository", back_populates="conversations")
    messages: Mapped[list[Message]] = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation", "conversation_id", "id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list | None] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    conversation: Mapped[Conversation] = relationship("Conversation", back_populates="messages")

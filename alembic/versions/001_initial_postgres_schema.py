"""initial postgres schema

Revision ID: 001
Revises:
Create Date: 2025-09-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "repositories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False, unique=True),
        sa.Column("default_branch", sa.String(255)),
        sa.Column("status", sa.String(50), nullable=False, server_default="queued"),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("repository_id", sa.String(36), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("files_scanned", sa.Integer, nullable=False, server_default="0"),
        sa.Column("symbols_extracted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("chunks_created", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_jobs_repo_created", "jobs", ["repository_id", "created_at"])

    op.create_table(
        "files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("repository_id", sa.String(36), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.String(2048), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("language", sa.String(50)),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("line_count", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("extra_meta", postgresql.JSONB),
        sa.UniqueConstraint("repository_id", "path", name="uq_files_repo_path"),
    )
    op.create_index("ix_files_repo_kind", "files", ["repository_id", "kind"])

    op.create_table(
        "symbols",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("repository_id", sa.String(36), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.String(36), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol_key", sa.String(1024), nullable=False),
        sa.Column("file_path", sa.String(2048), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("qualified_name", sa.String(1024), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("start_line", sa.Integer, nullable=False),
        sa.Column("end_line", sa.Integer, nullable=False),
        sa.Column("signature", sa.Text, nullable=False, server_default=""),
        sa.Column("docstring", sa.Text, nullable=False, server_default=""),
        sa.UniqueConstraint("repository_id", "symbol_key", name="uq_symbols_repo_key"),
    )
    op.create_index("ix_symbols_repo_name", "symbols", ["repository_id", "name"])
    op.create_index("ix_symbols_repo_file", "symbols", ["repository_id", "file_id"])

    op.create_table(
        "relationships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("repository_id", sa.String(36), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_key", sa.String(2048), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=False),
        sa.Column("target_key", sa.String(2048), nullable=False),
        sa.Column("relation", sa.String(100), nullable=False),
        sa.Column("evidence", sa.Text, nullable=False, server_default=""),
        sa.Column("line_number", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_index("ix_relationships_source", "relationships", ["repository_id", "source_type", "source_key"])
    op.create_index("ix_relationships_target", "relationships", ["repository_id", "target_type", "target_key"])

    op.create_table(
        "api_routes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("repository_id", sa.String(36), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(2048), nullable=False),
        sa.Column("file_path", sa.String(2048), nullable=False),
        sa.Column("handler", sa.String(512), nullable=False),
        sa.Column("line_number", sa.Integer, nullable=False),
        sa.Column("framework", sa.String(100)),
    )
    op.create_index("ix_api_routes_repo_path", "api_routes", ["repository_id", "path"])

    op.create_table(
        "chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("repository_id", sa.String(36), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.String(36), sa.ForeignKey("files.id", ondelete="CASCADE")),
        sa.Column("chunk_text", sa.Text, nullable=False),
        sa.Column("chunk_type", sa.String(50), nullable=False, server_default="text"),
        sa.Column("file_path", sa.String(2048)),
        sa.Column("start_line", sa.Integer, nullable=False, server_default="1"),
        sa.Column("end_line", sa.Integer, nullable=False, server_default="1"),
        sa.Column("language", sa.String(50)),
        sa.Column("symbol_name", sa.String(512)),
        sa.Column("extra_meta", postgresql.JSONB),
    )
    op.create_index("ix_chunks_repo", "chunks", ["repository_id"])

    op.execute("""
        CREATE TABLE embeddings (
            id VARCHAR(36) PRIMARY KEY,
            chunk_id VARCHAR(36) NOT NULL UNIQUE REFERENCES chunks(id) ON DELETE CASCADE,
            repository_id VARCHAR(36) NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
            embedding vector(384) NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_embeddings_repo ON embeddings(repository_id)")
    op.execute("CREATE INDEX ix_embeddings_vector ON embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)")

    op.create_table(
        "guides",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("repository_id", sa.String(36), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("content_md", sa.Text, nullable=False),
        sa.Column("content_json", postgresql.JSONB),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("repository_id", sa.String(36), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("evidence", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_messages_conversation", "messages", ["conversation_id", "id"])


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("guides")
    op.execute("DROP TABLE IF EXISTS embeddings")
    op.drop_table("chunks")
    op.drop_table("api_routes")
    op.drop_table("relationships")
    op.drop_table("symbols")
    op.drop_table("files")
    op.drop_table("jobs")
    op.drop_table("repositories")
    op.execute("DROP EXTENSION IF EXISTS vector")

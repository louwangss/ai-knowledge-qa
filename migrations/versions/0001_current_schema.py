"""建立 Alembic 接管前的完整数据库基线。"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_current_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "schema_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(
        sa.table("schema_version", sa.column("id", sa.Integer()), sa.column("version", sa.Integer())),
        [{"id": 1, "version": 3}],
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("last_active", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_session_user", "sessions", ["user_id", "status", "last_active"])
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=20), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("chunk_size", sa.Integer(), nullable=False),
        sa.Column("chunk_overlap", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_doc_hash", "documents", ["user_id", "content_hash"], unique=True)
    op.create_table(
        "chat_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_chat_session", "chat_history", ["user_id", "session_id", "created_at"])
    op.create_index("idx_chat_user_time", "chat_history", ["user_id", "created_at"])
    op.create_table(
        "chat_sources",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=500), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["chat_history.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_chat_source_message", "chat_sources", ["message_id", "position"])
    op.create_table(
        "chat_turns",
        sa.Column("client_turn_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("user_message_id", sa.BigInteger(), nullable=True),
        sa.Column("assistant_message_id", sa.BigInteger(), nullable=True),
        sa.Column("lease_owner", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["chat_history.id"], name="fk_chat_turn_assistant_message", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_chat_turn_session"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_chat_turn_user"),
        sa.ForeignKeyConstraint(["user_message_id"], ["chat_history.id"], name="fk_chat_turn_user_message", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("client_turn_id"),
    )
    op.create_index("idx_chat_turn_lease", "chat_turns", ["status", "lease_expires_at"])
    op.create_index("idx_chat_turn_session", "chat_turns", ["user_id", "session_id", "created_at"])
    op.create_index("idx_chat_turn_status", "chat_turns", ["status"])
    op.create_table(
        "episodic_memory",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_episodic_dedup", "episodic_memory", ["user_id", "event_type", "created_at"])
    op.create_index("idx_episodic_user_time", "episodic_memory", ["user_id", "created_at"])
    op.create_table(
        "semantic_memory",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("concept", sa.String(length=100), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("chroma_id", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_semantic_chroma", "semantic_memory", ["chroma_id"])
    op.create_index("idx_semantic_user", "semantic_memory", ["user_id"])
    op.create_table(
        "session_summary",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("compressed_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
    )
    op.create_index("idx_session_summary", "session_summary", ["session_id"], unique=True)


def downgrade() -> None:
    op.drop_table("session_summary")
    op.drop_table("semantic_memory")
    op.drop_table("episodic_memory")
    op.drop_table("chat_turns")
    op.drop_table("chat_sources")
    op.drop_table("chat_history")
    op.drop_table("documents")
    op.drop_table("sessions")
    op.drop_table("schema_version")
    op.drop_table("users")

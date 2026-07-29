"""增加持久化索引任务和实体索引版本。"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_index_jobs"
down_revision: Union[str, Sequence[str], None] = "0001_current_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("index_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("documents", sa.Column("indexed_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("semantic_memory", sa.Column("index_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("semantic_memory", sa.Column("indexed_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("semantic_memory", sa.Column("index_state", sa.String(length=20), nullable=False, server_default="pending"))
    op.create_table(
        "index_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("desired_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lease_owner", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("last_error_type", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_index_job_target_version",
        "index_jobs",
        ["entity_type", "entity_id", "operation", "desired_version"],
        unique=True,
    )
    op.create_index(
        "idx_index_job_available",
        "index_jobs",
        ["status", "next_attempt_at", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_table("index_jobs")
    op.drop_column("semantic_memory", "index_state")
    op.drop_column("semantic_memory", "indexed_version")
    op.drop_column("semantic_memory", "index_version")
    op.drop_column("documents", "indexed_version")
    op.drop_column("documents", "index_version")

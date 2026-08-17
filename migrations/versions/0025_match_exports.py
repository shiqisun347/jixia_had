"""Add bounded match export jobs and immutable cutoff items."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_match_exports"
down_revision: str | None = "0024_admin_data_capture"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    task_types = (
        "'HOST_TTS', 'LEADERBOARD_DAILY', 'TRANSCRIPT_AUTO_ARCHIVE', "
        "'POSTMATCH_AUDIO', 'FILE_CLEANUP', 'MATCH_EXPORT'"
    )
    op.drop_constraint("ck_background_tasks_type", "background_tasks", type_="check")
    op.create_check_constraint(
        "ck_background_tasks_type",
        "background_tasks",
        f"task_type IN ({task_types})",
    )
    op.create_table(
        "match_exports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(16), server_default="QUEUED", nullable=False),
        sa.Column("include_audio", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("total_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processed_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "scope", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("storage_path", sa.String(512)),
        sa.Column("byte_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("error_code", sa.String(128)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED', 'EXPIRED')",
            name="ck_match_exports_status",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_match_exports_owner_created", "match_exports", ["created_by_user_id", "created_at"]
    )
    op.create_index("ix_match_exports_status_expiry", "match_exports", ["status", "expires_at"])
    op.create_table(
        "match_export_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("export_id", sa.UUID(), nullable=False),
        sa.Column("match_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(16), server_default="PENDING", nullable=False),
        sa.Column("match_status", sa.String(32), nullable=False),
        sa.Column("cutoff_sequence", sa.Integer(), nullable=False),
        sa.Column("cutoff_context_version", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SUCCEEDED', 'FAILED', 'SKIPPED')",
            name="ck_match_export_items_status",
        ),
        sa.ForeignKeyConstraint(["export_id"], ["match_exports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("export_id", "match_id", name="uq_match_export_items_match"),
    )
    op.create_index(
        "ix_match_export_items_export_status", "match_export_items", ["export_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_match_export_items_export_status", table_name="match_export_items")
    op.drop_table("match_export_items")
    op.drop_index("ix_match_exports_status_expiry", table_name="match_exports")
    op.drop_index("ix_match_exports_owner_created", table_name="match_exports")
    op.drop_table("match_exports")
    op.drop_constraint("ck_background_tasks_type", "background_tasks", type_="check")
    task_types = (
        "'HOST_TTS', 'LEADERBOARD_DAILY', 'TRANSCRIPT_AUTO_ARCHIVE', "
        "'POSTMATCH_AUDIO', 'FILE_CLEANUP'"
    )
    op.create_check_constraint(
        "ck_background_tasks_type",
        "background_tasks",
        f"task_type IN ({task_types})",
    )

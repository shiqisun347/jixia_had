"""Add match files and post-match audio task types."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_audio_archive"
down_revision: str | None = "0013_postmatch_and_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_background_tasks_type", "background_tasks", type_="check")
    op.create_check_constraint(
        "ck_background_tasks_type",
        "background_tasks",
        "task_type IN ('HOST_TTS', 'LEADERBOARD_DAILY', 'TRANSCRIPT_AUTO_ARCHIVE', "
        "'POSTMATCH_AUDIO', 'FILE_CLEANUP')",
    )
    op.create_table(
        "match_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "match_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("matches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "speech_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("speeches.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
        ),
        sa.Column("file_key", sa.String(128), nullable=False),
        sa.Column("file_kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PROCESSING"),
        sa.Column("storage_path", sa.String(512)),
        sa.Column("codec", sa.String(32)),
        sa.Column("byte_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("permanent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.String(128)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "file_kind IN ('HUMAN_RAW', 'AGENT_RAW', 'MATCH_REPLAY', 'EXPORT_PACKAGE')",
            name="ck_match_files_kind",
        ),
        sa.CheckConstraint(
            "status IN ('PROCESSING', 'READY', 'FAILED', 'EXPIRED')",
            name="ck_match_files_status",
        ),
        sa.CheckConstraint("byte_count >= 0", name="ck_match_files_byte_count"),
        sa.UniqueConstraint("match_id", "file_key", name="uq_match_files_key"),
    )
    op.create_index("ix_match_files_expiry", "match_files", ["status", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_match_files_expiry", table_name="match_files")
    op.drop_table("match_files")
    op.drop_constraint("ck_background_tasks_type", "background_tasks", type_="check")
    op.create_check_constraint(
        "ck_background_tasks_type",
        "background_tasks",
        "task_type IN ('HOST_TTS', 'LEADERBOARD_DAILY', 'TRANSCRIPT_AUTO_ARCHIVE')",
    )

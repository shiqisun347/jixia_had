"""Extend diagnostic events into the version-aware RuntimeLog store."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_runtime_logs"
down_revision: str | None = "0034_format_workspace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_background_tasks_type", "background_tasks", type_="check")
    op.create_check_constraint(
        "ck_background_tasks_type",
        "background_tasks",
        "task_type IN ('HOST_TTS', 'LEADERBOARD_DAILY', 'TRANSCRIPT_AUTO_ARCHIVE', "
        "'POSTMATCH_AUDIO', 'FILE_CLEANUP', 'MATCH_EXPORT', 'EXPERIMENT_POSTMATCH', "
        "'EXPERIMENT_RESULT_PUBLISH', 'EXPERIMENT_BATCH_EXPORT', 'EXPERIMENT_RETENTION', "
        "'RUNTIME_LOG_RETENTION')",
    )
    op.drop_constraint("ck_system_log_events_level", "system_log_events", type_="check")
    op.create_check_constraint(
        "ck_system_log_events_level",
        "system_log_events",
        "level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')",
    )
    op.add_column("system_log_events", sa.Column("format_version_id", sa.UUID()))
    op.add_column("system_log_events", sa.Column("trace_id", sa.String(length=128)))
    op.create_foreign_key(
        "fk_system_log_events_format_version_id",
        "system_log_events",
        "format_versions",
        ["format_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_system_log_events_format_version",
        "system_log_events",
        ["format_version_id", "happened_at"],
    )
    op.create_index(
        "ix_system_log_events_request_trace",
        "system_log_events",
        ["request_id", "trace_id", "happened_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_system_log_events_request_trace", table_name="system_log_events")
    op.drop_index("ix_system_log_events_format_version", table_name="system_log_events")
    op.drop_constraint(
        "fk_system_log_events_format_version_id", "system_log_events", type_="foreignkey"
    )
    op.drop_column("system_log_events", "trace_id")
    op.drop_column("system_log_events", "format_version_id")
    op.drop_constraint("ck_system_log_events_level", "system_log_events", type_="check")
    op.create_check_constraint(
        "ck_system_log_events_level",
        "system_log_events",
        "level IN ('WARNING', 'ERROR', 'CRITICAL')",
    )
    op.drop_constraint("ck_background_tasks_type", "background_tasks", type_="check")
    op.create_check_constraint(
        "ck_background_tasks_type",
        "background_tasks",
        "task_type IN ('HOST_TTS', 'LEADERBOARD_DAILY', 'TRANSCRIPT_AUTO_ARCHIVE', "
        "'POSTMATCH_AUDIO', 'FILE_CLEANUP', 'MATCH_EXPORT', 'EXPERIMENT_POSTMATCH', "
        "'EXPERIMENT_RESULT_PUBLISH', 'EXPERIMENT_BATCH_EXPORT', 'EXPERIMENT_RETENTION')",
    )

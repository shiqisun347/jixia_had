"""Add post-match judging, transcript submission and leaderboard records."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_postmatch_and_admin"
down_revision: str | None = "0012_free_debate_stage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("matches", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.drop_constraint("ck_background_tasks_type", "background_tasks", type_="check")
    op.create_check_constraint(
        "ck_background_tasks_type",
        "background_tasks",
        "task_type IN ('HOST_TTS', 'LEADERBOARD_DAILY', 'TRANSCRIPT_AUTO_ARCHIVE')",
    )

    op.create_table(
        "match_participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "match_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("matches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT")
        ),
        sa.Column(
            "agent_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_profiles.id", ondelete="RESTRICT"),
        ),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("seat_no", sa.Integer(), nullable=False),
        sa.CheckConstraint("kind IN ('HUMAN', 'AGENT')", name="ck_match_participants_kind"),
        sa.CheckConstraint(
            "(kind = 'HUMAN' AND user_id IS NOT NULL AND agent_profile_id IS NULL) OR "
            "(kind = 'AGENT' AND user_id IS NULL AND agent_profile_id IS NOT NULL)",
            name="ck_match_participants_reference",
        ),
        sa.UniqueConstraint("match_id", "side", "seat_no", name="uq_match_participants_seat"),
    )
    op.create_index("ix_match_participants_match", "match_participants", ["match_id"])

    op.create_table(
        "transcript_submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "match_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("matches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column(
            "submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("auto_submitted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("match_id", "user_id", name="uq_transcript_submissions_user"),
    )

    op.create_table(
        "judge_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "model_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("judge_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "generation_params",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="ENABLED"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("status IN ('ENABLED', 'DISABLED')", name="ck_judge_profiles_status"),
    )

    op.create_table(
        "judge_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "match_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("matches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "judge_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("judge_profiles.id", ondelete="RESTRICT"),
        ),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column(
            "input_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column(
            "result", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("error_code", sa.String(128)),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_judge_results_status",
        ),
    )
    op.create_index("ix_judge_results_match", "judge_results", ["match_id", "created_at"])

    op.create_table(
        "leaderboard_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("average_personal_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('HUMAN', 'AGENT')", name="ck_leaderboard_snapshots_kind"),
        sa.UniqueConstraint("batch_id", "kind", "rank", name="uq_leaderboard_snapshot_rank"),
    )
    op.create_index(
        "ix_leaderboard_snapshots_latest", "leaderboard_snapshots", ["generated_at", "kind"]
    )


def downgrade() -> None:
    op.drop_index("ix_leaderboard_snapshots_latest", table_name="leaderboard_snapshots")
    op.drop_table("leaderboard_snapshots")
    op.drop_index("ix_judge_results_match", table_name="judge_results")
    op.drop_table("judge_results")
    op.drop_table("judge_profiles")
    op.drop_table("transcript_submissions")
    op.drop_index("ix_match_participants_match", table_name="match_participants")
    op.drop_table("match_participants")
    op.drop_constraint("ck_background_tasks_type", "background_tasks", type_="check")
    op.create_check_constraint(
        "ck_background_tasks_type", "background_tasks", "task_type IN ('HOST_TTS')"
    )
    op.drop_column("matches", "archived_at")

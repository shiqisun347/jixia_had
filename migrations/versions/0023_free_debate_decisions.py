"""Persist per-Agent free-debate decision outcomes for team analysis."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_free_debate_decisions"
down_revision: str | None = "0022_voice_playback_gain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_free_debate_decisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("match_id", sa.UUID(), nullable=False),
        sa.Column("action_key", sa.String(length=32), nullable=False),
        sa.Column("decision_round_id", sa.UUID(), nullable=False),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("agent_profile_id", sa.UUID(), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("seat_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="DECIDING", nullable=False),
        sa.Column("should_speak", sa.Boolean()),
        sa.Column("willingness", sa.Float()),
        sa.Column("attempt_no", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column("result_order", sa.Integer()),
        sa.Column("final_queue_rank", sa.Integer()),
        sa.Column("human_hand_at_result", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("human_hand_at_lock", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("selected", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("fallback", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('DECIDING', 'HAND', 'SKIP')",
            name="ck_agent_free_debate_decisions_status",
        ),
        sa.CheckConstraint(
            "willingness IS NULL OR (willingness >= 0 AND willingness <= 1)",
            name="ck_agent_free_debate_decisions_willingness",
        ),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_profile_id"], ["agent_profiles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "match_id",
            "decision_round_id",
            "agent_profile_id",
            name="uq_agent_free_debate_decisions_round_agent",
        ),
    )
    op.create_index(
        "ix_agent_free_debate_decisions_match",
        "agent_free_debate_decisions",
        ["match_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_free_debate_decisions_match", table_name="agent_free_debate_decisions")
    op.drop_table("agent_free_debate_decisions")

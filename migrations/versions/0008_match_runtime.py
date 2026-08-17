"""Add the first runtime match/event/speech persistence boundary.

Revision ID: 0008_match_runtime
Revises: 0007_room_occupancy_locks
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_match_runtime"
down_revision: str | None = "0007_room_occupancy_locks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "matches",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("room_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_stage_position", sa.Integer(), nullable=True),
        sa.Column("current_action_position", sa.Integer(), nullable=True),
        sa.Column("current_speech_id", sa.UUID(), nullable=True),
        sa.Column("sequence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("runtime_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('START_PENDING_RUNTIME', 'START_COUNTDOWN', 'RUNNING', 'FINISHED', "
            "'TERMINATED', 'SYSTEM_RECOVERY', 'ERROR')",
            name="ck_matches_status",
        ),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("room_id", name="uq_matches_room"),
    )
    op.create_index("ix_matches_status", "matches", ["status"])

    op.create_table(
        "match_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("match_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "sequence", name="uq_match_events_sequence"),
    )
    op.create_index("ix_match_events_match_sequence", "match_events", ["match_id", "sequence"])

    op.create_table(
        "speeches",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("match_id", sa.UUID(), nullable=False),
        sa.Column("action_key", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("seat_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="STARTED", nullable=False),
        sa.Column("finish_reason", sa.String(length=32), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("status IN ('STARTED', 'FINISHED', 'RESET')", name="ck_speeches_status"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_speeches_match", "speeches", ["match_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_speeches_match", table_name="speeches")
    op.drop_table("speeches")
    op.drop_index("ix_match_events_match_sequence", table_name="match_events")
    op.drop_table("match_events")
    op.drop_index("ix_matches_status", table_name="matches")
    op.drop_table("matches")

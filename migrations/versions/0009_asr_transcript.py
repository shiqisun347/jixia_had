"""Add Fun-ASR segments and editable transcript fields.

Revision ID: 0009_asr_transcript
Revises: 0008_match_runtime
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_asr_transcript"
down_revision: str | None = "0008_match_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("context_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.drop_constraint("ck_speeches_status", "speeches", type_="check")
    op.create_check_constraint(
        "ck_speeches_status",
        "speeches",
        "status IN ('STARTED', 'FINALIZING', 'FINALIZED', 'FAILED', 'RESET')",
    )
    op.add_column(
        "speeches", sa.Column("attempt_no", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column("speeches", sa.Column("asr_raw_final_text", sa.Text(), nullable=True))
    op.add_column("speeches", sa.Column("display_text", sa.Text(), nullable=True))
    op.add_column("speeches", sa.Column("first_interim_latency_ms", sa.Integer(), nullable=True))
    op.add_column("speeches", sa.Column("final_latency_ms", sa.Integer(), nullable=True))
    op.add_column("speeches", sa.Column("audio_duration_ms", sa.Integer(), nullable=True))
    op.add_column("speeches", sa.Column("asr_error_code", sa.String(length=128), nullable=True))
    op.add_column("speeches", sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "asr_segments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("speech_id", sa.UUID(), nullable=False),
        sa.Column("segment_no", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="STARTED", nullable=False),
        sa.Column("raw_final_text", sa.Text(), server_default="", nullable=False),
        sa.Column("first_interim_latency_ms", sa.Integer(), nullable=True),
        sa.Column("final_latency_ms", sa.Integer(), nullable=True),
        sa.Column("pcm_sample_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('STARTED', 'FINALIZED', 'FAILED', 'DISCARDED')",
            name="ck_asr_segments_status",
        ),
        sa.ForeignKeyConstraint(["speech_id"], ["speeches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("speech_id", "segment_no", name="uq_asr_segments_speech_no"),
        sa.UniqueConstraint("task_id", name="uq_asr_segments_task_id"),
    )
    op.create_index("ix_asr_segments_speech", "asr_segments", ["speech_id", "segment_no"])


def downgrade() -> None:
    op.drop_index("ix_asr_segments_speech", table_name="asr_segments")
    op.drop_table("asr_segments")
    op.drop_column("speeches", "finalized_at")
    op.drop_column("speeches", "asr_error_code")
    op.drop_column("speeches", "audio_duration_ms")
    op.drop_column("speeches", "final_latency_ms")
    op.drop_column("speeches", "first_interim_latency_ms")
    op.drop_column("speeches", "display_text")
    op.drop_column("speeches", "asr_raw_final_text")
    op.drop_column("speeches", "attempt_no")
    op.drop_constraint("ck_speeches_status", "speeches", type_="check")
    op.create_check_constraint(
        "ck_speeches_status",
        "speeches",
        "status IN ('STARTED', 'FINISHED', 'RESET')",
    )
    op.drop_column("matches", "context_version")

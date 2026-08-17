"""Persist TTS latency metrics for Agent voice evidence.

Revision ID: 0011_agent_voice_metrics
Revises: 0010_agent_voice
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_agent_voice_metrics"
down_revision: str | None = "0010_agent_voice"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_audio_assets", sa.Column("first_audio_latency_ms", sa.Integer()))
    op.add_column("agent_audio_assets", sa.Column("tts_completed_latency_ms", sa.Integer()))


def downgrade() -> None:
    op.drop_column("agent_audio_assets", "tts_completed_latency_ms")
    op.drop_column("agent_audio_assets", "first_audio_latency_ms")

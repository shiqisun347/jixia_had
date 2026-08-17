"""Add rule audio review state and bounded PostgreSQL background tasks.

Revision ID: 0004_rule_audio_tasks
Revises: 0003_rooms_foundation
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_rule_audio_tasks"
down_revision: str | None = "0003_rooms_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid_type = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "rules", sa.Column("audio_reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "background_tasks",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("task_type", sa.String(length=32), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="2", nullable=False),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("task_type IN ('HOST_TTS')", name="ck_background_tasks_type"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_background_tasks_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_background_tasks_attempts"),
        sa.CheckConstraint("max_attempts BETWEEN 1 AND 5", name="ck_background_tasks_max_attempts"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_background_tasks_claim",
        "background_tasks",
        ["status", "available_at", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_background_tasks_claim", table_name="background_tasks")
    op.drop_table("background_tasks")
    op.drop_column("rules", "audio_reviewed_at")

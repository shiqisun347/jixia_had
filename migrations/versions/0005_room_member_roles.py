"""Support organizer-only membership and durable ready state.

Revision ID: 0005_room_member_roles
Revises: 0004_rule_audio_tasks
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_room_member_roles"
down_revision: str | None = "0004_rule_audio_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_room_members_role", "room_members", type_="check")
    op.create_check_constraint(
        "ck_room_members_role",
        "room_members",
        "member_role IN ('ORGANIZER', 'DEBATER', 'SPECTATOR')",
    )
    op.add_column(
        "room_members",
        sa.Column("ready", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("room_members", "ready")
    op.drop_constraint("ck_room_members_role", "room_members", type_="check")
    op.create_check_constraint(
        "ck_room_members_role",
        "room_members",
        "member_role IN ('DEBATER', 'SPECTATOR')",
    )

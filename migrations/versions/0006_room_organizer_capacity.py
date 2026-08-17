"""Allow administrators to organize multiple all-Agent waiting rooms.

Revision ID: 0006_room_organizer_capacity
Revises: 0005_room_member_roles
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_room_organizer_capacity"
down_revision: str | None = "0005_room_member_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_room_members_active_user", table_name="room_members")
    op.create_index(
        "uq_room_members_active_user",
        "room_members",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("left_at IS NULL AND member_role <> 'ORGANIZER'"),
    )


def downgrade() -> None:
    op.drop_index("uq_room_members_active_user", table_name="room_members")
    op.create_index(
        "uq_room_members_active_user",
        "room_members",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("left_at IS NULL"),
    )

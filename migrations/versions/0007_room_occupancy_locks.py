"""Use transactional user locks instead of lifetime occupancy indexes.

Revision ID: 0007_room_occupancy_locks
Revises: 0006_room_organizer_capacity
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_room_occupancy_locks"
down_revision: str | None = "0006_room_organizer_capacity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_room_members_active_user", table_name="room_members")
    op.drop_index("uq_seats_active_human", table_name="seats")


def downgrade() -> None:
    op.create_index(
        "uq_room_members_active_user",
        "room_members",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("left_at IS NULL AND member_role <> 'ORGANIZER'"),
    )
    op.create_index(
        "uq_seats_active_human",
        "seats",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )

"""Persist room Agent-fill policy and restorable seat Agent assignments.

Revision ID: 0017_room_join_agent_restore
Revises: 0016_default_single_admin
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_room_join_agent_restore"
down_revision: str | None = "0016_default_single_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid_type = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "rooms",
        sa.Column("auto_fill_agents", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "seats",
        sa.Column("configured_agent_profile_id", uuid_type, nullable=True),
    )
    op.create_foreign_key(
        "fk_seats_configured_agent_profile_id",
        "seats",
        "agent_profiles",
        ["configured_agent_profile_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute(
        sa.text(
            """
            UPDATE seats
            SET configured_agent_profile_id = agent_profile_id
            WHERE occupant_type = 'AGENT'
              AND agent_profile_id IS NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE rooms AS room
            SET auto_fill_agents = true
            WHERE EXISTS (
                SELECT 1 FROM seats
                WHERE seats.room_id = room.id
                  AND seats.occupant_type = 'AGENT'
            )
              AND NOT EXISTS (
                SELECT 1 FROM seats
                WHERE seats.room_id = room.id
                  AND seats.occupant_type = 'EMPTY'
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_constraint("fk_seats_configured_agent_profile_id", "seats", type_="foreignkey")
    op.drop_column("seats", "configured_agent_profile_id")
    op.drop_column("rooms", "auto_fill_agents")

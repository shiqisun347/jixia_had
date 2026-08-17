"""Add transactional waiting-room seat swap requests."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_seat_swap_requests"
down_revision: str | None = "0020_voice_owned_agent_avatar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "seat_swap_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("room_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requester_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requester_seat_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_seat_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requester_seat_id"], ["seats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_seat_id"], ["seats.id"], ondelete="CASCADE"),
        sa.CheckConstraint("status IN ('PENDING','ACCEPTED','REJECTED','CANCELLED')", name="ck_seat_swap_requests_status"),
        sa.CheckConstraint("requester_user_id <> target_user_id", name="ck_seat_swap_requests_distinct_users"),
    )
    op.create_index("ix_seat_swap_requests_room_status", "seat_swap_requests", ["room_id", "status"])
    op.create_index("ix_seat_swap_requests_requester", "seat_swap_requests", ["requester_user_id", "created_at"])
    op.create_index("ix_seat_swap_requests_target", "seat_swap_requests", ["target_user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("seat_swap_requests")

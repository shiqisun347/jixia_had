"""Create public room, membership, seat, device-check, and capacity tables.

Revision ID: 0003_rooms_foundation
Revises: 0002_rules_catalogs
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_rooms_foundation"
down_revision: str | None = "0002_rules_catalogs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid_type = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "capacity_guards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(sa.text("INSERT INTO capacity_guards (id) VALUES (1)"))
    op.create_table(
        "rooms",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("code", sa.String(length=6), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("topic_id", uuid_type, nullable=True),
        sa.Column("topic_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("rule_id", uuid_type, nullable=False),
        sa.Column("rule_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("organizer_user_id", uuid_type, nullable=False),
        sa.Column("is_all_agent", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("sequence", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="WAITING", nullable=False),
        sa.CheckConstraint(
            "status IN ('WAITING', 'START_PENDING_RUNTIME', 'RUNNING', 'PAUSED', "
            "'FINISHED', 'TERMINATED')",
            name="ck_rooms_status",
        ),
        sa.ForeignKeyConstraint(["organizer_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_rooms_code"),
    )
    op.create_index("ix_rooms_status", "rooms", ["status"], unique=False)
    op.create_index("ix_rooms_created_at", "rooms", ["created_at"], unique=False)
    op.create_table(
        "room_members",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("room_id", uuid_type, nullable=False),
        sa.Column("user_id", uuid_type, nullable=False),
        sa.Column("member_role", sa.String(length=16), nullable=False),
        sa.Column("online", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("member_role IN ('DEBATER', 'SPECTATOR')", name="ck_room_members_role"),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("room_id", "user_id", name="uq_room_members_user"),
    )
    op.create_index(
        "uq_room_members_active_user",
        "room_members",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("left_at IS NULL"),
    )
    op.create_index(
        "ix_room_members_room_active", "room_members", ["room_id", "left_at"], unique=False
    )
    op.create_table(
        "seats",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("room_id", uuid_type, nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("seat_no", sa.Integer(), nullable=False),
        sa.Column("occupant_type", sa.String(length=16), server_default="EMPTY", nullable=False),
        sa.Column("user_id", uuid_type, nullable=True),
        sa.Column("agent_profile_id", uuid_type, nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("side IN ('AFFIRMATIVE', 'NEGATIVE')", name="ck_seats_side"),
        sa.CheckConstraint(
            "occupant_type IN ('EMPTY', 'HUMAN', 'AGENT')", name="ck_seats_occupant_type"
        ),
        sa.CheckConstraint("seat_no BETWEEN 1 AND 5", name="ck_seats_seat_no"),
        sa.CheckConstraint(
            "(occupant_type = 'EMPTY' AND user_id IS NULL AND agent_profile_id IS NULL) OR "
            "(occupant_type = 'HUMAN' AND user_id IS NOT NULL AND agent_profile_id IS NULL) OR "
            "(occupant_type = 'AGENT' AND user_id IS NULL AND agent_profile_id IS NOT NULL)",
            name="ck_seats_occupant_reference",
        ),
        sa.ForeignKeyConstraint(["agent_profile_id"], ["agent_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("room_id", "side", "seat_no", name="uq_seats_position"),
    )
    op.create_index(
        "uq_seats_active_human",
        "seats",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_table(
        "device_checks",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("room_id", uuid_type, nullable=False),
        sa.Column("user_id", uuid_type, nullable=False),
        sa.Column("check_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("warning_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('PASS', 'WARN', 'FAIL')", name="ck_device_checks_status"),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("room_id", "user_id", "check_version", name="uq_device_checks_version"),
    )
    op.create_index(
        "ix_device_checks_latest",
        "device_checks",
        ["room_id", "user_id", "check_version"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_room_connections_room_id",
        "room_connections",
        "rooms",
        ["room_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_room_connections_room_id", "room_connections", type_="foreignkey")
    op.drop_index("ix_device_checks_latest", table_name="device_checks")
    op.drop_table("device_checks")
    op.drop_index("uq_seats_active_human", table_name="seats")
    op.drop_table("seats")
    op.drop_index("ix_room_members_room_active", table_name="room_members")
    op.drop_index("uq_room_members_active_user", table_name="room_members")
    op.drop_table("room_members")
    op.drop_index("ix_rooms_created_at", table_name="rooms")
    op.drop_index("ix_rooms_status", table_name="rooms")
    op.drop_table("rooms")
    op.drop_table("capacity_guards")

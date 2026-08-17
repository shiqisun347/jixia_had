"""Persist human and Agent avatar preset keys."""

import sqlalchemy as sa
from alembic import op

revision = "0018_profile_avatar_catalog"
down_revision = "0017_room_join_agent_restore"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("default_avatar_key", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "agent_profiles",
        sa.Column("avatar_key", sa.String(length=32), nullable=True),
    )
    op.execute(
        """
        UPDATE users
        SET default_avatar_key = 'human-' || lpad(
            (1 + (('x' || substr(md5(id::text), 1, 8))::bit(32)::bigint % 16))::text,
            2,
            '0'
        )
        WHERE default_avatar_key IS NULL
        """
    )
    op.execute(
        """
        UPDATE agent_profiles
        SET avatar_key = 'agent-' || lpad(
            (1 + (('x' || substr(md5(id::text), 1, 8))::bit(32)::bigint % 12))::text,
            2,
            '0'
        )
        WHERE avatar_key IS NULL
        """
    )
    op.alter_column(
        "users",
        "default_avatar_key",
        nullable=False,
        server_default="human-01",
    )
    op.alter_column(
        "agent_profiles",
        "avatar_key",
        nullable=False,
        server_default="agent-01",
    )
    op.create_check_constraint(
        "ck_users_default_avatar_key",
        "users",
        "default_avatar_key ~ '^human-(0[1-9]|1[0-6])$'",
    )
    op.create_check_constraint(
        "ck_agent_profiles_avatar_key",
        "agent_profiles",
        "avatar_key ~ '^agent-(0[1-9]|1[0-2])$'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_agent_profiles_avatar_key", "agent_profiles", type_="check")
    op.drop_constraint("ck_users_default_avatar_key", "users", type_="check")
    op.drop_column("agent_profiles", "avatar_key")
    op.drop_column("users", "default_avatar_key")

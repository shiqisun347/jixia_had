"""Make the TTS voice the single source of truth for Agent avatars.

Revision ID: 0020_voice_owned_agent_avatar
Revises: 0019_voice_avatar_recommendation
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_voice_owned_agent_avatar"
down_revision: str | None = "0019_voice_avatar_recommendation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AGENT_AVATARS = tuple(f"agent-{index:02d}" for index in range(1, 13))


def upgrade() -> None:
    op.execute(
        """
        UPDATE voice_profiles AS voice
        SET recommended_avatar_key = source.avatar_key
        FROM (
            SELECT voice_profile_id, min(avatar_key) AS avatar_key
            FROM agent_profiles
            GROUP BY voice_profile_id
        ) AS source
        WHERE voice.id = source.voice_profile_id
          AND voice.kind = 'AGENT'
          AND voice.recommended_avatar_key IS NULL
        """
    )
    op.execute(
        """
        UPDATE voice_profiles
        SET recommended_avatar_key = 'agent-' || lpad(
            (1 + (('x' || substr(md5(id::text), 1, 8))::bit(32)::bigint % 12))::text,
            2,
            '0'
        )
        WHERE kind = 'AGENT' AND recommended_avatar_key IS NULL
        """
    )
    op.execute("UPDATE voice_profiles SET recommended_avatar_key = NULL WHERE kind = 'HOST'")
    op.drop_constraint("ck_voice_profiles_recommended_avatar_key", "voice_profiles", type_="check")
    op.alter_column("voice_profiles", "recommended_avatar_key", new_column_name="avatar_key")
    op.create_check_constraint(
        "ck_voice_profiles_avatar_key_by_kind",
        "voice_profiles",
        "(kind = 'AGENT' AND avatar_key IS NOT NULL AND avatar_key IN "
        f"({', '.join(repr(key) for key in _AGENT_AVATARS)})) OR "
        "(kind = 'HOST' AND avatar_key IS NULL)",
    )
    op.drop_constraint("ck_agent_profiles_avatar_key", "agent_profiles", type_="check")
    op.drop_column("agent_profiles", "avatar_key")


def downgrade() -> None:
    op.add_column(
        "agent_profiles",
        sa.Column("avatar_key", sa.String(length=32), nullable=True),
    )
    op.execute(
        """
        UPDATE agent_profiles AS agent
        SET avatar_key = voice.avatar_key
        FROM voice_profiles AS voice
        WHERE agent.voice_profile_id = voice.id
        """
    )
    op.alter_column("agent_profiles", "avatar_key", nullable=False, server_default="agent-01")
    op.create_check_constraint(
        "ck_agent_profiles_avatar_key",
        "agent_profiles",
        "avatar_key ~ '^agent-(0[1-9]|1[0-2])$'",
    )
    op.drop_constraint("ck_voice_profiles_avatar_key_by_kind", "voice_profiles", type_="check")
    op.alter_column("voice_profiles", "avatar_key", new_column_name="recommended_avatar_key")
    op.create_check_constraint(
        "ck_voice_profiles_recommended_avatar_key",
        "voice_profiles",
        "recommended_avatar_key IS NULL OR recommended_avatar_key IN "
        f"({', '.join(repr(key) for key in _AGENT_AVATARS)})",
    )

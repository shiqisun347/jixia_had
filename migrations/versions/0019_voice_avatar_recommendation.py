"""Persist the recommended Agent avatar for each TTS voice.

Revision ID: 0019_voice_avatar_recommendation
Revises: 0018_profile_avatar_catalog
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_voice_avatar_recommendation"
down_revision: str | None = "0018_profile_avatar_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AGENT_AVATARS = tuple(f"agent-{index:02d}" for index in range(1, 13))


def upgrade() -> None:
    op.add_column(
        "voice_profiles",
        sa.Column("recommended_avatar_key", sa.String(length=32), nullable=True),
    )
    op.create_check_constraint(
        "ck_voice_profiles_recommended_avatar_key",
        "voice_profiles",
        "recommended_avatar_key IS NULL OR recommended_avatar_key IN "
        f"({', '.join(repr(key) for key in _AGENT_AVATARS)})",
    )
    op.execute(
        """
        UPDATE voice_profiles
        SET recommended_avatar_key = CASE name
            WHEN '龙涟霓蓉' THEN 'agent-08'
            WHEN '龙莹松柳' THEN 'agent-02'
            WHEN '龙弦星岚' THEN 'agent-05'
            WHEN '龙蓉桃涟' THEN 'agent-10'
            WHEN '龙璨竹月' THEN 'agent-03'
            WHEN '龙翼暮凌' THEN 'agent-04'
            WHEN '龙安灵希' THEN 'agent-01'
            WHEN '龙漪暄珺' THEN 'agent-07'
            WHEN '龙昕蕊璇' THEN 'agent-11'
            WHEN '龙晴霄湘' THEN 'agent-09'
            ELSE recommended_avatar_key
        END
        WHERE kind = 'AGENT'
        """
    )
    op.execute(
        """
        UPDATE agent_profiles AS agent
        SET avatar_key = voice.recommended_avatar_key
        FROM voice_profiles AS voice
        WHERE agent.voice_profile_id = voice.id
          AND voice.recommended_avatar_key IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_voice_profiles_recommended_avatar_key", "voice_profiles", type_="check")
    op.drop_column("voice_profiles", "recommended_avatar_key")

"""Persist fixed per-voice playback gain calibrated from complete samples."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_voice_playback_gain"
down_revision: str | None = "0021_seat_swap_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_GAINS = {
    "龙露柳澈": 1.0,
    "龙涟霓蓉": 1.2006,
    "龙莹松柳": 0.9313,
    "龙弦星岚": 0.6702,
    "龙蓉桃涟": 0.9548,
    "龙璨竹月": 0.9534,
    "龙翼暮凌": 1.2484,
    "龙安灵希": 0.9875,
    "龙漪暄珺": 0.9849,
    "龙昕蕊璇": 1.7237,
    "龙晴霄湘": 1.4591,
}


def upgrade() -> None:
    op.add_column(
        "voice_profiles",
        sa.Column("playback_gain", sa.Float(), nullable=False, server_default="1.0"),
    )
    op.create_check_constraint(
        "ck_voice_profiles_playback_gain",
        "voice_profiles",
        "playback_gain BETWEEN 0.50 AND 2.00",
    )
    for name, gain in _GAINS.items():
        op.execute(
            sa.text("UPDATE voice_profiles SET playback_gain = :gain WHERE name = :name")
            .bindparams(gain=gain, name=name)
        )


def downgrade() -> None:
    op.drop_constraint("ck_voice_profiles_playback_gain", "voice_profiles", type_="check")
    op.drop_column("voice_profiles", "playback_gain")

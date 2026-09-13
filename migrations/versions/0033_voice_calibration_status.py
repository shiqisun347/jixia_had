"""Track whether a voice preview is calibrated for its current parameters."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_voice_calibration_status"
down_revision: str | None = "0032_system_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "voice_profiles",
        sa.Column(
            "calibration_status",
            sa.String(length=16),
            nullable=False,
            server_default="NOT_CALIBRATED",
        ),
    )
    op.add_column(
        "voice_profiles",
        sa.Column("calibrated_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "ck_voice_profiles_calibration_status",
        "voice_profiles",
        "calibration_status IN ('NOT_CALIBRATED', 'READY', 'STALE', 'FAILED')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_voice_profiles_calibration_status", "voice_profiles", type_="check"
    )
    op.drop_column("voice_profiles", "calibrated_at")
    op.drop_column("voice_profiles", "calibration_status")

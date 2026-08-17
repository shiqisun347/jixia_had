"""Store editable parameters for free-debate stages."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_free_debate_stage"
down_revision: str | None = "0011_agent_voice_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "matches", sa.Column("match_seed", sa.BigInteger(), server_default="0", nullable=False)
    )
    op.drop_constraint("ck_matches_status", "matches", type_="check")
    op.create_check_constraint(
        "ck_matches_status",
        "matches",
        "status IN ('START_PENDING_RUNTIME', 'START_COUNTDOWN', 'RUNNING', 'PAUSED', "
        "'FINISHED', 'TERMINATED', 'SYSTEM_RECOVERY', 'ERROR')",
    )
    op.add_column(
        "rule_stages",
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("matches", "match_seed")
    op.drop_column("rule_stages", "parameters")
    op.drop_constraint("ck_matches_status", "matches", type_="check")
    op.create_check_constraint(
        "ck_matches_status",
        "matches",
        "status IN ('START_PENDING_RUNTIME', 'START_COUNTDOWN', 'RUNNING', 'FINISHED', "
        "'TERMINATED', 'SYSTEM_RECOVERY', 'ERROR')",
    )

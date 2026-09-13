"""Freeze current rule configuration on new experiment batches."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038_experiment_rule_snapshot"
down_revision: str | None = "0037_rule_owned_agent_pools"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "experiment_batches",
        sa.Column("rule_config_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.drop_constraint(
        "ck_scheduled_seats_agent_not_first", "scheduled_seats", type_="check"
    )


def downgrade() -> None:
    op.create_check_constraint(
        "ck_scheduled_seats_agent_not_first",
        "scheduled_seats",
        "NOT (seat_no = 1 AND occupant_kind = 'AGENT')",
    )
    op.drop_column("experiment_batches", "rule_config_snapshot")

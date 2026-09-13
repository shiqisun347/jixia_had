"""Store the bounded reason returned by free-debate Agent decisions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_agent_decision_reason"
down_revision: str | None = "0041_ai_debate_questionnaires"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_free_debate_decisions",
        sa.Column("decision_reason", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_free_debate_decisions", "decision_reason")

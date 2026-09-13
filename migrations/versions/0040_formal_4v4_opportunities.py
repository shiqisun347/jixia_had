"""Allow formal 4v4 opportunities outside experiment batches."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_formal_4v4_opportunities"
down_revision: str | None = "0039_provider_api_request_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "free_debate_opportunities",
        "experiment_attempt_id",
        existing_type=sa.UUID(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "free_debate_opportunities",
        "experiment_attempt_id",
        existing_type=sa.UUID(),
        nullable=False,
    )

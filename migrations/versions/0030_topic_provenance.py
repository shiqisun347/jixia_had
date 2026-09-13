"""Add source provenance fields to topics."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_topic_provenance"
down_revision: str | None = "0029_paper_experiment_adaptation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("topics", sa.Column("source_text", sa.Text(), nullable=True))
    op.add_column("topics", sa.Column("cedar_id", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("topics", "cedar_id")
    op.drop_column("topics", "source_text")

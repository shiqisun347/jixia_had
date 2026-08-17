"""Add the private administrator note for terminal matches.

Revision ID: 0015_match_admin_note
Revises: 0014_audio_archive
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_match_admin_note"
down_revision: str | None = "0014_audio_archive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("admin_note", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("matches", "admin_note")

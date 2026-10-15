"""Record explicitly labelled administrator placeholders without changing old speeches."""

import sqlalchemy as sa
from alembic import op

revision = "0047_speech_origin"
down_revision = "0046_provider_capture_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "speeches",
        sa.Column("origin", sa.String(24), nullable=False, server_default="ORIGINAL"),
    )
    op.create_check_constraint(
        "ck_speeches_origin", "speeches", "origin IN ('ORIGINAL', 'ADMIN_PLACEHOLDER')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_speeches_origin", "speeches", type_="check")
    op.drop_column("speeches", "origin")

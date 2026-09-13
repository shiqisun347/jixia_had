"""Store revocable browser-scoped device check credentials."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043_browser_device_checks"
down_revision: str | None = "0042_agent_decision_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "browser_device_checks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_browser_device_checks_token_hash"),
    )
    op.create_index("ix_browser_device_checks_user", "browser_device_checks", ["user_id"])
    op.create_index(
        "ix_browser_device_checks_valid_until", "browser_device_checks", ["valid_until"]
    )


def downgrade() -> None:
    op.drop_index("ix_browser_device_checks_valid_until", table_name="browser_device_checks")
    op.drop_index("ix_browser_device_checks_user", table_name="browser_device_checks")
    op.drop_table("browser_device_checks")

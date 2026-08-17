"""Add deduplicated system incident records for the admin diagnostic center."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_system_incidents"
down_revision: str | None = "0025_match_exports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_incidents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), server_default="OPEN", nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("affected_match_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("affected_user_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("notes", sa.String(2000)),
        sa.Column("acknowledged_by_user_id", sa.UUID()),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by_user_id", sa.UUID()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "severity IN ('WARNING', 'ERROR', 'CRITICAL')", name="ck_system_incidents_severity"
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')", name="ck_system_incidents_status"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_system_incidents_fingerprint"),
    )
    op.create_index(
        "ix_system_incidents_status_seen", "system_incidents", ["status", "last_seen_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_system_incidents_status_seen", table_name="system_incidents")
    op.drop_table("system_incidents")

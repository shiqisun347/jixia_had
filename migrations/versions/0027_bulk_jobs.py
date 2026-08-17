"""Add bounded administrator bulk operation jobs."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_bulk_jobs"
down_revision: str | None = "0026_system_incidents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bulk_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("resource", sa.String(32), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), server_default="QUEUED", nullable=False),
        sa.Column("total_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processed_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("succeeded_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')",
            name="ck_bulk_jobs_status",
        ),
        sa.CheckConstraint(
            "operation IN ('ENABLE', 'DISABLE', 'EXPORT', 'DELETE')", name="ck_bulk_jobs_operation"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bulk_jobs_owner_created", "bulk_jobs", ["created_by_user_id", "created_at"])
    op.create_table(
        "bulk_job_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(16), server_default="PENDING", nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SUCCEEDED', 'FAILED', 'SKIPPED')",
            name="ck_bulk_job_items_status",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["bulk_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "target_id", name="uq_bulk_job_items_target"),
    )
    op.create_index("ix_bulk_job_items_job_status", "bulk_job_items", ["job_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_bulk_job_items_job_status", table_name="bulk_job_items")
    op.drop_table("bulk_job_items")
    op.drop_index("ix_bulk_jobs_owner_created", table_name="bulk_jobs")
    op.drop_table("bulk_jobs")

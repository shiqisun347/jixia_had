"""Separate rule lifecycle from host-audio generation and review state."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044_rule_audio_generations"
down_revision: str | None = "0043_browser_device_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rules",
        sa.Column("lifecycle_status", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "rules",
        sa.Column("current_audio_generation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_rules_lifecycle_status",
        "rules",
        "lifecycle_status IN ('DISABLED', 'ENABLED', 'ARCHIVED')",
    )

    op.create_table(
        "rule_audio_generations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "manifest", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("status", sa.String(length=16), server_default="QUEUED", nullable=False),
        sa.Column("review_status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ready_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('NOT_REQUIRED', 'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SUPERSEDED')",
            name="ck_rule_audio_generations_status",
        ),
        sa.CheckConstraint(
            "review_status IN ('NOT_REQUIRED', 'PENDING', 'APPROVED')",
            name="ck_rule_audio_generations_review_status",
        ),
        sa.CheckConstraint("total_count >= 0", name="ck_rule_audio_generations_total_nonnegative"),
        sa.CheckConstraint("ready_count >= 0", name="ck_rule_audio_generations_ready_nonnegative"),
        sa.CheckConstraint(
            "failed_count >= 0", name="ck_rule_audio_generations_failed_nonnegative"
        ),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rule_audio_generations_rule_current",
        "rule_audio_generations",
        ["rule_id", "is_current"],
    )
    op.create_foreign_key(
        "fk_rules_current_audio_generation",
        "rules",
        "rule_audio_generations",
        ["current_audio_generation_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "host_audio_assets",
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("host_audio_assets", sa.Column("input_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "host_audio_assets",
        sa.Column(
            "input_snapshot",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("host_audio_assets", sa.Column("active_task_id", postgresql.UUID(as_uuid=True)))
    op.add_column("host_audio_assets", sa.Column("byte_count", sa.Integer()))
    op.add_column("host_audio_assets", sa.Column("checksum", sa.String(length=64)))
    op.drop_constraint("ck_host_audio_assets_status", "host_audio_assets", type_="check")
    op.create_check_constraint(
        "ck_host_audio_assets_status",
        "host_audio_assets",
        "status IN ('PENDING', 'RUNNING', 'READY', 'FAILED')",
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("service", sa.String(length=32), primary_key=True),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_foreign_key(
        "fk_host_audio_assets_generation",
        "host_audio_assets",
        "rule_audio_generations",
        ["generation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("uq_host_audio_assets_segment", "host_audio_assets", type_="unique")
    op.create_unique_constraint(
        "uq_host_audio_assets_generation_segment",
        "host_audio_assets",
        ["generation_id", "segment_key"],
    )
    op.create_index("ix_host_audio_assets_generation", "host_audio_assets", ["generation_id"])

    op.execute(
        "UPDATE rules SET lifecycle_status = CASE WHEN status = 'ENABLED' THEN 'ENABLED' "
        "WHEN status = 'ARCHIVED' THEN 'ARCHIVED' ELSE 'DISABLED' END"
    )
    op.alter_column("rules", "lifecycle_status", nullable=False, server_default="DISABLED")


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
    for column in ("input_snapshot", "active_task_id", "byte_count", "checksum"):
        op.drop_column("host_audio_assets", column)
    op.drop_constraint("ck_host_audio_assets_status", "host_audio_assets", type_="check")
    op.create_check_constraint(
        "ck_host_audio_assets_status",
        "host_audio_assets",
        "status IN ('PENDING', 'READY', 'FAILED')",
    )
    op.drop_index("ix_host_audio_assets_generation", table_name="host_audio_assets")
    op.drop_constraint(
        "uq_host_audio_assets_generation_segment", "host_audio_assets", type_="unique"
    )
    op.create_unique_constraint(
        "uq_host_audio_assets_segment", "host_audio_assets", ["rule_id", "segment_key"]
    )
    op.drop_constraint("fk_host_audio_assets_generation", "host_audio_assets", type_="foreignkey")
    op.drop_column("host_audio_assets", "input_hash")
    op.drop_column("host_audio_assets", "generation_id")
    op.drop_constraint("fk_rules_current_audio_generation", "rules", type_="foreignkey")
    op.drop_index("ix_rule_audio_generations_rule_current", table_name="rule_audio_generations")
    op.drop_table("rule_audio_generations")
    op.drop_constraint("ck_rules_lifecycle_status", "rules", type_="check")
    op.drop_column("rules", "current_audio_generation_id")
    op.drop_column("rules", "lifecycle_status")

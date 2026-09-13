"""Bind experiment batches to immutable published format versions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_experiment_batch_format"
down_revision: str | None = "0035_runtime_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("experiment_batches", sa.Column("format_version_id", sa.UUID()))
    op.add_column(
        "experiment_batches",
        sa.Column("training_room_quota", sa.Integer(), nullable=False, server_default="3"),
    )
    op.create_check_constraint(
        "ck_experiment_batches_training_quota",
        "experiment_batches",
        "training_room_quota BETWEEN 1 AND 20",
    )
    op.add_column("experiment_match_attempts", sa.Column("created_by_user_id", sa.UUID()))
    op.create_foreign_key(
        "fk_experiment_match_attempts_created_by_user_id",
        "experiment_match_attempts",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_experiment_batches_format_version_id",
        "experiment_batches",
        "format_versions",
        ["format_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute(
        sa.text(
            """
            UPDATE experiment_batches b
            SET format_version_id = (
              SELECT v.id
              FROM format_versions v
              WHERE v.rule_id = b.rule_id AND v.status = 'PUBLISHED'
              ORDER BY v.version DESC, v.created_at DESC, v.id DESC
              LIMIT 1
            )
            WHERE b.format_version_id IS NULL
              AND EXISTS (
                SELECT 1 FROM format_versions v
                WHERE v.rule_id = b.rule_id AND v.status = 'PUBLISHED'
              )
            """
        )
    )
    op.create_index(
        "ix_experiment_batches_format_version",
        "experiment_batches",
        ["format_version_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_experiment_batches_format_version", table_name="experiment_batches")
    op.drop_constraint(
        "fk_experiment_batches_format_version_id", "experiment_batches", type_="foreignkey"
    )
    op.drop_column("experiment_batches", "format_version_id")
    op.drop_constraint(
        "fk_experiment_match_attempts_created_by_user_id",
        "experiment_match_attempts",
        type_="foreignkey",
    )
    op.drop_column("experiment_match_attempts", "created_by_user_id")
    op.drop_constraint(
        "ck_experiment_batches_training_quota", "experiment_batches", type_="check"
    )
    op.drop_column("experiment_batches", "training_room_quota")

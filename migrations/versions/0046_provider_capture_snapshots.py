"""Separate provider captures and freeze export capture projections."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "0046_provider_capture_snapshots"
down_revision: str | None = "0045_postmatch_showcase"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _raw_columns() -> list[sa.Column[str] | sa.Column[UUID] | sa.Column[int]]:
    return [
        sa.Column("provider_request_id", sa.String(256)),
        sa.Column("request_blob_id", sa.UUID()),
        sa.Column("response_blob_id", sa.UUID()),
        sa.Column("request_capture_status", sa.String(32)),
        sa.Column("response_capture_status", sa.String(32)),
        sa.Column("request_original_bytes", sa.BigInteger()),
        sa.Column("response_original_bytes", sa.BigInteger()),
        sa.Column("request_stored_bytes", sa.BigInteger()),
        sa.Column("response_stored_bytes", sa.BigInteger()),
        sa.Column("request_sha256", sa.String(64)),
        sa.Column("response_sha256", sa.String(64)),
        sa.Column("capture_error_code", sa.String(128)),
    ]


def upgrade() -> None:
    # Nullable additions deliberately preserve historical rows without a
    # backfill. Async mode and frozen-input version are set by new producers.
    op.add_column("external_calls", sa.Column("capture_storage_mode", sa.String(16)))
    op.create_check_constraint(
        "ck_external_calls_capture_storage_mode",
        "external_calls",
        "capture_storage_mode IS NULL OR capture_storage_mode IN ('LEGACY', 'ASYNC_V1')",
    )
    op.create_index(
        "ix_external_calls_capture_mode_started",
        "external_calls",
        ["capture_storage_mode", "started_at", "id"],
    )
    op.add_column("match_export_items", sa.Column("capture_snapshot_version", sa.Integer()))
    op.create_check_constraint(
        "ck_match_export_items_capture_snapshot_version",
        "match_export_items",
        "capture_snapshot_version IS NULL OR capture_snapshot_version >= 1",
    )
    op.create_table(
        "provider_call_captures",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("external_call_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        *_raw_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_call_id", name="uq_provider_call_captures_call"),
        sa.ForeignKeyConstraint(["external_call_id"], ["external_calls.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["request_blob_id"], ["call_content_blobs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["response_blob_id"], ["call_content_blobs.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_provider_call_captures_revision"),
        sa.CheckConstraint("schema_version >= 1", name="ck_provider_call_captures_schema_version"),
        sa.CheckConstraint(
            "request_capture_status IS NULL OR request_capture_status IN "
            "('COMPLETE', 'TRUNCATED', 'REJECTED_SENSITIVE', 'UNAVAILABLE')",
            name="ck_provider_call_captures_request_status",
        ),
        sa.CheckConstraint(
            "response_capture_status IS NULL OR response_capture_status IN "
            "('COMPLETE', 'TRUNCATED', 'REJECTED_SENSITIVE', 'UNAVAILABLE')",
            name="ck_provider_call_captures_response_status",
        ),
    )
    op.create_table(
        "export_provider_capture_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("export_item_id", sa.UUID(), nullable=False),
        # No call FK: a frozen input belongs to the export, not the current call.
        sa.Column("external_call_id", sa.UUID(), nullable=False),
        sa.Column("call_kind", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("operation", sa.String(128), nullable=False),
        sa.Column("model", sa.String(128)),
        sa.Column("voice", sa.String(128)),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("speech_id", sa.UUID()),
        sa.Column("generation_id", sa.UUID()),
        sa.Column("decision_round_id", sa.UUID()),
        sa.Column("context_version", sa.Integer()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_result_latency_ms", sa.Integer()),
        sa.Column("completed_latency_ms", sa.Integer()),
        sa.Column("error_code", sa.String(128)),
        sa.Column("source_kind", sa.String(32)),
        sa.Column("source_resource_id", sa.String(128)),
        sa.Column("logical_call_id", sa.UUID()),
        sa.Column("capture_revision", sa.Integer()),
        sa.Column("capture_version", sa.Integer()),
        sa.Column("captured_at", sa.DateTime(timezone=True)),
        *_raw_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "export_item_id", "external_call_id", name="uq_export_provider_capture_snapshots_call"
        ),
        sa.ForeignKeyConstraint(["export_item_id"], ["match_export_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["request_blob_id"], ["call_content_blobs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["response_blob_id"], ["call_content_blobs.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "capture_revision IS NULL OR capture_revision >= 1",
            name="ck_export_provider_capture_snapshots_revision",
        ),
    )
    op.create_index(
        "ix_export_provider_capture_snapshots_order",
        "export_provider_capture_snapshots",
        ["export_item_id", "started_at", "external_call_id"],
    )


def downgrade() -> None:
    # Operational rollback keeps the added schema/data. This explicit manual
    # downgrade exists for disposable migration tests, not production cleanup.
    op.drop_index(
        "ix_export_provider_capture_snapshots_order",
        table_name="export_provider_capture_snapshots",
    )
    op.drop_table("export_provider_capture_snapshots")
    op.drop_table("provider_call_captures")
    op.drop_constraint(
        "ck_match_export_items_capture_snapshot_version", "match_export_items", type_="check"
    )
    op.drop_column("match_export_items", "capture_snapshot_version")
    op.drop_index("ix_external_calls_capture_mode_started", table_name="external_calls")
    op.drop_constraint("ck_external_calls_capture_storage_mode", "external_calls", type_="check")
    op.drop_column("external_calls", "capture_storage_mode")

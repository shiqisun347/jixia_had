"""Add provider-native request log metadata to external calls."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_provider_api_request_logs"
down_revision: str | None = "0038_experiment_rule_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("external_calls", sa.Column("capture_version", sa.Integer()))
    op.add_column("external_calls", sa.Column("captured_at", sa.DateTime(timezone=True)))
    op.add_column("external_calls", sa.Column("source_kind", sa.String(length=32)))
    op.add_column("external_calls", sa.Column("source_resource_id", sa.String(length=128)))
    op.add_column("external_calls", sa.Column("logical_call_id", sa.UUID()))
    op.add_column("external_calls", sa.Column("provider_request_id", sa.String(length=256)))
    op.add_column("external_calls", sa.Column("request_capture_status", sa.String(length=32)))
    op.add_column("external_calls", sa.Column("response_capture_status", sa.String(length=32)))
    op.add_column("external_calls", sa.Column("request_original_bytes", sa.BigInteger()))
    op.add_column("external_calls", sa.Column("response_original_bytes", sa.BigInteger()))
    op.add_column("external_calls", sa.Column("request_stored_bytes", sa.BigInteger()))
    op.add_column("external_calls", sa.Column("response_stored_bytes", sa.BigInteger()))
    op.add_column("external_calls", sa.Column("request_sha256", sa.String(length=64)))
    op.add_column("external_calls", sa.Column("response_sha256", sa.String(length=64)))
    op.add_column("external_calls", sa.Column("capture_error_code", sa.String(length=128)))
    op.create_check_constraint(
        "ck_external_calls_source_kind",
        "external_calls",
        "source_kind IS NULL OR source_kind IN "
        "('MATCH', 'HOST_AUDIO', 'CONFIG_TEST', 'OPS_PROBE')",
    )
    op.create_check_constraint(
        "ck_external_calls_request_capture_status",
        "external_calls",
        "request_capture_status IS NULL OR request_capture_status IN "
        "('COMPLETE', 'TRUNCATED', 'REJECTED_SENSITIVE', 'UNAVAILABLE')",
    )
    op.create_check_constraint(
        "ck_external_calls_response_capture_status",
        "external_calls",
        "response_capture_status IS NULL OR response_capture_status IN "
        "('COMPLETE', 'TRUNCATED', 'REJECTED_SENSITIVE', 'UNAVAILABLE')",
    )
    op.create_index(
        "ix_external_calls_capture_started",
        "external_calls",
        ["capture_version", "started_at", "id"],
    )
    op.create_index(
        "ix_external_calls_source_started",
        "external_calls",
        ["source_kind", "started_at"],
    )
    op.create_index(
        "ix_external_calls_logical_attempt",
        "external_calls",
        ["logical_call_id", "attempt_no"],
    )


def downgrade() -> None:
    op.drop_index("ix_external_calls_logical_attempt", table_name="external_calls")
    op.drop_index("ix_external_calls_source_started", table_name="external_calls")
    op.drop_index("ix_external_calls_capture_started", table_name="external_calls")
    op.drop_constraint(
        "ck_external_calls_response_capture_status", "external_calls", type_="check"
    )
    op.drop_constraint(
        "ck_external_calls_request_capture_status", "external_calls", type_="check"
    )
    op.drop_constraint("ck_external_calls_source_kind", "external_calls", type_="check")
    for column in (
        "capture_error_code",
        "response_sha256",
        "request_sha256",
        "response_stored_bytes",
        "request_stored_bytes",
        "response_original_bytes",
        "request_original_bytes",
        "response_capture_status",
        "request_capture_status",
        "provider_request_id",
        "logical_call_id",
        "source_resource_id",
        "source_kind",
        "captured_at",
        "capture_version",
    ):
        op.drop_column("external_calls", column)

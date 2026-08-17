"""Add loss-aware administration data capture primitives."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_admin_data_capture"
down_revision: str | None = "0023_free_debate_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "call_content_blobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content_kind", sa.String(length=16), nullable=False),
        sa.Column("serialization_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("compression", sa.String(length=16), server_default="ZLIB", nullable=False),
        sa.Column("uncompressed_bytes", sa.BigInteger(), nullable=False),
        sa.Column("compressed_bytes", sa.BigInteger(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "content_kind IN ('REQUEST', 'RESPONSE')", name="ck_call_content_blobs_kind"
        ),
        sa.CheckConstraint("compression = 'ZLIB'", name="ck_call_content_blobs_compression"),
        sa.CheckConstraint(
            "uncompressed_bytes >= 0 AND compressed_bytes >= 0 AND chunk_count > 0",
            name="ck_call_content_blobs_sizes",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sha256",
            "serialization_version",
            "content_kind",
            name="uq_call_content_blobs_address",
        ),
    )
    op.create_table(
        "call_content_blob_chunks",
        sa.Column("blob_id", sa.UUID(), nullable=False),
        sa.Column("chunk_no", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.BYTEA(), nullable=False),
        sa.CheckConstraint("chunk_no >= 0", name="ck_call_content_blob_chunks_no"),
        sa.ForeignKeyConstraint(["blob_id"], ["call_content_blobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("blob_id", "chunk_no"),
    )
    op.create_table(
        "external_calls",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("call_kind", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128)),
        sa.Column("voice", sa.String(length=128)),
        sa.Column("attempt_no", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="STARTED", nullable=False),
        sa.Column("match_id", sa.UUID()),
        sa.Column("speech_id", sa.UUID()),
        sa.Column("agent_generation_id", sa.UUID()),
        sa.Column("agent_decision_id", sa.UUID()),
        sa.Column("asr_segment_id", sa.UUID()),
        sa.Column("judge_result_id", sa.UUID()),
        sa.Column("request_blob_id", sa.UUID()),
        sa.Column("response_blob_id", sa.UUID()),
        sa.Column("request_id", sa.String(length=128)),
        sa.Column("generation_id", sa.UUID()),
        sa.Column("decision_round_id", sa.UUID()),
        sa.Column("connection_epoch", sa.BigInteger()),
        sa.Column("context_version", sa.Integer()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_result_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("first_result_latency_ms", sa.Integer()),
        sa.Column("completed_latency_ms", sa.Integer()),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("audio_bytes", sa.BigInteger()),
        sa.Column("audio_duration_ms", sa.Integer()),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "call_kind IN ('LLM_SPEECH', 'LLM_DECISION', 'JUDGE', 'ASR', 'TTS')",
            name="ck_external_calls_kind",
        ),
        sa.CheckConstraint(
            "status IN ('STARTED', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name="ck_external_calls_status",
        ),
        sa.CheckConstraint("attempt_no > 0", name="ck_external_calls_attempt"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["speech_id"], ["speeches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["agent_generation_id"], ["agent_generations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["agent_decision_id"], ["agent_free_debate_decisions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["asr_segment_id"], ["asr_segments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["judge_result_id"], ["judge_results.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["request_blob_id"], ["call_content_blobs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["response_blob_id"], ["call_content_blobs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_external_calls_match_started", "external_calls", ["match_id", "started_at"])
    op.create_index("ix_external_calls_kind_status", "external_calls", ["call_kind", "status"])
    op.create_index(
        "ix_external_calls_speech_started", "external_calls", ["speech_id", "started_at"]
    )
    op.create_index(
        "ix_external_calls_decision_round",
        "external_calls",
        ["decision_round_id", "started_at"],
    )

    op.create_table(
        "system_log_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("service", sa.String(length=64), nullable=False),
        sa.Column("logger_name", sa.String(length=128), nullable=False),
        sa.Column("message", sa.String(length=1000), nullable=False),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column("request_id", sa.String(length=128)),
        sa.Column("match_id", sa.UUID()),
        sa.Column("speech_id", sa.UUID()),
        sa.Column("generation_id", sa.UUID()),
        sa.Column("decision_round_id", sa.UUID()),
        sa.Column("connection_epoch", sa.BigInteger()),
        sa.Column("incident_id", sa.UUID()),
        sa.Column(
            "details",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "level IN ('WARNING', 'ERROR', 'CRITICAL')", name="ck_system_log_events_level"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_system_log_events_happened", "system_log_events", ["happened_at"])
    op.create_index(
        "ix_system_log_events_service_level",
        "system_log_events",
        ["service", "level", "happened_at"],
    )
    op.create_index("ix_system_log_events_match", "system_log_events", ["match_id", "happened_at"])

    op.add_column(
        "agent_generations",
        sa.Column("call_type", sa.String(length=32), server_default="LLM_SPEECH", nullable=False),
    )
    op.add_column("agent_generations", sa.Column("provider", sa.String(length=64)))
    op.add_column("agent_generations", sa.Column("model_snapshot", sa.String(length=128)))
    op.add_column(
        "agent_generations",
        sa.Column(
            "generation_params_snapshot",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("agent_generations", sa.Column("request_blob_id", sa.UUID()))
    op.add_column("agent_generations", sa.Column("response_blob_id", sa.UUID()))
    op.add_column(
        "agent_generations",
        sa.Column("capture_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "agent_generations",
        sa.Column(
            "capture_completeness", sa.String(length=16), server_default="LIMITED", nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_agent_generations_capture_completeness",
        "agent_generations",
        "capture_completeness IN ('LIMITED', 'COMPLETE')",
    )
    op.create_foreign_key(
        "fk_agent_generations_request_blob",
        "agent_generations",
        "call_content_blobs",
        ["request_blob_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_agent_generations_response_blob",
        "agent_generations",
        "call_content_blobs",
        ["response_blob_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column("judge_results", sa.Column("request_blob_id", sa.UUID()))
    op.add_column("judge_results", sa.Column("response_blob_id", sa.UUID()))
    op.add_column(
        "judge_results",
        sa.Column("capture_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "judge_results",
        sa.Column(
            "capture_completeness", sa.String(length=16), server_default="LIMITED", nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_judge_results_capture_completeness",
        "judge_results",
        "capture_completeness IN ('LIMITED', 'COMPLETE')",
    )
    op.create_foreign_key(
        "fk_judge_results_request_blob",
        "judge_results",
        "call_content_blobs",
        ["request_blob_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_judge_results_response_blob",
        "judge_results",
        "call_content_blobs",
        ["response_blob_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_judge_results_response_blob", "judge_results", type_="foreignkey")
    op.drop_constraint("fk_judge_results_request_blob", "judge_results", type_="foreignkey")
    op.drop_constraint("ck_judge_results_capture_completeness", "judge_results", type_="check")
    for column in (
        "capture_completeness",
        "capture_version",
        "response_blob_id",
        "request_blob_id",
    ):
        op.drop_column("judge_results", column)

    op.drop_constraint(
        "fk_agent_generations_response_blob", "agent_generations", type_="foreignkey"
    )
    op.drop_constraint("fk_agent_generations_request_blob", "agent_generations", type_="foreignkey")
    op.drop_constraint(
        "ck_agent_generations_capture_completeness", "agent_generations", type_="check"
    )
    for column in (
        "capture_completeness",
        "capture_version",
        "response_blob_id",
        "request_blob_id",
        "generation_params_snapshot",
        "model_snapshot",
        "provider",
        "call_type",
    ):
        op.drop_column("agent_generations", column)

    op.drop_index("ix_system_log_events_match", table_name="system_log_events")
    op.drop_index("ix_system_log_events_service_level", table_name="system_log_events")
    op.drop_index("ix_system_log_events_happened", table_name="system_log_events")
    op.drop_table("system_log_events")
    op.drop_index("ix_external_calls_decision_round", table_name="external_calls")
    op.drop_index("ix_external_calls_speech_started", table_name="external_calls")
    op.drop_index("ix_external_calls_kind_status", table_name="external_calls")
    op.drop_index("ix_external_calls_match_started", table_name="external_calls")
    op.drop_table("external_calls")
    op.drop_table("call_content_blob_chunks")
    op.drop_table("call_content_blobs")

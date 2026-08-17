"""Add secure Agent LLM, TTS and playback persistence.

Revision ID: 0010_agent_voice
Revises: 0009_asr_transcript
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_agent_voice"
down_revision: str | None = "0009_asr_transcript"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("voice_profiles", sa.Column("chars_per_second", sa.Float(), nullable=True))

    op.add_column("model_profiles", sa.Column("base_url", sa.String(length=512), nullable=True))
    op.add_column("model_profiles", sa.Column("model_id", sa.String(length=256), nullable=True))
    op.add_column(
        "model_profiles",
        sa.Column("api_key_ciphertext", sa.LargeBinary(), nullable=True),
    )
    op.add_column("model_profiles", sa.Column("api_key_nonce", sa.LargeBinary(), nullable=True))
    op.add_column("model_profiles", sa.Column("api_key_last4", sa.String(length=4), nullable=True))
    op.add_column(
        "model_profiles",
        sa.Column("max_concurrency", sa.Integer(), server_default="50", nullable=False),
    )
    op.add_column(
        "model_profiles",
        sa.Column("token_per_char", sa.Float(), server_default="1.0", nullable=False),
    )
    op.add_column(
        "model_profiles",
        sa.Column(
            "generation_params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_model_profiles_max_concurrency",
        "model_profiles",
        "max_concurrency BETWEEN 1 AND 50",
    )
    op.create_check_constraint(
        "ck_model_profiles_token_per_char",
        "model_profiles",
        "token_per_char > 0 AND token_per_char <= 10",
    )

    op.add_column(
        "agent_profiles", sa.Column("system_prompt", sa.Text(), server_default="", nullable=False)
    )
    op.add_column(
        "agent_profiles", sa.Column("debater_prompt", sa.Text(), server_default="", nullable=False)
    )
    op.add_column(
        "agent_profiles",
        sa.Column(
            "generation_params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )

    op.alter_column("speeches", "user_id", existing_type=sa.UUID(), nullable=True)
    op.add_column(
        "speeches",
        sa.Column("speaker_kind", sa.String(length=16), server_default="HUMAN", nullable=False),
    )
    op.add_column("speeches", sa.Column("agent_profile_id", sa.UUID(), nullable=True))
    op.add_column("speeches", sa.Column("generation_id", sa.UUID(), nullable=True))
    op.add_column("speeches", sa.Column("llm_draft_text", sa.Text(), nullable=True))
    op.add_column(
        "speeches",
        sa.Column("audio_truncated", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("speeches", sa.Column("audio_storage_path", sa.String(length=512)))
    op.add_column("speeches", sa.Column("playback_started_at", sa.DateTime(timezone=True)))
    op.create_foreign_key(
        "fk_speeches_agent_profile_id",
        "speeches",
        "agent_profiles",
        ["agent_profile_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_speeches_speaker_kind",
        "speeches",
        "speaker_kind IN ('HUMAN', 'AGENT')",
    )
    op.create_check_constraint(
        "ck_speeches_speaker_reference",
        "speeches",
        "(speaker_kind = 'HUMAN' AND user_id IS NOT NULL AND agent_profile_id IS NULL) OR "
        "(speaker_kind = 'AGENT' AND user_id IS NULL AND agent_profile_id IS NOT NULL)",
    )

    op.create_table(
        "agent_generations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("match_id", sa.UUID(), nullable=False),
        sa.Column("action_key", sa.String(length=32), nullable=False),
        sa.Column("agent_profile_id", sa.UUID(), nullable=False),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="STARTED", nullable=False),
        sa.Column(
            "input_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("llm_draft_text", sa.Text(), server_default="", nullable=False),
        sa.Column("first_token_latency_ms", sa.Integer()),
        sa.Column("completed_latency_ms", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('STARTED', 'LLM_READY', 'PLAYING', 'FINALIZED', 'FAILED', 'CANCELLED')",
            name="ck_agent_generations_status",
        ),
        sa.ForeignKeyConstraint(["agent_profile_id"], ["agent_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_generations_match", "agent_generations", ["match_id", "created_at"]
    )

    op.create_table(
        "agent_audio_assets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("generation_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("voice", sa.String(length=128), nullable=False),
        sa.Column("rate", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="SPOOLING", nullable=False),
        sa.Column("spool_path", sa.String(length=512)),
        sa.Column("storage_path", sa.String(length=512)),
        sa.Column("byte_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("pcm_sample_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('SPOOLING', 'READY', 'PLAYING', 'FINALIZED', 'FAILED', 'DISCARDED')",
            name="ck_agent_audio_assets_status",
        ),
        sa.ForeignKeyConstraint(["generation_id"], ["agent_generations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("generation_id", name="uq_agent_audio_assets_generation"),
    )


def downgrade() -> None:
    op.drop_table("agent_audio_assets")
    op.drop_index("ix_agent_generations_match", table_name="agent_generations")
    op.drop_table("agent_generations")
    op.drop_constraint("ck_speeches_speaker_reference", "speeches", type_="check")
    op.drop_constraint("ck_speeches_speaker_kind", "speeches", type_="check")
    op.drop_constraint("fk_speeches_agent_profile_id", "speeches", type_="foreignkey")
    op.drop_column("speeches", "playback_started_at")
    op.drop_column("speeches", "audio_storage_path")
    op.drop_column("speeches", "audio_truncated")
    op.drop_column("speeches", "llm_draft_text")
    op.drop_column("speeches", "generation_id")
    op.drop_column("speeches", "agent_profile_id")
    op.drop_column("speeches", "speaker_kind")
    op.alter_column("speeches", "user_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("agent_profiles", "generation_params")
    op.drop_column("agent_profiles", "debater_prompt")
    op.drop_column("agent_profiles", "system_prompt")
    op.drop_constraint("ck_model_profiles_token_per_char", "model_profiles", type_="check")
    op.drop_constraint("ck_model_profiles_max_concurrency", "model_profiles", type_="check")
    op.drop_column("model_profiles", "generation_params")
    op.drop_column("model_profiles", "token_per_char")
    op.drop_column("model_profiles", "max_concurrency")
    op.drop_column("model_profiles", "api_key_last4")
    op.drop_column("model_profiles", "api_key_nonce")
    op.drop_column("model_profiles", "api_key_ciphertext")
    op.drop_column("model_profiles", "model_id")
    op.drop_column("model_profiles", "base_url")
    op.drop_column("voice_profiles", "chars_per_second")

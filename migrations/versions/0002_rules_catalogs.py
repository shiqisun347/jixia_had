"""Create rule, topic, voice, model, agent, and host-audio catalogs.

Revision ID: 0002_rules_catalogs
Revises: 0001_auth_and_users
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_rules_catalogs"
down_revision: str | None = "0001_auth_and_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

uuid_type = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "voice_profiles",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("provider_voice", sa.String(length=128), nullable=False),
        sa.Column("rate", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ENABLED", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('HOST', 'AGENT')", name="ck_voice_profiles_kind"),
        sa.CheckConstraint("status IN ('ENABLED', 'DISABLED')", name="ck_voice_profiles_status"),
        sa.CheckConstraint("rate BETWEEN 0.50 AND 2.00", name="ck_voice_profiles_rate"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_voice", name="uq_voice_profiles_provider_voice"),
    )
    op.create_table(
        "model_profiles",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("config_ref", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ENABLED", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("status IN ('ENABLED', 'DISABLED')", name="ck_model_profiles_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_model_profiles_name"),
    )
    op.create_table(
        "agent_profiles",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("model_profile_id", uuid_type, nullable=False),
        sa.Column("voice_profile_id", uuid_type, nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ENABLED", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("status IN ('ENABLED', 'DISABLED')", name="ck_agent_profiles_status"),
        sa.ForeignKeyConstraint(["model_profile_id"], ["model_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["voice_profile_id"], ["voice_profiles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_agent_profiles_name"),
    )
    op.create_table(
        "topics",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("topic_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("affirmative_text", sa.String(length=1000), nullable=False),
        sa.Column("negative_text", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ENABLED", nullable=False),
        sa.Column("created_by", uuid_type, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("status IN ('ENABLED', 'DISABLED')", name="ck_topics_status"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_key", "version", name="uq_topics_key_version"),
    )
    op.create_table(
        "rules",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("rule_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=1000), server_default="", nullable=False),
        sa.Column("side_size", sa.Integer(), nullable=False),
        sa.Column("estimated_seconds", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="DRAFT", nullable=False),
        sa.Column("created_by", uuid_type, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("side_size BETWEEN 1 AND 5", name="ck_rules_side_size"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'GENERATING_AUDIO', 'READY', 'ENABLED', 'DISABLED', "
            "'GENERATING_AUDIO_FAILED')",
            name="ck_rules_status",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_key", "version", name="uq_rules_key_version"),
    )
    op.create_table(
        "rule_stages",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("rule_id", uuid_type, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("stage_kind", sa.String(length=32), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column("start_host_text", sa.String(length=2000), server_default="", nullable=False),
        sa.Column("end_host_text", sa.String(length=2000), server_default="", nullable=False),
        sa.CheckConstraint(
            "stage_kind IN ('FIXED_SPEECH', 'FREE_DEBATE', 'PREPARATION', 'END')",
            name="ck_rule_stages_kind",
        ),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "position", name="uq_rule_stages_position"),
    )
    op.create_table(
        "stage_actions",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("stage_id", uuid_type, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("action_kind", sa.String(length=32), server_default="SPEECH", nullable=False),
        sa.Column("side", sa.String(length=16), nullable=True),
        sa.Column("seat_no", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "parameters", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.ForeignKeyConstraint(["stage_id"], ["rule_stages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stage_id", "position", name="uq_stage_actions_position"),
    )
    op.create_table(
        "host_audio_assets",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("rule_id", uuid_type, nullable=False),
        sa.Column("segment_key", sa.String(length=128), nullable=False),
        sa.Column("text", sa.String(length=2000), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("voice_profile_id", uuid_type, nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'READY', 'FAILED')", name="ck_host_audio_assets_status"
        ),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["voice_profile_id"], ["voice_profiles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "segment_key", name="uq_host_audio_assets_segment"),
    )


def downgrade() -> None:
    op.drop_table("host_audio_assets")
    op.drop_table("stage_actions")
    op.drop_table("rule_stages")
    op.drop_table("rules")
    op.drop_table("topics")
    op.drop_table("agent_profiles")
    op.drop_table("model_profiles")
    op.drop_table("voice_profiles")

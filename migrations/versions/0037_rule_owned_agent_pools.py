"""Add current rule configuration, rule-owned Agents, Prompts, and judge settings."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037_rule_owned_agent_pools"
down_revision: str | None = "0036_experiment_batch_format"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_rules_status", "rules", type_="check")
    op.create_check_constraint(
        "ck_rules_status",
        "rules",
        "status IN ('DRAFT', 'GENERATING_AUDIO', 'READY', 'ENABLED', 'DISABLED', "
        "'GENERATING_AUDIO_FAILED', 'ARCHIVED')",
    )
    op.add_column(
        "rules",
        sa.Column("host_voice_profile_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "rules",
        sa.Column("default_agent_model_profile_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "rules",
        sa.Column("topic_policy", sa.String(32), nullable=False, server_default="BOTH"),
    )
    op.add_column(
        "rules",
        sa.Column("config_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "rules",
        sa.Column("historical_read_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_check_constraint(
        "ck_rules_topic_policy",
        "rules",
        "topic_policy IN ('PRESET_ONLY', 'CUSTOM_ONLY', 'BOTH')",
    )
    op.create_check_constraint(
        "ck_rules_config_revision_positive", "rules", "config_revision >= 1"
    )
    op.create_foreign_key(
        "fk_rules_host_voice_profile_id",
        "rules",
        "voice_profiles",
        ["host_voice_profile_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_rules_default_agent_model_profile_id",
        "rules",
        "model_profiles",
        ["default_agent_model_profile_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column("agent_profiles", sa.Column("rule_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_agent_profiles_rule_id",
        "agent_profiles",
        "rules",
        ["rule_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_agent_profiles_rule_id", "agent_profiles", ["rule_id"])
    op.create_index(
        "ux_agent_profiles_rule_voice",
        "agent_profiles",
        ["rule_id", "voice_profile_id"],
        unique=True,
        postgresql_where=sa.text("rule_id IS NOT NULL"),
    )

    op.create_table(
        "stage_prompt_templates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("stage_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("template_text", sa.Text(), nullable=False),
        sa.Column(
            "variables",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("output_contract", sa.String(32), nullable=False, server_default="TEXT"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("purpose IN ('SPEECH', 'DECISION')", name="ck_stage_prompts_purpose"),
        sa.ForeignKeyConstraint(["stage_id"], ["rule_stages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stage_id", "purpose", name="uq_stage_prompts_slot"),
    )
    op.create_table(
        "agent_prompt_overrides",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("agent_profile_id", sa.UUID(), nullable=False),
        sa.Column("stage_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("template_text", sa.Text(), nullable=False),
        sa.Column(
            "variables",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("output_contract", sa.String(32), nullable=False, server_default="TEXT"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("purpose IN ('SPEECH', 'DECISION')", name="ck_agent_prompts_purpose"),
        sa.ForeignKeyConstraint(
            ["agent_profile_id"], ["agent_profiles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["stage_id"], ["rule_stages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_profile_id", "stage_id", "purpose", name="uq_agent_prompts_slot"
        ),
    )
    op.create_table(
        "rule_judge_configs",
        sa.Column("rule_id", sa.UUID(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_profile_id", sa.UUID(), nullable=True),
        sa.Column("judge_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "include_in_leaderboard",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "(enabled = false AND include_in_leaderboard = false) OR "
            "(enabled = true AND model_profile_id IS NOT NULL AND char_length(judge_prompt) > 0)",
            name="ck_rule_judge_enabled_config",
        ),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["model_profile_id"], ["model_profiles.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("rule_id"),
    )


def downgrade() -> None:
    op.drop_table("rule_judge_configs")
    op.drop_table("agent_prompt_overrides")
    op.drop_table("stage_prompt_templates")
    op.drop_index("ux_agent_profiles_rule_voice", table_name="agent_profiles")
    op.drop_index("ix_agent_profiles_rule_id", table_name="agent_profiles")
    op.drop_constraint("fk_agent_profiles_rule_id", "agent_profiles", type_="foreignkey")
    op.drop_column("agent_profiles", "rule_id")
    op.drop_constraint("fk_rules_default_agent_model_profile_id", "rules", type_="foreignkey")
    op.drop_constraint("fk_rules_host_voice_profile_id", "rules", type_="foreignkey")
    op.drop_constraint("ck_rules_config_revision_positive", "rules", type_="check")
    op.drop_constraint("ck_rules_topic_policy", "rules", type_="check")
    op.drop_column("rules", "historical_read_only")
    op.drop_column("rules", "config_revision")
    op.drop_column("rules", "topic_policy")
    op.drop_column("rules", "default_agent_model_profile_id")
    op.drop_column("rules", "host_voice_profile_id")
    op.drop_constraint("ck_rules_status", "rules", type_="check")
    op.create_check_constraint(
        "ck_rules_status",
        "rules",
        "status IN ('DRAFT', 'GENERATING_AUDIO', 'READY', 'ENABLED', 'DISABLED', "
        "'GENERATING_AUDIO_FAILED')",
    )

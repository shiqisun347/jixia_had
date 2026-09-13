"""Add format versions and version-owned Agent/Prompt/module records."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034_format_workspace"
down_revision: str | None = "0033_voice_calibration_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "format_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("format_key", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rule_id", sa.UUID(), nullable=False),
        sa.Column("source_version_id", sa.UUID(), nullable=True),
        sa.Column("change_note", sa.String(2000), nullable=False, server_default=""),
        sa.Column("published_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')", name="ck_format_versions_status"
        ),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_version_id"], ["format_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("format_key", "version", name="uq_format_versions_key_version"),
    )
    op.create_index(
        "ux_format_versions_one_draft",
        "format_versions",
        ["format_key"],
        unique=True,
        postgresql_where=sa.text("status = 'DRAFT'"),
    )
    op.create_index(
        "ux_format_versions_one_published",
        "format_versions",
        ["format_key"],
        unique=True,
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )
    op.create_table(
        "prompt_templates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("format_version_id", sa.UUID(), nullable=False),
        sa.Column("agent_profile_id", sa.UUID(), nullable=True),
        sa.Column("stage_key", sa.String(128), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("template_text", sa.Text(), nullable=False),
        sa.Column(
            "variables",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("output_contract", sa.String(64), nullable=False, server_default="TEXT"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["format_version_id"], ["format_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_profile_id"], ["agent_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_prompt_templates_default_slot",
        "prompt_templates",
        ["format_version_id", "stage_key", "purpose"],
        unique=True,
        postgresql_where=sa.text("agent_profile_id IS NULL"),
    )
    op.create_index(
        "ux_prompt_templates_agent_slot",
        "prompt_templates",
        ["format_version_id", "agent_profile_id", "stage_key", "purpose"],
        unique=True,
        postgresql_where=sa.text("agent_profile_id IS NOT NULL"),
    )
    op.create_table(
        "format_modules",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("format_version_id", sa.UUID(), nullable=False),
        sa.Column("module_key", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.ForeignKeyConstraint(["format_version_id"], ["format_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("format_version_id", "module_key", name="uq_format_modules_key"),
    )
    op.create_table(
        "format_judge_profiles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("format_version_id", sa.UUID(), nullable=False),
        sa.Column("model_profile_id", sa.UUID(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("judge_prompt", sa.Text(), nullable=False),
        sa.Column(
            "generation_params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "dimensions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "output_contract", sa.String(64), nullable=False, server_default="JUDGE_RESULT_V1"
        ),
        sa.ForeignKeyConstraint(["format_version_id"], ["format_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_profile_id"], ["model_profiles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("format_version_id", name="uq_format_judge_profiles_version"),
    )
    op.create_table(
        "format_topic_references",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("format_version_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=True),
        sa.Column("private_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "(topic_id IS NOT NULL AND private_snapshot IS NULL) OR "
            "(topic_id IS NULL AND private_snapshot IS NOT NULL)",
            name="ck_format_topic_references_source",
        ),
        sa.ForeignKeyConstraint(["format_version_id"], ["format_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("format_version_id", "position", name="uq_format_topic_refs_position"),
    )
    op.add_column("agent_profiles", sa.Column("format_version_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_agent_profiles_format_version_id",
        "agent_profiles",
        "format_versions",
        ["format_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("uq_agent_profiles_name", "agent_profiles", type_="unique")
    op.create_unique_constraint(
        "uq_agent_profiles_format_name", "agent_profiles", ["format_version_id", "name"]
    )
    op.add_column("rooms", sa.Column("format_version_id", sa.UUID(), nullable=True))
    op.add_column(
        "rooms",
        sa.Column(
            "format_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_rooms_format_version_id",
        "rooms",
        "format_versions",
        ["format_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_rooms_format_version_id", "rooms", type_="foreignkey")
    op.drop_column("rooms", "format_snapshot")
    op.drop_column("rooms", "format_version_id")
    op.drop_constraint("uq_agent_profiles_format_name", "agent_profiles", type_="unique")
    op.create_unique_constraint("uq_agent_profiles_name", "agent_profiles", ["name"])
    op.drop_constraint("fk_agent_profiles_format_version_id", "agent_profiles", type_="foreignkey")
    op.drop_column("agent_profiles", "format_version_id")
    op.drop_table("format_modules")
    op.drop_table("format_topic_references")
    op.drop_table("format_judge_profiles")
    op.drop_index("ux_prompt_templates_agent_slot", table_name="prompt_templates")
    op.drop_index("ux_prompt_templates_default_slot", table_name="prompt_templates")
    op.drop_table("prompt_templates")
    op.drop_index("ux_format_versions_one_published", table_name="format_versions")
    op.drop_index("ux_format_versions_one_draft", table_name="format_versions")
    op.drop_table("format_versions")

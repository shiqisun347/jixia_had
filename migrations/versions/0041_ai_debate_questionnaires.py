"""Add rule-controlled post-match and personal AI debate questionnaires."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041_ai_debate_questionnaires"
down_revision: str | None = "0040_formal_4v4_opportunities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rules",
        sa.Column(
            "postmatch_questionnaire_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "postmatch_survey_tasks",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "match_id", sa.UUID(), sa.ForeignKey("matches.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column(
            "questionnaire_version", sa.String(64), nullable=False, server_default="postmatch-v1"
        ),
        sa.Column(
            "answers", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'SUBMITTED')",
            name="ck_postmatch_survey_tasks_status",
        ),
        sa.UniqueConstraint("match_id", "user_id", name="uq_postmatch_survey_tasks_match_user"),
    )
    op.create_index(
        "ix_postmatch_survey_tasks_user_status", "postmatch_survey_tasks", ["user_id", "status"]
    )
    op.create_table(
        "personal_ai_survey_versions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "questions", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "created_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("version", name="uq_personal_ai_survey_versions_version"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')",
            name="ck_personal_ai_survey_versions_status",
        ),
    )
    op.create_table(
        "personal_ai_survey_responses",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "version_id",
            sa.UUID(),
            sa.ForeignKey("personal_ai_survey_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column(
            "answers", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED')", name="ck_personal_ai_survey_responses_status"
        ),
        sa.UniqueConstraint(
            "user_id", "version_id", name="uq_personal_ai_survey_responses_user_version"
        ),
    )
    op.create_index(
        "ix_personal_ai_survey_responses_user",
        "personal_ai_survey_responses",
        ["user_id", "updated_at"],
    )
    op.create_table(
        "personal_ai_survey_response_revisions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "response_id",
            sa.UUID(),
            sa.ForeignKey("personal_ai_survey_responses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "version_id",
            sa.UUID(),
            sa.ForeignKey("personal_ai_survey_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "answers", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_personal_ai_survey_response_revisions_response",
        "personal_ai_survey_response_revisions",
        ["response_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_personal_ai_survey_response_revisions_response",
        table_name="personal_ai_survey_response_revisions",
    )
    op.drop_table("personal_ai_survey_response_revisions")
    op.drop_index("ix_personal_ai_survey_responses_user", table_name="personal_ai_survey_responses")
    op.drop_table("personal_ai_survey_responses")
    op.drop_table("personal_ai_survey_versions")
    op.drop_index("ix_postmatch_survey_tasks_user_status", table_name="postmatch_survey_tasks")
    op.drop_table("postmatch_survey_tasks")
    op.drop_column("rules", "postmatch_questionnaire_enabled")

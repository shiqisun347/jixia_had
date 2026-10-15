"""Add conference postmatch presentation and audience voting."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045_postmatch_showcase"
down_revision: str | None = "0044_rule_audio_generations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "postmatch_showcases",
        sa.Column("match_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phase", sa.String(length=24), server_default="JUDGE_REVIEW", nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "display_payload",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
            "phase IN ('JUDGE_REVIEW', 'AUDIENCE_VOTE', 'AUDIENCE_RESULTS', 'AWARDS', 'CLOSED')",
            name="ck_postmatch_showcases_phase",
        ),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("match_id"),
    )
    op.create_index(
        "ix_postmatch_showcases_phase", "postmatch_showcases", ["phase", "is_published"]
    )

    op.create_table(
        "postmatch_judge_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("match_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("judge_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("comments", sa.Text(), server_default="", nullable=False),
        sa.Column("winner_side", sa.String(length=16), nullable=False),
        sa.Column("best_participant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
            "winner_side IN ('AFFIRMATIVE', 'NEGATIVE', 'DRAW')",
            name="ck_postmatch_judge_entries_winner",
        ),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["judge_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["best_participant_id"], ["match_participants.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", name="uq_postmatch_judge_entries_match"),
    )

    op.create_table(
        "audience_votes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("match_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("voter_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("winner_side", sa.String(length=16), nullable=False),
        sa.Column(
            "ranking", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "winner_side IN ('AFFIRMATIVE', 'NEGATIVE', 'DRAW')",
            name="ck_audience_votes_winner",
        ),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["voter_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "voter_user_id", name="uq_audience_votes_voter"),
    )
    op.create_index("ix_audience_votes_match", "audience_votes", ["match_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audience_votes_match", table_name="audience_votes")
    op.drop_table("audience_votes")
    op.drop_table("postmatch_judge_entries")
    op.drop_index("ix_postmatch_showcases_phase", table_name="postmatch_showcases")
    op.drop_table("postmatch_showcases")

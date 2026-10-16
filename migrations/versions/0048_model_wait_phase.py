"""Allow the bounded model decision wait without rewriting historical opportunities."""

from alembic import op

revision = "0048_model_wait_phase"
down_revision = "0047_speech_origin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_opportunities_selection_phase", "free_debate_opportunities", type_="check"
    )
    op.create_check_constraint(
        "ck_opportunities_selection_phase",
        "free_debate_opportunities",
        "selection_phase IN ('COMPETING', 'MODEL_WAIT', 'HUMAN_ONLY_WAIT', 'ALLOCATED')",
    )


def downgrade() -> None:
    # Never rewrite live or historical decisions merely to make a downgrade pass.
    rows = op.get_bind().exec_driver_sql(
        "SELECT 1 FROM free_debate_opportunities WHERE selection_phase='MODEL_WAIT' LIMIT 1"
    )
    if rows.first() is not None:
        raise RuntimeError("Cannot remove MODEL_WAIT while recorded opportunities use it")
    op.drop_constraint(
        "ck_opportunities_selection_phase", "free_debate_opportunities", type_="check"
    )
    op.create_check_constraint(
        "ck_opportunities_selection_phase",
        "free_debate_opportunities",
        "selection_phase IN ('COMPETING', 'HUMAN_ONLY_WAIT', 'ALLOCATED')",
    )

"""Allow existing human presets for Agent voices without changing stored choices."""

from alembic import op
from sqlalchemy import text

revision = "0049_agent_human_avatars"
down_revision = "0048_model_wait_phase"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_voice_profiles_avatar_key_by_kind"
_AGENT_KEYS = ",".join(f"'agent-{index:02d}'" for index in range(1, 13))
_HUMAN_KEYS = ",".join(f"'human-{index:02d}'" for index in range(1, 17))


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "voice_profiles", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "voice_profiles",
        f"(kind = 'AGENT' AND avatar_key IS NOT NULL AND avatar_key IN "
        f"({_AGENT_KEYS},{_HUMAN_KEYS})) OR (kind = 'HOST' AND avatar_key IS NULL)",
    )


def downgrade() -> None:
    rows = op.get_bind().execute(
        text(
            "SELECT 1 FROM voice_profiles WHERE kind='AGENT' AND avatar_key LIKE :pattern LIMIT 1"
        ),
        {"pattern": "human-%"},
    )
    if rows.first() is not None:
        raise RuntimeError("Cannot remove human avatar choices while Agent voices use them")
    op.drop_constraint(_CONSTRAINT, "voice_profiles", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "voice_profiles",
        f"(kind = 'AGENT' AND avatar_key IS NOT NULL AND avatar_key IN "
        f"({_AGENT_KEYS})) OR (kind = 'HOST' AND avatar_key IS NULL)",
    )

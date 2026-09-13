"""Store the code-declared capability schema for global model resources."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031_model_capability_schema"
down_revision: str | None = "0030_topic_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SCHEMA = (
    "jsonb_build_object("
    "'temperature', jsonb_build_object('minimum', 0.0, 'maximum', 2.0), "
    "'top_p', jsonb_build_object('minimum', 0.0, 'maximum', 1.0), "
    "'max_tokens', jsonb_build_object('minimum', 1, 'maximum', 32768)"
    ")"
)


def upgrade() -> None:
    op.add_column(
        "model_profiles",
        sa.Column(
            "capability_schema",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text(DEFAULT_SCHEMA),
        ),
    )


def downgrade() -> None:
    op.drop_column("model_profiles", "capability_schema")

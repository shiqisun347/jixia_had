"""Create the default single administrator.

Revision ID: 0016_default_single_admin
Revises: 0015_match_admin_note
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_default_single_admin"
down_revision: str | None = "0015_match_admin_note"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_ADMIN_ID = "00000000-0000-4000-8000-000000000001"
DEFAULT_ADMIN_HASH = (
    "$argon2id$v=19$m=19456,t=2,p=1$rZXW/U2lr0VWnPe75eObfA$"
    "B/eYLarb/vOBxfiRFT7ffu1c+SxOa44CQH0VvQP8Fxg"
)


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE users
            SET role = 'ADMIN',
                status = 'ACTIVE',
                password_hash = :password_hash,
                must_change_password = true,
                failed_login_count = 0,
                locked_until = NULL,
                password_changed_at = now(),
                updated_at = now()
            WHERE username_normalized = 'admin'
            """
        ),
        {"password_hash": DEFAULT_ADMIN_HASH},
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO users (
                id,
                username,
                username_normalized,
                real_name,
                password_hash,
                role,
                status,
                must_change_password,
                failed_login_count,
                password_changed_at
            )
            SELECT
                :admin_id,
                'admin',
                'admin',
                '系统管理员',
                :password_hash,
                'ADMIN',
                'ACTIVE',
                true,
                0,
                now()
            WHERE NOT EXISTS (
                SELECT 1 FROM users WHERE username_normalized = 'admin'
            )
            """
        ),
        {"admin_id": DEFAULT_ADMIN_ID, "password_hash": DEFAULT_ADMIN_HASH},
    )
    connection.execute(
        sa.text(
            """
            UPDATE sessions
            SET revoked_at = now()
            WHERE revoked_at IS NULL
              AND user_id IN (
                  SELECT id
                  FROM users
                  WHERE role = 'ADMIN' OR username_normalized = 'admin'
              )
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE users
            SET role = 'USER',
                updated_at = now()
            WHERE username_normalized <> 'admin'
              AND role = 'ADMIN'
            """
        )
    )
    op.create_index(
        "ux_users_single_admin",
        "users",
        ["role"],
        unique=True,
        postgresql_where=sa.text("role = 'ADMIN'"),
    )


def downgrade() -> None:
    op.drop_index("ux_users_single_admin", table_name="users")

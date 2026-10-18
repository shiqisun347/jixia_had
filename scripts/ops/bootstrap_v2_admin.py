"""Replace only the untouched migration ADMIN in an explicitly owned fresh local DB.

Default is dry-run. Passwords are collected by getpass, never arguments/environment.
This is not an administrator password reset or a production recovery tool.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ops.v2_resources import Refusal, database_guard, fresh_target, require

SEED_ID = "00000000-0000-4000-8000-000000000001"
SEED_HASH_SHA256 = "bc468d022e2f183158ac7bbaa23f1f9bace33aedde0f9fcfd31fe8e2cd9c90dd"
SEED_FIELDS = {
    "id": UUID(SEED_ID),
    "username": "admin",
    "username_normalized": "admin",
    "real_name": "系统管理员",
    "role": "ADMIN",
    "status": "ACTIVE",
    "must_change_password": True,
    "failed_login_count": 0,
    "locked_until": None,
    "avatar_path": None,
    "avatar_version": 0,
    # 0018 deterministically assigns this preset from md5(the fixed migration UUID).
    "default_avatar_key": "human-10",
    "last_login_at": None,
}


def verify_seed(row: dict[str, Any]) -> None:
    require(
        set(row)
        == set(SEED_FIELDS) | {"password_hash", "created_at", "updated_at", "password_changed_at"},
        "seed_columns_changed",
    )
    require(
        all(row[k] == v and type(row[k]) is type(v) for k, v in SEED_FIELDS.items()),
        "not_untouched_migration_admin",
    )
    require(
        hashlib.sha256(row["password_hash"].encode()).hexdigest() == SEED_HASH_SHA256,
        "seed_password_changed",
    )
    require(
        isinstance(row["created_at"], datetime)
        and row["created_at"].tzinfo is not None
        and row["created_at"] == row["updated_at"] == row["password_changed_at"],
        "seed_timestamps_changed",
    )


def collect_credentials(*, input_fn=None, password_fn=None) -> tuple[str, str, str]:
    input_fn = input if input_fn is None else input_fn
    password_fn = getpass.getpass if password_fn is None else password_fn
    username = input_fn("新管理员用户名：")
    real_name = input_fn("新管理员姓名：")
    password = password_fn("新管理员密码：")
    confirmation = password_fn("确认新密码：")
    require(password == confirmation, "password_confirmation_mismatch")
    require(bool(password), "password_required")
    return username, real_name, password


async def bootstrap(
    *,
    url: str,
    expected_db_name: str,
    apply: bool = False,
    credential_prompt=None,
    after_create=None,
) -> dict[str, Any]:
    """after_create is an integration-test seam; never accepted by the CLI."""
    from sqlalchemy import delete, func, select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from jx_core.auth.service import AuthService
    from jx_core.models import Base

    database_guard(url, expected_db_name)
    credentials = None
    if apply:
        credentials = (credential_prompt or collect_credentials)()
    engine = create_async_engine(url, echo=False)
    try:
        async with asyncio.timeout(60):
            async with engine.connect() as connection:
                async with connection.begin():
                    await connection.execute(text("SET LOCAL statement_timeout='5s'"))
                    await connection.execute(text("SET LOCAL lock_timeout='500ms'"))
                    require(
                        await connection.scalar(text("SELECT current_database()"))
                        == expected_db_name,
                        "connected_database_mismatch",
                    )
                    admin_id = await fresh_target(connection)
                    require(admin_id == SEED_ID, "migration_admin_required")
                    users = Base.metadata.tables["users"]
                    row = dict((await connection.execute(select(users))).mappings().one())
                    verify_seed(row)
                    audit = Base.metadata.tables["audit_logs"]
                    require(
                        await connection.scalar(select(func.count()).select_from(audit)) == 0,
                        "seed_has_audit_history",
                    )
                    if not apply:
                        return {
                            "status": "dry_run_untouched_migration_admin_verified",
                            "writes": 0,
                            "provider_calls": 0,
                        }
                    removed = await connection.execute(
                        delete(users).where(users.c.id == UUID(SEED_ID))
                    )
                    require(removed.rowcount == 1, "seed_delete_count")
                    factory = async_sessionmaker(
                        connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
                    )
                    async with factory() as session:
                        username, real_name, password = credentials
                        user = await AuthService().create_admin(
                            session, username=username, real_name=real_name, password=password
                        )
                        new_id = str(user.id)
                    # The native session committed only its savepoint, never this outer transaction.
                    if after_create is not None:
                        await after_create(connection)
                    actual = list((await connection.execute(select(users))).mappings())
                    records = list((await connection.execute(select(audit))).mappings())
                    require(
                        len(actual) == 1
                        and str(actual[0]["id"]) == new_id
                        and actual[0]["role"] == "ADMIN"
                        and actual[0]["status"] == "ACTIVE"
                        and actual[0]["must_change_password"] is False
                        and new_id != SEED_ID,
                        "created_admin_postcheck",
                    )
                    require(
                        len(records) == 1
                        and records[0]["action"] == "admin.created"
                        and records[0]["actor_user_id"] is None
                        and records[0]["target_id"] == new_id
                        and records[0]["result"] == "SUCCESS",
                        "created_admin_audit",
                    )
        return {
            "status": "fresh_admin_initialized",
            "admin_id": new_id,
            "normal_admin_audits": 1,
            "provider_calls": 0,
            "same_outer_transaction": True,
        }
    finally:
        async with asyncio.timeout(5):
            await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-db-name", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--database-env",
        choices=("V2_RESOURCE_DATABASE_URL", "TEST_DATABASE_URL"),
        default="V2_RESOURCE_DATABASE_URL",
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(
            bootstrap(
                url=os.environ.get(args.database_env, ""),
                expected_db_name=args.expected_db_name,
                apply=args.apply,
            )
        )
    except BaseException as error:
        result = {"status": "failed_safe_do_not_retry", "exception_class": type(error).__name__}
        if isinstance(error, Refusal):
            result["error_code"] = str(error)
        print(json.dumps(result))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

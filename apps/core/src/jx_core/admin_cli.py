"""Administrative recovery CLI that never accepts passwords as arguments."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence

from sqlalchemy import text

from .auth.errors import AuthError
from .auth.service import AuthService
from .config import Settings, load_settings
from .database import Database

REQUIRED_MIGRATION_REVISION = "0024_admin_data_capture"


class AdminCLIError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jx-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create-admin", help="create the first or another administrator")
    return parser


async def create_admin(
    settings: Settings,
    *,
    username: str,
    real_name: str,
    password: str,
) -> tuple[str, str]:
    database = Database(settings.database_url_value)
    try:
        async with database.engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != REQUIRED_MIGRATION_REVISION:
            raise AdminCLIError("database migration is not current")
        async with database.session_factory() as database_session:
            try:
                user = await AuthService().create_admin(
                    database_session,
                    username=username,
                    real_name=real_name,
                    password=password,
                )
            except AuthError as error:
                raise AdminCLIError(error.code) from None
        return str(user.id), user.username
    finally:
        await database.dispose()


def main(argv: Sequence[str] | None = None) -> None:
    arguments = build_parser().parse_args(argv)
    if arguments.command != "create-admin":
        raise SystemExit(2)
    try:
        settings = load_settings()
    except Exception:
        print("管理员工具配置无效。", file=sys.stderr)
        raise SystemExit(2) from None

    username = input("用户名：")
    real_name = input("真实姓名：")
    password = getpass.getpass("密码：")
    confirmation = getpass.getpass("确认密码：")
    if password != confirmation:
        print("两次输入的密码不一致。", file=sys.stderr)
        raise SystemExit(2)
    try:
        user_id, stored_username = asyncio.run(
            create_admin(
                settings,
                username=username,
                real_name=real_name,
                password=password,
            )
        )
    except AdminCLIError as error:
        print(f"创建管理员失败：{error}", file=sys.stderr)
        raise SystemExit(1) from None
    print(f"管理员已创建：id={user_id} username={stored_username}")


if __name__ == "__main__":
    main()

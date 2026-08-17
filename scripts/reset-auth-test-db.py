"""Reset only the independent database supplied for the 003 browser test."""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        raise SystemExit("TEST_DATABASE_URL is required")
    if database_url == os.environ.get("DATABASE_URL"):
        raise SystemExit("TEST_DATABASE_URL must not equal DATABASE_URL")
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE audit_logs, room_connections, user_consents, sessions, users "
                    "CASCADE"
                )
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

"""Print a credential-free database summary for release audits."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import text

from jx_core.config import load_settings
from jx_core.database import Database

ACTIVE_MATCH_STATUSES = (
    "START_PENDING_RUNTIME",
    "START_COUNTDOWN",
    "RUNNING",
    "PAUSED",
    "SYSTEM_RECOVERY",
    "ERROR",
)


async def collect() -> dict[str, Any]:
    settings = load_settings()
    database = Database(settings.database_url_value)
    try:
        async with database.session_factory() as session:
            match_rows = (
                await session.execute(
                    text("SELECT status, count(*) FROM matches GROUP BY status ORDER BY status")
                )
            ).all()
            room_rows = (
                await session.execute(
                    text("SELECT status, count(*) FROM rooms GROUP BY status ORDER BY status")
                )
            ).all()
            task_rows = (
                await session.execute(
                    text(
                        "SELECT status, count(*) FROM background_tasks "
                        "GROUP BY status ORDER BY status"
                    )
                )
            ).all()
            active_matches = await session.scalar(
                text("SELECT count(*) FROM matches WHERE status = ANY(:statuses)"),
                {"statuses": list(ACTIVE_MATCH_STATUSES)},
            )
            user_count = await session.scalar(text("SELECT count(*) FROM users"))
    finally:
        await database.dispose()
    return {
        "match_status_counts": {str(status): int(count) for status, count in match_rows},
        "room_status_counts": {str(status): int(count) for status, count in room_rows},
        "task_status_counts": {str(status): int(count) for status, count in task_rows},
        "active_matches": int(active_matches or 0),
        "user_count": int(user_count or 0),
    }


def main() -> None:
    print(json.dumps(asyncio.run(collect()), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

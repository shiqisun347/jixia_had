"""Safely inspect or repair terminal room timestamps and connection leases.

The default mode is read-only.  ``--apply`` refuses to mutate anything while
any non-terminal room or match exists, then performs the same bounded cleanup
that Core runs during startup.  Credentials are intentionally never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from sqlalchemy import func, select, update

from jx_core.config import load_settings
from jx_core.database import Database
from jx_core.models import Match, Room, RoomConnectionLease, RoomMember
from jx_core.room_connections import (
    ACTIVE_LEASE_CLEAR_BATCH_SIZE,
    RoomConnectionService,
    reconcile_terminal_room_end_times,
)


class RepairBlocked(RuntimeError):
    """The safety gate found live room or match state."""


def can_apply(report: dict[str, Any]) -> bool:
    """Return whether the destructive lease cleanup gate is closed safely."""

    if "nonterminal_rooms" not in report or "nonterminal_matches" not in report:
        return False
    return int(report["nonterminal_rooms"]) == 0 and int(report["nonterminal_matches"]) == 0


async def inspect(database: Database) -> dict[str, Any]:
    async with database.engine.connect() as connection:
        nonterminal_rooms = int(
            await connection.scalar(
                select(func.count())
                .select_from(Room)
                .where(Room.status.not_in(("FINISHED", "TERMINATED")))
            )
            or 0
        )
        nonterminal_matches = int(
            await connection.scalar(
                select(func.count())
                .select_from(Match)
                .where(Match.status.not_in(("FINISHED", "TERMINATED")))
            )
            or 0
        )
        terminal_rooms_missing_end_at = int(
            await connection.scalar(
                select(func.count())
                .select_from(Room)
                .where(
                    Room.status.in_(("FINISHED", "TERMINATED")),
                    Room.ended_at.is_(None),
                )
            )
            or 0
        )
        active_leases = int(
            await connection.scalar(select(func.count()).select_from(RoomConnectionLease)) or 0
        )
        terminal_room_leases = int(
            await connection.scalar(
                select(func.count())
                .select_from(RoomConnectionLease)
                .join(Room, Room.id == RoomConnectionLease.room_id)
                .where(Room.status.in_(("FINISHED", "TERMINATED")))
            )
            or 0
        )
        orphan_leases = int(
            await connection.scalar(
                select(func.count())
                .select_from(RoomConnectionLease)
                .outerjoin(Room, Room.id == RoomConnectionLease.room_id)
                .where(Room.id.is_(None))
            )
            or 0
        )
    report = {
        "nonterminal_rooms": nonterminal_rooms,
        "nonterminal_matches": nonterminal_matches,
        "terminal_rooms_missing_end_at": terminal_rooms_missing_end_at,
        "active_leases": active_leases,
        "terminal_room_leases": terminal_room_leases,
        "orphan_leases": orphan_leases,
    }
    report["ready_to_apply"] = can_apply(report)
    return report


async def apply_repair(database: Database, before: dict[str, Any]) -> dict[str, Any]:
    if not can_apply(before):
        raise RepairBlocked("nonterminal_room_or_match_present")

    repaired_rooms = 0
    cleared_leases = 0
    service = RoomConnectionService()
    async with database.session_factory() as session:
        repaired_rooms = await reconcile_terminal_room_end_times(session)
        while True:
            stale_leases = await service.clear_active_leases(
                session, batch_size=ACTIVE_LEASE_CLEAR_BATCH_SIZE
            )
            if not stale_leases:
                break
            cleared_leases += len(stale_leases)
            async with session.begin():
                for user_id, room_id in set(stale_leases):
                    await session.execute(
                        update(RoomMember)
                        .where(
                            RoomMember.user_id == user_id,
                            RoomMember.room_id == room_id,
                            RoomMember.left_at.is_(None),
                        )
                        .values(online=False)
                    )
    after = await inspect(database)
    return {
        "repaired_rooms": repaired_rooms,
        "cleared_leases": cleared_leases,
        "before": before,
        "after": after,
    }


async def run(*, apply: bool) -> dict[str, Any]:
    settings = load_settings()
    database = Database(settings.database_url_value)
    try:
        before = await inspect(database)
        if not apply:
            return {"apply": False, "report": before}
        result = await apply_repair(database, before)
        return {"apply": True, "report": result}
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit the guarded repair")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(apply=args.apply)), ensure_ascii=False))
    except RepairBlocked as error:
        print(json.dumps({"apply": True, "blocked": str(error)}, ensure_ascii=False))
        raise SystemExit(2) from None
    except Exception as error:
        print(json.dumps({"failed": type(error).__name__}, ensure_ascii=False))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

"""Spec307: real runtime-start/presence SQL lock ordering and API guards."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jx_core.auth.errors import AuthError
from jx_core.matches.domain import MatchCommand
from jx_core.matches.service import state_snapshot
from jx_core.models import Match, Room
from test_agent_runtime_fault_integration import AgentFaultProbe
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import (
    agent_fault_probe as agent_fault_probe,
)

pytestmark = pytest.mark.integration


async def prepared(probe: AgentFaultProbe) -> tuple[UUID, UUID]:
    """Initialize the existing synthetic fixture at its normal prepared gate."""
    async with probe.factory() as session:
        match = await session.get(Match, probe.actor.state.match_id)
        assert match is not None
        room = await session.get(Room, match.room_id)
        assert room is not None
        room_id, organizer = room.id, room.organizer_user_id
    pending = replace(
        probe.actor.state,
        status="START_PENDING_RUNTIME",
        action_state="NOT_STARTED",
        current_agent_profile_id=None,
        current_speaker_user_id=None,
        current_speaker_side=None,
        current_speaker_seat_no=None,
    )
    async with probe.factory() as session:
        async with session.begin():
            match = await session.get(Match, pending.match_id)
            room = await session.get(Room, room_id)
            assert match is not None and room is not None
            match.status = room.status = "START_PENDING_RUNTIME"
            match.runtime_snapshot = state_snapshot(pending)
    probe.actor.state = pending

    async def held_timer(_seconds: float) -> None:
        await asyncio.sleep(60)

    probe.actor._sleep = held_timer
    await probe.manager.submit(
        pending.match_id,
        MatchCommand(type="member.offline", message_id=str(uuid4()), actor_user_id=organizer),
    )
    return room_id, organizer


def safe_error(error: BaseException) -> dict[str, Any]:
    states: list[str] = []
    classes: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(classes) < 8:
        seen.add(id(current))
        classes.append(type(current).__name__)
        value = getattr(current, "sqlstate", None)
        if isinstance(value, str) and len(value) == 5 and value.isalnum():
            states.append(value)
        orig = getattr(current, "orig", None)
        current = (
            orig if isinstance(orig, BaseException) else current.__cause__ or current.__context__
        )
    return {"classes": classes, "sqlstates": sorted(set(states))}


@pytest.mark.parametrize("interleave", [False, True], ids=["serialized", "actual_sql_barriers"])
async def test_runtime_start_and_online_share_lock_order(
    agent_fault_probe: AgentFaultProbe, monkeypatch: pytest.MonkeyPatch, interleave: bool
) -> None:
    p = agent_fault_probe
    match_id = p.actor.state.match_id
    room_id, organizer = await prepared(p)
    first_locked, actor_attempted, actor_locked = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    release_start, release_actor = asyncio.Event(), asyncio.Event()
    observations: list[dict[str, Any]] = []
    pids: dict[str, int] = {}
    first_entity: list[str] = []
    start_task: asyncio.Task[Any] | None = None
    online_task: asyncio.Task[Any] | None = None
    scalar_original = AsyncSession.scalar

    async def scalar(self: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        locking = interleave and getattr(statement, "_for_update_arg", None) is not None
        descriptions = getattr(statement, "column_descriptions", []) if locking else []
        entity = descriptions[0].get("entity") if descriptions else None
        is_actor = entity is Match and asyncio.current_task() is p.actor._runner
        if is_actor and not actor_attempted.is_set():
            pids["actor"] = int(await scalar_original(self, text("SELECT pg_backend_pid()")))
            actor_attempted.set()
        result = await scalar_original(self, statement, *args, **kwargs)
        if (
            entity in (Match, Room)
            and asyncio.current_task() is start_task
            and not first_locked.is_set()
        ):
            assert result is not None
            first_entity.append("Match" if entity is Match else "Room")
            pids["start"] = int(await scalar_original(self, text("SELECT pg_backend_pid()")))
            observations.append(
                {"step": "start_first_row_lock_returned", "entity": first_entity[0]}
            )
            first_locked.set()
            await asyncio.wait_for(release_start.wait(), 5)
        elif is_actor and not actor_locked.is_set():
            assert result is not None and result.id == match_id
            observations.append({"step": "actor_Match_row_lock_returned"})
            actor_locked.set()
            await asyncio.wait_for(release_actor.wait(), 5)
        return result

    monkeypatch.setattr(AsyncSession, "scalar", scalar)

    async def start() -> Any:
        async with p.factory() as session:
            return await p.manager.start_existing_match_runtime(
                session, room_id=room_id, actor_user_id=organizer, actor_role="USER"
            )

    async def online() -> Any:
        return await p.manager.submit(
            match_id,
            MatchCommand(
                type="member.online",
                message_id=str(uuid4()),
                actor_user_id=organizer,
                payload={"connection_epoch": 2},
            ),
        )

    results: list[Any] = []
    same_match_blocked = False
    try:
        async with asyncio.timeout(15):
            if interleave:
                start_task = asyncio.create_task(start(), name="owned-runtime-start-lock-order")
                await asyncio.wait_for(first_locked.wait(), 5)
                online_task = asyncio.create_task(online(), name="owned-member-online-lock-order")
                await asyncio.wait_for(actor_attempted.wait(), 5)
                async with asyncio.timeout(3):
                    while not actor_locked.is_set():
                        async with p.factory() as session:
                            blockers = await session.scalar(
                                text("SELECT pg_blocking_pids(:pid)"), {"pid": pids["actor"]}
                            )
                        if blockers is not None and pids["start"] in blockers:
                            same_match_blocked = True
                            observations.append({"step": "actual_pg_actor_blocked_by_start"})
                            break
                        await asyncio.sleep(0.005)
                # Old Room-first code now holds the two opposite rows; corrected
                # Match-first code has an actual PG waiter on the same Match row.
                assert actor_locked.is_set() or same_match_blocked
                release_start.set()
                release_actor.set()
                results = list(
                    await asyncio.gather(start_task, online_task, return_exceptions=True)
                )
            else:
                results = [await online(), await start()]
    finally:
        release_start.set()
        release_actor.set()
        for task in (start_task, online_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(t for t in (start_task, online_task) if t is not None), return_exceptions=True
        )
    errors = [
        {"entrypoint": "runtime_start" if i == 0 else "member_online", **safe_error(value)}
        for i, value in enumerate(results)
        if isinstance(value, BaseException)
    ]
    async with p.factory() as session:
        match, room = await session.get(Match, match_id), await session.get(Room, room_id)
        assert match is not None and room is not None
        durable = {
            "match_status": match.status,
            "room_status": room.status,
            "sequence": match.sequence,
        }
    if evidence := os.environ.get("SPEC307_LOCK_FIX_OUT"):
        Path(evidence, "interleaved.json" if interleave else "serialized.json").write_text(
            json.dumps(
                {
                    "errors": errors,
                    "first_entity": first_entity,
                    "same_match_blocked": same_match_blocked,
                    "observations": observations,
                    "durable": durable,
                    "provider_calls": p.provider_calls,
                },
                indent=2,
            )
            + "\n"
        )
    assert not errors, {"safe_exceptions": errors, "durable": durable}
    assert durable == {"match_status": "START_COUNTDOWN", "room_status": "RUNNING", "sequence": 3}
    assert p.actor.state.sequence == 3 and p.provider_calls == 0
    if interleave:
        assert first_entity == ["Match"] and same_match_blocked


async def test_runtime_start_preserves_organizer_authority_and_duplicate_result(
    agent_fault_probe: AgentFaultProbe,
) -> None:
    p = agent_fault_probe
    room_id, organizer = await prepared(p)
    before = p.actor.state
    for actor_id, allowed in (
        (uuid4(), False),
        (organizer, True),
        (organizer, True),
        (uuid4(), False),
    ):
        async with p.factory() as session:
            if not allowed:
                with pytest.raises(AuthError) as caught:
                    await p.manager.start_existing_match_runtime(
                        session, room_id=room_id, actor_user_id=actor_id, actor_role="USER"
                    )
                assert caught.value.code == "forbidden"
            else:
                await p.manager.start_existing_match_runtime(
                    session, room_id=room_id, actor_user_id=actor_id, actor_role="USER"
                )
    assert p.actor.state.status == "START_COUNTDOWN"
    assert p.actor.state.sequence == before.sequence + 1
    assert p.provider_calls == 0

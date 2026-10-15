from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from jx_core.admin_routes import MatchControlRequest
from jx_core.agent.llm import LlmCapacityLimiter, LlmProviderError
from jx_core.matches.domain import (
    MatchAction,
    MatchActor,
    MatchCommand,
    MatchDomainError,
    MatchRuntimeState,
)
from jx_core.matches.emergency import MatchEmergencyCoordinator


class Manager:
    def __init__(self, actor: MatchActor) -> None:
        self.actor = actor
        self.events: list[str] = []
        self.begin_gate: asyncio.Event | None = None
        self.begin_entered = asyncio.Event()

    async def submit(self, match_id: UUID, command: MatchCommand) -> Any:
        if command.type == "speech.skip.begin" and self.begin_gate is not None:
            self.begin_entered.set()
            await self.begin_gate.wait()
        result = await self.actor.submit(command)
        self.events.extend(item.type for item in result.events)
        return result

    async def get_actor(self, match_id: UUID) -> MatchActor:
        return self.actor


def fixture() -> tuple[MatchActor, Manager, MatchEmergencyCoordinator, dict[str, Any]]:
    user = uuid4()
    actions = tuple(
        MatchAction(
            stage_position=index,
            action_position=1,
            action_kind="HUMAN_SPEECH",
            duration_seconds=30,
            side="AFFIRMATIVE",
            seat_no=1,
            speaker_user_id=user,
        )
        for index in (1, 2)
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HUMAN_SPEAKING",
            actions=actions,
            current_speech_id=uuid4(),
            current_speaker_user_id=user,
            current_speaker_side="AFFIRMATIVE",
            current_speaker_seat_no=1,
            speech_remaining_ms=30_000,
        )
    )
    manager = Manager(actor)
    coordinator = MatchEmergencyCoordinator(
        settings=cast(Any, SimpleNamespace()),
        session_factory=cast(Any, None),
        manager=cast(Any, manager),
        limiter=LlmCapacityLimiter(),
    )
    payload = {
        "operation_id": str(uuid4()),
        "expected_sequence": 0,
        "expected_action_key": "1:1",
        "expected_stage_generation": 0,
        "expected_speech_id": str(actor.state.current_speech_id),
        "expected_context_version": 0,
    }
    return actor, manager, coordinator, payload


async def settled(coordinator: MatchEmergencyCoordinator) -> None:
    async with asyncio.timeout(2):
        while coordinator._tasks:
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_skip_is_owned_after_http_ack_and_replays_without_regeneration() -> None:
    actor, manager, coordinator, payload = fixture()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def generate(*_: Any) -> str:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return "这是按本方争点生成的管理员辅助占位。"

    coordinator._generate = generate
    await actor.start()
    try:
        first = await coordinator.start_skip(
            actor.state.match_id, actor_user_id=uuid4(), payload=payload
        )
        assert first.state.status == "PAUSED"
        await entered.wait()
        duplicate = await coordinator.start_skip(
            actor.state.match_id, actor_user_id=uuid4(), payload=payload
        )
        assert duplicate.state.emergency_operation == first.state.emergency_operation
        with pytest.raises(MatchDomainError, match="emergency_operation_pending"):
            await coordinator.start_skip(
                actor.state.match_id,
                actor_user_id=uuid4(),
                payload={**payload, "operation_id": str(uuid4())},
            )
        release.set()
        await settled(coordinator)
        assert actor.state.status == "PAUSED"
        assert actor.state.current_action_index == 1
        assert (actor.state.emergency_operation or {})["status"] == "SUCCEEDED"
        await coordinator.start_skip(actor.state.match_id, actor_user_id=uuid4(), payload=payload)
        await settled(coordinator)
        assert calls == 1
        assert manager.events.count("speech.skipped") == 1
        assert "speech.started" not in manager.events
    finally:
        await coordinator.close()
        await actor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_or_cancelled_generation_keeps_recoverable_pause(cancel: bool) -> None:
    actor, manager, coordinator, payload = fixture()
    entered = asyncio.Event()

    async def generate(*_: Any) -> str:
        entered.set()
        if cancel:
            await asyncio.Event().wait()
        raise LlmProviderError("llm_provider_failed")

    coordinator._generate = generate
    await actor.start()
    try:
        await coordinator.start_skip(actor.state.match_id, actor_user_id=uuid4(), payload=payload)
        await entered.wait()
        if cancel:
            await coordinator.cancel(actor.state.match_id)
        await settled(coordinator)
        assert actor.state.status == "PAUSED"
        assert actor.state.current_action_index == 0
        assert (actor.state.emergency_operation or {})["status"] == (
            "CANCELLED" if cancel else "FAILED"
        )
        assert "speech.skipped" not in manager.events
    finally:
        await coordinator.close()
        await actor.close()


@pytest.mark.asyncio
async def test_http_disconnect_before_begin_does_not_orphan_owned_operation() -> None:
    actor, manager, coordinator, payload = fixture()
    manager.begin_gate = asyncio.Event()

    async def generate(*_: Any) -> str:
        return "管理员辅助占位。"

    coordinator._generate = generate
    await actor.start()
    caller = asyncio.create_task(
        coordinator.start_skip(actor.state.match_id, actor_user_id=uuid4(), payload=payload)
    )
    try:
        await manager.begin_entered.wait()
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        manager.begin_gate.set()
        await settled(coordinator)
        assert (actor.state.emergency_operation or {})["status"] == "SUCCEEDED"
        assert manager.events.count("speech.skipped") == 1
    finally:
        await coordinator.close()
        await actor.close()


def test_emergency_payload_rejects_missing_target_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        MatchControlRequest(action="skip_speech")
    with pytest.raises(ValidationError):
        MatchControlRequest(action="reset_stage", operation_id=uuid4())
    with pytest.raises(ValidationError):
        MatchControlRequest.model_validate({"action": "pause", "privileged": True})
    assert MatchControlRequest(action="reset_speech").operation_id is None

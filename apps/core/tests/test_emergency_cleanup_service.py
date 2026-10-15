from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import jx_core.matches.service as service_module
from jx_core.matches.domain import MatchDomainError, MatchEvent, MatchRuntimeState
from jx_core.matches.service import MatchRuntimeManager


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", ["paused", "stage", "context", "during_context"])
@pytest.mark.parametrize("action_kind", ["HOST_AUDIO", "HUMAN_SPEECH"])
async def test_queued_old_host_start_is_rejected_but_current_resumed_host_can_start(
    stale, action_kind
):
    from dataclasses import replace
    from types import SimpleNamespace

    from jx_core.matches.domain import MatchAction

    manager = MatchRuntimeManager(cast(Any, None))
    match_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=0,
        action_kind=action_kind,
        duration_seconds=4,
        host_audio_path="owned-host.ogg",
        host_audio_duration_ms=4000,
    )
    state = MatchRuntimeState(
        match_id=match_id,
        status="RUNNING",
        action_state="HOST_ANNOUNCING",
        actions=(action,),
        stage_generation=3,
    )
    actor = SimpleNamespace(state=state)
    manager._actors[match_id] = actor
    runtime = AsyncMock()
    manager.set_agent_runtime(runtime)

    async def context(_):
        if stale == "during_context":
            actor.state = replace(actor.state, status="PAUSED")
        return 8 if stale == "context" else 7

    manager._context_version = context
    queued = manager._start_host_for_current_action(
        match_id=match_id,
        action_key=action.action_key,
        storage_path=action.host_audio_path,
        duration_ms=4000,
        context_version=7,
        stage_generation=3,
    )
    if stale == "paused":
        actor.state = replace(state, status="PAUSED")
    elif stale == "stage":
        actor.state = replace(state, stage_generation=4)
    await queued
    runtime.start_host_playback.assert_not_awaited()
    actor.state = replace(state, stage_generation=4)
    manager._context_version = AsyncMock(return_value=9)
    await manager._start_host_for_current_action(
        match_id=match_id,
        action_key=action.action_key,
        storage_path=action.host_audio_path,
        duration_ms=4000,
        context_version=9,
        stage_generation=4,
    )
    runtime.start_host_playback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["stage.reset", "speech.reset", "speech.skip.started"])
async def test_recovery_retires_pending_start_before_waiting_for_asr(kind):
    from types import SimpleNamespace

    manager = MatchRuntimeManager(cast(Any, None))
    match_id = uuid4()
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()

    class Agent:
        def retire_pending_media_start(self, target):
            assert target == match_id
            calls.append("retire")

        async def fence_agent(self, _):
            calls.append("fence-agent")

        async def fence_host(self, _):
            calls.append("fence-host")

        async def cancel_free_decision(self, _):
            pass

        async def reset_agent(self, _):
            pass

        async def reset_host(self, _):
            pass

    async def reset_speech(_):
        calls.append("asr")
        entered.set()
        await release.wait()

    manager.set_speech_runtime(cast(Any, SimpleNamespace(reset_speech=reset_speech)))
    manager.set_agent_runtime(cast(Any, Agent()))
    state = MatchRuntimeState(
        match_id=match_id, status="PAUSED", action_state="RECOVERY_REQUIRED", actions=()
    )
    event = MatchEvent(
        type=kind,
        match_id=match_id,
        sequence=1,
        server_time_ms=0,
        payload={"operation_id": str(uuid4())},
    )
    pending = asyncio.create_task(manager._publish(state, (event,)))
    try:
        await entered.wait()
        assert calls == ["retire", "fence-agent", "fence-host", "asr"]
    finally:
        release.set()
        await pending


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [False, True])
async def test_committed_emergency_cleanup_rejects_failure_and_retires_other_owners(
    monkeypatch: pytest.MonkeyPatch, timeout: bool
) -> None:
    manager = MatchRuntimeManager(cast(Any, None))
    match_id = uuid4()
    calls: list[str] = []

    async def fail(_: Any) -> None:
        calls.append("asr")
        if timeout:
            await asyncio.Event().wait()
        raise RuntimeError("synthetic cleanup failure")

    async def cancelled(_: Any) -> None:
        calls.append("decision")

    async def agent(_: Any) -> None:
        calls.append("agent")

    async def host(_: Any) -> None:
        calls.append("host")

    manager.set_speech_runtime(cast(Any, type("ASR", (), {"reset_speech": staticmethod(fail)})()))
    manager.set_agent_runtime(
        cast(
            Any,
            type(
                "Agent",
                (),
                {
                    "cancel_free_decision": staticmethod(cancelled),
                    "reset_agent": staticmethod(agent),
                    "reset_host": staticmethod(host),
                },
            )(),
        )
    )
    monkeypatch.setattr(service_module, "EMERGENCY_RESOURCE_CLEANUP_TIMEOUT_SECONDS", 0.01)
    state = MatchRuntimeState(
        match_id=match_id, status="PAUSED", action_state="RECOVERY_REQUIRED", actions=()
    )
    event = MatchEvent(
        type="stage.reset", match_id=match_id, sequence=1, server_time_ms=0, payload={}
    )
    with pytest.raises(MatchDomainError, match="asr_start_timeout"):
        await manager._publish(state, (event,))
    assert calls == ["asr", "decision", "agent", "host"]


@pytest.mark.asyncio
async def test_operator_precommit_does_not_retire_media_before_transaction() -> None:
    manager = MatchRuntimeManager(cast(Any, None))
    runtime = AsyncMock()
    manager.set_speech_runtime(runtime)
    state = MatchRuntimeState(
        match_id=uuid4(), status="PAUSED", action_state="RECOVERY_REQUIRED", actions=()
    )
    from jx_core.matches.domain import MatchCommand

    await manager._pre_commit(
        state,
        state,
        (
            MatchEvent(
                type="stage.reset",
                match_id=state.match_id,
                sequence=1,
                server_time_ms=0,
                payload={},
            ),
        ),
        MatchCommand(
            type="stage.reset", message_id="operator", payload={"operation_id": str(uuid4())}
        ),
    )
    runtime.reset_speech.assert_not_awaited()


@pytest.mark.asyncio
async def test_human_start_pending_does_not_cancel_original_finalizer() -> None:
    from jx_core.asr.protocol import FunAsrError
    from jx_core.matches.domain import MatchCommand
    from test_match_service_reset import StartSpeechRecorder, _human_ready_actor

    match_id, user_id = uuid4(), uuid4()
    manager = MatchRuntimeManager(cast(Any, None))
    runtime = StartSpeechRecorder()
    runtime.error = FunAsrError("asr_finalization_pending")
    manager.set_speech_runtime(cast(Any, runtime))
    actor = _human_ready_actor(manager, match_id, user_id)
    manager._actors[match_id] = actor
    await actor.start()
    before = actor.state
    try:
        with pytest.raises(MatchDomainError, match="asr_finalization_pending"):
            await manager.submit(
                match_id,
                MatchCommand(type="speech.start", message_id="pending", actor_user_id=user_id),
            )
        assert actor.state == before
        assert runtime.calls == []
        runtime.error = None
        await manager.submit(
            match_id,
            MatchCommand(type="speech.start", message_id="explicit-retry", actor_user_id=user_id),
        )
        assert actor.state.action_state == "HUMAN_SPEAKING"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_terminal_callback_can_submit_and_finishes_before_actor_eviction(monkeypatch: Any):
    from jx_core.matches.domain import MatchCommand
    from test_match_service_reset import _human_ready_actor

    manager = MatchRuntimeManager(cast(Any, None))
    match_id = uuid4()
    actor = _human_ready_actor(manager, match_id, uuid4())
    actor._publish = manager._publish
    manager._actors[match_id] = actor
    entered, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(service_module, "TERMINAL_ACTOR_RETENTION_SECONDS", 0)

    async def terminal_callback(target: Any):
        assert target == match_id and actor.state.status == "TERMINATED"
        # Submitting here must be handled by the free Actor, rather than
        # deadlocking inside its current postcommit publishing operation.
        with pytest.raises(MatchDomainError, match="match_not_running"):
            await manager.submit(
                match_id, MatchCommand(type="speech.skip.cancel", message_id="late-cancel")
            )
        entered.set()
        await release.wait()

    manager.set_terminal_callback(terminal_callback)
    await actor.start()
    try:
        await manager.submit(
            match_id,
            MatchCommand(
                type="match.terminate", message_id="terminate", payload={"privileged": True}
            ),
        )
        async with asyncio.timeout(1):
            await entered.wait()
        assert manager._actors[match_id] is actor
        release.set()
        async with asyncio.timeout(1):
            while match_id in manager._actors:
                await asyncio.sleep(0.01)
    finally:
        release.set()
        await manager.close()

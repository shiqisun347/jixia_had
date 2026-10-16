"""Spec307: stable speech identity across ASR recovery and finalization."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from jx_core.matches.domain import (
    MatchAction,
    MatchActor,
    MatchCommand,
    MatchEvent,
    MatchRuntimeState,
)
from jx_core.matches.service import MatchRuntimeManager
from jx_core.models import Speech
from jx_core.runtime_identity import CallbackEnvelope


def active_state() -> MatchRuntimeState:
    user = uuid4()
    return MatchRuntimeState(
        match_id=uuid4(),
        status="RUNNING",
        action_state="HUMAN_SPEAKING",
        actions=(MatchAction(1, 1, "HUMAN_SPEECH", 180, speaker_user_id=user),),
        current_speech_id=uuid4(),
        current_speaker_user_id=user,
        speech_remaining_ms=180_000,
        speech_deadline_mono=280,
        speech_budget_start_ms=180_000,
        interim_text="已确认的有效前缀",
        connection_epochs=((user, 5),),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("held_boundary", ["sql", "actor"])
async def test_same_value_replacement_owner_rejects_old_interim_after_await(
    held_boundary: str,
) -> None:
    state = active_state()
    envelope = CallbackEnvelope(state.match_id, state.current_speech_id, 1, None, 5, 7, None, None)
    current = [envelope]
    entered, release = asyncio.Event(), asyncio.Event()
    actor = MatchActor(state)
    await actor.start()
    if held_boundary == "actor":
        actor._processing = True
    speech = SimpleNamespace(
        match_id=state.match_id,
        user_id=state.current_speaker_user_id,
        attempt_no=1,
        opportunity_id=None,
        status="STARTED",
    )

    async def get(model: object, _key: object) -> object:
        if model is Speech:
            entered.set()
            if held_boundary == "sql":
                await release.wait()
            return speech
        return SimpleNamespace(context_version=7)

    session = MagicMock()
    session.get = AsyncMock(side_effect=get)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    manager = MatchRuntimeManager(cast(Any, factory))
    manager._actors[state.match_id] = actor
    queue: asyncio.Queue[MatchEvent] = asyncio.Queue(maxsize=4)
    manager._subscribers[state.match_id] = {queue}

    def callback_owner(_match: object, _speech: object) -> CallbackEnvelope:
        return current[0]

    runtime = SimpleNamespace(speech_callback_envelope=callback_owner, close=AsyncMock())
    manager.set_speech_runtime(cast(Any, runtime))
    task = asyncio.create_task(
        manager.publish_asr_interim(envelope=envelope, segment_no=1, text="过期的识别文本")
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0.01)
        current[0] = replace(envelope)
        assert current[0] == envelope and current[0] is not envelope
        actor._processing = False
        release.set()
        await asyncio.wait_for(task, 1)
        assert actor.state.interim_text == state.interim_text
        assert queue.empty()
    finally:
        actor._processing = False
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await manager.close()


@pytest.mark.asyncio
async def test_asr_failure_preserves_attempt_text_and_actual_budget() -> None:
    state = active_state()
    actor = MatchActor(state, clock=lambda: 131.7, sleep=lambda _: asyncio.sleep(60))
    await actor.start()
    try:
        result = await actor.submit(
            MatchCommand(
                "asr.failed",
                "failure",
                payload={
                    "speech_id": str(state.current_speech_id),
                    "error_code": "asr_task_failed",
                },
            )
        )
        assert result.state.status == "ERROR"
        assert result.state.current_speech_id == state.current_speech_id
        assert result.state.speech_remaining_ms == 148_300
        assert result.state.interim_text == state.interim_text
        assert result.state.speech_deadline_mono is None
        assert not any(event.type == "speech.ready" for event in result.events)
        resume = await actor.submit(
            MatchCommand("match.resume", "unsafe-resume", payload={"privileged": True})
        )
        assert resume.state.status == "ERROR"
        assert [event.type for event in resume.events] == ["match.resume_check_failed"]
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_system_recovery_preserves_failed_recording_identity_and_used_budget() -> None:
    original = active_state()
    state = replace(
        original,
        status="ERROR",
        action_state="RECOVERY_REQUIRED",
        asr_failed_speech_id=original.current_speech_id,
        speech_remaining_ms=148_300,
        speech_consumed_ms=31_700,
        speech_deadline_mono=None,
        paused_from_action_state="HUMAN_SPEAKING",
        error_code="asr_task_failed",
        emergency_operation={"id": "retired-retry", "kind": "asr.retry", "status": "PENDING"},
    )
    actor = MatchActor(state, clock=lambda: 900)
    await actor.start()
    try:
        result = await actor.submit(MatchCommand("system.recover", "process-restarted"))
        assert result.state.status == "ERROR"
        assert result.state.current_speech_id == state.current_speech_id
        assert result.state.asr_failed_speech_id == state.asr_failed_speech_id
        assert result.state.speech_remaining_ms == 148_300
        assert result.state.speech_consumed_ms == 31_700
        assert result.state.interim_text == state.interim_text
        assert result.state.emergency_operation is not None
        assert result.state.emergency_operation["status"] == "FAILED"
        assert not any(event.type in {"speech.ready", "speech.started"} for event in result.events)
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_presence_start_failure_keeps_original_callback_envelope() -> None:
    state = active_state()
    manager = MatchRuntimeManager(cast(Any, None))
    actor = MatchActor(state, sleep=lambda _: asyncio.sleep(60))
    await actor.start()
    manager._actors[state.match_id] = actor
    envelope = CallbackEnvelope(state.match_id, state.current_speech_id, 2, None, 5, 7, uuid4(), 3)
    runtime = SimpleNamespace(start_speech=AsyncMock(side_effect=TimeoutError), close=AsyncMock())
    manager.set_speech_runtime(cast(Any, runtime))
    manager._speech_callback_envelope = AsyncMock(return_value=envelope)
    manager.handle_asr_failure = AsyncMock()
    try:
        manager._queue_speech_presence_transition(
            state,
            MatchEvent(
                "match.online",
                state.match_id,
                state.sequence,
                1,
                {"user_id": str(state.current_speaker_user_id)},
            ),
        )
        await asyncio.gather(*tuple(manager._background_tasks))
        call = manager.handle_asr_failure.await_args
        assert call is not None and call.kwargs["envelope"] == envelope
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_closed_final_owner_survives_normal_new_epoch_but_not_retirement() -> None:
    state = replace(active_state(), action_state="SPEECH_FINALIZING")
    envelope = CallbackEnvelope(state.match_id, state.current_speech_id, 2, None, 4, 7, None, None)
    speech = SimpleNamespace(
        match_id=state.match_id,
        user_id=state.current_speaker_user_id,
        attempt_no=2,
        opportunity_id=None,
        status="FINALIZING",
    )
    session = MagicMock()

    async def get(_model: object, key: object) -> object:
        return speech if key == state.current_speech_id else SimpleNamespace(context_version=7)

    session.get = AsyncMock(side_effect=get)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    manager = MatchRuntimeManager(cast(Any, factory))
    actor = MatchActor(state)
    owned = True

    def current(*, envelope: CallbackEnvelope) -> bool:
        return owned and envelope.connection_epoch == 4

    manager.set_speech_runtime(cast(Any, SimpleNamespace(finalization_callback_is_current=current)))
    assert await manager._asr_callback_is_current(actor=actor, envelope=envelope)
    assert not await manager._asr_callback_is_current(
        actor=actor, envelope=replace(envelope, attempt_no=1)
    )
    owned = False
    assert not await manager._asr_callback_is_current(actor=actor, envelope=envelope)
    speech.status = "RESET"
    owned = True
    assert not await manager._asr_callback_is_current(actor=actor, envelope=envelope)


@pytest.mark.asyncio
async def test_explicit_asr_retry_finishes_same_attempt_without_new_microphone() -> None:
    state = replace(active_state(), speech_deadline_mono=None, speech_remaining_ms=148_300)
    actor = MatchActor(state, sleep=lambda _: asyncio.sleep(60))
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(
                "asr.failed",
                "failed-for-retry",
                payload={
                    "speech_id": str(state.current_speech_id),
                    "error_code": "asr_task_failed",
                },
            )
        )
        operation_id = uuid4()
        payload = {
            "privileged": True,
            "operation_id": str(operation_id),
            "expected_sequence": actor.state.sequence,
            "expected_action_key": "1:1",
            "expected_stage_generation": 0,
            "expected_speech_id": str(state.current_speech_id),
            "expected_context_version": 7,
        }
        begun = await actor.submit(MatchCommand("asr.retry.begin", "retry-start", payload=payload))
        assert begun.state.status == "ERROR"
        assert begun.state.current_speech_id == state.current_speech_id
        assert begun.state.speech_remaining_ms == 148_300
        assert begun.state.emergency_operation is not None
        assert begun.state.emergency_operation["kind"] == "asr.retry"
        assert begun.state.emergency_operation["status"] == "PENDING"
        assert [event.type for event in begun.events] == ["asr.retry.started"]
        finished = await actor.submit(
            MatchCommand(
                "asr.finalized",
                "retry-final",
                payload={"speech_id": str(state.current_speech_id), "final_text": "完整识别正文"},
            )
        )
        assert finished.state.emergency_operation is not None
        assert finished.state.emergency_operation["status"] == "SUCCEEDED"
        assert finished.state.asr_failed_speech_id is None
        assert not any(event.type == "speech.started" for event in finished.events)
        assert any(event.type == "speech.finished" for event in finished.events)
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_final_callback_deadline_bounds_actor_precommit_itself() -> None:
    state = replace(active_state(), action_state="SPEECH_FINALIZING")
    manager = MatchRuntimeManager(cast(Any, None))
    held = asyncio.Event()

    async def current(**_kwargs: object) -> bool:
        await held.wait()
        return True

    manager._asr_callback_is_current = AsyncMock(side_effect=current)
    actor = MatchActor(state, pre_commit=manager._pre_commit)
    manager._actors[state.match_id] = actor
    await actor.start()
    envelope = CallbackEnvelope(state.match_id, state.current_speech_id, 1, None, 5, 0, None, None)
    try:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.2):
                await actor.submit(
                    MatchCommand(
                        "asr.finalized",
                        "bounded-actor-final",
                        payload={"speech_id": str(state.current_speech_id)},
                        asr_callback_envelope=envelope,
                        asr_deadline_mono=asyncio.get_running_loop().time() + 0.02,
                    )
                )
        assert actor.state == state
        # The Actor remains available for an authoritative failure/recovery.
        await actor.submit(
            MatchCommand(
                "asr.failed",
                "after-final-deadline",
                payload={
                    "speech_id": str(state.current_speech_id),
                    "error_code": "asr_finalization_timeout",
                },
            )
        )
        assert (
            actor.state.status == "ERROR"
            and actor.state.current_speech_id == state.current_speech_id
        )
    finally:
        held.set()
        await manager.close()

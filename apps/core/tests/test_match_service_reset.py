from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from jx_core.matches.domain import (
    MatchAction,
    MatchActor,
    MatchCommand,
    MatchDomainError,
    MatchRuntimeState,
)
from jx_core.matches.service import MatchRuntimeManager
from jx_core.runtime_identity import CallbackEnvelope

# This test deliberately wires the manager's internal actor registry and hook without a database.
# The production typecheck excludes test-only implementation details.
# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false


class ResetRecorder:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def reset_speech(self, match_id: object) -> None:
        self.calls.append(match_id)

    async def reset_agent(self, match_id: object) -> None:
        self.calls.append(match_id)

    async def cancel_free_decision(self, match_id: object) -> None:
        self.calls.append(("decision", match_id))



class PresenceSpeechRecorder(ResetRecorder):
    def __init__(self) -> None:
        super().__init__()
        self.pause_started = asyncio.Event()
        self.allow_pause = asyncio.Event()

    async def pause_speech(self, match_id: object, speech_id: object) -> None:
        self.calls.append(("pause", match_id, speech_id))
        self.pause_started.set()
        await self.allow_pause.wait()

    async def start_speech(
        self, match_id: object, speech_id: object, user_id: object, envelope: object
    ) -> None:
        self.calls.append(("start", match_id, speech_id, user_id, envelope))

    async def close(self) -> None:
        return


class StartSpeechRecorder(ResetRecorder):
    def __init__(self) -> None:
        super().__init__()
        self.starts: list[tuple[object, object, object, object]] = []
        self.error: Exception | None = None
        self.block = False

    async def start_speech(
        self, match_id: object, speech_id: object, user_id: object, envelope: object
    ) -> None:
        self.starts.append((match_id, speech_id, user_id, envelope))
        if self.error is not None:
            raise self.error
        if self.block:
            await asyncio.Event().wait()

    async def finish_speech(self, match_id: object, speech_id: object) -> None:
        return

    async def pause_speech(self, match_id: object, speech_id: object) -> None:
        return

    async def close_match(self, match_id: object) -> None:
        return

    async def close(self) -> None:
        return


def _human_ready_actor(
    manager: MatchRuntimeManager,
    match_id: object,
    speaker_id: object,
    *,
    commit: Any = None,
) -> MatchActor:
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        side="AFFIRMATIVE",
        seat_no=1,
        speaker_user_id=cast(Any, speaker_id),
    )
    return MatchActor(
        MatchRuntimeState(
            match_id=cast(Any, match_id),
            status="RUNNING",
            action_state="HUMAN_READY_TO_START",
            actions=(action,),
            current_action_index=0,
            current_speaker_user_id=cast(Any, speaker_id),
            speech_remaining_ms=30_000,
        ),
        commit=commit,
        pre_commit=manager._pre_commit,
        sleep=lambda _: asyncio.sleep(60),
    )


@pytest.mark.asyncio
async def test_manager_starts_asr_once_after_actor_idempotency_check() -> None:
    match_id = uuid4()
    speaker_id = uuid4()
    manager = MatchRuntimeManager(cast(Any, None))
    runtime = StartSpeechRecorder()
    manager.set_speech_runtime(cast(Any, runtime))
    actor = _human_ready_actor(manager, match_id, speaker_id)
    await actor.start()
    manager._actors[match_id] = actor
    command = MatchCommand(
        type="speech.start", message_id="start-once", actor_user_id=speaker_id
    )
    try:
        first = await manager.submit(match_id, command)
        duplicate = await manager.submit(match_id, command)

        assert first.state.action_state == "HUMAN_SPEAKING"
        assert duplicate.duplicate is True
        assert len(runtime.starts) == 1
        assert runtime.calls == []
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_manager_rejects_wrong_speaker_before_starting_asr() -> None:
    match_id = uuid4()
    speaker_id = uuid4()
    manager = MatchRuntimeManager(cast(Any, None))
    runtime = StartSpeechRecorder()
    manager.set_speech_runtime(cast(Any, runtime))
    actor = _human_ready_actor(manager, match_id, speaker_id)
    await actor.start()
    manager._actors[match_id] = actor
    try:
        with pytest.raises(MatchDomainError, match="not_current_speaker"):
            await manager.submit(
                match_id,
                MatchCommand(
                    type="speech.start",
                    message_id="wrong-speaker",
                    actor_user_id=uuid4(),
                ),
            )

        assert actor.state.action_state == "HUMAN_READY_TO_START"
        assert runtime.starts == []
        assert runtime.calls == []
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_manager_keeps_human_ready_when_asr_start_fails_then_allows_retry() -> None:
    match_id = uuid4()
    speaker_id = uuid4()
    manager = MatchRuntimeManager(cast(Any, None))
    runtime = StartSpeechRecorder()
    runtime.error = RuntimeError("provider details must stay private")
    manager.set_speech_runtime(cast(Any, runtime))
    actor = _human_ready_actor(manager, match_id, speaker_id)
    await actor.start()
    manager._actors[match_id] = actor
    try:
        with pytest.raises(MatchDomainError, match="asr_start_timeout"):
            await manager.submit(
                match_id,
                MatchCommand(
                    type="speech.start", message_id="start-fails", actor_user_id=speaker_id
                ),
            )

        assert actor.state.action_state == "HUMAN_READY_TO_START"
        assert actor.state.current_speech_id is None
        assert actor.state.speech_remaining_ms == 30_000
        assert runtime.calls == [match_id]

        runtime.error = None
        retried = await manager.submit(
            match_id,
            MatchCommand(
                type="speech.start", message_id="start-retry", actor_user_id=speaker_id
            ),
        )
        assert retried.state.action_state == "HUMAN_SPEAKING"
        assert len(runtime.starts) == 2
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_manager_bounds_hung_asr_start_and_preserves_full_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    match_id = uuid4()
    speaker_id = uuid4()
    manager = MatchRuntimeManager(cast(Any, None))
    runtime = StartSpeechRecorder()
    runtime.block = True
    manager.set_speech_runtime(cast(Any, runtime))
    actor = _human_ready_actor(manager, match_id, speaker_id)
    await actor.start()
    manager._actors[match_id] = actor
    monkeypatch.setattr("jx_core.matches.service.HUMAN_SPEECH_START_TIMEOUT_SECONDS", 0.01)
    try:
        with pytest.raises(MatchDomainError, match="asr_start_timeout"):
            await manager.submit(
                match_id,
                MatchCommand(
                    type="speech.start", message_id="start-timeout", actor_user_id=speaker_id
                ),
            )
        assert actor.state.action_state == "HUMAN_READY_TO_START"
        assert actor.state.speech_remaining_ms == 30_000
        assert runtime.calls == [match_id]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_manager_cleans_started_asr_when_actor_commit_fails() -> None:
    match_id = uuid4()
    speaker_id = uuid4()
    manager = MatchRuntimeManager(cast(Any, None))
    runtime = StartSpeechRecorder()
    manager.set_speech_runtime(cast(Any, runtime))

    async def fail_commit(*_args: object) -> None:
        raise RuntimeError("database details must stay private")

    actor = _human_ready_actor(
        manager,
        match_id,
        speaker_id,
        commit=fail_commit,
    )
    await actor.start()
    manager._actors[match_id] = actor
    try:
        with pytest.raises(RuntimeError, match="database details must stay private"):
            await manager.submit(
                match_id,
                MatchCommand(
                    type="speech.start",
                    message_id="start-commit-fails",
                    actor_user_id=speaker_id,
                ),
            )

        assert actor.state.action_state == "HUMAN_READY_TO_START"
        assert actor.state.current_speech_id is None
        assert actor.state.speech_remaining_ms == 30_000
        assert len(runtime.starts) == 1
        assert runtime.calls == [match_id]
        assert (match_id, "start-commit-fails") not in manager._prepared_speech_starts
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("publisher", ["agent", "asr"])
async def test_manager_does_not_publish_interim_text_after_match_finished(publisher: str) -> None:
    match_id = uuid4()
    speech_id = uuid4()
    manager = MatchRuntimeManager(cast(Any, None))
    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="FINISHED",
            action_state="MATCH_FINISHED",
            actions=(),
            current_speech_id=speech_id,
        ),
        publish=manager._publish,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    manager._actors[match_id] = actor
    queue = await manager.subscribe(match_id)
    try:
        if publisher == "agent":
            await manager.publish_agent_subtitle(
                envelope=CallbackEnvelope(
                    match_id=match_id,
                    speech_id=speech_id,
                    attempt_no=1,
                    generation_id=uuid4(),
                    connection_epoch=None,
                    context_version=0,
                    opportunity_id=None,
                    opportunity_generation=None,
                ),
                text="迟到字幕",
                played_ms=1_000,
            )
        else:
            await manager.publish_asr_interim(
                envelope=CallbackEnvelope(
                    match_id=match_id,
                    speech_id=speech_id,
                    attempt_no=1,
                    generation_id=None,
                    connection_epoch=1,
                    context_version=0,
                    opportunity_id=None,
                    opportunity_generation=None,
                ),
                segment_no=1,
                text="迟到字幕",
            )

        assert actor.state.interim_text == ""
        assert queue.empty()
    finally:
        await manager.unsubscribe(match_id, queue)
        await manager.close()


@pytest.mark.asyncio
async def test_manager_serializes_current_speaker_asr_pause_before_rapid_resume() -> None:
    match_id = uuid4()
    speaker_id = uuid4()
    speech_id = uuid4()
    manager = MatchRuntimeManager(cast(Any, None))
    runtime = PresenceSpeechRecorder()
    manager.set_speech_runtime(cast(Any, runtime))
    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="RUNNING",
            action_state="HUMAN_SPEAKING",
            actions=(),
            current_speech_id=speech_id,
            current_speaker_user_id=speaker_id,
            speech_remaining_ms=30_000,
        ),
        publish=manager._publish,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    manager._actors[match_id] = actor
    try:
        await actor.submit(
            MatchCommand(
                type="member.offline",
                message_id="speaker-offline",
                actor_user_id=speaker_id,
                payload={"connection_epoch": 1},
            )
        )
        await runtime.pause_started.wait()
        await actor.submit(
            MatchCommand(
                type="member.online",
                message_id="speaker-online",
                actor_user_id=speaker_id,
                payload={"connection_epoch": 2},
            )
        )
        await asyncio.sleep(0)
        assert runtime.calls == [("pause", match_id, speech_id)]
        runtime.allow_pause.set()
        await asyncio.gather(*tuple(manager._background_tasks))
        assert runtime.calls[0] == ("pause", match_id, speech_id)
        assert runtime.calls[1][:4] == ("start", match_id, speech_id, speaker_id)
    finally:
        runtime.allow_pause.set()
        await manager.close()


@pytest.mark.asyncio
async def test_manager_does_not_pause_asr_twice_for_higher_epoch_offline() -> None:
    match_id = uuid4()
    speaker_id = uuid4()
    speech_id = uuid4()
    manager = MatchRuntimeManager(cast(Any, None))
    runtime = PresenceSpeechRecorder()
    manager.set_speech_runtime(cast(Any, runtime))
    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="RUNNING",
            action_state="HUMAN_SPEAKING",
            actions=(),
            current_speech_id=speech_id,
            current_speaker_user_id=speaker_id,
            speech_remaining_ms=30_000,
        ),
        publish=manager._publish,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    manager._actors[match_id] = actor
    try:
        await actor.submit(
            MatchCommand(
                type="member.offline",
                message_id="speaker-epoch-4-offline",
                actor_user_id=speaker_id,
                payload={"connection_epoch": 4, "offline_since_ms": 1},
            )
        )
        await runtime.pause_started.wait()
        duplicate = await actor.submit(
            MatchCommand(
                type="member.offline",
                message_id="speaker-epoch-5-offline",
                actor_user_id=speaker_id,
                payload={"connection_epoch": 5, "offline_since_ms": 2},
            )
        )
        await asyncio.sleep(0)

        assert duplicate.events == ()
        assert runtime.calls == [("pause", match_id, speech_id)]
        assert dict(actor.state.connection_epochs)[speaker_id] == 5
        assert dict(actor.state.offline_since_ms)[speaker_id] == 1
    finally:
        runtime.allow_pause.set()
        await manager.close()


@pytest.mark.asyncio
async def test_manager_does_not_pause_asr_for_non_current_member() -> None:
    match_id = uuid4()
    speaker_id = uuid4()
    manager = MatchRuntimeManager(cast(Any, None))
    runtime = PresenceSpeechRecorder()
    manager.set_speech_runtime(cast(Any, runtime))
    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="RUNNING",
            action_state="HUMAN_SPEAKING",
            actions=(),
            current_speech_id=uuid4(),
            current_speaker_user_id=speaker_id,
            speech_remaining_ms=30_000,
        ),
        publish=manager._publish,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    manager._actors[match_id] = actor
    try:
        await actor.submit(
            MatchCommand(
                type="member.offline",
                message_id="other-offline",
                actor_user_id=uuid4(),
                payload={"connection_epoch": 1},
            )
        )
        await asyncio.sleep(0)
        assert runtime.calls == []
    finally:
        runtime.allow_pause.set()
        await manager.close()


@pytest.mark.asyncio
async def test_manager_ignores_late_asr_failure_for_offline_current_speaker() -> None:
    match_id = uuid4()
    speaker_id = uuid4()
    speech_id = uuid4()
    manager = MatchRuntimeManager(cast(Any, None))
    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="RUNNING",
            action_state="HUMAN_SPEAKING",
            actions=(),
            current_speech_id=speech_id,
            current_speaker_user_id=speaker_id,
            offline_since_ms=((speaker_id, 1),),
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    manager._actors[match_id] = actor
    try:
        await manager.handle_asr_failure(
            envelope=CallbackEnvelope(
                match_id=match_id,
                speech_id=speech_id,
                attempt_no=1,
                generation_id=None,
                connection_epoch=1,
                context_version=0,
                opportunity_id=None,
                opportunity_generation=None,
            ),
            code="asr_task_failed",
        )
        assert actor.state.status == "RUNNING"
        assert actor.state.current_speech_id == speech_id
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_manager_does_not_reset_finished_free_turn_on_late_asr_failure() -> None:
    match_id = uuid4()
    speaker_id = uuid4()
    speech_id = uuid4()
    opportunity_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="FREE_DEBATE",
        duration_seconds=30,
        participants=(),
    )
    speech = SimpleNamespace(
        match_id=match_id,
        opportunity_id=opportunity_id,
        action_key=action.action_key,
        status="FINALIZING",
        asr_error_code=None,
        ended_at=None,
    )
    session = MagicMock()
    session.get = AsyncMock(return_value=speech)
    session.scalar = AsyncMock(return_value=1)
    session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    session.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)

    manager = MatchRuntimeManager(cast(Any, factory))
    manager._asr_callback_is_current = AsyncMock(return_value=True)
    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="RUNNING",
            action_state="SPEECH_FINALIZING",
            actions=(action,),
            experiment_mode=True,
            current_speech_id=speech_id,
            current_speaker_user_id=speaker_id,
            current_speaker_side="AFFIRMATIVE",
            current_speaker_seat_no=1,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    manager._actors[match_id] = actor
    try:
        await manager.handle_asr_failure(
            envelope=CallbackEnvelope(
                match_id=match_id,
                speech_id=speech_id,
                attempt_no=1,
                generation_id=None,
                connection_epoch=1,
                context_version=0,
                opportunity_id=opportunity_id,
                opportunity_generation=1,
            ),
            code="asr_stream_failed",
        )
        assert speech.status == "FAILED"
        assert actor.state.action_state == "SPEECH_FINALIZING"
        assert actor.state.current_speech_id == speech_id
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_manager_cleans_runtimes_only_after_valid_nonduplicate_reset() -> None:
    match_id = uuid4()
    speaker_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        side="AFFIRMATIVE",
        seat_no=1,
        speaker_user_id=speaker_id,
    )
    manager = MatchRuntimeManager(cast(Any, None))
    speech_runtime = ResetRecorder()
    agent_runtime = ResetRecorder()
    manager.set_speech_runtime(cast(Any, speech_runtime))
    manager.set_agent_runtime(cast(Any, agent_runtime))
    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="RUNNING",
            action_state="HUMAN_SPEAKING",
            actions=(action,),
            current_speech_id=uuid4(),
            current_speaker_user_id=speaker_id,
        ),
        pre_commit=manager._pre_commit,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    manager._actors[match_id] = actor
    try:
        with pytest.raises(MatchDomainError, match="forbidden"):
            await manager.submit(
                match_id,
                MatchCommand(
                    type="speech.reset",
                    message_id="unauthorized-reset",
                    actor_user_id=uuid4(),
                ),
            )
        assert speech_runtime.calls == []
        assert agent_runtime.calls == []

        command = MatchCommand(
            type="speech.reset",
            message_id="authorized-reset",
            actor_user_id=speaker_id,
        )
        first = await manager.submit(match_id, command)
        duplicate = await manager.submit(match_id, command)
        assert first.duplicate is False
        assert duplicate.duplicate is True
        assert speech_runtime.calls == [match_id]
        assert agent_runtime.calls == [("decision", match_id), match_id]
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_resume_cleans_interrupted_agent_before_countdown() -> None:
    match_id = uuid4()
    agent_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="AGENT_SPEECH",
        duration_seconds=30,
        side="AFFIRMATIVE",
        seat_no=1,
        speaker_kind="AGENT",
        agent_profile_id=agent_id,
    )
    manager = MatchRuntimeManager(cast(Any, None))
    runtime = ResetRecorder()
    manager.set_agent_runtime(cast(Any, runtime))
    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="ERROR",
            action_state="RECOVERY_REQUIRED",
            actions=(action,),
            current_agent_profile_id=agent_id,
            paused_from_status="RUNNING",
            paused_from_action_state="AGENT_SPEAKING",
            pause_initiator_user_id=uuid4(),
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    manager._actors[match_id] = actor
    try:
        result = await manager.submit(
            match_id,
            MatchCommand(
                type="match.resume",
                message_id="resume-agent",
                payload={"privileged": True, "reasons": []},
            ),
        )
        assert result.state.action_state == "RESUME_COUNTDOWN"
        assert runtime.calls == [("decision", match_id), match_id]
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_late_experiment_decision_is_recorded_without_actor_submission() -> None:
    match_id = uuid4()
    round_id = uuid4()
    agent_id = uuid4()
    manager = MatchRuntimeManager(cast(Any, None))
    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(),
            experiment_mode=True,
            agent_decision_round_id=round_id,
            selection_phase="HUMAN_ONLY_WAIT",
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    manager._actors[match_id] = actor
    recorder = AsyncMock()
    manager._record_late_experiment_decision = recorder
    manager.submit = AsyncMock(side_effect=AssertionError("late result reached Actor"))
    opportunity_id = uuid4()
    try:
        result = await manager.report_free_decision(
            envelope=CallbackEnvelope(
                match_id=match_id,
                speech_id=None,
                attempt_no=1,
                generation_id=None,
                connection_epoch=None,
                context_version=0,
                opportunity_id=opportunity_id,
                opportunity_generation=1,
            ),
            action_key="3:0",
            agent_profile_id=agent_id,
            side="NEGATIVE",
            decision_round_id=round_id,
            should_speak=True,
            willingness=None,
            failed=False,
            attempt_no=1,
            duration_ms=3_100,
            error_code=None,
        )

        assert result.state.action_state == "FREE_SELECTING"
        assert actor.state.selection_phase == "HUMAN_ONLY_WAIT"
        manager.submit.assert_not_awaited()
        recorder.assert_awaited_once()
        assert recorder.await_args.kwargs["stale"] is False
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_experiment_decision_cutoff_race_falls_back_to_late_record() -> None:
    match_id = uuid4()
    round_id = uuid4()
    agent_id = uuid4()
    manager = MatchRuntimeManager(cast(Any, None))
    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(),
            experiment_mode=True,
            agent_decision_round_id=round_id,
            selection_phase="COMPETING",
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    manager._actors[match_id] = actor
    recorder = AsyncMock()
    manager._record_late_experiment_decision = recorder
    manager.submit = AsyncMock(side_effect=MatchDomainError("stale_callback"))
    opportunity_id = uuid4()
    try:
        result = await manager.report_free_decision(
            envelope=CallbackEnvelope(
                match_id=match_id,
                speech_id=None,
                attempt_no=1,
                generation_id=None,
                connection_epoch=None,
                context_version=0,
                opportunity_id=opportunity_id,
                opportunity_generation=1,
            ),
            action_key="3:0",
            agent_profile_id=agent_id,
            side="NEGATIVE",
            decision_round_id=round_id,
            should_speak=True,
            willingness=None,
            failed=False,
            attempt_no=1,
            duration_ms=3_001,
            error_code=None,
        )

        assert result.state.action_state == "FREE_SELECTING"
        assert actor.state.selection_phase == "COMPETING"
        manager.submit.assert_awaited_once()
        recorder.assert_awaited_once()
        assert recorder.await_args.kwargs["stale"] is False
    finally:
        await actor.close()

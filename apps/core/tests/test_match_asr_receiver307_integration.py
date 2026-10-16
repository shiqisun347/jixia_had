"""Real Receiver/Session/Protocol/Actor/PG; only provider socket and RTC frames controlled."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from livekit import rtc
from sqlalchemy import select

from jx_core.asr.protocol import FunAsrConnection
from jx_core.asr.runtime import AsrRuntime, MatchAudioReceiver
from jx_core.matches.domain import MatchCommand
from jx_core.models import AsrSegment, ExternalCall, Match, Speech
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe
from test_asr import FakeSocket
from test_asr_queue_recovery import OwnedStream
from test_match_emergency_domain import (
    operator_payload,  # pyright: ignore[reportUnknownVariableType]
)
from test_match_handoff_recovery_integration import prepare

pytestmark = pytest.mark.integration


async def wait(predicate: Any, seconds: float = 3) -> None:
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.01)


async def connect(
    probe: Any, monkeypatch: pytest.MonkeyPatch, factory: Any, *, fixed: bool = True
) -> tuple[Any, ...]:
    _now, user, _next, _envelopes = await prepare(probe, fixed=fixed)
    match_id = probe.actor.state.match_id
    settings = probe.runtime._settings.model_copy(
        update={"asr_model": "qwen-audio-3.1-asr-flash-streaming"}
    )
    runtime = AsrRuntime(settings=settings, callbacks=probe.manager, capture_writer=probe.writer)
    receiver = MatchAudioReceiver(
        match_id=match_id,
        settings=settings,
        callbacks=probe.manager,
        capture_writer=probe.writer,
        input_clock=lambda: probe.actor._clock(),
    )
    room: Any = SimpleNamespace(remote_participants={}, disconnect=AsyncMock())
    receiver._room = room

    def connection() -> FunAsrConnection:
        return FunAsrConnection(
            url="wss://synthetic.invalid/asr",
            api_key="synthetic",
            model=settings.asr_model,
            socket_factory=factory,
        )

    receiver._connection = connection()
    receiver._new_provider_connection = connection
    runtime._receivers[match_id] = receiver
    probe.manager.set_speech_runtime(runtime)
    stream = OwnedStream()

    def owned_stream(*_args: Any, **_kwargs: Any) -> OwnedStream:
        return stream

    monkeypatch.setattr(rtc, "AudioStream", owned_stream)
    track: Any = SimpleNamespace(kind=rtc.TrackKind.KIND_AUDIO)
    publication: Any = SimpleNamespace(sid="spec307-owned-input", track=track)
    participant: Any = SimpleNamespace(
        identity=f"jx-human-{match_id}-{user}-1-owned",
        track_publications={publication.sid: publication},
    )
    room.remote_participants[participant.identity] = participant
    receiver._on_track_subscribed(room, track, publication, participant)
    return runtime, receiver, stream, user


async def test_actual_receiver_failure_replays_once_then_explicit_retry_same_speech(
    agent_fault_probe: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broken(FakeSocket):
        async def send(self, message: str | bytes) -> None:
            if isinstance(message, bytes):
                raise OSError("controlled transport failure")
            await super().send(message)

    sockets: list[FakeSocket] = []

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        value = Broken() if len(sockets) < 2 else FakeSocket()
        sockets.append(value)
        return value

    probe = agent_fault_probe
    runtime, receiver, stream, user = await connect(probe, monkeypatch, factory)
    baseline = set(asyncio.all_tasks())
    try:
        await probe.manager.submit(
            probe.actor.state.match_id,
            MatchCommand("speech.start", "actual-input-start", actor_user_id=user),
        )
        speech_id = probe.actor.state.current_speech_id
        assert speech_id is not None
        await stream.emit()
        await wait(lambda: probe.actor.state.status == "ERROR" and receiver._speech is None)
        assert len(sockets) == 2 and runtime.recovery_source_available(
            probe.actor.state.match_id, speech_id
        )
        old_owner = runtime.recovery_source_envelope(probe.actor.state.match_id, speech_id)
        async with probe.factory() as session:
            speech = await session.get(Speech, speech_id)
            match = await session.get(Match, probe.actor.state.match_id)
            assert speech is not None and speech.status == "FAILED" and speech.attempt_no == 1
            assert match is not None
            payload = cast(
                dict[str, Any], operator_payload(probe.actor, context_version=match.context_version)
            )
        await probe.manager.submit(
            probe.actor.state.match_id,
            MatchCommand("asr.retry.begin", "actual-recording-retry", payload=payload),
        )
        await wait(
            lambda: bool(
                probe.actor.state.emergency_operation
                and probe.actor.state.emergency_operation.get("status") == "SUCCEEDED"
            )
        )
        async with probe.factory() as session:
            speeches = list(
                (
                    await session.scalars(
                        select(Speech).where(Speech.match_id == probe.actor.state.match_id)
                    )
                ).all()
            )
            calls = list(
                (
                    await session.scalars(
                        select(ExternalCall).where(
                            ExternalCall.match_id == probe.actor.state.match_id,
                            ExternalCall.call_kind == "ASR",
                        )
                    )
                ).all()
            )
            segments = list(
                (
                    await session.scalars(
                        select(AsrSegment).where(AsrSegment.speech_id == speech_id)
                    )
                ).all()
            )
        assert (
            len(speeches) == 1 and speeches[0].id == speech_id and speeches[0].status == "FINALIZED"
        )
        assert speeches[0].attempt_no == 1 and speeches[0].display_text
        assert speeches[0].audio_duration_ms == 100
        assert len(sockets) == 3 and len(sockets[2].audio_frames) == 1
        assert len(calls) == len(segments) == 3
        assert all(
            row.model == runtime.model_id == "qwen-audio-3.1-asr-flash-streaming" for row in calls
        )
        assert sorted(row.status for row in calls) == ["FAILED", "FAILED", "SUCCEEDED"]
        assert not runtime.finalization_callback_is_current(envelope=old_owner)
    finally:
        await runtime.close()
        await probe.manager.close()
        assert stream.closed and not runtime._receivers
        await wait(
            lambda: not any(task for task in asyncio.all_tasks() - baseline if not task.done())
        )


async def test_actual_closed_final_owner_commits_after_legitimate_new_connection_epoch(
    agent_fault_probe: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finishing, release = asyncio.Event(), asyncio.Event()

    class Held(FakeSocket):
        async def send(self, message: str | bytes) -> None:
            if isinstance(message, str) and "finish-task" in message:
                finishing.set()
                await release.wait()
            await super().send(message)

    socket = Held()

    async def factory(_url: str, _headers: dict[str, str]) -> Held:
        return socket

    probe = agent_fault_probe
    runtime, receiver, stream, user = await connect(probe, monkeypatch, factory)
    try:
        await probe.manager.submit(
            probe.actor.state.match_id,
            MatchCommand("speech.start", "start-before-epoch", actor_user_id=user),
        )
        speech_id = probe.actor.state.current_speech_id
        assert speech_id is not None
        await stream.emit()
        await wait(lambda: bool(socket.audio_frames))
        await probe.manager.submit(
            probe.actor.state.match_id,
            MatchCommand("speech.finish", "finish-before-epoch", actor_user_id=user),
        )
        await asyncio.wait_for(finishing.wait(), 1)
        owner = runtime.speech_callback_envelope(probe.actor.state.match_id, speech_id)
        assert owner is not None and owner.connection_epoch == 1
        await probe.manager.submit(
            probe.actor.state.match_id,
            MatchCommand(
                "member.online",
                "new-epoch-during-final",
                actor_user_id=user,
                payload={"connection_epoch": 2},
            ),
        )
        assert dict(probe.actor.state.connection_epochs)[user] == 2
        assert runtime.finalization_callback_is_current(envelope=owner)
        release.set()
        await wait(lambda: probe.actor.state.current_action_index == 1)
        async with probe.factory() as session:
            speech = await session.get(Speech, speech_id)
        assert speech is not None and speech.status == "FINALIZED" and speech.display_text
        assert speech.audio_duration_ms == 100
    finally:
        release.set()
        await runtime.close()
        assert stream.closed and receiver._speech is None and not runtime._receivers

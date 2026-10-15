"""Read-only P0 probes: actual Receiver/Session/Protocol, owned transport only."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import jx_core.asr.runtime as runtime_module
from jx_core.asr.protocol import FunAsrConnection
from jx_core.asr.runtime import MatchAudioReceiver
from jx_core.asr.session import PCM_FRAME_BYTES, QUEUE_FRAMES
from jx_core.runtime_identity import CallbackEnvelope
from test_asr_protocol_retirement_epoch import OwnedSocket, observe
from test_asr_protocol_retirement_epoch import external_guard as external_guard


class OwnedStream:
    def __init__(self) -> None:
        self.events: asyncio.Queue[Any] = asyncio.Queue(maxsize=10)
        self.closed = False
        self.frames = 0
        self.after_overflow = asyncio.Event()

    def __aiter__(self) -> OwnedStream:
        return self

    async def __anext__(self) -> Any:
        if self.frames >= QUEUE_FRAMES + 2:
            self.after_overflow.set()
        event = await self.events.get()
        self.frames += 1
        return event

    async def emit(self) -> None:
        await self.events.put(
            SimpleNamespace(
                frame=SimpleNamespace(
                    data=b"\x01" * PCM_FRAME_BYTES,
                    sample_rate=16000,
                    samples_per_channel=1600,
                    num_channels=1,
                )
            )
        )

    async def aclose(self) -> None:
        self.closed = True


def envelope(receiver: MatchAudioReceiver, attempt: int) -> CallbackEnvelope:
    return CallbackEnvelope(
        match_id=receiver.match_id,
        speech_id=uuid4(),
        attempt_no=attempt,
        generation_id=None,
        connection_epoch=1,
        context_version=0,
        opportunity_id=None,
        opportunity_generation=None,
    )


async def prepare(monkeypatch: pytest.MonkeyPatch, callbacks: AsyncMock):
    baseline = asyncio.all_tasks()
    stream, socket = OwnedStream(), OwnedSocket(emit_sentence=True)
    factory_entered, factory_release = asyncio.Event(), asyncio.Event()
    factory_calls = 0

    async def factory(_: str, __: dict[str, str]) -> OwnedSocket:
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            factory_entered.set()
            await factory_release.wait()
        return socket

    connection = FunAsrConnection(
        url="wss://synthetic.invalid/asr",
        api_key="synthetic-unused-material",
        socket_factory=factory,
    )
    receiver = MatchAudioReceiver(match_id=uuid4(), settings=SimpleNamespace(), callbacks=callbacks)
    room = SimpleNamespace(remote_participants={}, disconnect=AsyncMock())
    receiver._room, receiver._connection = room, connection
    monkeypatch.setattr(runtime_module.rtc, "AudioStream", lambda *_a, **_kw: stream)
    user_id = uuid4()
    old_envelope = envelope(receiver, 1)
    await receiver.start_speech(old_envelope.speech_id, user_id, old_envelope)
    track = SimpleNamespace(kind=runtime_module.rtc.TrackKind.KIND_AUDIO)
    publication = SimpleNamespace(sid="owned-track")
    participant = SimpleNamespace(identity=f"jx-human-{receiver.match_id}-{user_id}-1-owned")
    receiver._on_track_subscribed(room, track, publication, participant)
    track_task = next(iter(receiver._track_tasks))
    await stream.emit()
    await asyncio.wait_for(factory_entered.wait(), timeout=0.4)
    return SimpleNamespace(
        baseline=baseline,
        stream=stream,
        socket=socket,
        receiver=receiver,
        connection=connection,
        user_id=user_id,
        room=room,
        track=track,
        publication=publication,
        participant=participant,
        old_envelope=old_envelope,
        track_task=track_task,
        factory_release=factory_release,
        factory_calls=lambda: factory_calls,
    )


async def overflow(case: Any) -> None:
    for _ in range(QUEUE_FRAMES + 1):
        await case.stream.emit()
    assert await observe(
        lambda: case.track_task.done() or case.stream.after_overflow.is_set(), timeout=0.6
    )


async def cleanup(case: Any) -> None:
    case.factory_release.set()
    await asyncio.wait_for(case.receiver.close(), timeout=0.5)
    await asyncio.wait_for(case.connection.close(), timeout=0.5)
    if not case.track_task.done():
        case.track_task.cancel()
    await asyncio.wait_for(asyncio.gather(case.track_task, return_exceptions=True), timeout=0.5)
    assert await observe(
        lambda: not asyncio.all_tasks() - case.baseline - {asyncio.current_task()}, timeout=0.4
    )
    print("queue_probe_fixture=" + json.dumps({"pending": 0, "stream_closed": case.stream.closed}))


async def test_queue_failure_does_not_disable_same_live_publication_retry(
    monkeypatch: pytest.MonkeyPatch, external_guard: dict[str, int]
) -> None:
    callbacks = AsyncMock()
    case = await prepare(monkeypatch, callbacks)
    try:
        await overflow(case)
        # Duplicate publication events must not be required to recover, but the
        # actual duplicate callback also demonstrates the retained dedup key.
        case.receiver._on_track_subscribed(
            case.room, case.track, case.publication, case.participant
        )
        retry_envelope = envelope(case.receiver, 2)
        await case.receiver.start_speech(retry_envelope.speech_id, case.user_id, retry_envelope)
        retry_task = case.receiver._speech_task
        await case.stream.emit()
        await observe(lambda: case.socket.pcm_frames > 0, timeout=0.06)
        await case.receiver.finish_speech(retry_envelope.speech_id)
        codes = [call.kwargs["code"] for call in callbacks.handle_asr_failure.await_args_list]
        snapshot = {
            "codes": codes,
            "same_publication_consumer_done": case.track_task.done(),
            "consumer_count": len(case.receiver._track_tasks),
            "retry_pcm_frames": case.socket.pcm_frames,
            "retry_task_done": retry_task.done(),
            "guard": dict(external_guard),
        }
        print("queue_retry_product=" + json.dumps(snapshot, sort_keys=True))
        assert case.socket.pcm_frames == 1 and codes == ["asr_pcm_queue_full"], snapshot
        assert not external_guard
    finally:
        await cleanup(case)


async def test_old_queue_failure_callback_cannot_abort_replacement_session(
    monkeypatch: pytest.MonkeyPatch, external_guard: dict[str, int]
) -> None:
    callbacks = AsyncMock()
    case = await prepare(monkeypatch, callbacks)
    replacement: dict[str, Any] = {}

    async def handle_failure(**_: Any) -> None:
        # MatchService failure handling can await reset and publish readiness;
        # a fresh speech is then a valid replacement before this await returns.
        await case.receiver.abort_speech()
        new_envelope = envelope(case.receiver, 2)
        await case.receiver.start_speech(new_envelope.speech_id, case.user_id, new_envelope)
        replacement["session"] = case.receiver._speech
        replacement["task"] = case.receiver._speech_task
        replacement["session"].feed_pcm(b"\x02" * PCM_FRAME_BYTES)
        assert await observe(lambda: case.socket.pcm_frames == 1, timeout=0.2)

    callbacks.handle_asr_failure.side_effect = handle_failure
    try:
        await overflow(case)
        snapshot = {
            "replacement_still_current": case.receiver._speech is replacement["session"],
            "replacement_task_cancelled": replacement["task"].cancelled(),
            "new_provider_pcm_frames": case.socket.pcm_frames,
            "guard": dict(external_guard),
        }
        print("queue_owner_product=" + json.dumps(snapshot, sort_keys=True))
        assert snapshot["replacement_still_current"], snapshot
        assert not snapshot["replacement_task_cancelled"] and not external_guard, snapshot
    finally:
        await cleanup(case)


async def test_finish_closes_input_before_waiting_for_queue_sentinel(
    external_guard: dict[str, int],
) -> None:
    from jx_core.asr.session import AsrSpeechSession

    connection = FunAsrConnection(
        url="wss://synthetic.invalid/asr", api_key="synthetic-unused-material"
    )
    session = AsrSpeechSession(
        speech_id=uuid4(), connection=connection, on_interim=AsyncMock(), on_segment=AsyncMock()
    )
    session.feed_pcm(b"\x01" * (QUEUE_FRAMES * PCM_FRAME_BYTES))
    finish = asyncio.create_task(session.finish())
    try:
        assert await observe(lambda: session._closed, timeout=0.2)
        assert not finish.done()
        before = session._queue.qsize()
        session.feed_pcm(b"\x02" * ((QUEUE_FRAMES + 1) * PCM_FRAME_BYTES))
        snapshot = {
            "closed": session._closed,
            "finish_still_waits": not finish.done(),
            "queue_before": before,
            "queue_after": session._queue.qsize(),
            "guard": dict(external_guard),
        }
        print("finish_input_control=" + json.dumps(snapshot, sort_keys=True))
        assert before == session._queue.qsize() == QUEUE_FRAMES and not external_guard, snapshot
    finally:
        finish.cancel()
        await asyncio.gather(finish, return_exceptions=True)
        await connection.close()
        print("queue_probe_fixture=" + json.dumps({"pending": 0}))


async def test_new_start_waits_for_actual_old_finalizer_then_can_retry(
    external_guard: dict[str, int],
) -> None:
    baseline = asyncio.all_tasks()
    socket = OwnedSocket(emit_sentence=True)

    async def factory(_: str, __: dict[str, str]) -> OwnedSocket:
        return socket

    connection = FunAsrConnection(
        url="wss://synthetic.invalid/asr",
        api_key="synthetic-unused-material",
        socket_factory=factory,
    )
    callbacks = AsyncMock()
    entered, release = asyncio.Event(), asyncio.Event()

    async def persist(**_: Any) -> None:
        if not entered.is_set():
            entered.set()
            await release.wait()

    callbacks.persist_asr_segment.side_effect = persist
    receiver = MatchAudioReceiver(match_id=uuid4(), settings=SimpleNamespace(), callbacks=callbacks)
    receiver._room = SimpleNamespace(disconnect=AsyncMock())
    receiver._connection = connection
    old_envelope, new_envelope = envelope(receiver, 1), envelope(receiver, 2)
    finish_task = None
    try:
        await receiver.start_speech(old_envelope.speech_id, uuid4(), old_envelope)
        old_session, old_task = receiver._speech, receiver._speech_task
        old_session.feed_pcm(b"\x01" * PCM_FRAME_BYTES)
        finish_task = asyncio.create_task(receiver.finish_speech(old_envelope.speech_id))
        await asyncio.wait_for(entered.wait(), timeout=0.4)
        code = None
        try:
            await receiver.start_speech(new_envelope.speech_id, uuid4(), new_envelope)
        except Exception as error:
            code = getattr(error, "code", type(error).__name__)
        snapshot = {
            "start_rejection": code,
            "old_session_retained": receiver._speech is old_session,
            "old_task_pending": not old_task.done(),
            "guard": dict(external_guard),
        }
        print("handoff_pending_product=" + json.dumps(snapshot, sort_keys=True))
        assert code == "asr_finalization_pending", snapshot
        assert receiver._speech is old_session and not old_task.done(), snapshot
        release.set()
        await asyncio.wait_for(finish_task, timeout=0.4)
        assert receiver._speech is None and old_task.done()
        await receiver.start_speech(new_envelope.speech_id, uuid4(), new_envelope)
        receiver._speech.feed_pcm(b"\x02" * PCM_FRAME_BYTES)
        await asyncio.wait_for(receiver.finish_speech(new_envelope.speech_id), timeout=0.4)
        identities = [
            call.kwargs["envelope"].speech_id
            for call in callbacks.finalize_asr_speech.await_args_list
        ]
        assert identities == [old_envelope.speech_id, new_envelope.speech_id]
        assert socket.pcm_frames == 2 and not callbacks.handle_asr_failure.await_count
        assert not external_guard
        print("handoff_retry_product=" + json.dumps({"separate_speech_ids": True, "pcm": 2}))
    finally:
        release.set()
        await asyncio.wait_for(receiver.close(), timeout=0.5)
        if finish_task is not None:
            await asyncio.wait_for(asyncio.gather(finish_task, return_exceptions=True), timeout=0.5)
        await connection.close()
        assert await observe(
            lambda: not asyncio.all_tasks() - baseline - {asyncio.current_task()}, timeout=0.4
        )
        print("queue_probe_fixture=" + json.dumps({"pending": 0}))

"""Spec307 actual Receiver/Session/Protocol source ownership and replay controls."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from jx_core.asr.protocol import FunAsrConnection, FunAsrError
from jx_core.asr.runtime import MatchAudioReceiver
from jx_core.config import Settings
from jx_core.runtime_identity import CallbackEnvelope
from test_asr import FakeSocket


def make_receiver(tmp_path: Path) -> tuple[MatchAudioReceiver, Any]:
    callbacks = SimpleNamespace(
        **{
            name: AsyncMock()
            for name in (
                "publish_asr_interim",
                "persist_asr_segment",
                "start_asr_segment",
                "fail_asr_segment",
                "finalize_asr_speech",
                "handle_asr_failure",
                "persist_asr_capture",
            )
        }
    )
    settings = cast(
        Settings,
        SimpleNamespace(
            agent_audio_storage_dir=str(tmp_path),
            asr_api_key=SecretStr("synthetic"),
            asr_ws_url="wss://owned.test",
            asr_model="qwen-audio-3.1-asr-flash-streaming",
            asr_workspace_id=None,
        ),
    )
    receiver = MatchAudioReceiver(
        match_id=uuid4(), settings=settings, callbacks=cast(Any, callbacks)
    )
    receiver._room = cast(Any, SimpleNamespace(remote_participants={}, disconnect=AsyncMock()))
    return receiver, callbacks


def envelope(receiver: MatchAudioReceiver) -> CallbackEnvelope:
    return CallbackEnvelope(
        match_id=receiver.match_id,
        speech_id=uuid4(),
        attempt_no=2,
        generation_id=None,
        connection_epoch=3,
        context_version=5,
        opportunity_id=None,
        opportunity_generation=None,
    )


@pytest.mark.asyncio
async def test_session_envelope_is_distinct_object_and_closed_owner_survives_epoch_change(
    tmp_path: Path,
) -> None:
    receiver, _ = make_receiver(tmp_path)
    identity = envelope(receiver)

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        return FakeSocket()

    receiver._connection = FunAsrConnection(
        url="wss://owned.test", api_key="synthetic", socket_factory=factory
    )
    assert identity.speech_id is not None
    try:
        await receiver.start_speech(identity.speech_id, uuid4(), identity)
        current = receiver.speech_callback_envelope(identity.speech_id)
        assert current is not None
        assert current == identity and current is not identity
        assert not receiver.finalization_callback_is_current(current)
        assert receiver._speech is not None
        await receiver._speech.finish()
        assert receiver.finalization_callback_is_current(current)
        assert not receiver.finalization_callback_is_current(replace(current, connection_epoch=4))
        await receiver.abort_speech()
        assert not receiver.finalization_callback_is_current(current)
    finally:
        await receiver.close()


@pytest.mark.asyncio
async def test_two_failures_retain_audio_then_manual_retry_same_speech_without_new_pcm(
    tmp_path: Path,
) -> None:
    class BrokenSocket(FakeSocket):
        async def send(self, message: str | bytes) -> None:
            if isinstance(message, bytes):
                raise OSError("synthetic transport failure")
            await super().send(message)

    receiver, callbacks = make_receiver(tmp_path)
    identity = envelope(receiver)
    sockets: list[FakeSocket] = []

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        value = BrokenSocket() if len(sockets) < 2 else FakeSocket()
        sockets.append(value)
        return value

    def connection() -> FunAsrConnection:
        return FunAsrConnection(url="wss://owned.test", api_key="synthetic", socket_factory=factory)

    receiver._connection = connection()
    receiver._new_provider_connection = connection
    assert identity.speech_id is not None
    try:
        await receiver.start_speech(identity.speech_id, uuid4(), identity)
        session = receiver._speech
        task = receiver._speech_task
        assert session is not None and task is not None
        pcm = b"\x01\0" * 8000
        receiver._write_recording(pcm)
        session.feed_pcm(pcm)
        await session.finish()
        await asyncio.wait_for(task, 1)
        assert len(sockets) == 2
        assert callbacks.handle_asr_failure.await_count == 1
        retained = receiver._retained_speech
        assert retained is not None and retained.session.pcm_snapshot() == pcm
        stored = receiver._recording_storage_path
        assert stored is not None and stored.read_bytes() == pcm
        old_owner = retained.envelope
        await receiver.retry_from_recording(
            identity.speech_id, identity, deadline_mono=asyncio.get_running_loop().time() + 1
        )
        assert len(sockets) == 3 and len(sockets[2].audio_frames) == 5
        assert callbacks.finalize_asr_speech.await_count == 1
        final = callbacks.finalize_asr_speech.await_args.kwargs
        assert final["envelope"] == identity and final["envelope"] is not old_owner
        assert (
            final["audio_duration_ms"] == 500
            and Path(final["audio_storage_path"]).read_bytes() == pcm
        )
        assert callbacks.handle_asr_failure.await_count == 1
        assert not receiver.finalization_callback_is_current(old_owner)
    finally:
        await receiver.close()


@pytest.mark.asyncio
async def test_one_finalization_deadline_cannot_be_refreshed_by_second_finish(
    tmp_path: Path,
) -> None:
    receiver, _ = make_receiver(tmp_path)
    identity = envelope(receiver)
    socket = FakeSocket()

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        return socket

    receiver._connection = FunAsrConnection(
        url="wss://owned.test", api_key="synthetic", socket_factory=factory
    )
    assert identity.speech_id is not None
    try:
        await receiver.start_speech(identity.speech_id, uuid4(), identity)
        assert receiver._speech is not None
        session = receiver._speech
        deadline = asyncio.get_running_loop().time() + 0.1
        await session.finish(deadline_mono=deadline)
        await session.finish(deadline_mono=deadline + 20)
        assert session.finalization_deadline == deadline
    finally:
        await receiver.close()


@pytest.mark.asyncio
async def test_new_epoch_publication_waits_for_owner_then_works_without_resubscribe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livekit import rtc

    from test_asr_protocol_retirement_epoch import observe
    from test_asr_queue_recovery import OwnedStream

    receiver, callbacks = make_receiver(tmp_path)
    stream, socket = OwnedStream(), FakeSocket()

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        return socket

    receiver._connection = FunAsrConnection(
        url="wss://owned.test", api_key="synthetic", socket_factory=factory
    )
    identity = envelope(receiver)
    assert identity.speech_id is not None
    user = uuid4()
    room: Any = SimpleNamespace(remote_participants={}, disconnect=AsyncMock())
    receiver._room = room
    track: Any = SimpleNamespace(kind=rtc.TrackKind.KIND_AUDIO)
    publication: Any = SimpleNamespace(sid="same-publication", track=track)
    participant: Any = SimpleNamespace(
        identity=f"jx-human-{receiver.match_id}-{user}-4-owned",
        track_publications={publication.sid: publication},
    )
    room.remote_participants[participant.identity] = participant

    def owned_stream(*_args: Any, **_kwargs: Any) -> OwnedStream:
        return stream

    monkeypatch.setattr(rtc, "AudioStream", owned_stream)
    try:
        await receiver.start_speech(identity.speech_id, user, identity)
        receiver._on_track_subscribed(room, track, publication, participant)
        consumer = next(iter(receiver._track_tasks))
        await stream.emit()
        assert await observe(lambda: stream.frames == 1)
        assert not consumer.done() and not stream.closed
        assert receiver._speech is not None and receiver._speech.pcm_snapshot() == b""
        await receiver.pause_speech(identity.speech_id)
        new = replace(identity, connection_epoch=4)
        await receiver.start_speech(identity.speech_id, user, new)
        receiver._on_track_subscribed(room, track, publication, participant)
        assert receiver._track_tasks == {consumer}
        await receiver.wait_speech_ready(identity.speech_id, seconds=0.2)
        await stream.emit()
        assert await observe(lambda: len(socket.audio_frames) == 1)
        await receiver.finish_speech(identity.speech_id)
        assert callbacks.finalize_asr_speech.await_count == 1
        assert callbacks.finalize_asr_speech.await_args.kwargs["envelope"].connection_epoch == 4
    finally:
        await receiver.close()
        await connection_close(receiver._connection)


async def connection_close(connection: FunAsrConnection | None) -> None:
    if connection is not None:
        await connection.close()


@pytest.mark.asyncio
async def test_synchronous_pause_fence_revokes_callbacks_and_input_before_any_await(
    tmp_path: Path,
) -> None:
    receiver, callbacks = make_receiver(tmp_path)
    identity = envelope(receiver)
    socket = FakeSocket()

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        return socket

    receiver._connection = FunAsrConnection(
        url="wss://owned.test", api_key="synthetic", socket_factory=factory
    )
    assert identity.speech_id is not None
    try:
        await receiver.start_speech(identity.speech_id, uuid4(), identity)
        session = receiver._speech
        assert session is not None
        session.feed_pcm(b"\x01\0" * 1600)
        owner = receiver.speech_callback_envelope(identity.speech_id)
        assert owner is not None
        receiver.fence_speech_input(identity.speech_id)
        assert receiver.speech_callback_envelope(identity.speech_id) is None
        assert not receiver.finalization_callback_is_current(owner) and session.input_closed
        before = session.pcm_snapshot()
        session.feed_pcm(b"\x02\0" * 1600)
        assert session.pcm_snapshot() == before
        await receiver.pause_speech(identity.speech_id)
        assert callbacks.finalize_asr_speech.await_count == 0
        assert receiver._paused_speech is not None
    finally:
        await receiver.close()


@pytest.mark.asyncio
async def test_final_deadline_retires_nested_factory_and_keeps_exact_retry_source(
    tmp_path: Path,
) -> None:
    from test_asr_protocol_retirement_epoch import observe

    receiver, _ = make_receiver(tmp_path)
    identity = envelope(receiver)
    entered, release = asyncio.Event(), asyncio.Event()
    socket = FakeSocket()

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass
        return socket

    connection = FunAsrConnection(
        url="wss://owned.test", api_key="synthetic", socket_factory=factory
    )
    receiver._connection = connection
    assert identity.speech_id is not None
    baseline = asyncio.all_tasks()
    try:
        await receiver.start_speech(identity.speech_id, uuid4(), identity)
        session = receiver._speech
        assert session is not None
        pcm = b"\x03\0" * 1600
        receiver._write_recording(pcm)
        session.feed_pcm(pcm)
        await asyncio.wait_for(entered.wait(), 0.2)
        owner = receiver.speech_callback_envelope(identity.speech_id)
        assert owner is not None
        with pytest.raises(FunAsrError, match="asr_finalization_timeout"):
            await receiver.finish_speech(
                identity.speech_id, deadline_mono=asyncio.get_running_loop().time() + 0.02
            )
        assert not release.is_set() and not socket.audio_frames
        assert receiver.recovery_source_available(identity.speech_id)
        assert receiver.recovery_source_envelope(identity.speech_id) is owner
        assert receiver.finalization_callback_is_current(owner)
        assert receiver._connection is None and receiver._speech is None
        assert receiver._retained_speech is not None
        assert receiver._retained_speech.session.pcm_snapshot() == pcm
    finally:
        release.set()
        await receiver.close()
        await connection.close()
        assert await observe(
            lambda: not asyncio.all_tasks() - baseline - {asyncio.current_task()}, timeout=0.5
        )

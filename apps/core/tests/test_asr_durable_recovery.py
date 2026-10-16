"""Restart recovery uses one bounded owned PCM file; never reopens a microphone."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import hashlib
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from jx_core.asr.protocol import FunAsrConnection, FunAsrError
from jx_core.asr.runtime import AsrRuntime, MatchAudioReceiver
from test_asr import FakeSocket
from test_asr_retained_recovery import envelope, make_receiver


def recording(root: Path, match: Any, speech: Any, user: Any, data: bytes) -> Path:
    path = root / "matches" / str(match) / "human" / str(user) / f"{speech}.pcm"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.mark.asyncio
async def test_restarted_runtime_restores_complete_pcm_and_retries_without_room_or_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old, callbacks = make_receiver(tmp_path)
    identity, user = envelope(old), uuid4()
    assert identity.speech_id is not None
    pcm = b"\x01\0" * 8000
    path = recording(tmp_path, old.match_id, identity.speech_id, user, pcm)
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    runtime = AsrRuntime(settings=old._settings, callbacks=callbacks)
    sockets: list[FakeSocket] = []

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        socket = FakeSocket()
        sockets.append(socket)
        return socket

    def connection(_self: MatchAudioReceiver) -> FunAsrConnection:
        return FunAsrConnection(url="wss://owned.test", api_key="synthetic", socket_factory=factory)

    monkeypatch.setattr(MatchAudioReceiver, "_new_provider_connection", connection)
    try:
        assert not runtime.recovery_source_available(old.match_id, identity.speech_id)
        await runtime.restore_recovery_source(
            old.match_id,
            identity.speech_id,
            user,
            identity,
            audio_storage_path=str(path),
            audio_duration_ms=500,
            max_audio_duration_ms=1000,
            last_segment_no=2,
        )
        restored = runtime._receivers[old.match_id]
        assert restored._room is None and not sockets
        info = runtime.recovery_source_info(old.match_id, identity.speech_id)
        assert info == {
            "audio_storage_path": str(path),
            "audio_duration_ms": 500,
            "confirmed_prefix": "",
            "max_audio_duration_ms": 1000,
        }
        source_owner = runtime.recovery_source_envelope(old.match_id, identity.speech_id)
        assert (
            source_owner is not None and source_owner == identity and source_owner is not identity
        )
        assert runtime.speech_callback_envelope(old.match_id, identity.speech_id) is None
        assert not runtime.finalization_callback_is_current(envelope=source_owner)
        await runtime.retry_from_recording(
            old.match_id,
            identity.speech_id,
            source_owner,
            deadline_mono=asyncio.get_running_loop().time() + 1,
        )
        assert len(sockets) == 1 and b"".join(sockets[0].audio_frames) == pcm
        assert callbacks.start_asr_segment.await_args.kwargs["segment_no"] == 3
        assert callbacks.persist_asr_segment.await_args.kwargs["segment_no"] == 3
        final = callbacks.finalize_asr_speech.await_args.kwargs
        assert final["final_text"] == "你好，" and final["audio_duration_ms"] == 500
        assert final["audio_storage_path"] == str(path)
        assert final["envelope"] is not source_owner
        assert not runtime.recovery_source_available(old.match_id, identity.speech_id)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash
        callbacks.handle_asr_failure.assert_not_awaited()
    finally:
        await runtime.close()
        await old.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "wrong_speech",
        "wrong_user",
        "outside",
        "symlink",
        "directory_symlink",
        "odd",
        "duration",
        "budget",
        "empty",
    ],
)
async def test_restore_rejects_unowned_or_invalid_pcm_before_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    old, callbacks = make_receiver(tmp_path)
    identity, user = envelope(old), uuid4()
    assert identity.speech_id is not None
    pcm = b"\x01\0" * 8000
    path = recording(tmp_path, old.match_id, identity.speech_id, user, pcm)
    duration, budget = 500, 1000
    if fault == "wrong_speech":
        path = recording(tmp_path, old.match_id, uuid4(), user, pcm)
    elif fault == "wrong_user":
        path = recording(tmp_path, old.match_id, identity.speech_id, uuid4(), pcm)
    elif fault == "outside":
        path = tmp_path / "outside.pcm"
        path.write_bytes(pcm)
    elif fault == "symlink":
        other = tmp_path / "other.pcm"
        other.write_bytes(pcm)
        path.unlink()
        path.symlink_to(other)
    elif fault == "directory_symlink":
        directory = path.parent
        destination = directory.with_name("owned-target")
        directory.rename(destination)
        directory.symlink_to(destination, target_is_directory=True)
    elif fault == "odd":
        path.write_bytes(pcm + b"\x01")
    elif fault == "duration":
        duration = 499
    elif fault == "budget":
        budget = 499
    elif fault == "empty":
        path.write_bytes(b"")
    runtime = AsrRuntime(settings=old._settings, callbacks=callbacks)

    def forbidden(_self: MatchAudioReceiver) -> Any:
        raise AssertionError("invalid source reached provider construction")

    monkeypatch.setattr(MatchAudioReceiver, "_new_provider_connection", forbidden)
    try:
        with pytest.raises(FunAsrError, match="asr_recovery_source_invalid"):
            await runtime.restore_recovery_source(
                old.match_id,
                identity.speech_id,
                user,
                identity,
                audio_storage_path=str(path),
                audio_duration_ms=duration,
                max_audio_duration_ms=budget,
            )
        assert not runtime.recovery_source_available(old.match_id, identity.speech_id)
        callbacks.finalize_asr_speech.assert_not_awaited()
    finally:
        await runtime.close()
        await old.close()


@pytest.mark.asyncio
async def test_negative_durable_segment_number_is_rejected_before_provider(tmp_path: Path) -> None:
    old, callbacks = make_receiver(tmp_path)
    identity, user = envelope(old), uuid4()
    assert identity.speech_id is not None
    path = recording(tmp_path, old.match_id, identity.speech_id, user, b"\x01\0" * 1600)
    runtime = AsrRuntime(settings=old._settings, callbacks=callbacks)
    try:
        with pytest.raises(FunAsrError, match="asr_recovery_source_invalid"):
            await runtime.restore_recovery_source(
                old.match_id,
                identity.speech_id,
                user,
                identity,
                audio_storage_path=str(path),
                audio_duration_ms=100,
                max_audio_duration_ms=1000,
                last_segment_no=-1,
            )
        assert not runtime.recovery_source_available(old.match_id, identity.speech_id)
    finally:
        await runtime.close()
        await old.close()


@pytest.mark.asyncio
async def test_authority_rejected_frame_never_enters_durable_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livekit import rtc

    from test_asr_protocol_retirement_epoch import observe
    from test_asr_queue_recovery import OwnedStream

    receiver, callbacks = make_receiver(tmp_path)
    identity, user = envelope(receiver), uuid4()
    assert identity.speech_id is not None
    stream, socket = OwnedStream(), FakeSocket()

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        return socket

    receiver._connection = FunAsrConnection(
        url="wss://owned.test", api_key="synthetic", socket_factory=factory
    )

    def owned_stream(*_args: Any, **_kwargs: Any) -> OwnedStream:
        return stream

    monkeypatch.setattr(rtc, "AudioStream", owned_stream)
    consumer: asyncio.Task[None] | None = None
    try:
        await receiver.start_speech(identity.speech_id, user, identity, max_audio_duration_ms=100)
        consumer = asyncio.create_task(
            receiver._consume_track(
                cast(Any, object()), f"jx-human-{receiver.match_id}-{user}-3-owned"
            )
        )
        await stream.emit()
        await stream.emit()
        assert await observe(lambda: callbacks.handle_asr_failure.await_count == 1)
        retained = receiver._retained_speech
        assert retained is not None
        accepted = retained.session.pcm_snapshot()
        path = receiver._recording_storage_path
        assert path is not None
        assert len(accepted) == 3200
        assert path.read_bytes() == accepted
        info = receiver.recovery_source_info(identity.speech_id)
        assert info is not None and info["audio_duration_ms"] == 100
    finally:
        if consumer is not None:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        await receiver.close()
        assert stream.closed

"""Spec307: real Session/Protocol continuous input and readiness boundaries."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from uuid import uuid4

import pytest

from jx_core.asr.protocol import FunAsrConnection
from jx_core.asr.session import PCM_FRAME_BYTES, AsrQueueFull, AsrSpeechSession
from test_asr import FakeSocket


async def noop(*_args: object) -> None:
    return None


@pytest.mark.asyncio
async def test_continuous_input_does_not_wait_on_thirty_second_task_final() -> None:
    class HeldFinalSocket(FakeSocket):
        async def send(self, message: str | bytes) -> None:
            if isinstance(message, str) and "finish-task" in message:
                await release.wait()
            await super().send(message)

    release = asyncio.Event()
    socket = HeldFinalSocket()

    async def factory(_url: str, _headers: dict[str, str]) -> HeldFinalSocket:
        return socket

    connection = FunAsrConnection(
        url="wss://owned.test", api_key="synthetic", socket_factory=factory
    )
    speech = AsrSpeechSession(
        speech_id=uuid4(), connection=connection, on_interim=noop, on_segment=noop
    )
    running = asyncio.create_task(speech.run())
    overflow = False
    try:
        for _ in range(400):
            try:
                speech.feed_pcm(b"\x01\0" * 1600)
            except AsrQueueFull:
                overflow = True
                break
            await asyncio.sleep(0)
        # Product assertion precedes any release/fixture cleanup.
        assert not overflow, "30s final blocked the continuing PCM input"
        assert socket.sent_actions == ["run-task"]
        finishing = asyncio.create_task(speech.finish())
        release.set()
        await asyncio.wait_for(finishing, 1)
        result = await asyncio.wait_for(running, 1)
        assert result.audio_duration_ms == 40000
        assert len(result.segments) == 1
        assert len(socket.audio_frames) == 400
    finally:
        release.set()
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await connection.close()


@pytest.mark.asyncio
async def test_provider_ready_precedes_first_pcm_without_business_callback_deadlock() -> None:
    socket = FakeSocket()
    register = asyncio.Event()

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        return socket

    async def started(*_args: object) -> None:
        await register.wait()

    connection = FunAsrConnection(
        url="wss://owned.test", api_key="synthetic", socket_factory=factory
    )
    speech = AsrSpeechSession(
        speech_id=uuid4(),
        connection=connection,
        on_interim=noop,
        on_segment=noop,
        on_segment_started=started,
    )
    running = asyncio.create_task(speech.run())
    try:
        await asyncio.wait_for(speech.wait_ready(), 0.1)
        assert socket.sent_actions == ["run-task"]
        assert not socket.audio_frames
        register.set()
        speech.feed_pcm(b"\x01\0" * 1600)
        await speech.finish()
        result = await asyncio.wait_for(running, 1)
        assert result.audio_duration_ms == 100
    finally:
        register.set()
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await connection.close()


def test_continuous_storage_has_atomic_authoritative_audio_limit() -> None:
    connection = FunAsrConnection(url="wss://owned.test", api_key="synthetic")
    speech = AsrSpeechSession(
        speech_id=uuid4(),
        connection=connection,
        on_interim=noop,
        on_segment=noop,
        max_audio_duration_ms=1000,
    )
    speech.feed_pcm(b"\x01\0" * 16000)
    before = speech.pcm_snapshot()
    with pytest.raises(AsrQueueFull):
        speech.feed_pcm(b"\x02\0" * 1600)
    assert speech.pcm_snapshot() == before and len(before) == 10 * PCM_FRAME_BYTES


@pytest.mark.asyncio
async def test_one_replay_starts_after_confirmed_audio_without_duplicate_text_or_time() -> None:
    import json

    confirmed = asyncio.Event()

    class FailAfterConfirmed(FakeSocket):
        async def send(self, message: str | bytes) -> None:
            if isinstance(message, bytes) and self.audio_frames:
                await confirmed.wait()
                raise OSError("owned synthetic disconnect")
            await super().send(message)

        async def recv(self) -> str | bytes:
            value = json.loads(await super().recv())
            if value["header"].get("event") == "result-generated":
                value["payload"]["output"]["sentence"]["text"] = "已确认"
            return json.dumps(value)

    class Remaining(FakeSocket):
        async def recv(self) -> str | bytes:
            value = json.loads(await super().recv())
            if value["header"].get("event") == "result-generated":
                value["payload"]["output"]["sentence"]["text"] = "后续内容"
            return json.dumps(value)

    sockets: list[FakeSocket] = []

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        value = FailAfterConfirmed() if not sockets else Remaining()
        sockets.append(value)
        return value

    async def interim(*_args: object) -> None:
        confirmed.set()

    connection = FunAsrConnection(
        url="wss://owned.test", api_key="synthetic", socket_factory=factory
    )
    session = AsrSpeechSession(
        speech_id=uuid4(), connection=connection, on_interim=interim, on_segment=noop
    )
    frames = [bytes([n, 0]) * 1600 for n in range(1, 5)]
    for frame in frames:
        session.feed_pcm(frame)
    await session.finish()
    try:
        result = await asyncio.wait_for(session.run(), 1)
        assert result.final_text == "已确认后续内容"
        assert result.audio_duration_ms == 400 and session.automatic_retries_used == 1
        assert len(sockets) == 2 and sockets[1].audio_frames == frames[1:]
        assert session.pcm_snapshot() == b"".join(frames)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_prepared_provider_waits_for_authoritative_commit() -> None:
    socket = FakeSocket()
    started: list[object] = []

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        return socket

    async def register(*args: object) -> None:
        started.extend(args)

    connection = FunAsrConnection(
        url="wss://owned.test", api_key="synthetic", socket_factory=factory
    )
    session = AsrSpeechSession(
        speech_id=uuid4(),
        connection=connection,
        on_interim=noop,
        on_segment=noop,
        on_segment_started=register,
        defer_registration=True,
    )
    running = asyncio.create_task(session.run())
    try:
        await asyncio.wait_for(session.wait_ready(), 0.2)
        session.feed_pcm(b"\x01\0" * 1600)
        await asyncio.sleep(0)
        assert not started and not socket.audio_frames and session.pcm_snapshot() == b""
        session.admit_input()
        session.feed_pcm(b"\x02\0" * 1600)
        await session.finish()
        result = await asyncio.wait_for(running, 1)
        assert started and result.audio_duration_ms == 100
        assert socket.audio_frames == [b"\x02\0" * 1600]
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await connection.close()


def test_configured_actual_model_is_readonly_metadata() -> None:
    from types import SimpleNamespace
    from typing import Any, cast

    from jx_core.asr.runtime import AsrRuntime
    from jx_core.config import Settings

    model = "qwen-audio-3.1-asr-flash-streaming"
    connection = FunAsrConnection(url="wss://owned.test", api_key="synthetic", model=model)
    runtime = AsrRuntime(
        settings=cast(Settings, SimpleNamespace(asr_model=model)), callbacks=cast(Any, object())
    )
    assert connection.model_id == runtime.model_id == model

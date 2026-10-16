"""Receive failures use the existing replay while retirement keeps priority."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import json
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from websockets.exceptions import ConnectionClosedError

from jx_core.asr.protocol import FunAsrConnection, FunAsrError
from jx_core.asr.session import AsrSpeechSession
from test_asr_retained_recovery import envelope, make_receiver


class Socket:
    def __init__(self, text: str = "恢复段。") -> None:
        self.events: asyncio.Queue[str | BaseException] = asyncio.Queue()
        self.frames: list[bytes] = []
        self.first_frame = asyncio.Event()
        self.text = text
        self.task_id = ""
        self.closed = False

    async def send(self, message: str | bytes) -> None:
        if isinstance(message, bytes):
            self.frames.append(message)
            self.first_frame.set()
            if len(self.frames) == 1:
                await self.events.put(
                    json.dumps(
                        {
                            "header": {"task_id": self.task_id, "event": "result-generated"},
                            "payload": {
                                "output": {
                                    "sentence": {
                                        "text": self.text,
                                        "sentence_end": True,
                                        "heartbeat": False,
                                        "sentence_id": 1,
                                        "begin_time": 0,
                                        "end_time": 100,
                                    }
                                }
                            },
                        }
                    )
                )
            return
        payload = cast(dict[str, Any], json.loads(message))
        self.task_id = str(payload["header"]["task_id"])
        event = "task-started" if payload["header"]["action"] == "run-task" else "task-finished"
        await self.events.put(json.dumps({"header": {"task_id": self.task_id, "event": event}}))

    async def recv(self) -> str | bytes:
        value = await self.events.get()
        if isinstance(value, BaseException):
            raise value
        return value

    async def close(self) -> None:
        self.closed = True


def connection(factory: Any) -> FunAsrConnection:
    return FunAsrConnection(
        url="wss://synthetic.invalid/asr",
        api_key="synthetic",
        model="qwen-audio-3.1-asr-flash-streaming",
        socket_factory=factory,
    )


async def wait(predicate: Any) -> None:
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ConnectionClosedError(None, None), OSError("controlled")])
async def test_receive_transport_errors_use_existing_stream_code(error: Exception) -> None:
    socket = Socket()
    await socket.events.put(error)
    provider = connection(None)
    with pytest.raises(FunAsrError) as caught:
        await provider._receive_owned(socket, 0)
    assert caught.value.code == "asr_stream_failed" and caught.value.__cause__ is error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [FunAsrError("asr_task_failed"), asyncio.CancelledError(), BaseException("controlled")]
)
async def test_receive_preserves_existing_domain_cancellation_and_base_errors(
    error: BaseException,
) -> None:
    socket = Socket()
    await socket.events.put(error)
    provider = connection(None)
    with pytest.raises(type(error)) as caught:
        await provider._receive_owned(socket, 0)
    assert caught.value is error


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ConnectionClosedError(None, None), OSError("controlled")])
async def test_retirement_during_failed_receive_wins_over_retry(error: Exception) -> None:
    provider = connection(None)

    class Retiring(Socket):
        async def recv(self) -> str | bytes:
            provider._retirement_epoch += 1
            raise error

    with pytest.raises(asyncio.CancelledError):
        await provider._receive_owned(Retiring(), 0)


@pytest.mark.asyncio
async def test_retired_owner_never_starts_receive() -> None:
    provider = connection(None)
    provider._retirement_epoch = 1
    socket = Socket()
    with pytest.raises(asyncio.CancelledError):
        await provider._receive_owned(socket, 0)
    assert socket.events.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [ConnectionClosedError, OSError])
async def test_actual_session_protocol_replays_only_unconfirmed_pcm_once(
    error_type: type[Exception],
) -> None:
    sockets: list[Socket] = []
    starts: list[int] = []
    interim: list[str] = []
    failures: list[str] = []

    async def factory(_url: str, _headers: dict[str, str]) -> Socket:
        socket = Socket("第一段。" if not sockets else "恢复段。")
        sockets.append(socket)
        return socket

    async def on_interim(_speech: Any, _number: int, text: str) -> None:
        interim.append(text)

    async def on_start(_speech: Any, number: int, _task: Any) -> None:
        starts.append(number)

    async def on_failure(_speech: Any, _number: int, _task: Any, code: str) -> None:
        failures.append(code)

    async def on_segment(*_args: Any) -> None:
        return None

    provider = connection(factory)
    session = AsrSpeechSession(
        speech_id=uuid4(),
        connection=provider,
        on_interim=on_interim,
        on_segment=on_segment,
        on_segment_started=on_start,
        on_segment_failed=on_failure,
        initial_text="暂停前。",
        initial_sample_count=1600,
        max_audio_duration_ms=500,
    )
    task = asyncio.create_task(session.run())
    try:
        await session.wait_ready(2)
        session.feed_pcm(b"\x01\x00" * 1600)
        await wait(lambda: interim == ["暂停前。第一段。"])
        error = (
            ConnectionClosedError(None, None) if error_type is ConnectionClosedError else OSError()
        )
        await sockets[0].events.put(error)
        await wait(lambda: len(starts) == 2 or task.done())
        assert not task.done(), "receive failure must remain within the original speech replay"
        session.feed_pcm(b"\x02\x00" * 6400)
        await session.finish()
        result = await asyncio.wait_for(task, 2)
        assert starts == [1, 2] and failures == ["asr_stream_failed"]
        assert session.automatic_retries_used == 1 and len(sockets) == 2
        assert result.final_text == "暂停前。第一段。恢复段。"
        assert result.audio_duration_ms == 600
        assert len(sockets[0].frames) == 1 and len(sockets[1].frames) == 4
        assert b"".join(sockets[1].frames) == b"\x02\x00" * 6400
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await provider.close()


@pytest.mark.asyncio
async def test_repeated_receive_failure_stops_at_one_replay_and_keeps_speech_attempt(
    tmp_path: Path,
) -> None:
    receiver, callbacks = make_receiver(tmp_path)
    identity = envelope(receiver)
    sockets: list[Socket] = []

    async def factory(_url: str, _headers: dict[str, str]) -> Socket:
        socket = Socket()
        sockets.append(socket)
        return socket

    receiver._connection = connection(factory)
    assert identity.speech_id is not None
    try:
        await receiver.start_speech(identity.speech_id, uuid4(), identity)
        session, task = receiver._speech, receiver._speech_task
        assert session is not None and task is not None
        await session.wait_ready(2)
        session.feed_pcm(b"\x01\x00" * 8000)
        await wait(lambda: callbacks.start_asr_segment.await_count == 1)
        await sockets[0].events.put(ConnectionClosedError(None, None))
        await wait(lambda: callbacks.start_asr_segment.await_count == 2 or task.done())
        assert not task.done(), "the first raw receive failure must retry"
        await sockets[1].events.put(ConnectionClosedError(None, None))
        await asyncio.wait_for(task, 2)
        assert len(sockets) == 2 and session.automatic_retries_used == 1
        assert callbacks.handle_asr_failure.await_count == 1
        assert callbacks.finalize_asr_speech.await_count == 0
        assert callbacks.fail_asr_segment.await_count == 2
        for call in (
            callbacks.start_asr_segment.await_args_list
            + callbacks.fail_asr_segment.await_args_list
            + callbacks.handle_asr_failure.await_args_list
        ):
            assert call.kwargs["envelope"] == identity
            assert call.kwargs["envelope"].attempt_no == identity.attempt_no
            assert call.kwargs["envelope"].speech_id == identity.speech_id
        assert callbacks.handle_asr_failure.await_args.kwargs["code"] == "asr_stream_failed"
        assert receiver._retained_speech is not None
        assert receiver._retained_speech.session.pcm_snapshot() == b"\x01\x00" * 8000
    finally:
        await receiver.close()

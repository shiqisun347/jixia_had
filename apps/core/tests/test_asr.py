from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

import jx_core.asr.runtime as asr_runtime_module
from jx_core.asr.protocol import FunAsrConnection, FunAsrError
from jx_core.asr.runtime import (  # pyright: ignore[reportPrivateUsage]
    AsrRuntime,
    MatchAudioReceiver,
    _user_id_from_identity,
)
from jx_core.asr.session import PCM_FRAME_BYTES, QUEUE_FRAMES, AsrQueueFull, AsrSpeechSession
from jx_core.config import Settings
from jx_core.data_capture.provider import ProviderCallCapture
from jx_core.runtime_identity import CallbackEnvelope


class FakeSocket:
    def __init__(self) -> None:
        self.events: asyncio.Queue[str] = asyncio.Queue()
        self.frame_no = 0
        self.audio_frames: list[bytes] = []
        self.sent_actions: list[str] = []
        self.closed = False

    async def send(self, message: str | bytes) -> None:
        if isinstance(message, bytes):
            self.audio_frames.append(message)
            self.frame_no += 1
            if self.frame_no in {1, 250, 301}:
                await self.events.put(
                    json.dumps(
                        {
                            "header": {
                                "task_id": self.current_task,
                                "event": "result-generated",
                            },
                            "payload": {
                                "output": {
                                    "sentence": {
                                        "text": "你好，",
                                        "sentence_end": self.frame_no == 1 or self.frame_no >= 250,
                                        "heartbeat": False,
                                        "sentence_id": self.frame_no,
                                        "begin_time": 0,
                                        "end_time": 100,
                                    }
                                }
                            },
                        }
                    )
                )
            return
        payload = json.loads(message)
        action = payload["header"]["action"]
        task_id = payload["header"]["task_id"]
        self.current_task = task_id
        self.sent_actions.append(action)
        if action == "run-task":
            self.frame_no = 0
            await self.events.put(
                json.dumps({"header": {"task_id": task_id, "event": "task-started"}})
            )
        elif action == "finish-task":
            await self.events.put(
                json.dumps({"header": {"task_id": task_id, "event": "task-finished"}})
            )

    async def recv(self) -> str | bytes:
        return await self.events.get()

    async def close(self) -> None:
        self.closed = True


class _HeartbeatSocket:
    def __init__(self) -> None:
        self.task_id: str | None = None
        self.finished = False
        self.started = False
        self.started_event_sent = False
        self.closed = False

    async def send(self, message: str | bytes) -> None:
        if isinstance(message, bytes):
            return
        payload = json.loads(message)
        self.task_id = payload["header"]["task_id"]
        action = payload["header"]["action"]
        if action == "run-task":
            self.started = True
        elif action == "finish-task":
            self.finished = True

    async def recv(self) -> str | bytes:
        await asyncio.sleep(0.005)
        task_id = self.task_id or str(uuid4())
        if not self.started:
            return json.dumps({"header": {"task_id": task_id, "event": "task-started"}})
        if not self.started_event_sent:
            self.started_event_sent = True
            return json.dumps({"header": {"task_id": task_id, "event": "task-started"}})
        if self.finished:
            event = "result-generated"
        else:
            event = "result-generated"
        return json.dumps(
            {
                "header": {"task_id": task_id, "event": event},
                "payload": {
                    "output": {
                        "sentence": {
                            "text": "",
                            "sentence_end": False,
                            "heartbeat": True,
                            "sentence_id": 0,
                            "begin_time": 0,
                            "end_time": 0,
                        }
                    }
                },
            }
        )

    async def close(self) -> None:
        self.closed = True


class _WrongTaskStartedSocket(_HeartbeatSocket):
    async def recv(self) -> str | bytes:
        await asyncio.sleep(0.005)
        return json.dumps({"header": {"task_id": str(uuid4()), "event": "task-started"}})


async def _never_ending_pcm():
    while True:
        await asyncio.sleep(0.005)
        yield b"\x00" * 3200


async def _one_pcm_frame():
    yield b"\x00" * 3200


class _SilentSocket:
    """Starts a task, then never emits a result or completion event."""

    def __init__(self) -> None:
        self.task_id: str | None = None
        self.started = asyncio.Event()
        self.closed = False

    async def send(self, message: str | bytes) -> None:
        if isinstance(message, bytes):
            return
        payload = json.loads(message)
        self.task_id = payload["header"]["task_id"]
        if payload["header"]["action"] == "run-task":
            self.started.set()

    async def recv(self) -> str | bytes:
        await self.started.wait()
        if self.task_id is not None:
            # The first receive after run-task is the mandatory startup event.
            task_id, self.task_id = self.task_id, None
            return json.dumps({"header": {"task_id": task_id, "event": "task-started"}})
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def close(self) -> None:
        self.closed = True


class _EarlyFinishedSocket(_SilentSocket):
    def __init__(self) -> None:
        super().__init__()
        self.receive_count = 0

    async def recv(self) -> str | bytes:
        await self.started.wait()
        self.receive_count += 1
        task_id = self.task_id
        if task_id is None:
            raise AssertionError("task id missing")
        event = "task-started" if self.receive_count == 1 else "task-finished"
        return json.dumps({"header": {"task_id": task_id, "event": event}})


@pytest.mark.asyncio
async def test_fun_asr_heartbeat_cannot_extend_audio_task_deadline() -> None:
    socket = _HeartbeatSocket()

    async def factory(_: str, __: dict[str, str]) -> _HeartbeatSocket:
        return socket

    connection = FunAsrConnection(
        url="wss://asr.test",
        api_key="secret",
        socket_factory=factory,
        start_timeout_seconds=0.1,
        final_timeout_seconds=0.02,
        task_timeout_seconds=0.05,
    )
    with pytest.raises(FunAsrError, match="asr_audio_timeout"):
        await connection.recognize_segment(
            _never_ending_pcm(),
            on_sentence=lambda _: _record([], ""),
        )
    assert socket.closed is True


@pytest.mark.asyncio
async def test_fun_asr_socket_factory_is_bounded_by_task_deadline() -> None:
    async def factory(_: str, __: dict[str, str]) -> FakeSocket:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    connection = FunAsrConnection(
        url="wss://asr.test",
        api_key="secret",
        socket_factory=factory,
        start_timeout_seconds=0.2,
        task_timeout_seconds=0.02,
    )
    with pytest.raises(FunAsrError, match="asr_start_timeout"):
        await connection.recognize_segment(
            _never_ending_pcm(), on_sentence=lambda _: _record([], "")
        )


@pytest.mark.asyncio
async def test_fun_asr_sentence_callback_is_bounded_and_invalidates_socket() -> None:
    socket = FakeSocket()

    async def factory(_: str, __: dict[str, str]) -> FakeSocket:
        return socket

    async def blocked_callback(_sentence: object) -> None:
        await asyncio.Event().wait()

    connection = FunAsrConnection(
        url="wss://asr.test",
        api_key="secret",
        socket_factory=factory,
        start_timeout_seconds=0.1,
        task_timeout_seconds=0.03,
    )
    with pytest.raises(FunAsrError, match="asr_callback_timeout"):
        await connection.recognize_segment(_one_pcm_frame(), on_sentence=blocked_callback)
    assert socket.closed is True


@pytest.mark.asyncio
async def test_fun_asr_heartbeat_cannot_extend_final_deadline() -> None:
    socket = _HeartbeatSocket()

    async def factory(_: str, __: dict[str, str]) -> _HeartbeatSocket:
        return socket

    async def one_chunk():
        yield b"\x00" * 3200

    connection = FunAsrConnection(
        url="wss://asr.test",
        api_key="secret",
        socket_factory=factory,
        start_timeout_seconds=0.1,
        final_timeout_seconds=0.02,
        task_timeout_seconds=0.2,
    )
    with pytest.raises(FunAsrError, match="asr_final_timeout"):
        await connection.recognize_segment(one_chunk(), on_sentence=lambda _: _record([], ""))
    assert socket.closed is True


@pytest.mark.asyncio
async def test_fun_asr_wrong_task_frames_cannot_extend_start_deadline() -> None:
    socket = _WrongTaskStartedSocket()

    async def factory(_: str, __: dict[str, str]) -> _WrongTaskStartedSocket:
        return socket

    connection = FunAsrConnection(
        url="wss://asr.test",
        api_key="secret",
        socket_factory=factory,
        start_timeout_seconds=0.03,
        task_timeout_seconds=0.1,
    )
    with pytest.raises(FunAsrError, match="asr_start_timeout"):
        await connection.recognize_segment(
            _never_ending_pcm(), on_sentence=lambda _: _record([], "")
        )
    assert socket.closed is True


@pytest.mark.asyncio
async def test_fun_asr_sends_multiple_pcm_frames_in_order() -> None:
    socket = FakeSocket()

    async def factory(_: str, __: dict[str, str]) -> FakeSocket:
        return socket

    connection = FunAsrConnection(url="wss://asr.test", api_key="secret", socket_factory=factory)
    first = b"\x01" * 3200
    second = b"\x02" * 3200

    async def two_chunks():
        yield first
        yield second

    result = await connection.recognize_segment(two_chunks(), on_sentence=lambda _: _record([], ""))

    assert result.final_text == "你好，"
    assert socket.audio_frames == [first, second]
    capture = connection.take_capture(result.task_id)
    assert capture is not None and capture.request is not None
    assert capture.request.payload["binary_summary"]["frame_count"] == 2
    assert capture.request.payload["binary_summary"]["total_bytes"] == 6400


@pytest.mark.asyncio
async def test_fun_asr_empty_chunks_still_finishes_task() -> None:
    socket = FakeSocket()

    async def factory(_: str, __: dict[str, str]) -> FakeSocket:
        return socket

    connection = FunAsrConnection(url="wss://asr.test", api_key="secret", socket_factory=factory)

    async def no_chunks():
        if False:
            yield b""

    result = await connection.recognize_segment(no_chunks(), on_sentence=lambda _: _record([], ""))

    assert result.final_text == ""
    assert socket.audio_frames == []
    assert socket.sent_actions == ["run-task", "finish-task"]


@pytest.mark.asyncio
async def test_fun_asr_sender_error_wakes_silent_receiver() -> None:
    socket = _SilentSocket()

    async def factory(_: str, __: dict[str, str]) -> _SilentSocket:
        return socket

    async def broken_chunks():
        yield b"\x00" * 3200
        raise RuntimeError("synthetic pcm source failed")

    connection = FunAsrConnection(
        url="wss://asr.test",
        api_key="secret",
        socket_factory=factory,
        start_timeout_seconds=0.2,
        task_timeout_seconds=1.0,
    )
    started = asyncio.get_running_loop().time()
    with pytest.raises(FunAsrError, match="asr_stream_failed"):
        await connection.recognize_segment(broken_chunks(), on_sentence=lambda _: _record([], ""))
    assert asyncio.get_running_loop().time() - started < 0.5
    assert socket.closed is True


@pytest.mark.asyncio
async def test_fun_asr_blocked_sender_is_bounded_and_socket_isolated() -> None:
    socket = _SilentSocket()

    async def factory(_: str, __: dict[str, str]) -> _SilentSocket:
        return socket

    async def blocked_chunks():
        yield b"\x00" * 3200
        await asyncio.Event().wait()

    connection = FunAsrConnection(
        url="wss://asr.test",
        api_key="secret",
        socket_factory=factory,
        start_timeout_seconds=0.2,
        task_timeout_seconds=0.05,
    )
    with pytest.raises(FunAsrError, match="asr_audio_timeout"):
        await connection.recognize_segment(blocked_chunks(), on_sentence=lambda _: _record([], ""))
    assert socket.closed is True


@pytest.mark.asyncio
async def test_fun_asr_rejects_task_finished_before_finish_task() -> None:
    socket = _EarlyFinishedSocket()

    async def factory(_: str, __: dict[str, str]) -> _EarlyFinishedSocket:
        return socket

    async def blocked_chunks():
        await asyncio.Event().wait()
        yield b"\x00" * 3200

    connection = FunAsrConnection(
        url="wss://asr.test",
        api_key="secret",
        socket_factory=factory,
        start_timeout_seconds=0.2,
        task_timeout_seconds=1.0,
    )
    with pytest.raises(FunAsrError, match="asr_protocol_invalid"):
        await connection.recognize_segment(blocked_chunks(), on_sentence=lambda _: _record([], ""))
    assert socket.closed is True


@pytest.mark.asyncio
async def test_fun_asr_protocol_waits_for_task_started_and_finishes() -> None:
    socket = FakeSocket()

    async def factory(_: str, __: dict[str, str]) -> FakeSocket:
        return socket

    seen: list[str] = []
    connection = FunAsrConnection(url="wss://asr.test", api_key="secret", socket_factory=factory)

    async def one_chunk():
        yield b"\x00" * 3200

    result = await connection.recognize_segment(
        one_chunk(),
        on_sentence=lambda sentence: _record(seen, sentence.text),
    )
    assert result.final_text == "你好，"
    assert seen == ["你好，"]
    assert socket.sent_actions == ["run-task", "finish-task"]
    capture = connection.take_capture(result.task_id)
    assert isinstance(capture, ProviderCallCapture)
    assert capture.request is not None
    assert capture.request.payload["binary_summary"] == {
        "direction": "platform_to_provider",
        "format": "pcm_s16le_16000_mono",
        "frame_count": 1,
        "total_bytes": 3200,
        "audio_duration_ms": 100,
    }
    assert "\\x00" not in str(capture.request.payload)
    assert capture.response is not None
    assert capture.response.payload["events"][-1]["header"]["event"] == "task-finished"


def test_fun_asr_capture_buffer_is_bounded_and_cleared_on_close() -> None:
    connection = FunAsrConnection(url="wss://asr.test", api_key="secret")
    task_ids = [uuid4() for _ in range(connection._MAX_PENDING_CAPTURES + 1)]

    for task_id in task_ids:
        connection._store_capture(task_id, ProviderCallCapture())

    assert connection.take_capture(task_ids[0]) is None
    assert connection.take_capture(task_ids[-1]) is not None


@pytest.mark.asyncio
async def test_fun_asr_reconnects_after_active_socket_fails() -> None:
    class FailingSocket(FakeSocket):
        def __init__(self) -> None:
            super().__init__()
            self.recv_count = 0

        async def recv(self) -> str | bytes:
            self.recv_count += 1
            if self.recv_count == 2:
                raise ConnectionError("socket dropped")
            return await super().recv()

    failed_socket = FailingSocket()
    recovered_socket = FakeSocket()
    sockets = [failed_socket, recovered_socket]
    factory_calls = 0

    async def factory(_: str, __: dict[str, str]) -> FakeSocket:
        nonlocal factory_calls
        socket = sockets[factory_calls]
        factory_calls += 1
        return socket

    connection = FunAsrConnection(url="wss://asr.test", api_key="secret", socket_factory=factory)

    async def one_chunk():
        yield b"\x00" * 3200

    with pytest.raises(ConnectionError):
        await connection.recognize_segment(one_chunk(), on_sentence=lambda *_args: _record([], ""))

    result = await connection.recognize_segment(
        one_chunk(), on_sentence=lambda *_args: _record([], "")
    )

    assert failed_socket.closed is True
    assert factory_calls == 2
    assert result.final_text == "你好，"


@pytest.mark.asyncio
async def test_fun_asr_reuses_socket_after_normal_segments() -> None:
    socket = FakeSocket()
    factory_calls = 0

    async def factory(_: str, __: dict[str, str]) -> FakeSocket:
        nonlocal factory_calls
        factory_calls += 1
        return socket

    connection = FunAsrConnection(url="wss://asr.test", api_key="secret", socket_factory=factory)

    async def one_chunk():
        yield b"\x00" * 3200

    await connection.recognize_segment(one_chunk(), on_sentence=lambda *_args: _record([], ""))
    await connection.recognize_segment(one_chunk(), on_sentence=lambda *_args: _record([], ""))

    assert factory_calls == 1
    assert socket.closed is False


@pytest.mark.asyncio
async def test_fun_asr_cancellation_invalidates_active_socket() -> None:
    class HangingSocket(FakeSocket):
        def __init__(self) -> None:
            super().__init__()
            self.recv_count = 0
            self.wait_forever = asyncio.Event()

        async def recv(self) -> str | bytes:
            self.recv_count += 1
            if self.recv_count > 1:
                await self.wait_forever.wait()
            return await super().recv()

    socket = HangingSocket()

    async def factory(_: str, __: dict[str, str]) -> FakeSocket:
        return socket

    started = asyncio.Event()
    connection = FunAsrConnection(url="wss://asr.test", api_key="secret", socket_factory=factory)

    async def one_chunk():
        yield b"\x00" * 3200

    task = asyncio.create_task(
        connection.recognize_segment(
            one_chunk(),
            on_sentence=lambda *_args: _record([], ""),
            on_started=started.set,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert socket.closed is True


async def _record(target: list[str], value: str) -> None:
    target.append(value)


@pytest.mark.asyncio
async def test_asr_speech_session_rotates_after_bounded_task() -> None:
    socket = FakeSocket()

    async def factory(_: str, __: dict[str, str]) -> FakeSocket:
        return socket

    interim: list[str] = []
    segments: list[int] = []
    connection = FunAsrConnection(url="wss://asr.test", api_key="secret", socket_factory=factory)
    speech = AsrSpeechSession(
        speech_id=UUID("00000000-0000-0000-0000-000000000001"),
        connection=connection,
        on_interim=lambda _speech, _segment, text: _record(interim, text),
        on_segment=lambda _speech, segment, _result, _samples: _record_int(segments, segment),
    )

    async def feed() -> None:
        for _ in range(301):
            while True:
                try:
                    speech.feed_pcm(b"\x00" * 3200)
                    break
                except AsrQueueFull:
                    await asyncio.sleep(0)
        await speech.finish()

    result, _ = await asyncio.gather(speech.run(), feed())
    assert len(result.segments) == 2
    assert segments == [1, 2]
    assert result.audio_duration_ms == 301 * 100


@pytest.mark.asyncio
async def test_asr_session_resumes_same_speech_with_contiguous_segment_and_prefix() -> None:
    socket = FakeSocket()

    async def factory(_: str, __: dict[str, str]) -> FakeSocket:
        return socket

    segments: list[int] = []
    connection = FunAsrConnection(url="wss://asr.test", api_key="secret", socket_factory=factory)
    speech = AsrSpeechSession(
        speech_id=UUID("00000000-0000-0000-0000-000000000002"),
        connection=connection,
        on_interim=lambda *_args: _record([], ""),
        on_segment=lambda _speech, segment, _result, _samples: _record_int(segments, segment),
        start_segment_no=2,
        initial_text="前缀",
        initial_sample_count=1600,
    )
    speech.feed_pcm(b"\x00" * 3200)
    await speech.finish()
    result = await speech.run()
    assert result.final_text == "前缀你好，"
    assert result.last_segment_no == 3
    assert segments == [3]
    assert result.audio_duration_ms == 200


@pytest.mark.asyncio
async def test_asr_session_rejects_empty_audio_before_provider_request() -> None:
    provider_calls = 0

    async def factory(_: str, __: dict[str, str]) -> FakeSocket:
        nonlocal provider_calls
        provider_calls += 1
        return FakeSocket()

    connection = FunAsrConnection(url="wss://asr.test", api_key="secret", socket_factory=factory)
    speech = AsrSpeechSession(
        speech_id=uuid4(),
        connection=connection,
        on_interim=lambda *_args: _record([], ""),
        on_segment=lambda *_args: _record_int([], 0),
    )
    await speech.finish()

    with pytest.raises(FunAsrError, match="asr_empty_audio"):
        await speech.run()

    assert provider_calls == 0


def test_asr_session_rejects_oversized_pcm_before_growing_pending_buffer() -> None:
    connection = FunAsrConnection(url="wss://asr.test", api_key="secret")
    speech = AsrSpeechSession(
        speech_id=uuid4(),
        connection=connection,
        on_interim=lambda *_args: _record([], ""),
        on_segment=lambda *_args: _record_int([], 0),
    )

    with pytest.raises(AsrQueueFull, match="asr_pcm_queue_full"):
        speech.feed_pcm(b"\x00" * (PCM_FRAME_BYTES * (QUEUE_FRAMES + 1)))

    assert len(speech._pending) == 0
    assert speech._queue.qsize() == 0


@pytest.mark.asyncio
async def test_asr_capture_persistence_failure_does_not_fail_segment() -> None:
    socket = FakeSocket()

    async def factory(_: str, __: dict[str, str]) -> FakeSocket:
        return socket

    persisted_segments: list[int] = []

    async def failing_capture(_task_id: UUID, _capture: ProviderCallCapture) -> None:
        raise RuntimeError("capture database unavailable")

    connection = FunAsrConnection(url="wss://asr.test", api_key="secret", socket_factory=factory)
    speech = AsrSpeechSession(
        speech_id=uuid4(),
        connection=connection,
        on_interim=lambda *_args: _record([], ""),
        on_segment=lambda _speech, segment, _result, _samples: _record_int(
            persisted_segments, segment
        ),
        on_capture=failing_capture,
    )
    speech.feed_pcm(b"\x00" * 3200)
    await speech.finish()

    result = await speech.run()

    assert result.final_text == "你好，"
    assert persisted_segments == [1]


def test_asr_resolves_current_livekit_human_identity() -> None:
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    identity = (
        "jx-human-22222222-2222-2222-2222-222222222222-"
        "11111111-1111-1111-1111-111111111111-3-abcdef12"
    )

    assert _user_id_from_identity(identity) == user_id


def test_asr_keeps_legacy_identity_compatibility_and_rejects_other_participants() -> None:
    user_id = UUID("11111111-1111-1111-1111-111111111111")

    assert _user_id_from_identity(f"user-{user_id}") == user_id
    assert _user_id_from_identity("jx-agent-22222222-2222-2222-2222-222222222222-abcd") is None
    assert _user_id_from_identity("jx-human-not-a-valid-identity") is None


async def _record_int(target: list[int], value: int) -> None:
    target.append(value)


class RuntimeCallbacks:
    def __init__(self) -> None:
        self.segments: list[int] = []
        self.finalized: list[dict[str, Any]] = []
        self.failures: list[tuple[object, ...]] = []

    async def publish_asr_interim(self, **_kwargs: object) -> None:
        return

    async def persist_asr_segment(self, **payload: Any) -> None:
        self.segments.append(int(payload["segment_no"]))

    async def finalize_asr_speech(self, **payload: Any) -> object:
        self.finalized.append(payload)
        return object()

    async def handle_asr_failure(self, **_kwargs: object) -> None:
        self.failures.append(tuple(_kwargs.values()))


@pytest.mark.asyncio
async def test_receiver_pause_resumes_same_business_speech_without_early_final() -> None:
    socket = FakeSocket()

    async def factory(_: str, __: dict[str, str]) -> FakeSocket:
        return socket

    connection = FunAsrConnection(url="wss://asr.test", api_key="secret", socket_factory=factory)
    callbacks = RuntimeCallbacks()
    receiver = MatchAudioReceiver(
        match_id=uuid4(),
        settings=cast(Settings, object()),
        callbacks=callbacks,
    )
    receiver._room = cast(Any, object())
    receiver._connection = connection
    speech_id = uuid4()
    user_id = uuid4()
    envelope = CallbackEnvelope(
        match_id=receiver.match_id,
        speech_id=speech_id,
        attempt_no=1,
        generation_id=None,
        connection_epoch=1,
        context_version=0,
        opportunity_id=None,
        opportunity_generation=None,
    )

    resumed_envelope = CallbackEnvelope(
        match_id=receiver.match_id,
        speech_id=speech_id,
        attempt_no=1,
        generation_id=None,
        connection_epoch=2,
        context_version=0,
        opportunity_id=None,
        opportunity_generation=None,
    )
    await receiver.start_speech(speech_id, user_id, envelope)
    assert receiver._speech is not None
    receiver._speech.feed_pcm(b"\x00" * 3200)
    await receiver.pause_speech(speech_id)
    assert callbacks.finalized == []

    await receiver.start_speech(speech_id, user_id, resumed_envelope)
    assert receiver._speech is not None
    receiver._speech.feed_pcm(b"\x00" * 3200)
    await receiver.finish_speech(speech_id)

    assert callbacks.segments == [1, 2]
    assert len(callbacks.finalized) == 1
    assert callbacks.finalized[0]["envelope"].speech_id == speech_id
    assert callbacks.finalized[0]["envelope"].connection_epoch == 2
    assert callbacks.finalized[0]["final_text"] == "你好，你好，"
    assert callbacks.finalized[0]["audio_duration_ms"] == 200
    assert callbacks.failures == []


@pytest.mark.asyncio
async def test_receiver_pause_does_not_report_provider_close_failure() -> None:
    callbacks = RuntimeCallbacks()
    receiver = MatchAudioReceiver(
        match_id=uuid4(),
        settings=cast(Settings, object()),
        callbacks=callbacks,
    )
    speech_id = uuid4()

    class FailingSession:
        def __init__(self) -> None:
            self.speech_id = speech_id

        async def finish(self) -> None:
            return

        async def run(self) -> None:
            raise FunAsrError("asr_task_failed")

        def checkpoint(self) -> SimpleNamespace:
            return SimpleNamespace(
                final_text="已确认前缀",
                audio_duration_ms=2_000,
                last_segment_no=3,
                first_interim_latency_ms=120,
            )

    session = cast(Any, FailingSession())
    receiver._speech = session
    envelope = CallbackEnvelope(
        match_id=receiver.match_id,
        speech_id=speech_id,
        attempt_no=1,
        generation_id=None,
        connection_epoch=1,
        context_version=0,
        opportunity_id=None,
        opportunity_generation=None,
    )
    receiver._speech_task = asyncio.create_task(receiver._run_speech(session, envelope))

    await receiver.pause_speech(speech_id)

    assert callbacks.failures == []
    assert receiver._paused_speech is not None
    assert receiver._paused_speech.final_text == "已确认前缀"
    assert receiver._paused_speech.last_segment_no == 3


@pytest.mark.asyncio
async def test_receiver_close_bounds_tasks_that_ignore_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asr_runtime_module, "ASR_TASK_CANCEL_TIMEOUT_SECONDS", 0.01)
    release = asyncio.Event()

    async def stubborn_task() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    receiver = MatchAudioReceiver(
        match_id=uuid4(),
        settings=cast(Settings, object()),
        callbacks=RuntimeCallbacks(),
    )
    speech_task = asyncio.create_task(stubborn_task())
    track_task = asyncio.create_task(stubborn_task())
    receiver._speech_task = speech_task
    receiver._track_tasks.add(track_task)
    await asyncio.sleep(0)

    await asyncio.wait_for(receiver.close(), timeout=0.2)

    assert receiver._closed is True
    assert not speech_task.done()
    assert not track_task.done()
    release.set()
    await asyncio.gather(speech_task, track_task)


@pytest.mark.asyncio
async def test_receiver_close_detaches_cancellation_resistant_native_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asr_runtime_module, "ASR_CLEANUP_TIMEOUT_SECONDS", 0.01)
    release = asyncio.Event()

    class HangingResource:
        async def close(self) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()

        async def disconnect(self) -> None:
            await self.close()

    receiver = MatchAudioReceiver(
        match_id=uuid4(),
        settings=cast(Settings, object()),
        callbacks=RuntimeCallbacks(),
    )
    receiver._connection = cast(Any, HangingResource())
    receiver._room = cast(Any, HangingResource())

    await asyncio.wait_for(receiver.close(), timeout=0.2)

    assert receiver._closed is True
    release.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_asr_runtime_terminal_tombstone_rejects_late_receiver_creation() -> None:
    runtime = AsrRuntime(settings=cast(Settings, object()), callbacks=RuntimeCallbacks())
    match_id = uuid4()

    await runtime.close_match(match_id)

    with pytest.raises(FunAsrError, match="asr_receiver_closed"):
        await runtime.ensure_match(match_id)


def test_human_recording_publishes_atomically_and_reset_discards_spool(tmp_path) -> None:
    receiver = MatchAudioReceiver(
        match_id=uuid4(),
        settings=cast(Settings, SimpleNamespace(agent_audio_storage_dir=str(tmp_path))),
        callbacks=RuntimeCallbacks(),
    )
    speech_id = uuid4()
    receiver._start_recording(speech_id, uuid4())
    receiver._write_recording(b"\x01\x02" * 1600)
    storage_path = receiver._publish_recording()

    assert storage_path is not None
    assert storage_path.endswith(f"{speech_id}.pcm")
    assert Path(storage_path).read_bytes() == b"\x01\x02" * 1600

    next_speech_id = uuid4()
    receiver._start_recording(next_speech_id, uuid4())
    spool_path = receiver._recording_spool_path
    assert spool_path is not None and spool_path.exists()
    receiver._discard_recording()
    assert not spool_path.exists()

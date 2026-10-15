"""Reusable Qwen-Audio-TTS duplex stream adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from websockets.asyncio.client import connect

from ..data_capture.provider import CaptureEventBuffer, ProviderCallCapture
from ..provider_cleanup import close_provider_socket_bounded


class TtsProviderError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class WebSocketLike(Protocol):
    async def send(self, message: str | bytes) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


SocketFactory = Callable[[str, dict[str, str]], Awaitable[WebSocketLike]]
AudioCallback = Callable[[bytes], Awaitable[None]]
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
TTS_START_INTERVAL_SECONDS = 0.35
TTS_TASK_TIMEOUT_SECONDS = 180.0
TTS_MAX_AUDIO_BYTES = 16 * 1024 * 1024
TTS_MAX_CAPTURE_EVENTS = 128


class TtsStartRateLimiter:
    """Keep task submissions below the provider's three-RPS account limit."""

    def __init__(self, interval_seconds: float = TTS_START_INTERVAL_SECONDS) -> None:
        self._interval_seconds = interval_seconds
        self._next_start = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            delay = self._next_start - monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_start = monotonic() + self._interval_seconds


_DEFAULT_START_RATE_LIMITER = TtsStartRateLimiter()


@dataclass(frozen=True, slots=True)
class TtsStreamResult:
    task_id: UUID
    byte_count: int
    first_audio_latency_ms: int
    completed_latency_ms: int


async def _socket_factory(url: str, headers: dict[str, str]) -> WebSocketLike:
    return cast(
        WebSocketLike,
        await connect(url, additional_headers=headers, max_size=2**22, proxy=None),
    )


class QwenTtsConnection:
    _CLOSE_TIMEOUT_SECONDS = 2.0

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        model: str = "qwen-audio-3.0-tts-flash",
        workspace_id: str | None = None,
        socket_factory: SocketFactory = _socket_factory,
        timeout_seconds: float = 10.0,
        task_timeout_seconds: float = TTS_TASK_TIMEOUT_SECONDS,
        max_audio_bytes: int = TTS_MAX_AUDIO_BYTES,
        start_rate_limiter: TtsStartRateLimiter = _DEFAULT_START_RATE_LIMITER,
    ) -> None:
        self._url = url
        self._headers = {"Authorization": f"Bearer {api_key}", "user-agent": "jx-core/0.1"}
        if workspace_id:
            self._headers["X-DashScope-WorkSpace"] = workspace_id
        self._model = model
        self._socket_factory = socket_factory
        self._timeout_seconds = timeout_seconds
        self._task_timeout_seconds = task_timeout_seconds
        self._max_audio_bytes = max_audio_bytes
        self._start_rate_limiter = start_rate_limiter
        self._socket: WebSocketLike | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            await self._close_socket_bounded(socket)

    async def synthesize(
        self,
        chunks: AsyncIterable[str],
        *,
        voice: str,
        rate: float,
        on_audio: AudioCallback,
        on_event: EventCallback | None = None,
        task_id: UUID | None = None,
        capture: ProviderCallCapture | None = None,
    ) -> TtsStreamResult:
        async with self._lock:
            current_task = task_id or uuid4()
            task_deadline = monotonic() + self._task_timeout_seconds
            socket = await self._ensure_socket(timeout=self._remaining(task_deadline))
            sent_events = capture.event_buffer() if capture is not None else []
            received_events = capture.event_buffer() if capture is not None else []
            try:
                await asyncio.wait_for(
                    self._start_rate_limiter.wait(), timeout=self._remaining(task_deadline)
                )
                run_task = self._run_task(current_task, voice, rate)
                _record_event(sent_events, run_task)
                await self._send(socket, run_task, timeout=self._remaining(task_deadline))
                started_event = await self._wait_started(
                    socket, current_task, deadline=task_deadline
                )
                _record_event(received_events, started_event)
            except TimeoutError as error:
                await self._discard_socket(socket)
                _capture_tts(capture, self._url, sent_events, received_events, 0, current_task)
                raise TtsProviderError("tts_task_timeout") from error
            except BaseException:
                await self._discard_socket(socket)
                _capture_tts(capture, self._url, sent_events, received_events, 0, current_task)
                raise
            started = monotonic()
            first_audio_ms: int | None = None
            byte_count = 0

            async def send_text() -> None:
                sent = False
                async for chunk in chunks:
                    if not chunk:
                        continue
                    sent = True
                    payload: dict[str, Any] = {
                        "header": {
                            "action": "continue-task",
                            "task_id": str(current_task),
                            "streaming": "duplex",
                        },
                        "payload": {"input": {"text": chunk}},
                    }
                    _record_event(sent_events, payload)
                    await self._send(socket, payload, timeout=self._remaining(task_deadline))
                if not sent:
                    raise TtsProviderError("tts_text_empty")
                payload = {
                    "header": {
                        "action": "finish-task",
                        "task_id": str(current_task),
                        "streaming": "duplex",
                    },
                    "payload": {"input": {}},
                }
                _record_event(sent_events, payload)
                await self._send(socket, payload, timeout=self._remaining(task_deadline))

            sender = asyncio.create_task(send_text(), name=f"tts-text-{current_task}")
            try:
                while True:
                    try:
                        timeout = min(self._timeout_seconds, self._remaining(task_deadline))
                        if timeout <= 0:
                            raise TtsProviderError("tts_task_timeout")
                        raw = await asyncio.wait_for(socket.recv(), timeout=timeout)
                    except TimeoutError as error:
                        code = (
                            "tts_task_timeout"
                            if self._remaining(task_deadline) <= 0
                            else "tts_stream_stalled"
                        )
                        raise TtsProviderError(code) from error
                    if isinstance(raw, bytes):
                        if first_audio_ms is None:
                            first_audio_ms = int((monotonic() - started) * 1000)
                        byte_count += len(raw)
                        if byte_count > self._max_audio_bytes:
                            raise TtsProviderError("tts_audio_too_large")
                        await on_audio(raw)
                        continue
                    event = _event(raw)
                    header = _header(event)
                    if header.get("task_id") != str(current_task):
                        continue
                    if header.get("event") == "task-failed":
                        await self._discard_socket(socket)
                        _record_event(received_events, event)
                        raise TtsProviderError(_task_failure_code(header))
                    _record_event(received_events, event)
                    if on_event is not None:
                        await on_event(event)
                    if header.get("event") == "task-finished":
                        break
                await sender
            except asyncio.CancelledError:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
                await self._cancel(socket, current_task)
                await self._discard_socket(socket)
                _capture_tts(
                    capture, self._url, sent_events, received_events, byte_count, current_task
                )
                raise
            except Exception:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
                await self._discard_socket(socket)
                _capture_tts(
                    capture, self._url, sent_events, received_events, byte_count, current_task
                )
                raise
            if first_audio_ms is None or byte_count == 0:
                raise TtsProviderError("tts_audio_empty")
            result = TtsStreamResult(
                task_id=current_task,
                byte_count=byte_count,
                first_audio_latency_ms=first_audio_ms,
                completed_latency_ms=int((monotonic() - started) * 1000),
            )
            _capture_tts(capture, self._url, sent_events, received_events, byte_count, current_task)
            return result

    @staticmethod
    def _remaining(deadline: float) -> float:
        return max(0.0, deadline - monotonic())

    async def _ensure_socket(self, *, timeout: float | None = None) -> WebSocketLike:
        if self._socket is None:
            try:
                self._socket = await asyncio.wait_for(
                    self._socket_factory(self._url, dict(self._headers)),
                    timeout=timeout if timeout is not None else self._timeout_seconds,
                )
            except Exception as error:
                raise TtsProviderError("tts_connection_failed") from error
        return self._socket

    async def _wait_started(
        self, socket: WebSocketLike, task_id: UUID, *, deadline: float
    ) -> dict[str, Any]:
        while True:
            try:
                timeout = min(self._timeout_seconds, self._remaining(deadline))
                if timeout <= 0:
                    raise TtsProviderError("tts_task_timeout")
                raw = await asyncio.wait_for(socket.recv(), timeout=timeout)
            except TimeoutError as error:
                code = "tts_task_timeout" if self._remaining(deadline) <= 0 else "tts_start_timeout"
                raise TtsProviderError(code) from error
            if isinstance(raw, bytes):
                continue
            event = _event(raw)
            header = _header(event)
            if header.get("task_id") != str(task_id):
                continue
            if header.get("event") == "task-failed":
                raise TtsProviderError(_task_failure_code(header))
            if header.get("event") == "task-started":
                return event

    async def _send(
        self, socket: WebSocketLike, payload: dict[str, Any], *, timeout: float | None = None
    ) -> None:
        try:
            await asyncio.wait_for(
                socket.send(json.dumps(payload, ensure_ascii=False)),
                timeout=timeout if timeout is not None else self._timeout_seconds,
            )
        except TtsProviderError:
            raise
        except Exception as error:
            raise TtsProviderError("tts_send_failed") from error

    async def _cancel(self, socket: WebSocketLike, task_id: UUID) -> None:
        try:
            await self._send(
                socket,
                {
                    "header": {
                        "action": "finish-task",
                        "task_id": str(task_id),
                        "streaming": "duplex",
                    },
                    "payload": {"input": {"directive": "cancel"}},
                },
            )
        except TtsProviderError:
            return

    async def _discard_socket(self, socket: WebSocketLike) -> None:
        if self._socket is socket:
            self._socket = None
        await self._close_socket_bounded(socket)

    @classmethod
    async def _close_socket_bounded(cls, socket: WebSocketLike) -> None:
        """Release an unresponsive provider socket without blocking cleanup."""

        await close_provider_socket_bounded(
            socket, timeout_seconds=cls._CLOSE_TIMEOUT_SECONDS, task_name="tts-socket-close"
        )

    def _run_task(self, task_id: UUID, voice: str, rate: float) -> dict[str, Any]:
        return {
            "header": {
                "action": "run-task",
                "task_id": str(task_id),
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "model": self._model,
                "parameters": {
                    "text_type": "PlainText",
                    "voice": voice,
                    "format": "opus",
                    "sample_rate": 24000,
                    "bit_rate": 32,
                    "rate": rate,
                    "pitch": 1.0,
                    "volume": 50,
                    "enable_ssml": False,
                },
                "input": {},
            },
        }


def _task_failure_code(header: dict[str, Any]) -> str:
    """Map provider failure classes without exposing the provider payload."""

    if header.get("error_code") == "InvalidParameter":
        return "tts_parameter_invalid"
    return "tts_provider_failed"


def _event(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise TtsProviderError("tts_protocol_invalid") from error
    if not isinstance(value, dict):
        raise TtsProviderError("tts_protocol_invalid")
    return cast(dict[str, Any], value)


def _header(event: dict[str, Any]) -> dict[str, Any]:
    header = event.get("header")
    if not isinstance(header, dict):
        raise TtsProviderError("tts_protocol_invalid")
    return cast(dict[str, Any], header)


def _record_event(events: list[dict[str, Any]], event: dict[str, Any]) -> None:
    """Keep captures diagnostic without retaining an unbounded provider stream."""

    if isinstance(events, CaptureEventBuffer):
        events.append(event)
        return
    if len(events) < TTS_MAX_CAPTURE_EVENTS:
        events.append(event)
        return
    # Preserve the first half and the latest half; the terminal event is kept
    # when the provider sends a long heartbeat stream.
    tail_start = TTS_MAX_CAPTURE_EVENTS // 2
    events[tail_start - 1 :] = events[tail_start:]
    events[-1] = event


def _capture_tts(
    capture: ProviderCallCapture | None,
    url: str,
    sent_events: list[dict[str, Any]],
    received_events: list[dict[str, Any]],
    byte_count: int,
    task_id: UUID,
) -> None:
    if capture is None:
        return
    capture.provider_request_id = str(task_id)
    capture.capture_request(
        transport="WEBSOCKET",
        url=url,
        body={"messages": sent_events},
    )
    capture.capture_response(
        transport="WEBSOCKET",
        url=url,
        body={"task_id": str(task_id)},
        events=received_events,
        binary_summary={
            "direction": "provider_to_platform",
            "format": "ogg_opus",
            "total_bytes": byte_count,
        },
    )


__all__ = [
    "QwenTtsConnection",
    "TtsProviderError",
    "TtsStartRateLimiter",
    "TtsStreamResult",
]

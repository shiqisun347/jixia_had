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
    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        model: str = "qwen-audio-3.0-tts-flash",
        workspace_id: str | None = None,
        socket_factory: SocketFactory = _socket_factory,
        timeout_seconds: float = 10.0,
        start_rate_limiter: TtsStartRateLimiter = _DEFAULT_START_RATE_LIMITER,
    ) -> None:
        self._url = url
        self._headers = {"Authorization": f"Bearer {api_key}", "user-agent": "jx-core/0.1"}
        if workspace_id:
            self._headers["X-DashScope-WorkSpace"] = workspace_id
        self._model = model
        self._socket_factory = socket_factory
        self._timeout_seconds = timeout_seconds
        self._start_rate_limiter = start_rate_limiter
        self._socket: WebSocketLike | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            await socket.close()

    async def synthesize(
        self,
        chunks: AsyncIterable[str],
        *,
        voice: str,
        rate: float,
        on_audio: AudioCallback,
        on_event: EventCallback | None = None,
        task_id: UUID | None = None,
    ) -> TtsStreamResult:
        async with self._lock:
            current_task = task_id or uuid4()
            socket = await self._ensure_socket()
            await self._start_rate_limiter.wait()
            await self._send(socket, self._run_task(current_task, voice, rate))
            await self._wait_started(socket, current_task)
            started = monotonic()
            first_audio_ms: int | None = None
            byte_count = 0

            async def send_text() -> None:
                sent = False
                async for chunk in chunks:
                    if not chunk:
                        continue
                    sent = True
                    await self._send(
                        socket,
                        {
                            "header": {
                                "action": "continue-task",
                                "task_id": str(current_task),
                                "streaming": "duplex",
                            },
                            "payload": {"input": {"text": chunk}},
                        },
                    )
                if not sent:
                    raise TtsProviderError("tts_text_empty")
                await self._send(
                    socket,
                    {
                        "header": {
                            "action": "finish-task",
                            "task_id": str(current_task),
                            "streaming": "duplex",
                        },
                        "payload": {"input": {}},
                    },
                )

            sender = asyncio.create_task(send_text(), name=f"tts-text-{current_task}")
            try:
                while True:
                    try:
                        raw = await asyncio.wait_for(socket.recv(), timeout=self._timeout_seconds)
                    except TimeoutError as error:
                        raise TtsProviderError("tts_stream_stalled") from error
                    if isinstance(raw, bytes):
                        if first_audio_ms is None:
                            first_audio_ms = int((monotonic() - started) * 1000)
                        byte_count += len(raw)
                        await on_audio(raw)
                        continue
                    event = _event(raw)
                    header = _header(event)
                    if header.get("task_id") != str(current_task):
                        continue
                    if header.get("event") == "task-failed":
                        self._socket = None
                        await socket.close()
                        raise TtsProviderError("tts_provider_failed")
                    if on_event is not None:
                        await on_event(event)
                    if header.get("event") == "task-finished":
                        break
                await sender
            except asyncio.CancelledError:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
                await self._cancel(socket, current_task)
                raise
            except Exception:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
                raise
            if first_audio_ms is None or byte_count == 0:
                raise TtsProviderError("tts_audio_empty")
            return TtsStreamResult(
                task_id=current_task,
                byte_count=byte_count,
                first_audio_latency_ms=first_audio_ms,
                completed_latency_ms=int((monotonic() - started) * 1000),
            )

    async def _ensure_socket(self) -> WebSocketLike:
        if self._socket is None:
            try:
                self._socket = await asyncio.wait_for(
                    self._socket_factory(self._url, dict(self._headers)),
                    timeout=self._timeout_seconds,
                )
            except Exception as error:
                raise TtsProviderError("tts_connection_failed") from error
        return self._socket

    async def _wait_started(self, socket: WebSocketLike, task_id: UUID) -> None:
        while True:
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=self._timeout_seconds)
            except TimeoutError as error:
                raise TtsProviderError("tts_start_timeout") from error
            if isinstance(raw, bytes):
                continue
            event = _event(raw)
            header = _header(event)
            if header.get("task_id") != str(task_id):
                continue
            if header.get("event") == "task-failed":
                raise TtsProviderError("tts_provider_failed")
            if header.get("event") == "task-started":
                return

    async def _send(self, socket: WebSocketLike, payload: dict[str, Any]) -> None:
        try:
            await asyncio.wait_for(
                socket.send(json.dumps(payload, ensure_ascii=False)),
                timeout=self._timeout_seconds,
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


__all__ = [
    "QwenTtsConnection",
    "TtsProviderError",
    "TtsStartRateLimiter",
    "TtsStreamResult",
]

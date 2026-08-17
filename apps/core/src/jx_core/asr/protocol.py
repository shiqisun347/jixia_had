"""Thin, recorded-frame-testable Fun-ASR duplex WebSocket adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from websockets.asyncio.client import connect


class FunAsrError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class WebSocketLike(Protocol):
    async def send(self, message: str | bytes) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


SocketFactory = Callable[[str, dict[str, str]], Awaitable[WebSocketLike]]


@dataclass(frozen=True, slots=True)
class FunAsrSentence:
    task_id: UUID
    text: str
    sentence_end: bool
    heartbeat: bool
    begin_time_ms: int
    end_time_ms: int
    sentence_id: int


@dataclass(frozen=True, slots=True)
class SegmentResult:
    task_id: UUID
    final_text: str
    first_interim_latency_ms: int | None
    final_latency_ms: int


SentenceCallback = Callable[[FunAsrSentence], Awaitable[None]]
StartedCallback = Callable[[], None]


async def _default_socket_factory(url: str, headers: dict[str, str]) -> WebSocketLike:
    return cast(
        WebSocketLike,
        await connect(url, additional_headers=headers, max_size=2**20, proxy=None),
    )


class FunAsrConnection:
    """One reusable provider connection with sequential task execution."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        model: str = "fun-asr-realtime",
        workspace_id: str | None = None,
        socket_factory: SocketFactory = _default_socket_factory,
        start_timeout_seconds: float = 10.0,
        final_timeout_seconds: float = 3.0,
    ) -> None:
        self._url = url
        self._headers = {"Authorization": f"Bearer {api_key}", "user-agent": "jx-core/0.1"}
        if workspace_id:
            self._headers["X-DashScope-WorkSpace"] = workspace_id
        self._model = model
        self._socket_factory = socket_factory
        self._start_timeout_seconds = start_timeout_seconds
        self._final_timeout_seconds = final_timeout_seconds
        self._socket: WebSocketLike | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            await socket.close()

    async def _ensure_socket(self) -> WebSocketLike:
        if self._socket is None:
            try:
                self._socket = await self._socket_factory(self._url, dict(self._headers))
            except Exception as error:
                raise FunAsrError("asr_connection_failed") from error
        return self._socket

    async def recognize_segment(
        self,
        chunks: AsyncIterable[bytes],
        *,
        on_sentence: SentenceCallback,
        on_started: StartedCallback | None = None,
        task_id: UUID | None = None,
    ) -> SegmentResult:
        async with self._lock:
            current_task_id = task_id or uuid4()
            socket = await self._ensure_socket()
            sender: asyncio.Task[None] | None = None
            try:
                await socket.send(json.dumps(self._run_task(current_task_id), ensure_ascii=False))
                await self._wait_started(socket, current_task_id)
                if on_started is not None:
                    on_started()
                started_at = asyncio.get_running_loop().time()
                first_interim_ms: int | None = None
                stable_sentences: dict[int, str] = {}
                finish_sent_at: float | None = None

                async def send_audio() -> None:
                    nonlocal finish_sent_at
                    try:
                        async for chunk in chunks:
                            await socket.send(chunk)
                        await socket.send(
                            json.dumps(
                                {
                                    "header": {
                                        "action": "finish-task",
                                        "task_id": str(current_task_id),
                                        "streaming": "duplex",
                                    },
                                    "payload": {"input": {}},
                                }
                            )
                        )
                        finish_sent_at = asyncio.get_running_loop().time()
                    except Exception as error:
                        raise FunAsrError("asr_stream_failed") from error

                sender = asyncio.create_task(send_audio(), name=f"asr-send-{current_task_id}")
                finish_seen_at: float | None = None
                while True:
                    timeout = self._final_timeout_seconds if sender.done() else 30.0
                    try:
                        raw = await asyncio.wait_for(socket.recv(), timeout=timeout)
                    except TimeoutError as error:
                        code = "asr_final_timeout" if sender.done() else "asr_audio_timeout"
                        raise FunAsrError(code) from error
                    message = self._decode_message(raw)
                    header = self._header(message)
                    if header.get("task_id") != str(current_task_id):
                        continue
                    event = header.get("event")
                    if event == "task-failed":
                        self._socket = None
                        await socket.close()
                        raise FunAsrError("asr_task_failed")
                    if event == "result-generated":
                        sentence = self._sentence(message, current_task_id)
                        if sentence.heartbeat:
                            continue
                        latency_ms = int((asyncio.get_running_loop().time() - started_at) * 1000)
                        if first_interim_ms is None:
                            first_interim_ms = latency_ms
                        if sentence.sentence_end:
                            stable_sentences[sentence.sentence_id] = sentence.text
                        await on_sentence(sentence)
                    elif event == "task-finished":
                        finish_seen_at = asyncio.get_running_loop().time()
                        break
                await sender
                assert finish_seen_at is not None
                if finish_sent_at is None:
                    raise FunAsrError("asr_protocol_invalid")
                return SegmentResult(
                    task_id=current_task_id,
                    final_text="".join(stable_sentences[key] for key in sorted(stable_sentences)),
                    first_interim_latency_ms=first_interim_ms,
                    final_latency_ms=int((finish_seen_at - finish_sent_at) * 1000),
                )
            except BaseException:
                if sender is not None:
                    sender.cancel()
                    await asyncio.gather(sender, return_exceptions=True)
                await self._invalidate_socket(socket)
                raise

    async def _invalidate_socket(self, socket: WebSocketLike) -> None:
        if self._socket is socket:
            self._socket = None
        try:
            await socket.close()
        except Exception:
            pass

    async def _wait_started(self, socket: WebSocketLike, task_id: UUID) -> None:
        try:
            while True:
                raw = await asyncio.wait_for(socket.recv(), timeout=self._start_timeout_seconds)
                message = self._decode_message(raw)
                header = self._header(message)
                if header.get("task_id") != str(task_id):
                    continue
                if header.get("event") == "task-started":
                    return
                if header.get("event") == "task-failed":
                    self._socket = None
                    await socket.close()
                    raise FunAsrError("asr_task_failed")
        except TimeoutError as error:
            raise FunAsrError("asr_start_timeout") from error
        except FunAsrError:
            raise
        except Exception as error:
            raise FunAsrError("asr_connection_failed") from error

    def _run_task(self, task_id: UUID) -> dict[str, Any]:
        return {
            "header": {
                "action": "run-task",
                "task_id": str(task_id),
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self._model,
                "parameters": {
                    "format": "pcm",
                    "sample_rate": 16000,
                    "language_hints": ["zh"],
                    "heartbeat": True,
                    "semantic_punctuation_enabled": False,
                    "multi_threshold_mode_enabled": True,
                    "max_sentence_silence": 1300,
                },
                "input": {},
            },
        }

    @staticmethod
    def _decode_message(raw: str | bytes) -> dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            message = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FunAsrError("asr_protocol_invalid") from error
        if not isinstance(message, dict):
            raise FunAsrError("asr_protocol_invalid")
        return cast(dict[str, Any], message)

    @staticmethod
    def _header(message: dict[str, Any]) -> dict[str, Any]:
        header = message.get("header")
        if not isinstance(header, dict):
            raise FunAsrError("asr_protocol_invalid")
        return cast(dict[str, Any], header)

    @staticmethod
    def _sentence(message: dict[str, Any], task_id: UUID) -> FunAsrSentence:
        try:
            sentence = message["payload"]["output"]["sentence"]
            return FunAsrSentence(
                task_id=task_id,
                text=str(sentence.get("text", "")),
                sentence_end=bool(sentence.get("sentence_end", False)),
                heartbeat=bool(sentence.get("heartbeat", False)),
                begin_time_ms=int(sentence.get("begin_time") or 0),
                end_time_ms=int(sentence.get("end_time") or 0),
                sentence_id=int(sentence.get("sentence_id") or 0),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise FunAsrError("asr_protocol_invalid") from error

"""Thin, recorded-frame-testable Fun-ASR duplex WebSocket adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from websockets.asyncio.client import connect

from ..data_capture.provider import ProviderCallCapture, RawProviderEvent
from ..data_capture.provider_writer import ProviderCaptureWriter
from ..provider_cleanup import close_provider_socket_bounded


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


def _consume_asr_task_result(completed: asyncio.Task[Any]) -> None:
    try:
        completed.exception()
    except BaseException:
        pass


async def _default_socket_factory(url: str, headers: dict[str, str]) -> WebSocketLike:
    return cast(
        WebSocketLike,
        await connect(url, additional_headers=headers, max_size=2**20, proxy=None),
    )


class FunAsrConnection:
    """One reusable provider connection with sequential task execution."""

    _MAX_PENDING_CAPTURES = 16
    _CLOSE_TIMEOUT_SECONDS = 2.0

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
        task_timeout_seconds: float = 35.0,
        capture_writer: ProviderCaptureWriter | None = None,
        capture_factory: Callable[[], ProviderCallCapture] | None = None,
    ) -> None:
        if start_timeout_seconds <= 0 or final_timeout_seconds <= 0 or task_timeout_seconds <= 0:
            raise ValueError("ASR timeouts must be positive")
        self._capture_writer = capture_writer
        self._capture_factory = capture_factory
        self._url = url
        self._headers = {"Authorization": f"Bearer {api_key}", "user-agent": "jx-core/0.1"}
        if workspace_id:
            self._headers["X-DashScope-WorkSpace"] = workspace_id
        self._model = model
        self._socket_factory = socket_factory
        self._start_timeout_seconds = start_timeout_seconds
        self._final_timeout_seconds = final_timeout_seconds
        self._task_timeout_seconds = task_timeout_seconds
        self._socket: WebSocketLike | None = None
        self._retirement_epoch = 0
        self._lock = asyncio.Lock()
        self._captures: dict[UUID, ProviderCallCapture] = {}

    def take_capture(self, task_id: UUID) -> ProviderCallCapture | None:
        return self._captures.pop(task_id, None)

    async def close(self) -> None:
        self._retirement_epoch += 1
        socket, self._socket = self._socket, None
        for capture in self._captures.values():
            capture.close()
        self._captures.clear()
        if socket is not None:
            await self._close_socket_bounded(socket)

    def _store_capture(self, task_id: UUID, capture: ProviderCallCapture) -> None:
        self._captures[task_id] = capture
        while len(self._captures) > self._MAX_PENDING_CAPTURES:
            self._captures.pop(next(iter(self._captures))).close()

    def _require_owner(self, owner_epoch: int) -> None:
        if owner_epoch != self._retirement_epoch:
            raise asyncio.CancelledError

    async def _ensure_socket(self, owner_epoch: int) -> WebSocketLike:
        self._require_owner(owner_epoch)
        if self._socket is None:
            try:
                socket = await self._socket_factory(self._url, dict(self._headers))
            except Exception as error:
                self._require_owner(owner_epoch)
                raise FunAsrError("asr_connection_failed") from error
            if owner_epoch != self._retirement_epoch:
                try:
                    await self._close_socket_bounded(socket)
                finally:
                    raise asyncio.CancelledError
            self._socket = socket
        self._require_owner(owner_epoch)
        return self._socket

    async def _receive_owned(self, socket: WebSocketLike, owner_epoch: int) -> str | bytes:
        self._require_owner(owner_epoch)
        try:
            raw = await socket.recv()
        except BaseException:
            self._require_owner(owner_epoch)
            raise
        self._require_owner(owner_epoch)
        return raw

    async def recognize_segment(
        self,
        chunks: AsyncIterable[bytes],
        *,
        on_sentence: SentenceCallback,
        on_started: StartedCallback | None = None,
        task_id: UUID | None = None,
    ) -> SegmentResult:
        owner_epoch = self._retirement_epoch
        async with self._lock:
            self._require_owner(owner_epoch)
            current_task_id = task_id or uuid4()
            capture = (
                self._capture_factory()
                if self._capture_factory is not None
                else self._capture_writer.new_capture()
                if self._capture_writer is not None
                else ProviderCallCapture()
            )
            capture.provider_request_id = str(current_task_id)
            run_task = self._run_task(current_task_id)
            sent_events = capture.event_buffer()
            sent_events.append(run_task)
            received_events = capture.event_buffer()
            audio_frames = 0
            audio_bytes = 0
            loop = asyncio.get_running_loop()
            task_deadline = loop.time() + self._task_timeout_seconds
            socket: WebSocketLike | None = None
            sender: asyncio.Task[None] | None = None
            receive_task: asyncio.Task[str | bytes] | None = None
            try:
                try:
                    socket = await asyncio.wait_for(
                        self._ensure_socket(owner_epoch),
                        timeout=min(
                            self._start_timeout_seconds, max(0.0, task_deadline - loop.time())
                        ),
                    )
                except TimeoutError as error:
                    self._require_owner(owner_epoch)
                    raise FunAsrError("asr_start_timeout") from error
                self._require_owner(owner_epoch)
                start_deadline = min(task_deadline, loop.time() + self._start_timeout_seconds)
                await self._send_with_timeout(
                    socket,
                    json.dumps(run_task, ensure_ascii=False),
                    owner_epoch=owner_epoch,
                    timeout_seconds=max(0.0, start_deadline - loop.time()),
                )
                started_event = await self._wait_started(
                    socket, current_task_id, deadline=start_deadline, owner_epoch=owner_epoch
                )
                self._require_owner(owner_epoch)
                received_events.append(started_event)
                if on_started is not None:
                    on_started()
                started_at = loop.time()
                first_interim_ms: int | None = None
                stable_sentences: dict[int, str] = {}
                finish_sent_at: float | None = None

                async def send_audio() -> None:
                    nonlocal audio_bytes, audio_frames, finish_sent_at
                    try:
                        self._require_owner(owner_epoch)
                        async for chunk in chunks:
                            self._require_owner(owner_epoch)
                            audio_frames += 1
                            audio_bytes += len(chunk)
                            await self._send_with_timeout(
                                socket,
                                chunk,
                                owner_epoch=owner_epoch,
                                timeout_seconds=max(0.0, task_deadline - loop.time()),
                                timeout_code="asr_audio_timeout",
                            )
                        self._require_owner(owner_epoch)
                        finish_event: dict[str, Any] = {
                            "header": {
                                "action": "finish-task",
                                "task_id": str(current_task_id),
                                "streaming": "duplex",
                            },
                            "payload": {"input": {}},
                        }
                        sent_events.append(finish_event)
                        await self._send_with_timeout(
                            socket,
                            json.dumps(finish_event),
                            owner_epoch=owner_epoch,
                            timeout_seconds=max(0.0, task_deadline - loop.time()),
                            timeout_code="asr_audio_timeout",
                        )
                        finish_sent_at = loop.time()
                    except FunAsrError:
                        self._require_owner(owner_epoch)
                        raise
                    except Exception as error:
                        self._require_owner(owner_epoch)
                        raise FunAsrError("asr_stream_failed") from error

                sender = asyncio.create_task(send_audio(), name=f"asr-send-{current_task_id}")
                finish_seen_at: float | None = None
                while True:
                    self._require_owner(owner_epoch)
                    if sender.done():
                        if sender.cancelled():
                            raise asyncio.CancelledError
                        sender_error = sender.exception()
                        if sender_error is not None:
                            raise sender_error
                    now = loop.time()
                    if finish_sent_at is not None:
                        deadline = min(task_deadline, finish_sent_at + self._final_timeout_seconds)
                        code = "asr_final_timeout"
                    else:
                        deadline = task_deadline
                        code = "asr_audio_timeout"
                    timeout = deadline - now
                    if timeout <= 0:
                        raise FunAsrError(code)
                    if receive_task is None:
                        receive_task = asyncio.create_task(
                            self._receive_owned(socket, owner_epoch),
                            name=f"asr-recv-{current_task_id}",
                        )
                    wait_tasks: set[asyncio.Task[Any]] = {receive_task}
                    if not sender.done():
                        wait_tasks.add(sender)
                    done, _ = await asyncio.wait(
                        wait_tasks,
                        timeout=timeout,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    self._require_owner(owner_epoch)
                    if not done:
                        raise FunAsrError(code)
                    if sender in done:
                        if sender.cancelled():
                            raise asyncio.CancelledError
                        sender_error = sender.exception()
                        if sender_error is not None:
                            raise sender_error
                    if receive_task not in done:
                        # The sender finished successfully. Keep the outstanding
                        # recv task so no overlapping websocket recv is issued.
                        continue
                    raw = receive_task.result()
                    receive_task = None
                    message = self._decode_message(raw)
                    header = self._header(message)
                    if header.get("task_id") != str(current_task_id):
                        continue
                    event = header.get("event")
                    if event == "task-failed":
                        received_events.append(message)
                        await self._invalidate_socket(socket)
                        raise FunAsrError("asr_task_failed")
                    if event == "result-generated":
                        sentence = self._sentence(message, current_task_id)
                        if sentence.heartbeat:
                            continue
                        if not any(
                            self._header(item).get("event") == "result-generated"
                            for item in received_events
                        ):
                            received_events.append(message)
                        latency_ms = int((loop.time() - started_at) * 1000)
                        if first_interim_ms is None:
                            first_interim_ms = latency_ms
                        if sentence.sentence_end:
                            stable_sentences[sentence.sentence_id] = sentence.text
                            if (
                                received_events
                                and self._header(received_events[-1]).get("event")
                                == "result-generated"
                            ):
                                received_events[-1] = message
                        callback_timeout = task_deadline - loop.time()
                        if callback_timeout <= 0:
                            raise FunAsrError("asr_callback_timeout")
                        try:
                            self._require_owner(owner_epoch)
                            await asyncio.wait_for(on_sentence(sentence), timeout=callback_timeout)
                        except TimeoutError as error:
                            self._require_owner(owner_epoch)
                            raise FunAsrError("asr_callback_timeout") from error
                        self._require_owner(owner_epoch)
                    elif event == "task-finished":
                        if finish_sent_at is None:
                            raise FunAsrError("asr_protocol_invalid")
                        received_events.append(message)
                        finish_seen_at = loop.time()
                        break
                await sender
                self._require_owner(owner_epoch)
                assert finish_seen_at is not None
                if finish_sent_at is None:
                    raise FunAsrError("asr_protocol_invalid")
                result = SegmentResult(
                    task_id=current_task_id,
                    final_text="".join(stable_sentences[key] for key in sorted(stable_sentences)),
                    first_interim_latency_ms=first_interim_ms,
                    final_latency_ms=int((finish_seen_at - finish_sent_at) * 1000),
                )
                self._store_capture(
                    current_task_id,
                    _capture_asr(
                        capture,
                        self._url,
                        sent_events,
                        received_events,
                        audio_frames,
                        audio_bytes,
                    ),
                )
                return result
            except BaseException:
                if owner_epoch != self._retirement_epoch:
                    capture.close()
                children: tuple[asyncio.Task[Any], ...] = tuple(
                    task for task in (receive_task, sender) if task is not None
                )
                for child in children:
                    child.add_done_callback(_consume_asr_task_result)
                    child.cancel()
                try:
                    if children:
                        await asyncio.gather(*children, return_exceptions=True)
                    if socket is not None:
                        await self._invalidate_socket(socket)
                finally:
                    if owner_epoch != self._retirement_epoch:
                        capture.close()
                    else:
                        self._store_capture(
                            current_task_id,
                            _capture_asr(
                                capture,
                                self._url,
                                sent_events,
                                received_events,
                                audio_frames,
                                audio_bytes,
                            ),
                        )
                self._require_owner(owner_epoch)
                raise

    async def _invalidate_socket(self, socket: WebSocketLike) -> None:
        if self._socket is socket:
            self._socket = None
        await self._close_socket_bounded(socket)

    @classmethod
    async def _close_socket_bounded(cls, socket: WebSocketLike) -> None:
        await close_provider_socket_bounded(
            socket, timeout_seconds=cls._CLOSE_TIMEOUT_SECONDS, task_name="asr-socket-close"
        )

    async def _send_with_timeout(
        self,
        socket: WebSocketLike,
        message: str | bytes,
        *,
        owner_epoch: int,
        timeout_seconds: float | None = None,
        timeout_code: str = "asr_start_timeout",
    ) -> None:
        self._require_owner(owner_epoch)
        remaining = self._start_timeout_seconds if timeout_seconds is None else timeout_seconds
        if remaining <= 0:
            raise FunAsrError(timeout_code)
        try:
            await asyncio.wait_for(socket.send(message), timeout=remaining)
        except TimeoutError as error:
            self._require_owner(owner_epoch)
            raise FunAsrError(timeout_code) from error
        except FunAsrError:
            self._require_owner(owner_epoch)
            raise
        except Exception as error:
            self._require_owner(owner_epoch)
            raise FunAsrError("asr_stream_failed") from error
        self._require_owner(owner_epoch)

    async def _wait_started(
        self, socket: WebSocketLike, task_id: UUID, *, deadline: float, owner_epoch: int
    ) -> dict[str, Any]:
        try:
            while True:
                timeout = deadline - asyncio.get_running_loop().time()
                if timeout <= 0:
                    raise TimeoutError
                raw = await asyncio.wait_for(
                    self._receive_owned(socket, owner_epoch), timeout=timeout
                )
                message = self._decode_message(raw)
                header = self._header(message)
                if header.get("task_id") != str(task_id):
                    continue
                if header.get("event") == "task-started":
                    return message
                if header.get("event") == "task-failed":
                    await self._invalidate_socket(socket)
                    raise FunAsrError("asr_task_failed")
        except TimeoutError as error:
            self._require_owner(owner_epoch)
            raise FunAsrError("asr_start_timeout") from error
        except FunAsrError:
            self._require_owner(owner_epoch)
            raise
        except Exception as error:
            self._require_owner(owner_epoch)
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
        if isinstance(message, RawProviderEvent):
            return {"event": message.event_name}
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


def _capture_asr(
    capture: ProviderCallCapture,
    url: str,
    sent_events: list[dict[str, Any]],
    received_events: list[dict[str, Any]],
    audio_frames: int,
    audio_bytes: int,
) -> ProviderCallCapture:
    capture.capture_request(
        transport="WEBSOCKET",
        url=url,
        body={"messages": sent_events},
        binary_summary={
            "direction": "platform_to_provider",
            "format": "pcm_s16le_16000_mono",
            "frame_count": audio_frames,
            "total_bytes": audio_bytes,
            "audio_duration_ms": audio_bytes // 32,
        },
    )
    capture.capture_response(
        transport="WEBSOCKET",
        url=url,
        body={"task_id": capture.provider_request_id},
        events=received_events,
    )
    return capture

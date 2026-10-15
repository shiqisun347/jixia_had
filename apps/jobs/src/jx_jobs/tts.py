"""DashScope Qwen-Audio-TTS duplex WebSocket adapter."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

from websockets.asyncio.client import ClientConnection, connect
from websockets.protocol import State

from jx_core.data_capture.provider import CaptureEventBuffer, ProviderCallCapture
from jx_core.data_capture.provider_writer import new_provider_capture
from jx_core.provider_cleanup import close_provider_socket_bounded


class TTSProviderError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


TTS_TASK_TIMEOUT_SECONDS = 180.0
TTS_MAX_AUDIO_BYTES = 16 * 1024 * 1024
TTS_MAX_CAPTURE_EVENTS = 128


class DashScopeTTSClient:
    # Provider websocket close can wait on a network handshake.  Cleanup runs
    # from the bounded worker task, so it must not hold the worker forever when
    # the provider stops responding.
    _CLOSE_TIMEOUT_SECONDS = 2.0

    def __init__(
        self,
        *,
        websocket_url: str,
        api_key: str,
        workspace: str,
        model: str = "qwen-audio-3.1-tts-flash",
        timeout_seconds: float = 10.0,
        task_timeout_seconds: float = TTS_TASK_TIMEOUT_SECONDS,
        max_audio_bytes: int = TTS_MAX_AUDIO_BYTES,
        capture_factory: Callable[[], ProviderCallCapture] | None = None,
    ) -> None:
        self.websocket_url = websocket_url
        self.api_key = api_key
        self.workspace = workspace
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.task_timeout_seconds = task_timeout_seconds
        self.max_audio_bytes = max_audio_bytes
        self._capture_factory = capture_factory or (lambda: new_provider_capture(None))
        self._connection: ClientConnection | None = None
        self._last_capture: ProviderCallCapture | None = None

    def take_capture(self) -> ProviderCallCapture | None:
        capture, self._last_capture = self._last_capture, None
        return capture

    async def _connection_or_open(self, *, timeout: float | None = None) -> ClientConnection:
        if self._connection is not None and self._connection.state is State.OPEN:
            return self._connection
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.workspace:
            headers["X-DashScope-WorkSpace"] = self.workspace
        try:
            self._connection = await asyncio.wait_for(
                connect(self.websocket_url, additional_headers=headers),
                timeout=timeout if timeout is not None else self.timeout_seconds,
            )
        except Exception as error:
            raise TTSProviderError("tts_connection_failed") from error
        return self._connection

    async def synthesize_to_file(
        self,
        *,
        text: str,
        voice: str,
        rate: float,
        output_path: Path,
    ) -> None:
        if not text.strip():
            raise TTSProviderError("tts_text_empty")
        task_deadline = monotonic() + self.task_timeout_seconds
        connection = await self._connection_or_open(timeout=self._remaining(task_deadline))
        task_id = str(uuid4())
        if self._last_capture is not None:
            self._last_capture.close()
            self._last_capture = None
        capture = self._capture_factory()
        capture.provider_request_id = task_id
        sent_events = capture.event_buffer()
        received_events = capture.event_buffer()
        byte_count = 0
        run_task = {
            "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "model": self.model,
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
        _record_event(sent_events, run_task)
        try:
            await self._send(
                connection,
                run_task,
                timeout=self._remaining(task_deadline),
            )
            _record_event(
                received_events,
                await self._wait_event(connection, task_id, "task-started", deadline=task_deadline),
            )
        except BaseException:
            await self.close()
            capture.capture_request(
                transport="WEBSOCKET", url=self.websocket_url, body={"messages": sent_events}
            )
            capture.capture_response(
                transport="WEBSOCKET",
                url=self.websocket_url,
                body={"task_id": task_id},
                events=received_events,
                binary_summary={
                    "direction": "provider_to_platform",
                    "format": "ogg_opus",
                    "total_bytes": 0,
                },
            )
            self._last_capture = capture
            raise
        continue_task = {
            "header": {"action": "continue-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {"input": {"text": text}},
        }
        try:
            _record_event(sent_events, continue_task)
            await self._send(
                connection,
                continue_task,
                timeout=self._remaining(task_deadline),
            )
            finish_task: dict[str, Any] = {
                "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
                "payload": {"input": {}},
            }
            _record_event(sent_events, finish_task)
            await self._send(
                connection,
                finish_task,
                timeout=self._remaining(task_deadline),
            )
        except BaseException:
            await self.close()
            capture.capture_request(
                transport="WEBSOCKET", url=self.websocket_url, body={"messages": sent_events}
            )
            capture.capture_response(
                transport="WEBSOCKET",
                url=self.websocket_url,
                body={"task_id": task_id},
                events=received_events,
                binary_summary={
                    "direction": "provider_to_platform",
                    "format": "ogg_opus",
                    "total_bytes": 0,
                },
            )
            self._last_capture = capture
            raise
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_fd, temporary_name = tempfile.mkstemp(prefix=".tts-", dir=output_path.parent)
        os.close(temporary_fd)
        temporary_path = Path(temporary_name)
        try:
            with temporary_path.open("wb") as output:
                while True:
                    timeout = min(self.timeout_seconds, self._remaining(task_deadline))
                    if timeout <= 0:
                        raise TTSProviderError("tts_task_timeout")
                    message = await self._receive(connection, timeout=timeout)
                    if isinstance(message, bytes):
                        output.write(message)
                        byte_count += len(message)
                        if byte_count > self.max_audio_bytes:
                            raise TTSProviderError("tts_audio_too_large")
                        continue
                    event = json.loads(message)
                    header = event.get("header", {})
                    if header.get("task_id") != task_id:
                        continue
                    _record_event(received_events, event)
                    if header.get("event") == "task-failed":
                        raise TTSProviderError("tts_provider_failed")
                    if header.get("event") == "task-finished":
                        break
            if temporary_path.stat().st_size == 0:
                raise TTSProviderError("tts_audio_empty")
            os.replace(temporary_path, output_path)
        except asyncio.CancelledError:
            await self.cancel(task_id)
            temporary_path.unlink(missing_ok=True)
            await self.close()
            raise
        except Exception:
            temporary_path.unlink(missing_ok=True)
            await self.close()
            raise
        finally:
            capture.capture_request(
                transport="WEBSOCKET", url=self.websocket_url, body={"messages": sent_events}
            )
            capture.capture_response(
                transport="WEBSOCKET",
                url=self.websocket_url,
                body={"task_id": task_id},
                events=received_events,
                binary_summary={
                    "direction": "provider_to_platform",
                    "format": "ogg_opus",
                    "total_bytes": byte_count,
                },
            )
            self._last_capture = capture

    async def cancel(self, task_id: str) -> None:
        connection = self._connection
        if connection is None:
            return
        try:
            await self._send(
                connection,
                {
                    "header": {
                        "action": "finish-task",
                        "task_id": task_id,
                        "streaming": "duplex",
                    },
                    "payload": {"input": {"directive": "cancel"}},
                },
            )
        except TTSProviderError:
            # Cancellation is best effort.  The caller still closes the
            # connection, and the original cancellation/error must survive a
            # provider that has already stopped accepting frames.
            return

    async def close(self) -> None:
        connection, self._connection = self._connection, None
        capture, self._last_capture = self._last_capture, None
        if capture is not None:
            capture.close()
        if connection is not None:
            await self._close_connection_bounded(connection)

    @classmethod
    async def _close_connection_bounded(cls, connection: ClientConnection) -> None:
        """Release an unresponsive provider socket without blocking cleanup."""

        await close_provider_socket_bounded(
            connection,
            timeout_seconds=cls._CLOSE_TIMEOUT_SECONDS,
            task_name="jobs-tts-socket-close",
        )

    @staticmethod
    def _remaining(deadline: float) -> float:
        return max(0.0, deadline - monotonic())

    async def _send(
        self,
        connection: ClientConnection,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> None:
        try:
            await asyncio.wait_for(
                connection.send(json.dumps(payload, ensure_ascii=False)),
                timeout if timeout is not None else self.timeout_seconds,
            )
        except Exception as error:
            raise TTSProviderError("tts_send_failed") from error

    async def _receive(
        self, connection: ClientConnection, *, timeout: float | None = None
    ) -> str | bytes:
        try:
            return await asyncio.wait_for(
                connection.recv(), timeout if timeout is not None else self.timeout_seconds
            )
        except Exception as error:
            raise TTSProviderError("tts_receive_timeout") from error

    async def _wait_event(
        self,
        connection: ClientConnection,
        task_id: str,
        expected: str,
        *,
        deadline: float,
    ) -> dict[str, Any]:
        while True:
            timeout = min(self.timeout_seconds, self._remaining(deadline))
            if timeout <= 0:
                raise TTSProviderError("tts_task_timeout")
            try:
                message = await self._receive(connection, timeout=timeout)
            except TTSProviderError as error:
                if self._remaining(deadline) <= 0:
                    raise TTSProviderError("tts_task_timeout") from error
                raise
            if isinstance(message, bytes):
                continue
            event = json.loads(message)
            header = event.get("header", {})
            if header.get("task_id") != task_id:
                continue
            if header.get("event") == "task-failed":
                raise TTSProviderError("tts_provider_failed")
            if header.get("event") == expected:
                return event


def _record_event(events: list[dict[str, Any]], event: dict[str, Any]) -> None:
    if isinstance(events, CaptureEventBuffer):
        events.append(event)
        return
    if len(events) < TTS_MAX_CAPTURE_EVENTS:
        events.append(event)
        return
    tail_start = TTS_MAX_CAPTURE_EVENTS // 2
    events[tail_start - 1 :] = events[tail_start:]
    events[-1] = event


__all__ = ["DashScopeTTSClient", "TTSProviderError"]

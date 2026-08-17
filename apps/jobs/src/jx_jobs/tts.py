"""DashScope Qwen-Audio-TTS duplex WebSocket adapter."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from websockets.asyncio.client import ClientConnection, connect
from websockets.protocol import State


class TTSProviderError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DashScopeTTSClient:
    def __init__(
        self,
        *,
        websocket_url: str,
        api_key: str,
        workspace: str,
        model: str = "qwen-audio-3.0-tts-flash",
        timeout_seconds: float = 10.0,
    ) -> None:
        self.websocket_url = websocket_url
        self.api_key = api_key
        self.workspace = workspace
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._connection: ClientConnection | None = None

    async def _connection_or_open(self) -> ClientConnection:
        if self._connection is not None and self._connection.state is State.OPEN:
            return self._connection
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.workspace:
            headers["X-DashScope-WorkSpace"] = self.workspace
        try:
            self._connection = await asyncio.wait_for(
                connect(self.websocket_url, additional_headers=headers), self.timeout_seconds
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
        connection = await self._connection_or_open()
        task_id = str(uuid4())
        await self._send(
            connection,
            {
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
            },
        )
        await self._wait_event(connection, task_id, "task-started")
        await self._send(
            connection,
            {
                "header": {"action": "continue-task", "task_id": task_id, "streaming": "duplex"},
                "payload": {"input": {"text": text}},
            },
        )
        await self._send(
            connection,
            {
                "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
                "payload": {"input": {}},
            },
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_fd, temporary_name = tempfile.mkstemp(prefix=".tts-", dir=output_path.parent)
        os.close(temporary_fd)
        temporary_path = Path(temporary_name)
        try:
            with temporary_path.open("wb") as output:
                while True:
                    message = await self._receive(connection)
                    if isinstance(message, bytes):
                        output.write(message)
                        continue
                    event = json.loads(message)
                    header = event.get("header", {})
                    if header.get("task_id") != task_id:
                        continue
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
            raise
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    async def cancel(self, task_id: str) -> None:
        if self._connection is None:
            return
        await self._send(
            self._connection,
            {
                "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
                "payload": {"input": {"directive": "cancel"}},
            },
        )

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def _send(self, connection: ClientConnection, payload: dict[str, Any]) -> None:
        try:
            await asyncio.wait_for(
                connection.send(json.dumps(payload, ensure_ascii=False)), self.timeout_seconds
            )
        except Exception as error:
            raise TTSProviderError("tts_send_failed") from error

    async def _receive(self, connection: ClientConnection) -> str | bytes:
        try:
            return await asyncio.wait_for(connection.recv(), self.timeout_seconds)
        except Exception as error:
            raise TTSProviderError("tts_receive_timeout") from error

    async def _wait_event(self, connection: ClientConnection, task_id: str, expected: str) -> None:
        while True:
            message = await self._receive(connection)
            if isinstance(message, bytes):
                continue
            event = json.loads(message)
            header = event.get("header", {})
            if header.get("task_id") != task_id:
                continue
            if header.get("event") == "task-failed":
                raise TTSProviderError("tts_provider_failed")
            if header.get("event") == expected:
                return


__all__ = ["DashScopeTTSClient", "TTSProviderError"]

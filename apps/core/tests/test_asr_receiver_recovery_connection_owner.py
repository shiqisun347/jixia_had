"""Actual new Session must not wait behind a retired Protocol factory lock."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

import jx_core.asr.runtime as runtime_module
from jx_core.asr.protocol import FunAsrConnection
from jx_core.asr.runtime import MatchAudioReceiver
from jx_core.asr.session import PCM_FRAME_BYTES
from test_asr_protocol_retirement_epoch import OwnedSocket, ResistantFactory, observe
from test_asr_protocol_retirement_epoch import external_guard as external_guard
from test_asr_receiver_session_owner import envelope


@pytest.mark.asyncio
async def test_actual_receiver_recovery_starts_new_provider_without_releasing_old_factory(
    monkeypatch: pytest.MonkeyPatch, external_guard: dict[str, int], tmp_path: Path
) -> None:
    baseline = asyncio.all_tasks()
    old_socket, new_socket = OwnedSocket(), OwnedSocket()
    old_factory = ResistantFactory(old_socket)
    new_entered = asyncio.Event()
    factory_calls = 0
    connections: list[FunAsrConnection] = []

    async def factory(url: str, headers: dict[str, str]) -> OwnedSocket:
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            return await old_factory(url, headers)
        assert factory_calls == 2
        new_entered.set()
        return new_socket

    actual_connection = FunAsrConnection

    def constructor(**kwargs: Any) -> FunAsrConnection:
        assert kwargs["url"] == "wss://synthetic.invalid/asr"
        assert kwargs["model"] == "synthetic-fixed-model"
        connection = actual_connection(**kwargs, socket_factory=factory)
        connections.append(connection)
        return connection

    monkeypatch.setattr(runtime_module, "FunAsrConnection", constructor)
    settings = SimpleNamespace(
        asr_ws_url="wss://synthetic.invalid/asr",
        asr_api_key=SecretStr("synthetic-unused-material"),
        asr_model="synthetic-fixed-model",
        asr_workspace_id=None,
        agent_audio_storage_dir=tmp_path,
    )
    receiver = MatchAudioReceiver(
        match_id=uuid4(), settings=cast(Any, settings), callbacks=AsyncMock()
    )
    room = SimpleNamespace(disconnect=AsyncMock())
    receiver._room = cast(Any, room)
    old_connection = constructor(
        url=settings.asr_ws_url,
        api_key=settings.asr_api_key.get_secret_value(),
        model=settings.asr_model,
        workspace_id=None,
    )
    receiver._connection = old_connection
    old_task: asyncio.Task[None] | None = None
    new_task: asyncio.Task[None] | None = None
    old_session = None
    try:
        old_envelope = envelope(receiver.match_id, uuid4(), attempt=1)
        assert old_envelope.speech_id is not None
        await receiver.start_speech(old_envelope.speech_id, uuid4(), old_envelope)
        old_task, old_session = receiver._speech_task, receiver._speech
        assert old_task is not None and old_session is not None
        old_session.feed_pcm(b"\x00" * PCM_FRAME_BYTES)
        await asyncio.wait_for(old_factory.entered.wait(), timeout=1)
        assert old_connection._lock.locked() and factory_calls == 1
        await asyncio.wait_for(receiver.abort_speech(), timeout=0.4)
        assert old_session.pending_provider_tasks and old_factory.cancelled.is_set()
        assert not old_factory.release.is_set()
        new_envelope = envelope(receiver.match_id, uuid4(), attempt=2)
        assert new_envelope.speech_id is not None
        await receiver.start_speech(new_envelope.speech_id, uuid4(), new_envelope)
        new_task, new_session = receiver._speech_task, receiver._speech
        assert new_task is not None and new_session is not None
        new_session.feed_pcm(b"\x00" * PCM_FRAME_BYTES)
        ready = await observe(lambda: new_session._ready.is_set(), timeout=0.1)
        if ready:
            await observe(lambda: new_socket.pcm_frames == 1)
        snapshot = {
            "new_session_ready": ready,
            "new_provider_entered": new_entered.is_set(),
            "new_pcm_frames": new_socket.pcm_frames,
            "old_factory_still_held": not old_factory.release.is_set(),
            "old_provider_task_pending": bool(old_session.pending_provider_tasks),
            "connections": len(connections),
            "factory_calls": factory_calls,
            "current_reuses_old_connection": receiver._connection is old_connection,
            "rtc_room_unchanged": cast(Any, receiver._room) is room,
            "guard": dict(external_guard),
        }
        print("recovery_connection_product_observations=" + json.dumps(snapshot, sort_keys=True))
        assert ready and new_entered.is_set() and new_socket.pcm_frames == 1, snapshot
        assert receiver._connection is not old_connection and len(connections) == 2, snapshot
        assert not old_factory.release.is_set() and old_session.pending_provider_tasks, snapshot
        assert cast(Any, receiver._room) is room and not external_guard
    finally:
        await asyncio.wait_for(receiver.close(), timeout=0.4)
        old_factory.release.set()
        if old_session is not None:
            await old_session.finish()
        tasks = [task for task in (old_task, new_task) if task is not None]
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=1)
        for connection in connections:
            await asyncio.wait_for(connection.close(), timeout=0.4)
        current = asyncio.current_task()
        assert await observe(lambda: not asyncio.all_tasks() - baseline - {current})
        print("recovery_connection_fixture_observations=" + json.dumps({"pending": 0}))

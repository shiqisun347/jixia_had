"""An awaited old callback must not adopt an identical-envelope replacement."""

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
from test_asr_protocol_retirement_epoch import OwnedSocket, Probe, observe
from test_asr_protocol_retirement_epoch import external_guard as external_guard
from test_asr_receiver_session_owner import envelope


@pytest.mark.asyncio
async def test_old_segment_callback_return_cannot_touch_same_envelope_new_recording(
    monkeypatch: pytest.MonkeyPatch, external_guard: dict[str, int], tmp_path: Path
) -> None:
    baseline = asyncio.all_tasks()
    probe = Probe()
    callback_entered, callback_release = asyncio.Event(), asyncio.Event()
    callback_cancellations = 0
    segment_calls = 0
    sockets: list[OwnedSocket] = []
    connections: list[FunAsrConnection] = []

    async def factory(_: str, __: dict[str, str]) -> OwnedSocket:
        owned = OwnedSocket(emit_sentence=True)
        sockets.append(owned)
        return owned

    actual_connection = FunAsrConnection

    def constructor(**kwargs: Any) -> FunAsrConnection:
        assert kwargs["url"] == "wss://synthetic.invalid/asr"
        assert kwargs["model"] == "synthetic-fixed-model"
        kwargs["capture_factory"] = probe.capture_factory
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
    callbacks = AsyncMock()

    async def persist_segment(**_: Any) -> None:
        nonlocal callback_cancellations, segment_calls
        segment_calls += 1
        if segment_calls != 1:
            return
        callback_entered.set()
        while not callback_release.is_set():
            try:
                await callback_release.wait()
            except asyncio.CancelledError:
                callback_cancellations += 1

    callbacks.persist_asr_segment.side_effect = persist_segment
    receiver = MatchAudioReceiver(
        match_id=uuid4(), settings=cast(Any, settings), callbacks=callbacks
    )
    room = SimpleNamespace(disconnect=AsyncMock())
    receiver._room = cast(Any, room)
    receiver._connection = constructor(
        url=settings.asr_ws_url,
        api_key=settings.asr_api_key.get_secret_value(),
        model=settings.asr_model,
        workspace_id=None,
    )
    speech_id, user_id = uuid4(), uuid4()
    same_envelope = envelope(receiver.match_id, speech_id, attempt=1)
    old_task: asyncio.Task[None] | None = None
    new_task: asyncio.Task[None] | None = None
    try:
        await receiver.start_speech(speech_id, user_id, same_envelope)
        old_session, old_task = receiver._speech, receiver._speech_task
        assert old_session is not None and old_task is not None
        old_pcm = b"\x01" * PCM_FRAME_BYTES
        receiver._write_recording(old_pcm)
        old_session.feed_pcm(old_pcm)
        await old_session.finish()
        await asyncio.wait_for(callback_entered.wait(), timeout=1)
        assert segment_calls == 1 and not old_task.done()
        await receiver.abort_speech()
        assert callback_cancellations >= 1 and not old_task.done()
        await receiver.start_speech(speech_id, user_id, same_envelope)
        new_session, new_task = receiver._speech, receiver._speech_task
        assert new_session is not None and new_session is not old_session and new_task is not None
        new_pcm = b"\x02" * PCM_FRAME_BYTES
        receiver._write_recording(new_pcm)
        new_session.feed_pcm(new_pcm)
        await asyncio.wait_for(new_session.wait_ready(), timeout=1)
        storage, spool = receiver._recording_storage_path, receiver._recording_spool_path
        assert storage is not None and spool is not None
        assert receiver._recording_file is not None and not new_task.done()
        assert callbacks.finalize_asr_speech.await_count == 0
        callback_release.set()
        assert await observe(old_task.done)
        recording_file_after_callback = cast(Any, receiver)._recording_file
        snapshot = {
            "same_envelope": receiver._speech_envelope == same_envelope,
            "current_is_new_session": receiver._speech is new_session,
            "current_task_preserved": receiver._speech_task is new_task,
            "recording_still_open": recording_file_after_callback is not None,
            "spool_exists": spool.exists(),
            "storage_published_early": storage.exists(),
            "final_callbacks": callbacks.finalize_asr_speech.await_count,
            "old_task_done": old_task.done(),
            "new_task_pending": not new_task.done(),
            "connections": len(connections),
            "rtc_room_unchanged": cast(Any, receiver._room) is room,
            "guard": dict(external_guard),
        }
        print(
            "session_boundary_review_product_observations=" + json.dumps(snapshot, sort_keys=True)
        )
        assert receiver._speech is new_session and receiver._speech_task is new_task, snapshot
        assert recording_file_after_callback is not None and spool.is_file(), snapshot
        assert not storage.exists() and callbacks.finalize_asr_speech.await_count == 0, snapshot
        assert cast(Any, receiver._room) is room and not external_guard
        await receiver.finish_speech(speech_id)
        assert new_task.done() and not new_task.cancelled()
        assert callbacks.finalize_asr_speech.await_count == 1
        final = callbacks.finalize_asr_speech.await_args
        assert final is not None and final.kwargs["envelope"] == same_envelope
        assert final.kwargs["audio_storage_path"] == str(storage)
        assert storage.read_bytes() == new_pcm
        assert (
            probe.budget.items == probe.budget.retained_bytes == probe.budget.workspace_bytes == 0
        )
    finally:
        callback_release.set()
        await asyncio.wait_for(receiver.close(), timeout=0.4)
        tasks = [task for task in (old_task, new_task) if task is not None]
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=1)
        for connection in connections:
            await asyncio.wait_for(connection.close(), timeout=0.4)
        for capture in probe.captures:
            capture.close()
        current = asyncio.current_task()
        assert await observe(lambda: not asyncio.all_tasks() - baseline - {current})
        print("session_boundary_review_fixture_observations=" + json.dumps({"pending": 0}))
        assert receiver._recording_file is None
        assert (
            probe.budget.items == probe.budget.retained_bytes == probe.budget.workspace_bytes == 0
        )

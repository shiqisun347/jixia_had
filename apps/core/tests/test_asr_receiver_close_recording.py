"""A retired real speech error must not unlink same-speech replacement audio."""

from __future__ import annotations

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false
import asyncio
import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from livekit.rtc import _ffi_client
from pydantic_settings import BaseSettings
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

import jx_core.asr.runtime as module
from jx_core.asr.runtime import MatchAudioReceiver
from jx_core.runtime_identity import CallbackEnvelope


@pytest.fixture
def recording_probe_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, int]]:
    attempts: dict[str, int] = {}

    def forbidden(category: str) -> Callable[..., Any]:
        def reject(*_: Any, **__: Any) -> Any:
            attempts[category] = attempts.get(category, 0) + 1
            raise AssertionError(f"forbidden recording probe boundary: {category}")

        return reject

    assert _ffi_client.FfiClient._instance is None
    monkeypatch.setattr(BaseSettings, "__init__", forbidden("Settings"))
    monkeypatch.setattr(socket.socket, "connect", forbidden("network"))
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden("network"))
    monkeypatch.setattr(socket, "create_connection", forbidden("network"))
    monkeypatch.setattr(_ffi_client.FfiClient, "__init__", forbidden("native init"))
    monkeypatch.setattr(_ffi_client, "get_ffi_lib", forbidden("native library"))
    monkeypatch.setattr(_ffi_client.FfiHandle, "__init__", forbidden("native handle"))
    monkeypatch.setattr(Engine, "__init__", forbidden("database"))
    monkeypatch.setattr(AsyncEngine, "__init__", forbidden("database"))
    monkeypatch.setattr(module, "ASR_TASK_CANCEL_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(module, "ASR_CLEANUP_TIMEOUT_SECONDS", 0.03)
    yield attempts
    assert not attempts, attempts
    assert _ffi_client.FfiClient._instance is None


@dataclass
class OwnedResource:
    calls: int = 0

    async def close(self) -> None:
        self.calls += 1

    async def disconnect(self) -> None:
        self.calls += 1


@dataclass
class LateSession:
    speech_id: UUID
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def run(self) -> None:
        self.entered.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
        # Deliberate provider fault drives the real _run_speech error branch.
        raise RuntimeError("synthetic_late_retired_session_failure")


@pytest.mark.asyncio
async def test_recording_unlink_failure_detaches_paths_and_preserves_replacement_after_late_error(
    monkeypatch: pytest.MonkeyPatch,
    recording_probe_guard: dict[str, int],
    tmp_path: Path,
) -> None:
    original_tasks = asyncio.all_tasks()
    callbacks: list[str] = []

    async def failure_callback(**values: Any) -> None:
        callbacks.append(values["code"])

    match_id, speech_id, user_id = uuid4(), uuid4(), uuid4()
    settings = SimpleNamespace(agent_audio_storage_dir=str(tmp_path))
    target = MatchAudioReceiver(
        match_id=match_id,
        settings=cast(Any, settings),
        callbacks=cast(Any, SimpleNamespace(handle_asr_failure=failure_callback)),
    )
    target._start_recording(speech_id, user_id)
    target._write_recording(b"\0\0" * 16)
    old_spool = target._recording_spool_path
    old_storage = target._recording_storage_path
    assert old_spool is not None and old_storage is not None
    original_unlink = Path.unlink

    def fail_exact_owned_spool(path: Path, missing_ok: bool = False) -> None:
        if path == old_spool:
            raise OSError("synthetic_owned_spool_unlink_failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_exact_owned_spool)
    session = LateSession(speech_id)
    provider, room = OwnedResource(), OwnedResource()
    target._connection = cast(Any, provider)
    target._room = cast(Any, room)
    target._speech = cast(Any, session)
    callback_envelope = CallbackEnvelope(
        match_id=match_id,
        speech_id=speech_id,
        attempt_no=1,
        generation_id=None,
        connection_epoch=1,
        context_version=0,
        opportunity_id=None,
        opportunity_generation=None,
    )
    task = asyncio.create_task(target._run_speech(cast(Any, session), callback_envelope))
    target._speech_task = task
    replacement = MatchAudioReceiver(
        match_id=match_id,
        settings=cast(Any, settings),
        callbacks=cast(Any, SimpleNamespace(handle_asr_failure=failure_callback)),
    )
    payload = b"\0\0" * 32
    try:
        await asyncio.wait_for(session.entered.wait(), timeout=1)
        await asyncio.wait_for(target.close(), timeout=0.3)
        assert session.cancelled.is_set() and not task.done()
        assert target._closed and target._speech is None and target._speech_task is None
        assert target._connection is None and target._room is None
        assert provider.calls == room.calls == 1
        replacement._start_recording(speech_id, user_id)
        replacement._write_recording(payload)
        published = replacement._publish_recording()
        assert published == str(old_storage) and old_storage.is_file()
        assert old_storage.read_bytes() == payload
        session.release.set()
        done, pending = await asyncio.wait({task}, timeout=0.3)
        assert task in done and not pending
        deadline = asyncio.get_running_loop().time() + 0.3
        while provider.calls < 2 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.001)
        observations = {
            "replacement_exists_after_retired_error": old_storage.is_file(),
            "retired_storage_reference_retained": target._recording_storage_path == old_storage,
            "retired_spool_reference_retained": target._recording_spool_path == old_spool,
            "provider_close_calls": provider.calls,
            "room_disconnect_calls": room.calls,
            "forbidden_boundaries": recording_probe_guard,
        }
        # Product assertions precede any fixture discard/gather result retrieval.
        assert old_storage.is_file(), observations
        assert old_storage.read_bytes() == payload
        assert target._recording_file is None and target._recording_speech_id is None
        assert target._recording_spool_path is target._recording_storage_path is None
        assert target._recording_error is None
        assert not replacement._closed and replacement._recording_storage_path == old_storage
        assert provider.calls == 2 and room.calls == 1
        assert not task._log_traceback
        assert callbacks == []
    finally:
        session.release.set()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=1)
        monkeypatch.setattr(Path, "unlink", original_unlink)
        replacement._discard_recording()
        target._discard_recording()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        pending_tasks = asyncio.all_tasks() - original_tasks - {asyncio.current_task()}
        assert not pending_tasks, f"owned recording probe tasks remain: {pending_tasks}"

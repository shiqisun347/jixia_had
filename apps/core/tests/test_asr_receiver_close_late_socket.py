"""Actual FunAsrConnection cancellation can create a socket after receiver close.

Only the socket transport and already-owned Room are synthetic. The receiver
close, protocol wait_for, cancellation path, capture storage and byte ledger run
unchanged. Fixture cleanup runs after strong product observations.
"""

from __future__ import annotations

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false
import asyncio
import json
import socket
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from livekit.rtc import _ffi_client
from pydantic_settings import BaseSettings
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

import jx_core.asr.runtime as runtime_module
from jx_core.asr.protocol import FunAsrConnection, FunAsrSentence
from jx_core.asr.runtime import MatchAudioReceiver
from jx_core.data_capture.provider import ProviderCallCapture
from jx_core.data_capture.provider_budget import METADATA_BYTES, CaptureBudget


class GuardViolation(AssertionError):
    pass


@pytest.fixture
def forbid_external_boundaries(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, int]]:
    counts: dict[str, int] = {}

    def forbidden(category: str) -> Callable[..., Any]:
        def reject(*_: Any, **__: Any) -> Any:
            counts[category] = counts.get(category, 0) + 1
            raise GuardViolation(f"forbidden late-socket probe boundary: {category}")

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
    # Engine constructors are normally imported by name; also guard the public
    # module entry points before any runtime in this test could request one.
    monkeypatch.setattr("sqlalchemy.create_engine", forbidden("database"))
    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", forbidden("database"))
    monkeypatch.setattr(runtime_module, "ASR_TASK_CANCEL_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(runtime_module, "ASR_CLEANUP_TIMEOUT_SECONDS", 0.03)
    yield counts
    assert not counts, counts
    assert _ffi_client.FfiClient._instance is None


@dataclass
class OwnedSocket:
    close_calls: int = 0
    send_calls: int = 0
    recv_calls: int = 0
    pcm_frames: int = 0
    actions: list[str] = field(default_factory=lambda: list[str]())
    events: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue[str]())

    async def send(self, message: str | bytes) -> None:
        self.send_calls += 1
        if isinstance(message, bytes):
            self.pcm_frames += 1
            return
        payload = json.loads(message)
        action = payload["header"]["action"]
        self.actions.append(action)
        assert action in {"run-task", "finish-task"}
        event = "task-started" if action == "run-task" else "task-finished"
        self.events.put_nowait(
            json.dumps({"header": {"task_id": payload["header"]["task_id"], "event": event}})
        )

    async def recv(self) -> str | bytes:
        self.recv_calls += 1
        return await self.events.get()

    async def close(self) -> None:
        self.close_calls += 1


@dataclass
class OwnedRoom:
    disconnect_calls: int = 0

    async def disconnect(self) -> None:
        self.disconnect_calls += 1


@dataclass
class ResistantFactory:
    socket: OwnedSocket
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[Any] | None = None
    calls: int = 0
    cancellations: int = 0

    async def __call__(self, url: str, _: dict[str, str]) -> OwnedSocket:
        assert url == "wss://synthetic.invalid/asr"
        self.calls += 1
        self.task = asyncio.current_task()
        self.entered.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancellations += 1
                self.cancelled.set()
        return self.socket


async def no_audio() -> AsyncIterator[bytes]:
    if False:
        yield b""


async def ignored_sentence(_: FunAsrSentence) -> None:
    raise GuardViolation("retired startup must not invoke a sentence callback")


async def observe(predicate: Callable[[], bool], *, timeout: float = 0.3) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.001)
    await asyncio.sleep(0)
    return True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "late", [False, True], ids=["task-done-before-close", "factory-after-close"]
)
async def test_receiver_close_releases_actual_protocol_late_socket_and_capture_ledger(
    monkeypatch: pytest.MonkeyPatch,
    forbid_external_boundaries: dict[str, int],
    late: bool,
) -> None:
    baseline_tasks = asyncio.all_tasks()
    socket = OwnedSocket()
    room = OwnedRoom()
    factory = ResistantFactory(socket)
    budget = CaptureBudget()
    captures: list[ProviderCallCapture] = []

    def capture_factory() -> ProviderCallCapture:
        reservation = budget.reserve(METADATA_BYTES)
        assert reservation is not None
        capture = ProviderCallCapture(budget=budget, reservation=reservation, budgeted=True)
        captures.append(capture)
        return capture

    connection = FunAsrConnection(
        url="wss://synthetic.invalid/asr",
        api_key="synthetic-unused-material",
        socket_factory=factory,
        capture_factory=capture_factory,
    )
    original_close = connection.close
    provider_close_calls = 0

    async def counted_actual_close() -> None:
        nonlocal provider_close_calls
        provider_close_calls += 1
        await original_close()

    monkeypatch.setattr(connection, "close", counted_actual_close)
    receiver = MatchAudioReceiver(
        match_id=uuid4(), settings=cast(Any, SimpleNamespace()), callbacks=AsyncMock()
    )
    receiver._connection = connection
    receiver._room = cast(Any, room)
    recognition = asyncio.create_task(
        connection.recognize_segment(no_audio(), on_sentence=ignored_sentence),
        name="synthetic-owned-asr-late-factory-recognition",
    )
    receiver._speech_task = cast(asyncio.Task[None], cast(object, recognition))
    try:
        await asyncio.wait_for(factory.entered.wait(), timeout=1)
        assert factory.calls == 1 and len(captures) == 1
        assert budget.items == 1 and budget.retained_bytes > 0
        assert connection._socket is None and not connection._captures
        if not late:
            factory.release.set()
            assert await observe(recognition.done)
            assert not recognition.cancelled() and recognition.exception() is None
            # This is real protocol state before the product close. The
            # control must keep its one close, not need a retired finalizer.
            assert connection._socket is socket and len(connection._captures) == 1
            assert budget.items == 1 and socket.close_calls == 0

        await asyncio.wait_for(receiver.close(), timeout=0.3)
        assert receiver._closed and receiver._speech_task is None
        assert receiver._connection is None and receiver._room is None
        assert room.disconnect_calls == 1
        assert provider_close_calls == 1
        if late:
            assert factory.cancelled.is_set() and not recognition.done()
            assert not factory.release.is_set() and socket.close_calls == 0
            factory.release.set()
            assert await observe(recognition.done)
        if late:
            assert recognition.cancelled()
        else:
            assert not recognition.cancelled() and recognition.exception() is None
        await observe(
            lambda: (
                connection._socket is None
                and not connection._captures
                and socket.close_calls == 1
                and budget.items == budget.retained_bytes == budget.workspace_bytes == 0
            )
        )
        observations = {
            "late": late,
            "provider_close_calls": provider_close_calls,
            "socket_retained": cast(Any, connection._socket) is socket,
            "socket_close_calls": socket.close_calls,
            "capture_entries": len(connection._captures),
            "capture_reservations": budget.items,
            "retained_bytes": budget.retained_bytes,
            "workspace_bytes": budget.workspace_bytes,
            "room_disconnect_calls": room.disconnect_calls,
            "recognition_cancelled": recognition.cancelled(),
            "factory_calls": factory.calls,
            "factory_cancellations": factory.cancellations,
            "send_calls": socket.send_calls,
            "recv_calls": socket.recv_calls,
            "actions": list(socket.actions),
            "pcm_frames": socket.pcm_frames,
            "forbidden_boundaries": dict(forbid_external_boundaries),
        }
        print("owned_late_socket_product_observations=" + json.dumps(observations, sort_keys=True))
        assert not connection._captures and budget.items == budget.retained_bytes == 0, observations
        assert connection._socket is None and socket.close_calls == 1, observations
        assert budget.workspace_bytes == 0 and socket.pcm_frames == 0
        assert socket.actions == ([] if late else ["run-task", "finish-task"])
        assert not cast(AsyncMock, receiver._callbacks).mock_calls
        if not late:
            assert provider_close_calls == 1
    finally:
        # This hygiene deliberately follows the product snapshot/assertions.
        # Its extra close must never become evidence that the application closed.
        factory.release.set()
        if not recognition.done():
            recognition.cancel()
        await asyncio.wait_for(asyncio.gather(recognition, return_exceptions=True), timeout=1)
        if factory.task is not None:
            await asyncio.wait_for(asyncio.gather(factory.task, return_exceptions=True), timeout=1)
        await original_close()
        for capture in captures:
            capture.close()
        assert connection._socket is None and not connection._captures
        assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        pending = asyncio.all_tasks() - baseline_tasks - {asyncio.current_task()}
        assert not pending, f"owned late-socket fixture tasks remain pending: {pending}"

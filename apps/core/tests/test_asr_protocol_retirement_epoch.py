"""Strong lifecycle tests for actual ASR protocol; synthetic transport only.

These tests intentionally expose the pre-Spec302 protocol's retired requests.
They do not patch protocol methods, run Settings or invoke external services.
Owned fixture cleanup follows the product observations and strong assertions.
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
from jx_core.asr.protocol import FunAsrConnection, FunAsrError, FunAsrSentence, SegmentResult
from jx_core.asr.runtime import MatchAudioReceiver
from jx_core.data_capture.provider import ProviderCallCapture
from jx_core.data_capture.provider_budget import METADATA_BYTES, CaptureBudget


class GuardViolation(AssertionError):
    pass


@pytest.fixture
def external_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, int]]:
    counts: dict[str, int] = {}

    def forbidden(category: str) -> Callable[..., Any]:
        def reject(*_: Any, **__: Any) -> Any:
            counts[category] = counts.get(category, 0) + 1
            raise GuardViolation(f"forbidden retirement probe boundary: {category}")

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
    monkeypatch.setattr("sqlalchemy.create_engine", forbidden("database"))
    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", forbidden("database"))
    monkeypatch.setattr(runtime_module, "ASR_TASK_CANCEL_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(runtime_module, "ASR_CLEANUP_TIMEOUT_SECONDS", 0.03)
    yield counts
    assert not counts, counts
    assert _ffi_client.FfiClient._instance is None


@dataclass
class OwnedSocket:
    actions: list[str] = field(default_factory=lambda: list[str]())
    pcm_frames: int = 0
    close_calls: int = 0
    recv_calls: int = 0
    defer_start: bool = False
    emit_sentence: bool = False
    start_release: asyncio.Event = field(default_factory=asyncio.Event)
    recv_entered: asyncio.Event = field(default_factory=asyncio.Event)
    events: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue[str]())

    async def send(self, message: str | bytes) -> None:
        if isinstance(message, bytes):
            self.pcm_frames += 1
            return
        payload = json.loads(message)
        action = payload["header"]["action"]
        assert action in {"run-task", "finish-task"}
        self.actions.append(action)
        if action == "finish-task" and self.emit_sentence:
            self.events.put_nowait(
                json.dumps(
                    {
                        "header": {
                            "task_id": payload["header"]["task_id"],
                            "event": "result-generated",
                        },
                        "payload": {
                            "output": {
                                "sentence": {
                                    "text": "synthetic sentence",
                                    "sentence_end": True,
                                    "sentence_id": 1,
                                }
                            }
                        },
                    }
                )
            )
        self.events.put_nowait(
            json.dumps(
                {
                    "header": {
                        "task_id": payload["header"]["task_id"],
                        "event": "task-started" if action == "run-task" else "task-finished",
                    }
                }
            )
        )

    async def recv(self) -> str | bytes:
        self.recv_calls += 1
        self.recv_entered.set()
        if self.defer_start and self.recv_calls == 1:
            await self.start_release.wait()
        return await self.events.get()

    async def close(self) -> None:
        self.close_calls += 1


@dataclass
class ResistantFactory:
    owned_socket: OwnedSocket
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    calls: int = 0
    cancellations: int = 0
    task: asyncio.Task[Any] | None = None

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
        return self.owned_socket


@dataclass
class Probe:
    budget: CaptureBudget = field(default_factory=CaptureBudget)
    captures: list[ProviderCallCapture] = field(default_factory=lambda: list[ProviderCallCapture]())
    started: int = 0
    sentence_calls: int = 0

    def capture_factory(self) -> ProviderCallCapture:
        reservation = self.budget.reserve(METADATA_BYTES)
        assert reservation is not None
        capture = ProviderCallCapture(budget=self.budget, reservation=reservation, budgeted=True)
        self.captures.append(capture)
        return capture

    def on_started(self) -> None:
        self.started += 1

    async def on_sentence(self, _: FunAsrSentence) -> None:
        self.sentence_calls += 1

    def connection(self, factory: Any) -> FunAsrConnection:
        return FunAsrConnection(
            url="wss://synthetic.invalid/asr",
            api_key="synthetic-unused-material",
            socket_factory=factory,
            capture_factory=self.capture_factory,
            start_timeout_seconds=1,
            task_timeout_seconds=2,
        )

    def snapshot(
        self, connection: FunAsrConnection, owned_socket: OwnedSocket, task: asyncio.Task[Any]
    ) -> dict[str, Any]:
        exception = None if task.cancelled() else task.exception() if task.done() else None
        return {
            "actions": list(owned_socket.actions),
            "pcm_frames": owned_socket.pcm_frames,
            "socket_close_calls": owned_socket.close_calls,
            "socket_retained": connection._socket is owned_socket,
            "capture_entries": len(connection._captures),
            "reservation_items": self.budget.items,
            "retained_bytes": self.budget.retained_bytes,
            "workspace_bytes": self.budget.workspace_bytes,
            "capture_objects": len(self.captures),
            "started_calls": self.started,
            "sentence_calls": self.sentence_calls,
            "task_done": task.done(),
            "task_cancelled": task.cancelled(),
            "exception_type": type(exception).__name__ if exception is not None else None,
        }


async def no_audio() -> AsyncIterator[bytes]:
    if False:
        yield b""


async def observe(predicate: Callable[[], bool], *, timeout: float = 0.4) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.001)
    await asyncio.sleep(0)
    return True


async def hygiene(
    connection: FunAsrConnection,
    probe: Probe,
    tasks: list[asyncio.Task[Any]],
    baseline: set[asyncio.Task[Any]],
) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=1)
    await asyncio.wait_for(connection.close(), timeout=1)
    for capture in probe.captures:
        capture.close()
    assert probe.budget.items == probe.budget.retained_bytes == probe.budget.workspace_bytes == 0
    current = asyncio.current_task()
    # Recognition's done callback can schedule an exact Provider tail owner.
    # Observe it after product assertions; two event-loop turns are insufficient
    # for its nested bounded socket-close owner to reach terminal state.
    finished = await observe(lambda: not asyncio.all_tasks() - baseline - {current})
    pending = asyncio.all_tasks() - baseline - {current}
    print(
        "retirement_fixture_observations="
        + json.dumps(
            {
                "all_owned_tasks_terminal": finished,
                "pending_names": sorted(task.get_name() for task in pending),
                "reservation_items": probe.budget.items,
                "retained_bytes": probe.budget.retained_bytes,
                "workspace_bytes": probe.budget.workspace_bytes,
            },
            sort_keys=True,
        )
    )
    assert finished and not pending, pending


@pytest.mark.asyncio
@pytest.mark.parametrize("via_receiver", [False, True], ids=["provider-close", "receiver-close"])
async def test_retired_factory_cannot_send_run_or_finish_after_close(
    external_guard: dict[str, int], via_receiver: bool
) -> None:
    baseline = asyncio.all_tasks()
    probe = Probe()
    owned_socket = OwnedSocket()
    factory = ResistantFactory(owned_socket)
    connection = probe.connection(factory)
    recognition = asyncio.create_task(
        connection.recognize_segment(
            no_audio(), on_sentence=probe.on_sentence, on_started=probe.on_started
        )
    )
    room = SimpleNamespace(disconnect=AsyncMock())
    receiver = MatchAudioReceiver(
        match_id=uuid4(), settings=cast(Any, SimpleNamespace()), callbacks=AsyncMock()
    )
    receiver._connection = connection
    receiver._room = cast(Any, room)
    receiver._speech_task = cast(asyncio.Task[None], cast(object, recognition))
    try:
        await asyncio.wait_for(factory.entered.wait(), timeout=1)
        assert connection._socket is None and probe.budget.items == 1
        if via_receiver:
            await asyncio.wait_for(receiver.close(), timeout=0.3)
            assert receiver._closed and receiver._connection is None
            assert room.disconnect.await_count == 1
        else:
            recognition.cancel()
            await asyncio.wait_for(factory.cancelled.wait(), timeout=0.3)
            await asyncio.wait_for(connection.close(), timeout=0.3)
        assert factory.cancelled.is_set() and not recognition.done()
        assert not factory.release.is_set() and not owned_socket.actions
        factory.release.set()
        assert await observe(recognition.done)
        await observe(
            lambda: (
                connection._socket is None
                and not connection._captures
                and probe.budget.items == probe.budget.retained_bytes == 0
            )
        )
        snapshot = probe.snapshot(connection, owned_socket, recognition)
        snapshot.update(via_receiver=via_receiver, guard=dict(external_guard))
        print("retired_factory_product_observations=" + json.dumps(snapshot, sort_keys=True))
        assert not owned_socket.actions and owned_socket.pcm_frames == 0, snapshot
        assert probe.started == probe.sentence_calls == 0, snapshot
        assert recognition.cancelled(), snapshot
        assert connection._socket is None and not connection._captures, snapshot
        assert probe.budget.items == probe.budget.retained_bytes == 0, snapshot
        assert owned_socket.close_calls >= 1 and not external_guard
    finally:
        factory.release.set()
        tasks = [recognition] + ([factory.task] if factory.task is not None else [])
        await hygiene(connection, probe, tasks, baseline)


@pytest.mark.asyncio
async def test_recognition_queued_before_close_cannot_start_in_new_epoch(
    external_guard: dict[str, int],
) -> None:
    baseline = asyncio.all_tasks()
    probe = Probe()
    owned_socket = OwnedSocket()
    factory_calls = 0

    async def factory(_: str, __: dict[str, str]) -> OwnedSocket:
        nonlocal factory_calls
        factory_calls += 1
        return owned_socket

    connection = probe.connection(factory)
    await connection._lock.acquire()
    recognition = asyncio.create_task(
        connection.recognize_segment(no_audio(), on_sentence=probe.on_sentence)
    )
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert connection._lock.locked() and not recognition.done()
        assert factory_calls == len(probe.captures) == 0
        await asyncio.wait_for(connection.close(), timeout=0.3)
        connection._lock.release()
        assert await observe(recognition.done)
        snapshot = probe.snapshot(connection, owned_socket, recognition)
        snapshot.update(factory_calls=factory_calls, guard=dict(external_guard))
        print("queued_recognition_product_observations=" + json.dumps(snapshot, sort_keys=True))
        assert factory_calls == len(probe.captures) == 0, snapshot
        assert recognition.cancelled() and not owned_socket.actions, snapshot
        assert connection._socket is None and not connection._captures, snapshot
        assert probe.budget.items == probe.budget.retained_bytes == 0
        assert not external_guard
    finally:
        if connection._lock.locked():
            connection._lock.release()
        await hygiene(connection, probe, [recognition], baseline)


@pytest.mark.asyncio
async def test_pcm_iterator_resuming_after_close_cannot_send_pcm_or_finish(
    external_guard: dict[str, int],
) -> None:
    baseline = asyncio.all_tasks()
    probe = Probe()
    owned_socket = OwnedSocket()
    chunks_entered, chunks_release = asyncio.Event(), asyncio.Event()

    async def factory(_: str, __: dict[str, str]) -> OwnedSocket:
        return owned_socket

    async def chunks() -> AsyncIterator[bytes]:
        chunks_entered.set()
        await chunks_release.wait()
        yield b"\x00" * 3200

    connection = probe.connection(factory)
    recognition = asyncio.create_task(
        connection.recognize_segment(
            chunks(), on_sentence=probe.on_sentence, on_started=probe.on_started
        )
    )
    try:
        await asyncio.wait_for(chunks_entered.wait(), timeout=1)
        assert probe.started == 1 and owned_socket.actions == ["run-task"]
        await asyncio.wait_for(connection.close(), timeout=0.3)
        assert owned_socket.close_calls == 1 and connection._socket is None
        chunks_release.set()
        assert await observe(recognition.done)
        snapshot = probe.snapshot(connection, owned_socket, recognition)
        print("retired_pcm_product_observations=" + json.dumps(snapshot, sort_keys=True))
        assert owned_socket.pcm_frames == 0 and owned_socket.actions == ["run-task"], snapshot
        assert recognition.cancelled(), snapshot
        assert not connection._captures and probe.budget.items == probe.budget.retained_bytes == 0
        assert not external_guard
    finally:
        chunks_release.set()
        await hygiene(connection, probe, [recognition], baseline)


@pytest.mark.asyncio
async def test_task_started_received_after_close_cannot_publish_ready_or_finish(
    external_guard: dict[str, int],
) -> None:
    baseline = asyncio.all_tasks()
    probe = Probe()
    owned_socket = OwnedSocket(defer_start=True)

    async def factory(_: str, __: dict[str, str]) -> OwnedSocket:
        return owned_socket

    connection = probe.connection(factory)
    recognition = asyncio.create_task(
        connection.recognize_segment(
            no_audio(), on_sentence=probe.on_sentence, on_started=probe.on_started
        )
    )
    try:
        await asyncio.wait_for(owned_socket.recv_entered.wait(), timeout=1)
        assert owned_socket.actions == ["run-task"] and probe.started == 0
        await asyncio.wait_for(connection.close(), timeout=0.3)
        assert owned_socket.close_calls == 1
        owned_socket.start_release.set()
        assert await observe(recognition.done)
        snapshot = probe.snapshot(connection, owned_socket, recognition)
        print("retired_ready_product_observations=" + json.dumps(snapshot, sort_keys=True))
        assert probe.started == 0 and owned_socket.actions == ["run-task"], snapshot
        assert recognition.cancelled() and probe.sentence_calls == 0, snapshot
        assert not connection._captures and probe.budget.items == probe.budget.retained_bytes == 0
        assert not external_guard
    finally:
        owned_socket.start_release.set()
        await hygiene(connection, probe, [recognition], baseline)


@pytest.mark.asyncio
async def test_normal_segments_reuse_socket_and_new_recognition_after_close_reconnects(
    external_guard: dict[str, int],
) -> None:
    baseline = asyncio.all_tasks()
    probe = Probe()
    sockets = [OwnedSocket(), OwnedSocket()]
    factory_calls = 0

    async def factory(_: str, __: dict[str, str]) -> OwnedSocket:
        nonlocal factory_calls
        value = sockets[factory_calls]
        factory_calls += 1
        return value

    connection = probe.connection(factory)
    tasks: list[asyncio.Task[SegmentResult]] = []
    try:
        for _ in range(2):
            task = asyncio.create_task(
                connection.recognize_segment(no_audio(), on_sentence=probe.on_sentence)
            )
            tasks.append(task)
            result = await asyncio.wait_for(task, timeout=0.3)
            capture = connection.take_capture(result.task_id)
            assert capture is not None
            capture.close()
        assert factory_calls == 1 and sockets[0].close_calls == 0
        assert sockets[0].actions == ["run-task", "finish-task", "run-task", "finish-task"]
        await asyncio.wait_for(connection.close(), timeout=0.3)
        assert sockets[0].close_calls == 1 and connection._socket is None
        task = asyncio.create_task(
            connection.recognize_segment(no_audio(), on_sentence=probe.on_sentence)
        )
        tasks.append(task)
        result = await asyncio.wait_for(task, timeout=0.3)
        assert factory_calls == 2 and connection._socket is sockets[1]
        assert sockets[1].actions == ["run-task", "finish-task"]
        capture = connection.take_capture(result.task_id)
        assert capture is not None
        capture.close()
        assert probe.budget.items == probe.budget.retained_bytes == 0
        assert not external_guard
    finally:
        await hygiene(connection, probe, cast(list[asyncio.Task[Any]], tasks), baseline)


@pytest.mark.asyncio
async def test_sentence_callback_returning_after_close_cannot_publish_success_or_capture(
    external_guard: dict[str, int],
) -> None:
    baseline = asyncio.all_tasks()
    probe = Probe()
    owned_socket = OwnedSocket(emit_sentence=True)
    callback_entered, callback_release = asyncio.Event(), asyncio.Event()

    async def factory(_: str, __: dict[str, str]) -> OwnedSocket:
        return owned_socket

    async def on_sentence(sentence: FunAsrSentence) -> None:
        await probe.on_sentence(sentence)
        callback_entered.set()
        await callback_release.wait()

    connection = probe.connection(factory)
    recognition = asyncio.create_task(
        connection.recognize_segment(no_audio(), on_sentence=on_sentence)
    )
    try:
        await asyncio.wait_for(callback_entered.wait(), timeout=1)
        assert probe.sentence_calls == 1 and not recognition.done()
        assert owned_socket.actions == ["run-task", "finish-task"]
        await asyncio.wait_for(connection.close(), timeout=0.3)
        assert connection._socket is None and owned_socket.close_calls == 1
        callback_release.set()
        assert await observe(recognition.done)
        snapshot = probe.snapshot(connection, owned_socket, recognition)
        print("retired_callback_product_observations=" + json.dumps(snapshot, sort_keys=True))
        assert recognition.cancelled(), snapshot
        assert probe.sentence_calls == 1 and not connection._captures, snapshot
        assert probe.budget.items == probe.budget.retained_bytes == 0, snapshot
        assert not external_guard
    finally:
        callback_release.set()
        await hygiene(connection, probe, [recognition], baseline)


@pytest.mark.asyncio
async def test_normal_factory_error_keeps_asr_connection_failed_and_owned_capture(
    external_guard: dict[str, int],
) -> None:
    baseline = asyncio.all_tasks()
    probe = Probe()

    async def factory(_: str, __: dict[str, str]) -> OwnedSocket:
        raise ConnectionError("synthetic factory failure")

    connection = probe.connection(factory)
    task_id = uuid4()
    recognition = asyncio.create_task(
        connection.recognize_segment(no_audio(), on_sentence=probe.on_sentence, task_id=task_id)
    )
    try:
        with pytest.raises(FunAsrError, match="asr_connection_failed"):
            await asyncio.wait_for(recognition, timeout=0.3)
        assert connection._socket is None and len(connection._captures) == 1
        capture = connection.take_capture(task_id)
        assert capture is not None and capture.request is not None and capture.response is not None
        capture.close()
        assert probe.budget.items == probe.budget.retained_bytes == 0
        assert probe.started == probe.sentence_calls == 0 and not external_guard
    finally:
        await hygiene(connection, probe, [recognition], baseline)

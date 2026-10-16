"""ASR startup ownership through the installed SDK and fixture-owned FFI.

Only FFI transport, token material and forbidden external boundaries are fakes.
The actual receiver, registry, manager timeout and SDK connect/ready/disconnect
methods run unchanged. Fixture cleanup follows all product assertions.
"""

from __future__ import annotations

# These tests deliberately inspect SDK subscriptions and ASR startup ownership.
# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false
import asyncio
import importlib
import socket
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from importlib.metadata import version
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import sqlalchemy.ext.asyncio as sqlalchemy_asyncio
from livekit.rtc import _ffi_client, participant, room, track, track_publication
from livekit.rtc._proto import ffi_pb2
from pydantic_settings import BaseSettings


class GuardViolation(AssertionError):
    pass


class OwnedQueue(_ffi_client.FfiQueue[Any]):
    def __init__(self) -> None:
        super().__init__()
        self.owned: list[Any] = []

    def subscribe(self, *args: Any, **kwargs: Any) -> Any:
        value = super().subscribe(*args, **kwargs)
        self.owned.append(value)
        return value

    @property
    def count(self) -> int:
        return len(self._subscribers)

    def release_fixture_owned(self) -> None:
        for value in self.owned:
            self.unsubscribe(value)


@dataclass
class SyntheticHandle:
    handle: int
    disposed: bool = False

    def dispose(self) -> None:
        self.disposed = True


@dataclass
class Join:
    async_id: int
    room: room.Room
    task: asyncio.Task[Any]
    room_handle: int
    participant_handle: int
    delivered: bool = False
    actions: list[str] = field(default_factory=lambda: list[str]())


class SyntheticMaterial:
    def get_secret_value(self) -> str:
        return "synthetic-unused-material"


class SyntheticToken:
    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    def with_identity(self, *_: Any) -> SyntheticToken:
        return self

    def with_ttl(self, *_: Any) -> SyntheticToken:
        return self

    def with_grants(self, *_: Any) -> SyntheticToken:
        return self

    def to_jwt(self) -> str:
        return "synthetic-unused-token"


class FakeFfi:
    def __init__(self, case: AsrSdkCase) -> None:
        self.case = case
        self.queue = OwnedQueue()
        self.joins: list[Join] = []
        self.next_id = 0

    def request(self, request: Any) -> Any:
        self.next_id += 1
        kind = request.WhichOneof("message")
        response = ffi_pb2.FfiResponse()
        if kind == "connect":
            current = asyncio.current_task()
            assert current is not None
            handle = 1000 + len(self.joins) * 10
            value = Join(self.next_id, self.case.rooms[-1], current, handle, handle + 1)
            value.actions.append("connect")
            self.joins.append(value)
            response.connect.async_id = value.async_id
        elif kind == "ready_for_room_event":
            value = self.for_room_handle(request.ready_for_room_event.room_handle)
            assert value.delivered and value.room.isconnected()
            assert value.room._task is not None and not value.room._task.done()
            assert asyncio.current_task() is value.task, "ready must come from SDK connect"
            value.actions.append("ready")
        elif kind == "disconnect":
            value = self.for_room_handle(request.disconnect.room_handle)
            assert "ready" in value.actions, "public disconnect must follow SDK ready"
            value.actions.append("disconnect")
            response.disconnect.async_id = self.next_id
            event = ffi_pb2.FfiEvent()
            event.disconnect.async_id = self.next_id
            self.queue.put(event)
            eos = ffi_pb2.FfiEvent()
            eos.room_event.room_handle = value.room_handle
            eos.room_event.eos.SetInParent()
            value.actions.append("eos")
            self.queue.put(eos)
        else:
            raise GuardViolation(f"unsupported synthetic FFI request: {kind}")
        return response

    def for_room_handle(self, handle: int) -> Join:
        return next(value for value in self.joins if value.room_handle == handle)

    def deliver(self, value: Join, *, error: bool) -> None:
        assert not value.delivered
        value.delivered = True
        event = ffi_pb2.FfiEvent()
        event.connect.async_id = value.async_id
        if error:
            value.actions.append("connect_error")
            event.connect.error = "synthetic_connect_rejection"
        else:
            value.actions.append("connect_success")
            event.connect.result.room.handle.id = value.room_handle
            event.connect.result.room.info.sid = f"RM_synthetic_{value.async_id}"
            event.connect.result.local_participant.handle.id = value.participant_handle
            event.connect.result.local_participant.info.sid = f"PA_synthetic_{value.async_id}"
        self.queue.put(event)


@dataclass
class AsrSdkCase:
    module: ModuleType | None = None
    service_module: ModuleType | None = None
    receiver: Any = None
    runtime: Any = None
    manager: Any = None
    match_id: UUID = field(default_factory=uuid4)
    rooms: list[room.Room] = field(default_factory=lambda: list[room.Room]())
    handles: list[SyntheticHandle] = field(default_factory=lambda: list[SyntheticHandle]())
    connections: list[Any] = field(default_factory=lambda: list[Any]())
    snapshots: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    guard_attempts: dict[str, int] = field(default_factory=lambda: dict[str, int]())
    tasks_before: set[asyncio.Task[Any]] = field(default_factory=lambda: set[asyncio.Task[Any]]())
    ffi: FakeFfi = field(init=False)

    def __post_init__(self) -> None:
        self.ffi = FakeFfi(self)

    def guard(self, category: str) -> Callable[..., Any]:
        def forbidden(*_: Any, **__: Any) -> Any:
            self.guard_attempts[category] = self.guard_attempts.get(category, 0) + 1
            raise GuardViolation(f"forbidden {category} boundary")

        return forbidden

    def new_room(self, *_: Any, **__: Any) -> room.Room:
        value = room.Room()
        self.rooms.append(value)
        return value

    def new_handle(self, value: int) -> SyntheticHandle:
        handle = SyntheticHandle(value)
        self.handles.append(handle)
        return handle

    async def observe(self, predicate: Callable[[], bool], *, timeout: float = 0.25) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.001)
        await asyncio.sleep(0)
        return True

    async def next_join(self, caller: asyncio.Task[Any]) -> Join:
        count = len(self.ffi.joins)
        assert await self.observe(lambda: len(self.ffi.joins) > count or caller.done())
        assert len(self.ffi.joins) > count, "caller failed before synthetic SDK request"
        return self.ffi.joins[-1]

    def snapshot(self, phase: str, caller: asyncio.Task[Any], value: Join) -> None:
        self.snapshots.append(
            {
                "phase": phase,
                "subscriptions": self.ffi.queue.count,
                "join_done": value.task.done(),
                "join_cancelled": value.task.cancelled(),
                "join_is_caller": value.task is caller,
                "actions": list(value.actions),
                "registry_contains_match": self.match_id in self.runtime._receivers,
                "receiver_closed": self.receiver._closed,
                "receiver_has_room": self.receiver._room is not None,
                "provider_constructions": len(self.connections),
            }
        )

    async def retired(self, trigger: str) -> tuple[asyncio.Task[Any], Join]:
        assert self.module is not None and self.service_module is not None
        if trigger == "manager-timeout":
            previous = SimpleNamespace(match_id=self.match_id, free_source_speech_id=None)
            candidate = SimpleNamespace(current_speech_id=uuid4(), speech_remaining_ms=180000)
            command = self.service_module.MatchCommand(
                type="speech.start", message_id="synthetic-start", actor_user_id=uuid4()
            )
            caller = asyncio.create_task(self.manager._pre_commit(previous, candidate, (), command))
        else:
            caller = asyncio.create_task(self.runtime.ensure_match(self.match_id))
        value = await self.next_join(caller)
        self.receiver = self.runtime._receivers[self.match_id]
        assert self.ffi.queue.count == 2, "SDK request must start with both subscriptions"
        if trigger == "external-cancel":
            caller.cancel()
            assert await self.observe(caller.done)
            with pytest.raises(asyncio.CancelledError):
                await caller
        elif trigger == "receiver-timeout":
            assert await self.observe(caller.done)
            with pytest.raises(self.module.FunAsrError) as caught:
                await caller
            assert caught.value.code == "asr_start_timeout"
        else:
            assert trigger == "manager-timeout"
            assert await self.observe(caller.done)
            with pytest.raises(self.service_module.MatchDomainError, match="asr_start_timeout"):
                await caller
            assert self.manager._prepared_speech_starts == set()
        self.snapshot("after-caller-retired", caller, value)
        return caller, value

    def assert_no_pending_owned_tasks(self) -> None:
        pending = asyncio.all_tasks() - self.tasks_before - {asyncio.current_task()}
        assert not pending, f"owned ASR/SDK tasks still pending: {pending}"

    async def fixture_cleanup(self) -> None:
        # Complete and close only owned fake protocol objects, after assertions.
        for value in self.ffi.joins:
            if not value.delivered:
                self.ffi.deliver(value, error=True)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await self.runtime.close()
        await self.receiver.close()
        for value in self.rooms:
            if value.isconnected():
                closing = asyncio.create_task(value.disconnect())
                done, _ = await asyncio.wait({closing}, timeout=0.3)
                assert closing in done, "synthetic fixture disconnect did not settle"
                await asyncio.gather(closing, return_exceptions=True)
        owned = asyncio.all_tasks() - self.tasks_before - {asyncio.current_task()}
        for task in owned:
            task.cancel()
        if owned:
            done, pending = await asyncio.wait(owned, timeout=0.3)
            assert not pending, "synthetic fixture task failed to settle"
            await asyncio.gather(*done, return_exceptions=True)
        for value in self.ffi.joins:
            await asyncio.gather(value.task, return_exceptions=True)
        for connection in self.connections:
            await connection.close()
        self.ffi.queue.release_fixture_owned()
        assert self.ffi.queue.count == 0
        for value in self.rooms:
            # Prevent a later real Room.__del__ from consulting restored singleton.
            value._ffi_handle = None
        for handle in self.handles:
            handle.dispose()
        assert all(handle.disposed for handle in self.handles)
        assert not self.guard_attempts, self.guard_attempts
        self.assert_no_pending_owned_tasks()


@pytest_asyncio.fixture
async def asr_sdk_case(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsrSdkCase]:
    assert version("livekit") == "1.1.20", "the validated installed SDK must remain pinned"
    case = AsrSdkCase()
    case.tasks_before = asyncio.all_tasks()
    original = _ffi_client.FfiClient._instance
    assert original is None, "native-free SDK test requires an untouched singleton"
    with monkeypatch.context() as patches:
        patches.setattr(_ffi_client.FfiClient, "__init__", case.guard("native init"))
        patches.setattr(_ffi_client, "get_ffi_lib", case.guard("native library"))
        patches.setattr(_ffi_client.FfiHandle, "__init__", case.guard("native handle"))
        patches.setattr(socket.socket, "connect", case.guard("network"))
        patches.setattr(socket.socket, "connect_ex", case.guard("network"))
        patches.setattr(socket, "create_connection", case.guard("network"))
        patches.setattr(BaseSettings, "__init__", case.guard("Settings"))
        patches.setattr(sqlalchemy_asyncio, "create_async_engine", case.guard("database"))
        patches.setattr(sqlalchemy_asyncio.AsyncSession, "__init__", case.guard("database"))
        patches.setattr(sqlalchemy_asyncio.AsyncEngine, "connect", case.guard("database"))
        patches.setattr(_ffi_client.FfiClient, "_instance", case.ffi)
        for module in (room, participant, track, track_publication):
            patches.setattr(module, "FfiHandle", case.new_handle)
        case.module = importlib.import_module("jx_core.asr.runtime")
        case.service_module = importlib.import_module("jx_core.matches.service")
        patches.setattr(case.module.rtc, "Room", case.new_room)
        patches.setattr(case.module.rtc, "AudioSource", case.guard("media source"))
        patches.setattr(case.module.rtc, "AudioStream", case.guard("media consumption"))
        patches.setattr(case.module.api, "AccessToken", SyntheticToken)
        connection_class = case.module.FunAsrConnection

        def new_connection(**kwargs: Any) -> Any:
            value = connection_class(**kwargs)
            case.connections.append(value)
            return value

        patches.setattr(case.module, "FunAsrConnection", new_connection)
        settings = SimpleNamespace(
            livekit_url="wss://synthetic.invalid",
            livekit_api_key=SyntheticMaterial(),
            livekit_api_secret=SyntheticMaterial(),
            asr_api_key=SyntheticMaterial(),
            asr_ws_url="wss://synthetic.invalid/asr",
            asr_model="synthetic-unused-model",
            asr_workspace_id=None,
        )
        case.receiver = case.module.MatchAudioReceiver(
            match_id=case.match_id, settings=settings, callbacks=SimpleNamespace()
        )
        case.runtime = case.module.AsrRuntime(settings=settings, callbacks=SimpleNamespace())
        case.manager = case.service_module.MatchRuntimeManager(cast(Any, None))
        case.manager.set_speech_runtime(case.runtime)
        try:
            yield case
        finally:
            await case.fixture_cleanup()
    assert _ffi_client.FfiClient._instance is original


@pytest.mark.asyncio
async def test_control_actual_sdk_success_ready_disconnect(asr_sdk_case: AsrSdkCase) -> None:
    case = asr_sdk_case
    value = case.new_room()
    caller = asyncio.create_task(value.connect("wss://synthetic.invalid", "synthetic-token"))
    join = await case.next_join(caller)
    case.ffi.deliver(join, error=False)
    await caller
    assert value.isconnected() and join.actions == ["connect", "connect_success", "ready"]
    await value.disconnect()
    assert join.actions == ["connect", "connect_success", "ready", "disconnect", "eos"]
    assert case.ffi.queue.count == 0
    case.assert_no_pending_owned_tasks()


@pytest.mark.asyncio
async def test_control_actual_sdk_rejection_releases_both_subscriptions(
    asr_sdk_case: AsrSdkCase,
) -> None:
    case = asr_sdk_case
    value = case.new_room()
    caller = asyncio.create_task(value.connect("wss://synthetic.invalid", "synthetic-token"))
    join = await case.next_join(caller)
    case.ffi.deliver(join, error=True)
    with pytest.raises(room.ConnectError):
        await caller
    assert case.ffi.queue.count == 0 and not case.handles
    case.assert_no_pending_owned_tasks()


@pytest.mark.asyncio
async def test_control_receiver_start_transfers_room_and_creates_only_unopened_provider(
    asr_sdk_case: AsrSdkCase,
) -> None:
    case = asr_sdk_case
    caller = asyncio.create_task(case.receiver.start())
    join = await case.next_join(caller)
    case.ffi.deliver(join, error=False)
    await caller
    assert case.receiver._room is join.room and join.room.isconnected()
    assert case.receiver._connection is case.connections[0]
    assert len(case.connections) == 1 and case.connections[0]._socket is None
    assert join.actions == ["connect", "connect_success", "ready"]
    await case.receiver.close()
    assert join.actions == ["connect", "connect_success", "ready", "disconnect", "eos"]
    assert case.ffi.queue.count == 0
    case.assert_no_pending_owned_tasks()


@pytest.mark.asyncio
async def test_control_ensure_match_rejection_removes_and_closes_exact_receiver(
    asr_sdk_case: AsrSdkCase,
) -> None:
    case = asr_sdk_case
    caller = asyncio.create_task(case.runtime.ensure_match(case.match_id))
    join = await case.next_join(caller)
    receiver = case.runtime._receivers[case.match_id]
    case.receiver = receiver
    case.ffi.deliver(join, error=True)
    with pytest.raises(room.ConnectError):
        await caller
    assert case.match_id not in case.runtime._receivers and receiver._closed
    assert case.ffi.queue.count == 0 and not case.connections
    case.assert_no_pending_owned_tasks()


@pytest.mark.asyncio
async def test_control_cancelled_concurrent_waiter_preserves_active_receiver_join(
    asr_sdk_case: AsrSdkCase,
) -> None:
    case = asr_sdk_case
    active = asyncio.create_task(case.runtime.ensure_match(case.match_id))
    join = await case.next_join(active)
    receiver = case.runtime._receivers[case.match_id]
    case.receiver = receiver
    waiting = asyncio.create_task(case.runtime.ensure_match(case.match_id))
    assert await case.observe(lambda: len(receiver._lifecycle_lock._waiters or ()) == 1)
    assert len(case.ffi.joins) == 1 and not active.done() and not waiting.done()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    case.snapshot("after-concurrent-waiter-cancel", waiting, join)
    assert case.runtime._receivers[case.match_id] is receiver and not receiver._closed
    assert not active.done() and not join.task.done() and not join.task.cancelled()
    assert case.ffi.queue.count == 2 and not case.connections
    case.ffi.deliver(join, error=False)
    result = await active
    case.snapshot("after-active-join-success", active, join)
    assert result is receiver and case.runtime._receivers[case.match_id] is receiver
    assert receiver._room is join.room and join.room.isconnected()
    assert len(case.connections) == 1 and receiver._connection is case.connections[0]
    assert join.actions == ["connect", "connect_success", "ready"]
    await case.runtime.close()
    assert join.actions == ["connect", "connect_success", "ready", "disconnect", "eos"]
    assert case.ffi.queue.count == 0
    case.assert_no_pending_owned_tasks()


@pytest.mark.asyncio
async def test_external_cancel_preserves_pending_sdk_owner_and_removes_failed_registry_entry(
    asr_sdk_case: AsrSdkCase,
) -> None:
    case = asr_sdk_case
    caller, join = await case.retired("external-cancel")
    assert join.task is not caller and not join.task.done() and not join.task.cancelled()
    assert case.ffi.queue.count == 2, "cancelled ASR start lost the pending SDK owner"
    assert case.match_id not in case.runtime._receivers, "failed receiver stayed registered"
    assert case.receiver._closed and case.receiver._room is None


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ("external-cancel", "receiver-timeout", "manager-timeout"))
@pytest.mark.parametrize("late_error", (True, False), ids=("late-error", "late-success"))
async def test_retired_asr_start_settles_actual_sdk_then_closes_only_old_room(
    asr_sdk_case: AsrSdkCase,
    monkeypatch: pytest.MonkeyPatch,
    trigger: str,
    late_error: bool,
) -> None:
    case = asr_sdk_case
    assert case.module is not None and case.service_module is not None
    if trigger == "receiver-timeout":
        monkeypatch.setattr(case.module, "LIVEKIT_CONNECT_TIMEOUT_SECONDS", 0.01)
    if trigger == "manager-timeout":
        monkeypatch.setattr(case.service_module, "HUMAN_SPEECH_START_TIMEOUT_SECONDS", 0.01)
    caller, join = await case.retired(trigger)
    case.ffi.deliver(join, error=late_error)
    await case.observe(
        lambda: (
            join.task.done()
            and case.ffi.queue.count == 0
            and (late_error or "disconnect" in join.actions)
        )
    )
    await asyncio.sleep(0)
    case.snapshot("after-late-result", caller, join)
    assert case.ffi.queue.count == 0, case.snapshots
    assert join.task.done() and not join.task.cancelled(), case.snapshots
    assert case.match_id not in case.runtime._receivers, case.snapshots
    assert case.receiver._closed and case.receiver._room is None, case.snapshots
    assert not case.connections and not case.receiver._track_tasks, case.snapshots
    if late_error:
        assert join.actions == ["connect", "connect_error"]
        assert isinstance(join.task._exception, room.ConnectError)
        assert not join.task._log_traceback, "late SDK failure lacked a consuming owner"
    else:
        assert join.actions == ["connect", "connect_success", "ready", "disconnect", "eos"]
        assert not join.room.isconnected()
        assert {join.room_handle, join.participant_handle}.issubset(
            {handle.handle for handle in case.handles}
        )
    case.assert_no_pending_owned_tasks()

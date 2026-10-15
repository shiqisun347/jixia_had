"""Actual SDK join ownership with synthetic FFI; no native RTC or services.

The SDK's public connect/ready/disconnect and publication methods are unchanged.
Fixture-only unsubscribe/handle disposal happens after product assertions, never
as a substitute for application retirement. Native handles below are fakes.
"""

from __future__ import annotations

# SDK ownership and late-error consumption deliberately inspect private state.
# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false
import asyncio
import importlib
import socket
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
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
    def __init__(self, case: SdkCase) -> None:
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
            assert current_task_is(value.task), "SDK connect must send its own ready"
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
        elif kind == "publish_track":
            value = next(
                item
                for item in self.joins
                if item.participant_handle == request.publish_track.local_participant_handle
            )
            value.actions.append("publish")
            response.publish_track.async_id = self.next_id
            event = ffi_pb2.FfiEvent()
            event.publish_track.async_id = self.next_id
            event.publish_track.publication.handle.id = self.next_id + 10_000
            event.publish_track.publication.info.sid = f"TR_synthetic_{self.next_id}"
            self.queue.put(event)
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


def current_task_is(task: asyncio.Task[Any]) -> bool:
    return asyncio.current_task() is task


@dataclass
class SdkCase:
    runtime_module: ModuleType | None = None
    receiver: Any = None
    rooms: list[room.Room] = field(default_factory=lambda: list[room.Room]())
    handles: list[SyntheticHandle] = field(default_factory=lambda: list[SyntheticHandle]())
    sources: list[Any] = field(default_factory=lambda: list[Any]())
    media_allowed: bool = False
    source_constructions: int = 0
    track_constructions: int = 0
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

    def new_source(self, *_: Any, **__: Any) -> Any:
        self.source_constructions += 1
        assert self.media_allowed, "retired join must not construct an AudioSource"
        handle = self.new_handle(100_000 + self.source_constructions * 10)
        source = SimpleNamespace(_ffi_handle=handle, close_calls=0, clear_calls=0)

        async def close() -> None:
            source.close_calls += 1
            handle.dispose()

        def clear() -> None:
            source.clear_calls += 1

        source.aclose = close
        source.clear_queue = clear
        self.sources.append(source)
        return source

    def new_track(self, *_: Any) -> Any:
        self.track_constructions += 1
        assert self.media_allowed, "retired join must not construct a LocalAudioTrack"
        return SimpleNamespace(
            _ffi_handle=self.new_handle(200_000 + self.track_constructions),
            _info=SimpleNamespace(sid=""),
        )

    def start_helper(self) -> asyncio.Task[Any]:
        assert self.runtime_module is not None
        return asyncio.create_task(self.receiver._connect_publisher(uuid4()))

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

    async def cancel_helper(self) -> tuple[asyncio.Task[Any], Join]:
        caller = self.start_helper()
        value = await self.next_join(caller)
        caller.cancel()
        assert await self.observe(caller.done), "caller cancellation exceeded observation budget"
        assert caller.cancelled()
        return caller, value

    async def settle(self, value: Join, *, error: bool) -> None:
        self.ffi.deliver(value, error=error)
        await self.observe(
            lambda: (
                value.task.done()
                and (error or "disconnect" in value.actions)
                and self.ffi.queue.count == 0
            )
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    def assert_no_pending_owned_tasks(self) -> None:
        pending = asyncio.all_tasks() - self.tasks_before - {asyncio.current_task()}
        assert not pending, f"owned SDK/helper tasks still pending: {pending}"

    async def fixture_cleanup(self) -> None:
        # First settle exact pending joins through fake public protocol events.
        # This is fixture hygiene after product assertions, not product repair.
        for value in self.ffi.joins:
            if not value.delivered:
                self.ffi.deliver(value, error=True)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
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
        for value in self.sources:
            if not value.close_calls:
                await value.aclose()
        self.ffi.queue.release_fixture_owned()
        assert self.ffi.queue.count == 0
        for value in self.rooms:
            # Real Room.__del__ consults the singleton. Detach only our fake
            # handles before restoring it, preventing a later native init.
            value._ffi_handle = None
        for handle in self.handles:
            handle.dispose()
        assert all(handle.disposed for handle in self.handles)
        assert not self.guard_attempts, self.guard_attempts
        self.assert_no_pending_owned_tasks()


@pytest_asyncio.fixture
async def sdk_case(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SdkCase]:
    case = SdkCase()
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
        patches.setattr(_ffi_client.FfiClient, "_instance", case.ffi)
        for module in (room, participant, track, track_publication):
            patches.setattr(module, "FfiHandle", case.new_handle)
        case.runtime_module = importlib.import_module("jx_core.agent.runtime")
        patches.setattr(case.runtime_module.rtc, "Room", case.new_room)
        patches.setattr(case.runtime_module.rtc, "AudioSource", case.new_source)
        patches.setattr(
            case.runtime_module.rtc,
            "LocalAudioTrack",
            SimpleNamespace(create_audio_track=case.new_track),
        )
        patches.setattr(case.runtime_module.api, "AccessToken", SyntheticToken)
        receiver = object.__new__(case.runtime_module.AgentRuntime)
        receiver._settings = SimpleNamespace(
            livekit_url="wss://synthetic.invalid",
            livekit_api_key=SyntheticMaterial(),
            livekit_api_secret=SyntheticMaterial(),
        )
        case.receiver = receiver
        try:
            yield case
        finally:
            await case.fixture_cleanup()
    assert _ffi_client.FfiClient._instance is original


@pytest.mark.asyncio
async def test_normal_actual_sdk_connect_ready_and_disconnect_release_both_subscriptions(
    sdk_case: SdkCase,
) -> None:
    case = sdk_case
    value = case.new_room()
    task = asyncio.create_task(value.connect("wss://synthetic.invalid", "unused-synthetic-token"))
    join = await case.next_join(task)
    assert case.ffi.queue.count == 2
    case.ffi.deliver(join, error=False)
    await task
    assert value.isconnected() and join.actions == ["connect", "connect_success", "ready"]
    assert {join.room_handle, join.participant_handle}.issubset(
        {handle.handle for handle in case.handles}
    )
    await value.disconnect()
    assert case.ffi.queue.count == 0
    assert join.actions == ["connect", "connect_success", "ready", "disconnect", "eos"]
    case.assert_no_pending_owned_tasks()


@pytest.mark.asyncio
async def test_normal_actual_sdk_connect_error_removes_both_subscriptions(
    sdk_case: SdkCase,
) -> None:
    case = sdk_case
    value = case.new_room()
    task = asyncio.create_task(value.connect("wss://synthetic.invalid", "unused-synthetic-token"))
    join = await case.next_join(task)
    case.ffi.deliver(join, error=True)
    with pytest.raises(room.ConnectError):
        await task
    assert case.ffi.queue.count == 0 and not value.isconnected()
    assert not case.handles
    case.assert_no_pending_owned_tasks()


@pytest.mark.asyncio
async def test_normal_business_helper_transfers_connected_sdk_room_without_early_close(
    sdk_case: SdkCase,
) -> None:
    case = sdk_case
    case.media_allowed = True
    caller = case.start_helper()
    join = await case.next_join(caller)
    case.ffi.deliver(join, error=False)
    value, source = await caller
    assert value is join.room and source is case.sources[0]
    assert source.close_calls == 0 and "disconnect" not in join.actions
    assert case.source_constructions == case.track_constructions == 1
    assert join.actions == ["connect", "connect_success", "ready", "publish"]
    await source.aclose()
    await value.disconnect()
    assert case.ffi.queue.count == 0
    case.assert_no_pending_owned_tasks()


@pytest.mark.asyncio
async def test_helper_cancellation_preserves_sdk_join_owner_and_both_subscriptions(
    sdk_case: SdkCase,
) -> None:
    case = sdk_case
    caller, join = await case.cancel_helper()
    assert join.task is not caller, "caller cancellation reached the actual SDK join"
    assert not join.task.done() and not join.task.cancelled()
    assert case.ffi.queue.count == 2, "pending SDK join lost a subscription/owner"
    assert case.source_constructions == case.track_constructions == 0
    await case.settle(join, error=True)
    assert case.ffi.queue.count == 0 and join.task.done() and not join.task.cancelled()
    assert isinstance(join.task._exception, room.ConnectError)
    assert not join.task._log_traceback, "retired SDK error was not consumed by its owner"
    case.assert_no_pending_owned_tasks()


@pytest.mark.asyncio
async def test_cancelled_helper_late_sdk_error_releases_subscriptions_and_consumes_failure(
    sdk_case: SdkCase,
) -> None:
    case = sdk_case
    _, join = await case.cancel_helper()
    await case.settle(join, error=True)
    assert case.ffi.queue.count == 0, "late SDK rejection left an orphan subscriber"
    assert join.task.done() and not join.task.cancelled()
    assert isinstance(join.task._exception, room.ConnectError)
    assert not join.task._log_traceback
    assert case.source_constructions == case.track_constructions == 0
    case.assert_no_pending_owned_tasks()


@pytest.mark.asyncio
async def test_cancelled_helper_late_success_finishes_sdk_ready_then_disconnects_old_room(
    sdk_case: SdkCase,
) -> None:
    case = sdk_case
    _, join = await case.cancel_helper()
    await case.settle(join, error=False)
    assert join.actions == ["connect", "connect_success", "ready", "disconnect", "eos"]
    assert case.ffi.queue.count == 0 and not join.room.isconnected()
    assert join.task.done() and not join.task.cancelled()
    assert {join.room_handle, join.participant_handle}.issubset(
        {handle.handle for handle in case.handles}
    )
    assert case.source_constructions == case.track_constructions == 0
    case.assert_no_pending_owned_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("late_error", (True, False), ids=("late-error", "late-success"))
async def test_five_cancelled_helper_joins_settle_without_accumulating_sdk_subscriptions(
    sdk_case: SdkCase, late_error: bool
) -> None:
    case = sdk_case
    snapshots: list[dict[str, Any]] = []
    for _ in range(5):
        caller, join = await case.cancel_helper()
        pending_owned = join.task is not caller and not join.task.done()
        pending_subscribers = case.ffi.queue.count
        await case.settle(join, error=late_error)
        snapshots.append(
            {
                "pending_owned": pending_owned,
                "pending_subscribers": pending_subscribers,
                "after_subscribers": case.ffi.queue.count,
                "task_cancelled": join.task.cancelled(),
                "actions": list(join.actions),
            }
        )
    assert all(value["pending_owned"] for value in snapshots), snapshots
    assert [value["pending_subscribers"] for value in snapshots] == [2] * 5, snapshots
    assert [value["after_subscribers"] for value in snapshots] == [0] * 5, snapshots
    assert not any(value["task_cancelled"] for value in snapshots), snapshots
    assert case.source_constructions == case.track_constructions == 0
    if not late_error:
        assert all(
            value["actions"] == ["connect", "connect_success", "ready", "disconnect", "eos"]
            for value in snapshots
        ), snapshots
    case.assert_no_pending_owned_tasks()

"""Real ASR receiver/runtime startup with synthetic RTC/provider boundaries.

No Settings constructor, dotenv, SDK/native connection, network or database.
The application startup, registry and cleanup paths are not replaced.
"""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from livekit import api, rtc
from pydantic import SecretStr

import jx_core.asr.runtime as module
from jx_core.asr.runtime import AsrRuntime, MatchAudioReceiver


class FakeToken:
    def __init__(self, *_: Any) -> None:
        return None

    def with_identity(self, _: str) -> FakeToken:
        return self

    def with_ttl(self, _: Any) -> FakeToken:
        return self

    def with_grants(self, _: Any) -> FakeToken:
        return self

    def to_jwt(self) -> str:
        return "synthetic-token"


class FakeRoom:
    def __init__(self, owner: FakeSdk) -> None:
        self.owner = owner
        self.listeners: list[Callable[..., None]] = []
        self.unsubscribe_listeners: list[Callable[..., None]] = []
        self.retirement_listeners: dict[str, list[Callable[..., None]]] = {}
        self.remote_participants: dict[str, Any] = {}
        self.join_pending = owner.join_pending
        self.join_entered = asyncio.Event()
        self.join_release = asyncio.Event()
        self.join_cancelled = asyncio.Event()
        self.join_done = asyncio.Event()
        self.join_task: asyncio.Task[Any] | None = None
        self.disconnected = 0
        self.disconnect_entered = asyncio.Event()
        self.emit_during_join = owner.emit_during_join
        self.track = SimpleNamespace(kind=rtc.TrackKind.KIND_AUDIO, sid=f"TR-{uuid4().hex}")
        self.publication = SimpleNamespace(track=self.track, sid=self.track.sid)
        self.participant = SimpleNamespace(
            identity=f"user-{uuid4()}-synthetic",
            track_publications={self.track.sid: self.publication},
        )
        if owner.prepublished_track:
            self.remote_participants["participant"] = self.participant

    def on(self, name: str, callback: Callable[..., None]) -> None:
        assert name in {
            "track_subscribed",
            "track_unsubscribed",
            "track_unpublished",
            "participant_disconnected",
            "participant_connected",
            "disconnected",
            "reconnected",
        }
        if name != "track_subscribed":
            self.retirement_listeners.setdefault(name, []).append(callback)
            if name == "track_unsubscribed":
                self.unsubscribe_listeners.append(callback)
            return
        self.listeners.append(callback)
        self.owner.step("listener")

    async def connect(self, *_: Any, **__: Any) -> None:
        self.join_task = asyncio.current_task()
        self.join_entered.set()
        try:
            self.owner.step("connect")
            if self.join_pending:
                try:
                    await self.join_release.wait()
                except asyncio.CancelledError:
                    self.join_cancelled.set()
                    raise
            if self.emit_during_join:
                self.emit_track()
        finally:
            self.join_done.set()

    async def disconnect(self) -> None:
        self.disconnected += 1
        self.disconnect_entered.set()

    def emit_track(self) -> None:
        for callback in self.listeners:
            callback(self.track, self.publication, self.participant)


class FakeConnection:
    def __init__(self) -> None:
        self.closed = 0
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_cancelled = asyncio.Event()
        self.close_done = asyncio.Event()
        self.close_task: asyncio.Task[Any] | None = None
        self.hung_close = False
        self.late_error: BaseException | None = None

    async def close(self) -> None:
        self.closed += 1
        self.close_task = asyncio.current_task()
        self.close_entered.set()
        try:
            if self.hung_close:
                while not self.close_release.is_set():
                    try:
                        await self.close_release.wait()
                    except asyncio.CancelledError:
                        self.close_cancelled.set()
            if self.late_error is not None:
                raise self.late_error
        finally:
            self.close_done.set()


class FakeStream:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.closed = 0

    def __aiter__(self) -> AsyncIterator[Any]:
        return self

    async def __anext__(self) -> Any:
        await self.release.wait()
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed += 1


@dataclass
class FakeSdk:
    fail_step: str | None = None
    error: RuntimeError = field(default_factory=lambda: RuntimeError("synthetic_asr_start_failure"))
    join_pending: bool = False
    prepublished_track: bool = False
    emit_during_join: bool = False
    rooms: list[FakeRoom] = field(default_factory=lambda: list[FakeRoom]())
    connections: list[FakeConnection] = field(default_factory=lambda: list[FakeConnection]())
    calls: list[str] = field(default_factory=lambda: list[str]())
    streams: list[FakeStream] = field(default_factory=lambda: list[FakeStream]())

    def step(self, phase: str) -> None:
        self.calls.append(phase)
        if self.fail_step == phase:
            raise self.error

    def room(self) -> FakeRoom:
        room = FakeRoom(self)
        self.rooms.append(room)
        return room

    def connection(self, **_: Any) -> FakeConnection:
        self.step("provider-constructor")
        connection = FakeConnection()
        self.connections.append(connection)
        return connection

    def stream(self, *_: Any, **options: Any) -> FakeStream:
        assert options == {
            "capacity": 10,
            "sample_rate": 16000,
            "num_channels": 1,
            "frame_size_ms": 100,
        }
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def grants(**values: Any) -> SimpleNamespace:
            return SimpleNamespace(**values)

        monkeypatch.setattr(api, "AccessToken", FakeToken)
        monkeypatch.setattr(api, "VideoGrants", grants)
        monkeypatch.setattr(rtc, "Room", self.room)
        monkeypatch.setattr(rtc, "RoomOptions", grants)
        monkeypatch.setattr(module, "FunAsrConnection", self.connection)
        monkeypatch.setattr(rtc, "AudioStream", self.stream)


def settings() -> Any:
    return SimpleNamespace(
        livekit_url="wss://synthetic.invalid",
        livekit_api_key=SecretStr("synthetic-app-key"),
        livekit_api_secret=SecretStr("synthetic-app-secret"),
        asr_api_key=SecretStr("synthetic-asr-key"),
        asr_ws_url="wss://synthetic.invalid/asr",
        asr_model="synthetic-asr",
        asr_workspace_id=None,
        agent_audio_storage_dir=None,
    )


def receiver() -> MatchAudioReceiver:
    return MatchAudioReceiver(match_id=uuid4(), settings=settings(), callbacks=AsyncMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["connect", "provider-constructor", "listener"])
async def test_real_receiver_start_error_reclaims_local_room_and_preserves_original_exception(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    sdk = FakeSdk(fail_step=phase)
    sdk.install(monkeypatch)
    target = receiver()
    try:
        with pytest.raises(RuntimeError) as failure:
            await target.start()
        assert failure.value is sdk.error
        assert len(sdk.rooms) == 1 and sdk.rooms[0].disconnected == 1
        assert target._room is None and target._connection is None
        assert not target._track_tasks
    finally:
        await target.close()


@pytest.mark.asyncio
async def test_runtime_cancelled_ensure_match_removes_only_its_failed_receiver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = FakeSdk(join_pending=True)
    sdk.install(monkeypatch)
    runtime = AsrRuntime(settings=settings(), callbacks=AsyncMock())
    match_id = uuid4()
    task = asyncio.create_task(runtime.ensure_match(match_id))
    try:
        async with asyncio.timeout(1):
            while not sdk.rooms:
                await asyncio.sleep(0)
            await sdk.rooms[0].join_entered.wait()
        old = runtime._receivers[match_id]
        task.cancel()
        result = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=0.3)
        assert isinstance(result[0], asyncio.CancelledError)
        assert match_id not in runtime._receivers
        assert old._room is None and old._connection is None
        assert not sdk.rooms[0].join_cancelled.is_set()
        sdk.rooms[0].join_release.set()
        await asyncio.wait_for(sdk.rooms[0].disconnect_entered.wait(), timeout=1)
        assert not sdk.connections
    finally:
        for room in sdk.rooms:
            room.join_release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_real_receiver_normal_start_transfers_exact_room_and_provider_without_early_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = FakeSdk()
    sdk.install(monkeypatch)
    target = receiver()
    try:
        await target.start()
        await asyncio.sleep(0)
        assert target._room is sdk.rooms[0] and target._connection is sdk.connections[0]
        assert sdk.rooms[0].disconnected == sdk.connections[0].closed == 0
        await target.start()
        assert len(sdk.rooms) == len(sdk.connections) == 1
    finally:
        await target.close()
    assert sdk.rooms[0].disconnected == sdk.connections[0].closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "emit_during_join", [False, True], ids=["inventory", "event-and-inventory"]
)
async def test_prepublished_audio_track_starts_one_real_consumer_after_handoff(
    monkeypatch: pytest.MonkeyPatch,
    emit_during_join: bool,
) -> None:
    sdk = FakeSdk(prepublished_track=True, emit_during_join=emit_during_join)
    sdk.install(monkeypatch)
    target = receiver()
    try:
        await target.start()
        async with asyncio.timeout(1):
            while not sdk.streams:
                await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(sdk.streams) == len(target._track_tasks) == 1
        assert target._room is sdk.rooms[0] and target._connection is sdk.connections[0]
        sdk.rooms[0].emit_track()
        await asyncio.sleep(0)
        assert len(sdk.streams) == len(target._track_tasks) == 1
    finally:
        for stream in sdk.streams:
            stream.release.set()
        await target.close()


@pytest.mark.asyncio
async def test_cancelled_receiver_late_track_cannot_consume_or_close_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = FakeSdk(join_pending=True)
    sdk.install(monkeypatch)
    runtime = AsrRuntime(settings=settings(), callbacks=AsyncMock())
    match_id = uuid4()
    old_task = asyncio.create_task(runtime.ensure_match(match_id))
    try:
        async with asyncio.timeout(1):
            while not sdk.rooms:
                await asyncio.sleep(0)
            await sdk.rooms[0].join_entered.wait()
        old_receiver, old_room = runtime._receivers[match_id], sdk.rooms[0]
        old_task.cancel()
        await asyncio.wait_for(asyncio.gather(old_task, return_exceptions=True), timeout=0.3)
        assert match_id not in runtime._receivers and not old_room.join_cancelled.is_set()
        sdk.join_pending = False
        replacement = await runtime.ensure_match(match_id)
        assert replacement is not old_receiver
        assert replacement._room is sdk.rooms[1] and replacement._connection is sdk.connections[0]
        old_room.join_release.set()
        await asyncio.wait_for(old_room.disconnect_entered.wait(), timeout=1)
        old_room.emit_track()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not sdk.streams and not old_receiver._track_tasks
        assert runtime._receivers[match_id] is replacement
        assert len(sdk.connections) == 1 and sdk.connections[0].closed == 0
        assert old_room.disconnected == 1 and sdk.rooms[1].disconnected == 0
    finally:
        for room in sdk.rooms:
            room.join_release.set()
        for stream in sdk.streams:
            stream.release.set()
        old_task.cancel()
        await asyncio.gather(old_task, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_second_receiver_close_cancel_still_schedules_independent_room_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = FakeSdk()
    sdk.install(monkeypatch)
    monkeypatch.setattr(module, "ASR_CLEANUP_TIMEOUT_SECONDS", 0.03)
    target = receiver()
    await target.start()
    sdk.connections[0].hung_close = True
    task = asyncio.create_task(target.close())
    try:
        await asyncio.wait_for(sdk.connections[0].close_entered.wait(), timeout=1)
        task.cancel()
        task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=0.3)
        await asyncio.wait_for(sdk.rooms[0].disconnect_entered.wait(), timeout=0.3)
        assert sdk.connections[0].closed == sdk.rooms[0].disconnected == 1
        assert target._closed and target._room is None and target._connection is None
    finally:
        sdk.connections[0].close_release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.wait_for(sdk.connections[0].close_done.wait(), timeout=1)


@pytest.mark.asyncio
async def test_hung_provider_close_preserves_room_cleanup_and_consumes_late_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = FakeSdk()
    sdk.install(monkeypatch)
    monkeypatch.setattr(module, "ASR_CLEANUP_TIMEOUT_SECONDS", 0.03)
    target = receiver()
    await target.start()
    connection = sdk.connections[0]
    connection.hung_close = True
    connection.late_error = RuntimeError("synthetic_late_asr_close_error")
    try:
        await asyncio.wait_for(target.close(), timeout=0.3)
        assert connection.close_cancelled.is_set() and not connection.close_done.is_set()
        assert sdk.rooms[0].disconnected == 1
        connection.close_release.set()
        await asyncio.wait_for(connection.close_done.wait(), timeout=1)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert connection.close_task is not None and connection.close_task.done()
        assert not getattr(connection.close_task, "_log_traceback", True)
    finally:
        connection.close_release.set()
        await asyncio.wait_for(connection.close_done.wait(), timeout=1)


@pytest.mark.asyncio
async def test_cancelled_second_waiter_does_not_retire_primary_startup_receiver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = FakeSdk(join_pending=True)
    sdk.install(monkeypatch)
    runtime = AsrRuntime(settings=settings(), callbacks=AsyncMock())
    match_id = uuid4()
    primary = asyncio.create_task(runtime.ensure_match(match_id))
    secondary: asyncio.Task[MatchAudioReceiver] | None = None
    try:
        async with asyncio.timeout(1):
            while not sdk.rooms:
                await asyncio.sleep(0)
            await sdk.rooms[0].join_entered.wait()
        target = runtime._receivers[match_id]
        secondary = asyncio.create_task(runtime.ensure_match(match_id))
        async with asyncio.timeout(1):
            while not any(
                not waiter.done()
                for waiter in (getattr(target._lifecycle_lock, "_waiters", None) or ())
            ):
                await asyncio.sleep(0)
        secondary.cancel()
        done, _ = await asyncio.wait({secondary}, timeout=0.3)
        assert done, "secondary waiter cancellation must not await the primary startup lock"
        outcome = (await asyncio.gather(secondary, return_exceptions=True))[0]
        assert isinstance(outcome, asyncio.CancelledError)
        assert runtime._receivers[match_id] is target and not target._closed
        assert not primary.done() and sdk.rooms[0].disconnected == 0
        sdk.rooms[0].join_release.set()
        assert await asyncio.wait_for(primary, timeout=1) is target
        assert target._room is sdk.rooms[0] and target._connection is sdk.connections[0]
        assert len(sdk.rooms) == len(sdk.connections) == 1
    finally:
        for room in sdk.rooms:
            room.join_release.set()
        if secondary is not None:
            await asyncio.gather(secondary, return_exceptions=True)
        await asyncio.gather(primary, return_exceptions=True)
        await runtime.close()

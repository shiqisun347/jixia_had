"""Actual publisher helper ownership with synthetic SDK boundaries only.

No Settings, database, network, native media, or installed SDK edits are used.
The application helper, error propagation and resource cleanup remain real.
"""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from livekit import api, rtc
from pydantic import SecretStr

import jx_core.agent.runtime as module
from jx_core.agent.runtime import AgentRuntime


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


class FakeSource:
    def __init__(self, owner: FakeSdk) -> None:
        self.owner = owner
        self.cleared = 0
        self.closed = 0
        self.close_constructed = 0
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_cancelled = asyncio.Event()
        self.close_done = asyncio.Event()
        self.close_task: asyncio.Task[Any] | None = None
        self.pcm_frames = 0

    def clear_queue(self) -> None:
        self.cleared += 1
        if self.owner.clear_error is not None:
            raise self.owner.clear_error

    def aclose(self) -> Coroutine[Any, Any, None]:
        self.close_constructed += 1
        if self.owner.source_close_factory_error is not None:
            raise self.owner.source_close_factory_error

        async def close() -> None:
            self.close_task = asyncio.current_task()
            self.closed += 1
            self.close_entered.set()
            try:
                if self.owner.hung_source_close:
                    while not self.close_release.is_set():
                        try:
                            await self.close_release.wait()
                        except asyncio.CancelledError:
                            self.close_cancelled.set()
                if self.owner.source_close_late_error is not None:
                    raise self.owner.source_close_late_error
            finally:
                self.close_done.set()

        return close()

    async def capture_frame(self, *_: Any) -> None:
        self.pcm_frames += 1


class FakeRoom:
    def __init__(self, owner: FakeSdk) -> None:
        self.owner = owner
        self.disconnected = 0
        self.join_pending = owner.join_pending
        self.join_entered = asyncio.Event()
        self.join_release = asyncio.Event()
        self.join_cancelled = asyncio.Event()
        self.join_done = asyncio.Event()
        self.join_task: asyncio.Task[Any] | None = None
        self.disconnect_entered = asyncio.Event()
        self.disconnect_before_join_done = False
        self.local_participant = SimpleNamespace(publish_track=self.publish_track)

    async def connect(self, *_: Any) -> None:
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
            if self.owner.join_late_error is not None:
                raise self.owner.join_late_error
        finally:
            self.join_done.set()

    async def publish_track(self, *_: Any) -> None:
        self.owner.publish_entered.set()
        if self.owner.publish_pending:
            await self.owner.publish_release.wait()
        self.owner.step("publish")

    async def disconnect(self) -> None:
        self.disconnected += 1
        self.disconnect_before_join_done = not self.join_done.is_set()
        self.disconnect_entered.set()


@dataclass
class FakeSdk:
    fail_step: str | None = None
    error: RuntimeError = field(default_factory=lambda: RuntimeError("synthetic_startup_failure"))
    rooms: list[FakeRoom] = field(default_factory=lambda: list[FakeRoom]())
    sources: list[FakeSource] = field(default_factory=lambda: list[FakeSource]())
    tracks: list[object] = field(default_factory=lambda: list[object]())
    calls: list[str] = field(default_factory=lambda: list[str]())
    publish_pending: bool = False
    publish_entered: asyncio.Event = field(default_factory=asyncio.Event)
    publish_release: asyncio.Event = field(default_factory=asyncio.Event)
    join_pending: bool = False
    join_late_error: BaseException | None = None
    clear_error: BaseException | None = None
    source_close_factory_error: BaseException | None = None
    source_close_late_error: BaseException | None = None
    hung_source_close: bool = False

    def step(self, phase: str) -> None:
        self.calls.append(phase)
        if self.fail_step == phase:
            raise self.error

    def room(self) -> FakeRoom:
        room = FakeRoom(self)
        self.rooms.append(room)
        return room

    def source(self, *_: Any, **__: Any) -> FakeSource:
        self.step("source")
        source = FakeSource(self)
        self.sources.append(source)
        return source

    def track(self, *_: Any) -> object:
        self.step("track")
        track = object()
        self.tracks.append(track)
        return track

    def options(self) -> SimpleNamespace:
        self.step("options")
        return SimpleNamespace()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def grants(**values: Any) -> SimpleNamespace:
            return SimpleNamespace(**values)

        monkeypatch.setattr(api, "AccessToken", FakeToken)
        monkeypatch.setattr(api, "VideoGrants", grants)
        monkeypatch.setattr(rtc, "Room", self.room)
        monkeypatch.setattr(rtc, "AudioSource", self.source)
        monkeypatch.setattr(rtc.LocalAudioTrack, "create_audio_track", self.track)
        monkeypatch.setattr(rtc, "TrackPublishOptions", self.options)


def application() -> AgentRuntime:
    return AgentRuntime(
        settings=cast(
            Any,
            SimpleNamespace(
                llm_global_concurrency=2,
                livekit_url="wss://synthetic.invalid",
                livekit_api_key=SecretStr("synthetic-app-key"),
                livekit_api_secret=SecretStr("synthetic-app-secret"),
            ),
        ),
        session_factory=cast(Any, None),
        callbacks=AsyncMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["connect", "source", "track", "options", "publish"])
async def test_real_publisher_initialization_failure_closes_every_returned_local_resource(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    sdk = FakeSdk(fail_step=phase)
    sdk.install(monkeypatch)
    runtime = application()
    with pytest.raises(RuntimeError) as failure:
        await runtime._connect_publisher(uuid4())
    assert failure.value is sdk.error
    assert len(sdk.rooms) == 1 and sdk.rooms[0].disconnected == 1
    assert len(sdk.sources) == (0 if phase in {"connect", "source"} else 1)
    assert all(source.cleared == 1 and source.closed == 1 for source in sdk.sources)
    assert not runtime._runs and not runtime._host_runs


@pytest.mark.asyncio
async def test_real_publisher_normal_return_transfers_exact_resources_without_early_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = FakeSdk()
    sdk.install(monkeypatch)
    runtime = application()
    room, source = await runtime._connect_publisher(uuid4())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert room is sdk.rooms[0] and source is sdk.sources[0]
    assert sdk.calls == ["connect", "source", "track", "options", "publish"]
    assert sdk.rooms[0].disconnected == sdk.sources[0].cleared == sdk.sources[0].closed == 0
    # Ownership is now the caller's. These are explicit test-owned closes.
    sdk.sources[0].clear_queue()
    await sdk.sources[0].aclose()
    await sdk.rooms[0].disconnect()


@pytest.mark.asyncio
async def test_cancel_during_publish_preserves_cancellation_and_closes_both_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = FakeSdk(publish_pending=True)
    sdk.install(monkeypatch)
    runtime = application()
    task = asyncio.create_task(runtime._connect_publisher(uuid4()))
    try:
        await asyncio.wait_for(sdk.publish_entered.wait(), timeout=1)
        task.cancel()
        result = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=1)
        assert isinstance(result[0], asyncio.CancelledError)
        assert len(sdk.rooms) == len(sdk.sources) == 1
        assert sdk.sources[0].cleared == sdk.sources[0].closed == 1
        assert sdk.rooms[0].disconnected == 1
    finally:
        sdk.publish_release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class SyntheticCleanupAbort(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["sync-factory", "clear-baseexception"])
async def test_rollback_cleanup_fault_preserves_original_startup_error_and_other_close(
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    sdk = FakeSdk(fail_step="publish")
    if fault == "sync-factory":
        sdk.source_close_factory_error = RuntimeError("synthetic_close_factory_failed")
    else:
        sdk.clear_error = SyntheticCleanupAbort("synthetic_clear_aborted")
    sdk.install(monkeypatch)
    with pytest.raises(RuntimeError) as failure:
        await application()._connect_publisher(uuid4())
    assert failure.value is sdk.error
    assert sdk.rooms[0].disconnected == 1
    assert sdk.sources[0].close_constructed == 1
    assert sdk.sources[0].closed == (0 if fault == "sync-factory" else 1)
    assert sdk.sources[0].cleared == 1


@pytest.mark.asyncio
async def test_hung_cleanup_keeps_late_failure_owned_without_overwriting_startup_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = FakeSdk(
        fail_step="publish",
        hung_source_close=True,
        source_close_late_error=RuntimeError("synthetic_late_close_failure"),
    )
    sdk.install(monkeypatch)
    monkeypatch.setattr(module, "AGENT_RESOURCE_CLEANUP_TIMEOUT_SECONDS", 0.03)
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled: list[dict[str, Any]] = []
    loop.set_exception_handler(lambda _, context: unhandled.append(context))
    try:
        with pytest.raises(RuntimeError) as failure:
            await asyncio.wait_for(application()._connect_publisher(uuid4()), timeout=0.3)
        assert failure.value is sdk.error
        assert sdk.sources[0].closed == sdk.rooms[0].disconnected == 1
        assert sdk.sources[0].close_cancelled.is_set()
        assert not sdk.sources[0].close_done.is_set()
        sdk.sources[0].close_release.set()
        await asyncio.wait_for(sdk.sources[0].close_done.wait(), timeout=1)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert sdk.sources[0].close_task is not None and sdk.sources[0].close_task.done()
        # The fixture still owns a reference; an empty loop error handler alone
        # would not prove the application's done callback retrieved this error.
        assert not getattr(sdk.sources[0].close_task, "_log_traceback", True)
        assert not unhandled
    finally:
        for source in sdk.sources:
            source.close_release.set()
        await asyncio.sleep(0)
        loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
async def test_second_outer_cancel_does_not_unschedule_other_owned_resource_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = FakeSdk(publish_pending=True, hung_source_close=True)
    sdk.install(monkeypatch)
    monkeypatch.setattr(module, "AGENT_RESOURCE_CLEANUP_TIMEOUT_SECONDS", 0.03)
    task = asyncio.create_task(application()._connect_publisher(uuid4()))
    try:
        await asyncio.wait_for(sdk.publish_entered.wait(), timeout=1)
        task.cancel()
        await asyncio.wait_for(sdk.sources[0].close_entered.wait(), timeout=1)
        task.cancel()
        outcome = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=0.3)
        assert isinstance(outcome[0], asyncio.CancelledError)
        await asyncio.wait_for(sdk.rooms[0].disconnect_entered.wait(), timeout=0.3)
        assert sdk.sources[0].close_constructed == sdk.sources[0].closed == 1
        assert sdk.sources[0].cleared == sdk.rooms[0].disconnected == 1
    finally:
        sdk.publish_release.set()
        for source in sdk.sources:
            source.close_release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if sdk.sources:
            await asyncio.wait_for(sdk.sources[0].close_done.wait(), timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("late_error", [False, True], ids=["late-success", "late-error"])
async def test_cancelled_startup_keeps_join_uncancelled_and_only_disconnects_old_room(
    monkeypatch: pytest.MonkeyPatch,
    late_error: bool,
) -> None:
    sdk = FakeSdk(join_pending=True)
    sdk.install(monkeypatch)
    runtime = application()
    callbacks = cast(AsyncMock, runtime._callbacks)
    task = asyncio.create_task(runtime._connect_publisher(uuid4()))
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled: list[dict[str, Any]] = []
    loop.set_exception_handler(lambda _, context: unhandled.append(context))
    try:
        async with asyncio.timeout(1):
            while not sdk.rooms:
                await asyncio.sleep(0)
            await sdk.rooms[0].join_entered.wait()
        room = sdk.rooms[0]
        task.cancel()
        outcome = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=0.3)
        assert isinstance(outcome[0], asyncio.CancelledError)
        assert room.join_task is not task and room.join_task is not None
        assert not room.join_task.done() and not room.join_cancelled.is_set()
        assert room.disconnected == 0
        if late_error:
            sdk.join_late_error = sdk.error
        room.join_release.set()
        await asyncio.wait_for(room.join_done.wait(), timeout=1)
        await asyncio.wait_for(room.disconnect_entered.wait(), timeout=1)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert room.disconnected == 1 and not room.disconnect_before_join_done
        assert room.join_task.done()
        if late_error:
            assert not getattr(room.join_task, "_log_traceback", True)
        assert not sdk.sources and not sdk.tracks
        assert sdk.calls == ["connect"] and not sdk.publish_entered.is_set()
        assert not callbacks.mock_calls and not unhandled
    finally:
        for room in sdk.rooms:
            room.join_release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        joins = [room.join_task for room in sdk.rooms if room.join_task is not None]
        if joins:
            await asyncio.wait_for(asyncio.gather(*joins, return_exceptions=True), timeout=1)
        await asyncio.sleep(0)
        loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
async def test_late_retired_join_cleanup_never_closes_successful_replacement_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = FakeSdk(join_pending=True)
    sdk.install(monkeypatch)
    runtime = application()
    match_id = uuid4()
    old_task = asyncio.create_task(runtime._connect_publisher(match_id))
    try:
        async with asyncio.timeout(1):
            while not sdk.rooms:
                await asyncio.sleep(0)
            await sdk.rooms[0].join_entered.wait()
        old = sdk.rooms[0]
        old_task.cancel()
        await asyncio.wait_for(asyncio.gather(old_task, return_exceptions=True), timeout=0.3)
        assert not old.join_cancelled.is_set()
        sdk.join_pending = False
        room, source = await runtime._connect_publisher(match_id)
        assert room is sdk.rooms[1] and source is sdk.sources[0]
        assert not old.join_done.is_set() and old.disconnected == 0
        before_late = list(sdk.calls)
        old.join_release.set()
        await asyncio.wait_for(old.disconnect_entered.wait(), timeout=1)
        assert sdk.calls == before_late
        assert old.disconnected == 1 and sdk.rooms[1].disconnected == 0
        assert len(sdk.sources) == len(sdk.tracks) == 1
        assert sdk.sources[0].cleared == sdk.sources[0].closed == sdk.sources[0].pcm_frames == 0
    finally:
        for old_room in sdk.rooms:
            old_room.join_release.set()
        old_task.cancel()
        await asyncio.gather(old_task, return_exceptions=True)
        for owned_source in sdk.sources:
            owned_source.clear_queue()
            await owned_source.aclose()
        for owned_room in sdk.rooms:
            if owned_room.disconnected == 0 and owned_room.join_done.is_set():
                await owned_room.disconnect()

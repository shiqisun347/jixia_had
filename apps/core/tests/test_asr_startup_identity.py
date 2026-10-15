"""ASR startup ownership identities with real receiver/runtime orchestration.

Synthetic RTC/token/provider boundaries only; no Settings, dotenv, DB, network,
native SDK connection or PCM. Existing startup/SDK strong tests stay separate.
"""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import gc
import re
import weakref
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from livekit import api, rtc

import jx_core.asr.runtime as module
from jx_core.asr.runtime import AsrRuntime, MatchAudioReceiver


class SyntheticMaterial:
    def get_secret_value(self) -> str:
        return "synthetic-unused-material"


@dataclass
class SyntheticToken:
    identity: str = ""
    ttl: timedelta | None = None
    grants: Any = None

    def with_identity(self, identity: str) -> SyntheticToken:
        self.identity = identity
        return self

    def with_ttl(self, ttl: timedelta) -> SyntheticToken:
        self.ttl = ttl
        return self

    def with_grants(self, grants: Any) -> SyntheticToken:
        self.grants = grants
        return self

    def to_jwt(self) -> str:
        return self.identity


class SyntheticRoom:
    def __init__(self, owner: Boundary, index: int) -> None:
        self.owner = owner
        self.index = index
        self.listeners: dict[str, list[Callable[..., None]]] = {}
        self.remote_participants: dict[str, Any] = {}
        self.join_entered = asyncio.Event()
        self.join_release = asyncio.Event()
        self.join_cancelled = asyncio.Event()
        self.join_done = asyncio.Event()
        self.join_task: asyncio.Task[Any] | None = None
        self.join_error: BaseException | None = None
        self.disconnected = 0
        self.disconnect_entered = asyncio.Event()
        self.token: str | None = None
        if index not in owner.blocked_rooms:
            self.join_release.set()
        if owner.inventory:
            self.remote_participants["synthetic-participant"] = owner.participant

    def on(self, event: str, callback: Callable[..., None]) -> None:
        self.listeners.setdefault(event, []).append(callback)

    def off(self, event: str, callback: Callable[..., None]) -> None:
        listeners = self.listeners.get(event, [])
        if callback in listeners:
            listeners.remove(callback)

    async def connect(self, _: str, token: str, __: Any) -> None:
        self.token = token
        self.join_task = asyncio.current_task()
        self.join_entered.set()
        try:
            try:
                await self.join_release.wait()
            except asyncio.CancelledError:
                self.join_cancelled.set()
                raise
            if self.join_error is not None:
                raise self.join_error
            if self.owner.emit_during_join:
                self.emit_track()
        finally:
            self.join_done.set()

    async def disconnect(self) -> None:
        self.disconnected += 1
        self.disconnect_entered.set()

    def emit_track(self) -> None:
        for callback in tuple(self.listeners.get("track_subscribed", ())):
            callback(self.owner.track, self.owner.publication, self.owner.participant)


@dataclass
class SyntheticProvider:
    closed: int = 0

    async def close(self) -> None:
        self.closed += 1


class SyntheticStream:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.closed = 0
        self.task: asyncio.Task[Any] | None = None

    def __aiter__(self) -> AsyncIterator[Any]:
        return self

    async def __anext__(self) -> Any:
        self.task = asyncio.current_task()
        await self.release.wait()
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed += 1


@dataclass
class Boundary:
    blocked_rooms: set[int] = field(default_factory=lambda: set[int]())
    inventory: bool = False
    emit_during_join: bool = False
    tokens: list[SyntheticToken] = field(default_factory=lambda: list[SyntheticToken]())
    rooms: list[SyntheticRoom] = field(default_factory=lambda: list[SyntheticRoom]())
    providers: list[SyntheticProvider] = field(default_factory=lambda: list[SyntheticProvider]())
    streams: list[SyntheticStream] = field(default_factory=lambda: list[SyntheticStream]())
    track: Any = field(
        default_factory=lambda: SimpleNamespace(kind=rtc.TrackKind.KIND_AUDIO, sid="TR-same-object")
    )
    publication: Any = None
    participant: Any = None

    def __post_init__(self) -> None:
        self.publication = SimpleNamespace(track=self.track, sid=self.track.sid)
        self.participant = SimpleNamespace(
            identity=f"user-{uuid4()}-synthetic",
            track_publications={self.track.sid: self.publication},
        )

    def room(self) -> SyntheticRoom:
        result = SyntheticRoom(self, len(self.rooms))
        self.rooms.append(result)
        return result

    def provider(self, **_: Any) -> SyntheticProvider:
        result = SyntheticProvider()
        self.providers.append(result)
        return result

    def stream(self, _: Any, **options: Any) -> SyntheticStream:
        assert options == {
            "capacity": 10,
            "sample_rate": 16000,
            "num_channels": 1,
            "frame_size_ms": 100,
        }
        result = SyntheticStream()
        self.streams.append(result)
        return result

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def token(*_: Any) -> SyntheticToken:
            value = SyntheticToken()
            self.tokens.append(value)
            return value

        def options(**values: Any) -> SimpleNamespace:
            return SimpleNamespace(**values)

        monkeypatch.setattr(api, "AccessToken", token)
        monkeypatch.setattr(api, "VideoGrants", options)
        monkeypatch.setattr(rtc, "Room", self.room)
        monkeypatch.setattr(rtc, "RoomOptions", options)
        monkeypatch.setattr(rtc, "AudioStream", self.stream)
        monkeypatch.setattr(module, "FunAsrConnection", self.provider)

    async def finish_fixture(
        self,
        receivers: tuple[MatchAudioReceiver, ...],
        callers: tuple[asyncio.Task[Any], ...] = (),
    ) -> None:
        for room in self.rooms:
            room.join_release.set()
        for stream in self.streams:
            stream.release.set()
        async with asyncio.timeout(1):
            await asyncio.gather(*callers, return_exceptions=True)
            await asyncio.gather(
                *(receiver.close() for receiver in receivers), return_exceptions=True
            )
            tasks = {
                task
                for task in (
                    *(room.join_task for room in self.rooms),
                    *(stream.task for stream in self.streams),
                )
                if task is not None and task is not asyncio.current_task()
            }
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)
        await asyncio.sleep(0)


def settings() -> Any:
    return SimpleNamespace(
        livekit_url="wss://synthetic.invalid",
        livekit_api_key=SyntheticMaterial(),
        livekit_api_secret=SyntheticMaterial(),
        asr_api_key=SyntheticMaterial(),
        asr_ws_url="wss://synthetic.invalid/asr",
        asr_model="synthetic-unused-asr-model",
        asr_workspace_id=None,
        agent_audio_storage_dir=None,
    )


def receiver(match_id: UUID, callbacks: Any = None) -> MatchAudioReceiver:
    return MatchAudioReceiver(
        match_id=match_id,
        settings=settings(),
        callbacks=callbacks if callbacks is not None else AsyncMock(),
    )


@pytest.mark.asyncio
async def test_same_match_private_room_identities_are_unique_with_original_grants_and_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = Boundary()
    boundary.install(monkeypatch)
    match_id = uuid4()
    first, second = receiver(match_id), receiver(match_id)
    try:
        await first.start()
        await second.start()
        identities = [token.identity for token in boundary.tokens]
        assert len(identities) == len(set(identities)) == 2
        for token in boundary.tokens:
            assert re.fullmatch(rf"jx-asr-{match_id}-[0-9a-f]{{32}}", token.identity)
            assert token.ttl == timedelta(minutes=10)
            assert vars(token.grants) == {
                "room_join": True,
                "room": f"jx-match-{match_id}",
                "can_publish": False,
                "can_subscribe": True,
                "can_publish_data": False,
            }
        assert first._room is boundary.rooms[0] and second._room is boundary.rooms[1]
        assert first._connection is boundary.providers[0]
        assert second._connection is boundary.providers[1]
        assert all(room.disconnected == 0 for room in boundary.rooms)
    finally:
        await boundary.finish_fixture((first, second))


@pytest.mark.asyncio
async def test_track_event_before_exact_room_handoff_cannot_start_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = Boundary(blocked_rooms={0}, inventory=True)
    boundary.install(monkeypatch)
    target = receiver(uuid4())
    caller = asyncio.create_task(target.start())
    try:
        async with asyncio.timeout(1):
            while not boundary.rooms:
                await asyncio.sleep(0)
            await boundary.rooms[0].join_entered.wait()
        boundary.rooms[0].emit_track()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not boundary.streams and not target._track_tasks
        assert target._room is None and target._connection is None
        assert not boundary.providers
        boundary.rooms[0].join_release.set()
        await asyncio.wait_for(caller, timeout=1)
        async with asyncio.timeout(1):
            while not boundary.streams:
                await asyncio.sleep(0)
        assert len(boundary.streams) == len(target._track_tasks) == 1
        assert target._room is boundary.rooms[0] and target._connection is boundary.providers[0]
    finally:
        await boundary.finish_fixture((target,), (caller,))


@pytest.mark.asyncio
async def test_retired_room_listener_does_not_keep_its_receiver_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = Boundary()
    boundary.install(monkeypatch)
    target = receiver(uuid4())
    reference = weakref.ref(target)
    await target.start()
    await target.close()
    old_room = boundary.rooms[0]
    del target
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        gc.collect()
        assert reference() is None, "retired listener/cleanup ownership still retains receiver"
        old_room.emit_track()
        await asyncio.sleep(0)
        assert not boundary.streams and len(boundary.providers) == 1
        assert old_room.disconnected == 1 and boundary.providers[0].closed == 1
    finally:
        remaining = reference()
        await boundary.finish_fixture((remaining,) if remaining is not None else ())


@pytest.mark.asyncio
async def test_same_track_publication_is_deduplicated_per_room_without_cross_room_suppression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = Boundary(inventory=True, emit_during_join=True)
    boundary.install(monkeypatch)
    match_id = uuid4()
    first, second = receiver(match_id), receiver(match_id)
    try:
        await first.start()
        await second.start()
        async with asyncio.timeout(1):
            while len(boundary.streams) < 2:
                await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(boundary.streams) == 2
        assert len(first._track_tasks) == len(second._track_tasks) == 1
        for room in boundary.rooms:
            room.emit_track()
            room.emit_track()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(boundary.streams) == 2
        assert len(first._track_tasks) == len(second._track_tasks) == 1
        assert first._room is boundary.rooms[0] and second._room is boundary.rooms[1]
    finally:
        await boundary.finish_fixture((first, second))


@pytest.mark.asyncio
async def test_failed_active_owner_and_cancelled_waiter_cannot_retire_successful_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = Boundary(blocked_rooms={0})
    boundary.install(monkeypatch)
    callbacks = AsyncMock()
    runtime = AsrRuntime(settings=settings(), callbacks=callbacks)
    match_id = uuid4()
    active = asyncio.create_task(runtime.ensure_match(match_id))
    waiting: asyncio.Task[MatchAudioReceiver] | None = None
    old: MatchAudioReceiver | None = None
    replacement: MatchAudioReceiver | None = None
    try:
        async with asyncio.timeout(1):
            while not boundary.rooms:
                await asyncio.sleep(0)
            await boundary.rooms[0].join_entered.wait()
        old = runtime._receivers[match_id]
        waiting = asyncio.create_task(runtime.ensure_match(match_id))
        async with asyncio.timeout(1):
            while not any(
                not waiter.done()
                for waiter in (getattr(old._lifecycle_lock, "_waiters", None) or ())
            ):
                await asyncio.sleep(0)
        replacement = receiver(match_id, callbacks)
        runtime._receivers[match_id] = replacement
        await replacement.start()
        waiting.cancel()
        done, _ = await asyncio.wait({waiting}, timeout=0.3)
        assert done, "cancelled non-owner waiter must not await active startup cleanup"
        outcome = (await asyncio.gather(waiting, return_exceptions=True))[0]
        assert isinstance(outcome, asyncio.CancelledError)
        assert runtime._receivers[match_id] is replacement
        assert not replacement._closed and not old._closed and not active.done()
        original_error = RuntimeError("synthetic_active_owner_join_failure")
        boundary.rooms[0].join_error = original_error
        boundary.rooms[0].join_release.set()
        with pytest.raises(RuntimeError) as failure:
            await active
        assert failure.value is original_error
        assert runtime._receivers[match_id] is replacement and not replacement._closed
        assert old._closed and old._room is None and old._connection is None
        assert boundary.rooms[0].disconnected == 1
        assert boundary.rooms[1].disconnected == 0 and boundary.providers[0].closed == 0
        assert replacement._room is boundary.rooms[1]
        assert replacement._connection is boundary.providers[0]
        assert len(boundary.providers) == 1 and not callbacks.mock_calls
    finally:
        callers = (active, waiting) if waiting is not None else (active,)
        receivers = tuple(value for value in (old, replacement) if value is not None)
        await boundary.finish_fixture(receivers, callers)
        await runtime.close()


@pytest.mark.asyncio
async def test_double_startup_cancel_retires_only_old_owner_and_drops_its_late_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = Boundary(blocked_rooms={0})
    boundary.install(monkeypatch)
    callbacks = AsyncMock()
    runtime = AsrRuntime(settings=settings(), callbacks=callbacks)
    match_id = uuid4()
    active = asyncio.create_task(runtime.ensure_match(match_id))
    old: MatchAudioReceiver | None = None
    replacement: MatchAudioReceiver | None = None
    try:
        async with asyncio.timeout(1):
            while not boundary.rooms:
                await asyncio.sleep(0)
            await boundary.rooms[0].join_entered.wait()
        old = runtime._receivers[match_id]
        old_room = boundary.rooms[0]
        assert active.cancel() and active.cancel()
        assert active.cancelling() == 2
        done, _ = await asyncio.wait({active}, timeout=0.3)
        assert done, "startup caller retirement must be independent of the pending SDK join"
        outcome = (await asyncio.gather(active, return_exceptions=True))[0]
        assert isinstance(outcome, asyncio.CancelledError)
        assert match_id not in runtime._receivers
        assert old._closed and old._room is None and old._connection is None
        assert not old_room.join_cancelled.is_set() and not old_room.join_done.is_set()
        replacement = await runtime.ensure_match(match_id)
        assert replacement is not old
        assert replacement._room is boundary.rooms[1]
        assert replacement._connection is boundary.providers[0]
        old_room.join_release.set()
        await asyncio.wait_for(old_room.disconnect_entered.wait(), timeout=0.3)
        old_room.emit_track()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert old_room.join_task is not None and old_room.join_task.done()
        assert not old_room.join_task.cancelled()
        assert not getattr(old_room.join_task, "_log_traceback", True)
        assert not old._track_tasks and not boundary.streams
        assert old._room is None and old._connection is None
        assert runtime._receivers[match_id] is replacement and not replacement._closed
        assert len(boundary.providers) == 1 and not callbacks.mock_calls
        assert old_room.disconnected == 1 and boundary.rooms[1].disconnected == 0
        assert boundary.providers[0].closed == 0
    finally:
        receivers = tuple(value for value in (old, replacement) if value is not None)
        await boundary.finish_fixture(receivers, (active,))
        await runtime.close()

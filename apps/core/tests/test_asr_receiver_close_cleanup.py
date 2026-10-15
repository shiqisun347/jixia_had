"""Already-owned ASR receiver cleanup with synthetic resource interfaces only.

The real close/detach/cancellation methods run. No startup, Settings, dotenv,
network, native RTC, database, provider stream or PCM input is used.
"""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

import jx_core.asr.runtime as module
from jx_core.asr.runtime import MatchAudioReceiver


@dataclass
class OwnedTask:
    resistant: bool = False
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    late_error: BaseException | None = None
    task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        self.ready.set()
        try:
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
                    if not self.resistant:
                        raise
            if self.late_error is not None:
                raise self.late_error
        finally:
            self.done.set()


@dataclass
class OwnedResource:
    resistant: bool = False
    calls: int = 0
    started: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    factory_error: BaseException | None = None
    late_error: BaseException | None = None
    task: asyncio.Task[Any] | None = None

    def close(self) -> Awaitable[None]:
        self.calls += 1
        if self.factory_error is not None:
            raise self.factory_error
        return self._run()

    def disconnect(self) -> Awaitable[None]:
        return self.close()

    async def _run(self) -> None:
        self.task = asyncio.current_task()
        self.started.set()
        try:
            while self.resistant and not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
            if self.late_error is not None:
                raise self.late_error
        finally:
            self.done.set()


@dataclass
class OwnedReceiver:
    receiver: MatchAudioReceiver
    speech: OwnedTask
    track: OwnedTask
    provider: OwnedResource
    room: OwnedResource

    async def observe_all_started(self) -> None:
        async with asyncio.timeout(0.3):
            await asyncio.gather(
                self.speech.cancelled.wait(),
                self.track.cancelled.wait(),
                self.provider.started.wait(),
                self.room.started.wait(),
            )

    async def finish_fixture(self) -> None:
        for resource in (self.speech, self.track, self.provider, self.room):
            resource.release.set()
        for task in (self.speech.task, self.track.task):
            if task is not None and not task.done():
                task.cancel()
        tasks = {
            resource.task
            for resource in (self.speech, self.track, self.provider, self.room)
            if resource.task is not None
        }
        if tasks:
            async with asyncio.timeout(1):
                await asyncio.gather(*tasks, return_exceptions=True)


def new_receiver(match_id: UUID | None = None) -> MatchAudioReceiver:
    synthetic_settings: Any = SimpleNamespace()
    return MatchAudioReceiver(
        match_id=match_id or uuid4(), settings=synthetic_settings, callbacks=AsyncMock()
    )


async def owned_receiver(*, blocked: str | None = None) -> OwnedReceiver:
    target = new_receiver()
    speech = OwnedTask(resistant=blocked == "speech")
    track = OwnedTask(resistant=blocked == "track")
    speech.task = asyncio.create_task(speech.run(), name="synthetic-owned-asr-speech")
    track.task = asyncio.create_task(track.run(), name="synthetic-owned-asr-track")
    await asyncio.gather(speech.ready.wait(), track.ready.wait())
    provider = OwnedResource(resistant=blocked == "provider")
    room = OwnedResource(resistant=blocked == "room")
    provider_interface: Any = provider
    room_interface: Any = room
    target._speech_task = speech.task
    target._track_tasks.add(track.task)
    target._connection = provider_interface
    target._room = room_interface
    return OwnedReceiver(target, speech, track, provider, room)


@pytest.fixture
def short_existing_cleanup_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "ASR_CLEANUP_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(module, "ASR_TASK_CANCEL_TIMEOUT_SECONDS", 0.03)


@pytest.mark.asyncio
@pytest.mark.usefixtures("short_existing_cleanup_budgets")
@pytest.mark.parametrize("stage", ["speech", "track", "provider", "room"])
async def test_repeated_close_cancellation_still_starts_every_exact_owned_resource(
    stage: str,
) -> None:
    owned = await owned_receiver(blocked=stage)
    task = asyncio.create_task(owned.receiver.close())
    try:
        resource = getattr(owned, stage)
        entered = resource.cancelled if stage in {"speech", "track"} else resource.started
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=0.3)
        assert done, "repeated cancellation must not retain the caller in resource close"
        outcome = (await asyncio.gather(task, return_exceptions=True))[0]
        assert isinstance(outcome, asyncio.CancelledError)
        await owned.observe_all_started()
        assert owned.provider.calls == owned.room.calls == 1
        assert owned.receiver._closed and owned.receiver._speech_task is None
        assert not owned.receiver._track_tasks
        assert owned.receiver._connection is None and owned.receiver._room is None
    finally:
        await owned.finish_fixture()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_normal_close_is_idempotent_and_retires_exact_transferred_resources() -> None:
    owned = await owned_receiver()
    try:
        await asyncio.wait_for(owned.receiver.close(), timeout=0.3)
        await owned.observe_all_started()
        assert owned.provider.calls == owned.room.calls == 1
        assert owned.speech.task is not None and owned.speech.task.cancelled()
        assert owned.track.task is not None and owned.track.task.cancelled()
        assert owned.receiver._closed and owned.receiver._speech_task is None
        assert owned.receiver._speech is None and owned.receiver._speech_envelope is None
        assert owned.receiver._connection is None and owned.receiver._room is None
        assert not owned.receiver._track_tasks
        assert not cast(AsyncMock, owned.receiver._callbacks).mock_calls
        await owned.receiver.close()
        assert owned.provider.calls == owned.room.calls == 1
    finally:
        await owned.finish_fixture()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["provider", "room"])
async def test_synchronous_close_factory_failure_does_not_skip_other_owned_resources(
    stage: str,
) -> None:
    owned = await owned_receiver()
    getattr(owned, stage).factory_error = RuntimeError("synthetic_close_factory_failed")
    try:
        await asyncio.wait_for(owned.receiver.close(), timeout=0.3)
        other = owned.room if stage == "provider" else owned.provider
        async with asyncio.timeout(0.3):
            await asyncio.gather(
                owned.speech.cancelled.wait(), owned.track.cancelled.wait(), other.started.wait()
            )
        assert owned.provider.calls == owned.room.calls == 1
        assert owned.receiver._closed
        assert owned.receiver._connection is None and owned.receiver._room is None
    finally:
        await owned.finish_fixture()


@pytest.mark.asyncio
@pytest.mark.usefixtures("short_existing_cleanup_budgets")
@pytest.mark.parametrize("stage", ["speech", "track", "provider", "room"])
async def test_resistant_owned_cleanup_is_bounded_and_its_late_error_is_consumed(
    stage: str,
) -> None:
    owned = await owned_receiver(blocked=stage)
    resource = getattr(owned, stage)
    resource.late_error = RuntimeError("synthetic_late_owned_cleanup_error")
    try:
        await asyncio.wait_for(owned.receiver.close(), timeout=0.3)
        await owned.observe_all_started()
        assert owned.provider.calls == owned.room.calls == 1
        assert resource.cancelled.is_set() and not resource.done.is_set()
        resource.release.set()
        await asyncio.wait_for(resource.done.wait(), timeout=1)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert resource.task is not None and resource.task.done()
        # Observe before fixture gather retrieves results: the application owner
        # must consume the exact late task's exception, not just drop its handle.
        assert not getattr(resource.task, "_log_traceback", True)
    finally:
        await owned.finish_fixture()


@pytest.mark.asyncio
@pytest.mark.usefixtures("short_existing_cleanup_budgets")
async def test_retired_receiver_late_cleanup_cannot_close_same_match_replacement() -> None:
    owned = await owned_receiver(blocked="provider")
    replacement = new_receiver(owned.receiver.match_id)
    new_provider, new_room = OwnedResource(), OwnedResource()
    replacement_provider: Any = new_provider
    replacement_room: Any = new_room
    replacement._connection = replacement_provider
    replacement._room = replacement_room
    owned.provider.late_error = RuntimeError("synthetic_retired_provider_error")
    try:
        await asyncio.wait_for(owned.receiver.close(), timeout=0.3)
        await owned.observe_all_started()
        assert owned.provider.calls == owned.room.calls == 1
        assert new_provider.calls == new_room.calls == 0
        assert replacement._connection is new_provider and replacement._room is new_room
        assert not replacement._closed
        owned.provider.release.set()
        await asyncio.wait_for(owned.provider.done.wait(), timeout=1)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert owned.provider.task is not None
        assert not getattr(owned.provider.task, "_log_traceback", True)
        assert new_provider.calls == new_room.calls == 0
        assert replacement._connection is new_provider and replacement._room is new_room
        assert not replacement._closed
        assert not cast(AsyncMock, replacement._callbacks).mock_calls
    finally:
        await owned.finish_fixture()
        await replacement.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("slot", ["speech", "track"])
async def test_resource_task_closing_its_receiver_does_not_cancel_itself(slot: str) -> None:
    target = new_receiver()
    provider, room = OwnedResource(), OwnedResource()
    provider_interface: Any = provider
    room_interface: Any = room
    target._connection = provider_interface
    target._room = room_interface

    async def self_close() -> None:
        task = asyncio.current_task()
        assert task is not None
        if slot == "speech":
            target._speech_task = task
        else:
            target._track_tasks.add(task)
        await target.close()
        assert not task.cancelled() and task.cancelling() == 0
        assert provider.calls == room.calls == 1
        assert target._closed and target._speech_task is None and not target._track_tasks

    task = asyncio.create_task(self_close())
    try:
        await asyncio.wait_for(task, timeout=0.3)
        assert not task.cancelled()
        assert target._connection is None and target._room is None
    finally:
        provider.release.set()
        room.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

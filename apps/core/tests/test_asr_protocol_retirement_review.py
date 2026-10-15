"""Three independent lifetime checks for the approved Spec302 candidate."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest

from test_asr_protocol_retirement_epoch import OwnedSocket, Probe, hygiene, no_audio, observe
from test_asr_protocol_retirement_epoch import external_guard as external_guard


@pytest.mark.asyncio
async def test_current_epoch_caller_cancel_keeps_owned_capture_for_take(
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
        if False:
            yield b""

    connection = probe.connection(factory)
    task_id = uuid4()
    recognition: asyncio.Task[Any] = asyncio.create_task(
        connection.recognize_segment(chunks(), on_sentence=probe.on_sentence, task_id=task_id)
    )
    try:
        await asyncio.wait_for(chunks_entered.wait(), timeout=1)
        assert owned_socket.actions == ["run-task"] and not recognition.done()
        recognition.cancel()
        assert await observe(recognition.done)
        snapshot = probe.snapshot(connection, owned_socket, recognition)
        print("current_cancel_review_product_observations=" + json.dumps(snapshot, sort_keys=True))
        assert recognition.cancelled(), snapshot
        assert connection._socket is None and owned_socket.close_calls == 1, snapshot
        assert len(connection._captures) == probe.budget.items == 1, snapshot
        capture = connection.take_capture(task_id)
        assert capture is probe.captures[0]
        assert capture is not None and capture.request is not None and capture.response is not None
        capture.close()
        assert not connection._captures
        assert (
            probe.budget.items == probe.budget.retained_bytes == probe.budget.workspace_bytes == 0
        )
        assert not external_guard
    finally:
        chunks_release.set()
        await hygiene(connection, probe, [recognition], baseline)


@pytest.mark.asyncio
async def test_old_close_return_does_not_clear_new_epoch_socket_or_capture(
    external_guard: dict[str, int],
) -> None:
    baseline = asyncio.all_tasks()
    probe = Probe()
    old_close_entered, old_close_release = asyncio.Event(), asyncio.Event()

    class DelayedCloseSocket(OwnedSocket):
        async def close(self) -> None:
            self.close_calls += 1
            old_close_entered.set()
            await old_close_release.wait()

    old_socket, new_socket = DelayedCloseSocket(), OwnedSocket()
    sockets = [old_socket, new_socket]
    factory_calls = 0

    async def factory(_: str, __: dict[str, str]) -> OwnedSocket:
        nonlocal factory_calls
        owned = sockets[factory_calls]
        factory_calls += 1
        return owned

    connection = probe.connection(factory)
    tasks: list[asyncio.Task[Any]] = []
    first = asyncio.create_task(
        connection.recognize_segment(no_audio(), on_sentence=probe.on_sentence)
    )
    tasks.append(first)
    try:
        first_result = await asyncio.wait_for(first, timeout=1)
        assert connection._captures[first_result.task_id] is probe.captures[0]
        old_close = asyncio.create_task(connection.close())
        tasks.append(old_close)
        await asyncio.wait_for(old_close_entered.wait(), timeout=1)
        assert not old_close.done() and connection._socket is None
        assert not connection._captures and probe.budget.items == 0
        second = asyncio.create_task(
            connection.recognize_segment(no_audio(), on_sentence=probe.on_sentence)
        )
        tasks.append(second)
        second_result = await asyncio.wait_for(second, timeout=1)
        current_capture = connection._captures[second_result.task_id]
        current_bytes = probe.budget.retained_bytes
        assert current_capture is probe.captures[1] and current_bytes > 0
        old_close_release.set()
        await asyncio.wait_for(old_close, timeout=1)
        snapshot = probe.snapshot(connection, new_socket, second)
        snapshot.update(
            factory_calls=factory_calls,
            old_socket_close_calls=old_socket.close_calls,
            new_socket_close_calls=new_socket.close_calls,
            new_capture_retained=connection._captures.get(second_result.task_id) is current_capture,
            new_bytes_unchanged=probe.budget.retained_bytes == current_bytes,
            old_capture_released=probe.captures[0].reservation is None,
            guard=dict(external_guard),
        )
        print("replacement_review_product_observations=" + json.dumps(snapshot, sort_keys=True))
        assert factory_calls == 2 and connection._socket is new_socket, snapshot
        assert old_socket.close_calls == 1 and new_socket.close_calls == 0, snapshot
        assert connection._captures.get(second_result.task_id) is current_capture, snapshot
        assert probe.budget.items == 1 and probe.budget.retained_bytes == current_bytes, snapshot
        assert probe.captures[0].reservation is None, snapshot
        assert connection.take_capture(second_result.task_id) is current_capture
        current_capture.close()
        assert (
            probe.budget.items == probe.budget.retained_bytes == probe.budget.workspace_bytes == 0
        )
        assert not external_guard
    finally:
        old_close_release.set()
        await hygiene(connection, probe, tasks, baseline)


@pytest.mark.asyncio
async def test_retired_double_cancel_releases_local_capture_before_fixture_cleanup(
    external_guard: dict[str, int],
) -> None:
    baseline = asyncio.all_tasks()
    probe = Probe()
    receiver_release = asyncio.Event()
    receiver_cancelled_twice = asyncio.Event()
    receiver_cancelled_once = asyncio.Event()
    chunks_entered, chunks_release = asyncio.Event(), asyncio.Event()
    sender_owners: list[asyncio.Task[Any]] = []

    class ResistantReceiveSocket(OwnedSocket):
        cancellations = 0

        async def recv(self) -> str | bytes:
            if self.recv_calls == 0:
                return await super().recv()
            self.recv_calls += 1
            while not receiver_release.is_set():
                try:
                    await receiver_release.wait()
                except asyncio.CancelledError:
                    self.cancellations += 1
                    receiver_cancelled_once.set()
                    if self.cancellations >= 2:
                        receiver_cancelled_twice.set()
            return "{}"

    owned_socket = ResistantReceiveSocket()

    async def factory(_: str, __: dict[str, str]) -> OwnedSocket:
        return owned_socket

    async def chunks() -> AsyncIterator[bytes]:
        owner = asyncio.current_task()
        assert owner is not None
        sender_owners.append(owner)
        chunks_entered.set()
        await chunks_release.wait()
        if False:
            yield b""

    connection = probe.connection(factory)
    recognition: asyncio.Task[Any] = asyncio.create_task(
        connection.recognize_segment(chunks(), on_sentence=probe.on_sentence)
    )
    try:
        await asyncio.wait_for(chunks_entered.wait(), timeout=1)
        assert await observe(lambda: owned_socket.recv_calls == 2)
        assert probe.budget.items == 1 and not connection._captures
        await asyncio.wait_for(connection.close(), timeout=0.3)
        recognition.cancel()
        await asyncio.wait_for(receiver_cancelled_once.wait(), timeout=1)
        assert not recognition.done()
        before_second_cancel_bytes = probe.budget.retained_bytes
        recognition.cancel()
        await asyncio.wait_for(receiver_cancelled_twice.wait(), timeout=1)
        receiver_release.set()
        assert await observe(recognition.done)
        snapshot = probe.snapshot(connection, owned_socket, recognition)
        snapshot.update(
            receiver_cancellations=owned_socket.cancellations,
            sender_count=len(sender_owners),
            sender_pending_before_fixture=any(not owner.done() for owner in sender_owners),
            before_second_cancel_bytes=before_second_cancel_bytes,
            local_capture_released=probe.captures[0].reservation is None,
            guard=dict(external_guard),
        )
        print("double_cancel_review_product_observations=" + json.dumps(snapshot, sort_keys=True))
        assert recognition.cancelled() and owned_socket.cancellations >= 2, snapshot
        assert connection._socket is None and not connection._captures, snapshot
        assert probe.captures[0].reservation is None, snapshot
        assert (
            probe.budget.items == probe.budget.retained_bytes == probe.budget.workspace_bytes == 0
        )
        assert owned_socket.actions == ["run-task"] and owned_socket.pcm_frames == 0
        assert len(sender_owners) == 1 and all(owner.done() for owner in sender_owners), snapshot
        assert not external_guard
    finally:
        receiver_release.set()
        chunks_release.set()
        await hygiene(connection, probe, [recognition], baseline)

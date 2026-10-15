"""Actual sender/recv children must recheck ownership at first execution.

Python 3.12 positive-timeout wait_for awaits its coroutine inline. These probes
target actual create_task boundaries and callbacks that schedule public close.
"""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import json
from collections.abc import Coroutine
from contextvars import Context
from typing import Any, Literal

import pytest

from jx_core.asr.protocol import FunAsrSentence
from test_asr_protocol_retirement_epoch import OwnedSocket, Probe, hygiene, no_audio, observe
from test_asr_protocol_retirement_epoch import external_guard as external_guard


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["sender", "receiver", "on_started", "callback"])
async def test_public_close_runs_before_actual_protocol_child_first_execution(
    external_guard: dict[str, int],
    boundary: Literal["sender", "receiver", "on_started", "callback"],
) -> None:
    baseline = asyncio.all_tasks()
    loop = asyncio.get_running_loop()
    old_factory = loop.get_task_factory()
    probe = Probe()
    owned_socket = OwnedSocket(emit_sentence=boundary in {"receiver", "callback"})
    factory_calls = 0
    callback_entries = 0
    close_owners: list[asyncio.Task[None]] = []
    selected_child_name: str | None = None

    async def factory(_: str, __: dict[str, str]) -> OwnedSocket:
        nonlocal factory_calls
        factory_calls += 1
        return owned_socket

    async def on_sentence(sentence: FunAsrSentence) -> None:
        nonlocal callback_entries
        callback_entries += 1
        await probe.on_sentence(sentence)
        if boundary == "callback":
            close_owners.append(asyncio.Task(connection.close(), loop=loop))

    def on_started() -> None:
        probe.on_started()
        if boundary == "on_started":
            close_owners.append(asyncio.Task(connection.close(), loop=loop))

    connection = probe.connection(factory)
    selected_code = {
        "sender": "send_audio",
        # The candidate schedules this actual guarded wrapper. Its entry must
        # reject the old owner before the unchanged OwnedSocket.recv is entered.
        "receiver": "_receive_owned",
        "on_started": None,
        "callback": None,
    }[boundary]

    def schedule_close_first(
        owner_loop: asyncio.AbstractEventLoop,
        coroutine: Coroutine[Any, Any, Any],
        context: Context | None = None,
    ) -> asyncio.Task[Any]:
        nonlocal selected_child_name
        code = getattr(coroutine, "cr_code", None)
        if not close_owners and code is not None and code.co_name == selected_code:
            selected_child_name = code.co_qualname
            # Direct Task construction avoids recursive test interception.
            # asyncio queues this actual public close before the transport child
            # gets its first turn; no protocol method or transport body is patched.
            close_owners.append(asyncio.Task(connection.close(), loop=owner_loop))
        return asyncio.Task(coroutine, loop=owner_loop, context=context)

    loop.set_task_factory(schedule_close_first)
    recognition = asyncio.create_task(
        connection.recognize_segment(no_audio(), on_sentence=on_sentence, on_started=on_started)
    )
    try:
        assert await observe(recognition.done)
        assert len(close_owners) == 1
        if boundary in {"sender", "receiver"}:
            assert selected_child_name is not None
        await asyncio.wait_for(asyncio.gather(*close_owners), timeout=0.4)
        snapshot = probe.snapshot(connection, owned_socket, recognition)
        snapshot.update(
            boundary=boundary,
            selected_child_name=selected_child_name,
            factory_calls=factory_calls,
            recv_calls=owned_socket.recv_calls,
            callback_entries=callback_entries,
            close_owner_done=close_owners[0].done(),
            guard=dict(external_guard),
        )
        print("retirement_scheduling_product_observations=" + json.dumps(snapshot, sort_keys=True))
        if boundary in {"sender", "on_started"}:
            assert owned_socket.actions == ["run-task"], snapshot
            assert owned_socket.pcm_frames == 0 and owned_socket.recv_calls == 1, snapshot
            assert callback_entries == probe.sentence_calls == 0, snapshot
        elif boundary == "receiver":
            assert owned_socket.recv_calls == 1 and callback_entries == probe.sentence_calls == 0
            assert owned_socket.actions == ["run-task", "finish-task"], snapshot
        else:
            # One callback entered before it schedules close. Its effect cannot
            # be undone; no subsequent recv/success/capture may start after close.
            assert callback_entries == probe.sentence_calls == 1, snapshot
            assert owned_socket.recv_calls == 2, snapshot
            assert owned_socket.actions == ["run-task", "finish-task"], snapshot
        assert recognition.cancelled(), snapshot
        assert connection._socket is None and not connection._captures, snapshot
        assert probe.budget.items == probe.budget.retained_bytes == 0, snapshot
        assert not external_guard
    finally:
        loop.set_task_factory(old_factory)
        await hygiene(connection, probe, [recognition, *close_owners], baseline)

"""Close epoch must outrank late ordinary errors, not current errors."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Literal
from uuid import uuid4

import pytest

from jx_core.asr.protocol import FunAsrConnection, FunAsrError, FunAsrSentence
from test_asr_protocol_retirement_epoch import OwnedSocket, Probe, hygiene, no_audio, observe
from test_asr_protocol_retirement_epoch import external_guard as external_guard


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "retired"),
    [("factory", True), ("send", True), ("callback", True)],
    ids=["late-factory", "late-send", "late-callback"],
)
async def test_ordinary_error_is_original_for_current_epoch_and_cancel_for_retired_epoch(
    external_guard: dict[str, int],
    stage: Literal["factory", "send", "chunks", "callback"],
    retired: bool,
) -> None:
    baseline = asyncio.all_tasks()
    probe = Probe()
    entered, release = asyncio.Event(), asyncio.Event()
    original_error = ConnectionError("synthetic delayed ordinary error")

    async def fail_after_release() -> None:
        entered.set()
        await release.wait()
        raise original_error

    class ErrorSocket(OwnedSocket):
        async def send(self, message: str | bytes) -> None:
            if stage == "send":
                await fail_after_release()
            await super().send(message)

    owned_socket = ErrorSocket(emit_sentence=stage == "callback")

    async def factory(_: str, __: dict[str, str]) -> OwnedSocket:
        if stage == "factory":
            await fail_after_release()
        return owned_socket

    async def chunks() -> AsyncIterator[bytes]:
        if stage == "chunks":
            await fail_after_release()
        if False:
            yield b""

    async def on_sentence(sentence: FunAsrSentence) -> None:
        await probe.on_sentence(sentence)
        if stage == "callback":
            await fail_after_release()

    connection = probe.connection(factory)
    task_id = uuid4()
    recognition = asyncio.create_task(
        connection.recognize_segment(chunks(), on_sentence=on_sentence, task_id=task_id)
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert not recognition.done() and len(probe.captures) == 1
        if retired:
            await asyncio.wait_for(connection.close(), timeout=0.3)
        release.set()
        assert await observe(recognition.done)
        snapshot = probe.snapshot(connection, owned_socket, recognition)
        error: BaseException | None = None
        if not recognition.cancelled():
            error = recognition.exception()
        snapshot.update(
            stage=stage,
            retired=retired,
            error_code=getattr(error, "code", None),
            original_error_identity=error is original_error,
            guard=dict(external_guard),
        )
        print("retirement_error_product_observations=" + json.dumps(snapshot, sort_keys=True))
        if retired:
            assert recognition.cancelled(), snapshot
            assert not connection._captures and connection._socket is None, snapshot
            assert probe.budget.items == probe.budget.retained_bytes == 0, snapshot
        else:
            assert not recognition.cancelled(), snapshot
            if stage == "callback":
                assert error is original_error, snapshot
            else:
                expected = "asr_connection_failed" if stage == "factory" else "asr_stream_failed"
                assert isinstance(error, FunAsrError) and error.code == expected, snapshot
                assert error.__cause__ is original_error, snapshot
            capture = connection.take_capture(task_id)
            assert capture is probe.captures[0]
            assert (
                capture is not None and capture.request is not None and capture.response is not None
            )
            capture.close()
            assert probe.budget.items == probe.budget.retained_bytes == 0
        assert not external_guard
    finally:
        release.set()
        await hygiene(connection, probe, [recognition], baseline)


@pytest.mark.asyncio
@pytest.mark.parametrize("retired", [True], ids=["close-during-cleanup"])
async def test_exception_cleanup_await_cannot_store_capture_after_close(
    external_guard: dict[str, int], retired: bool
) -> None:
    baseline = asyncio.all_tasks()
    probe = Probe()
    close_entered, close_release = asyncio.Event(), asyncio.Event()
    original_error = ConnectionError("synthetic receive failure")

    class ErrorAndDelayedCloseSocket(OwnedSocket):
        async def recv(self) -> str | bytes:
            if self.recv_calls >= 1:
                raise original_error
            return await super().recv()

        async def close(self) -> None:
            self.close_calls += 1
            close_entered.set()
            await close_release.wait()

    owned_socket = ErrorAndDelayedCloseSocket()

    async def factory(_: str, __: dict[str, str]) -> OwnedSocket:
        return owned_socket

    connection: FunAsrConnection = probe.connection(factory)
    task_id = uuid4()
    recognition: asyncio.Task[Any] = asyncio.create_task(
        connection.recognize_segment(no_audio(), on_sentence=probe.on_sentence, task_id=task_id)
    )
    try:
        await asyncio.wait_for(close_entered.wait(), timeout=1)
        assert not recognition.done() and connection._socket is None
        assert not connection._captures and probe.budget.items == 1
        if retired:
            await asyncio.wait_for(connection.close(), timeout=0.3)
        close_release.set()
        assert await observe(recognition.done)
        snapshot = probe.snapshot(connection, owned_socket, recognition)
        snapshot.update(retired=retired, guard=dict(external_guard))
        print("retirement_cleanup_product_observations=" + json.dumps(snapshot, sort_keys=True))
        if retired:
            assert recognition.cancelled(), snapshot
            assert (
                not connection._captures and probe.budget.items == probe.budget.retained_bytes == 0
            )
        else:
            assert recognition.exception() is original_error, snapshot
            capture = connection.take_capture(task_id)
            assert capture is probe.captures[0] and capture is not None
            capture.close()
            assert probe.budget.items == probe.budget.retained_bytes == 0
        assert not external_guard
    finally:
        close_release.set()
        await hygiene(connection, probe, [recognition], baseline)

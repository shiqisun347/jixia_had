"""Independent startup boundary assertions; all external resources are synthetic."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import socket
from collections.abc import Callable, Iterator
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from livekit.rtc import _ffi_client
from pydantic_settings import BaseSettings
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from jx_core.asr.protocol import FunAsrError
from jx_core.asr.runtime import AsrRuntime
from test_asr_startup_cleanup import FakeSdk, receiver, settings


@pytest.fixture
def review_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, int]]:
    attempts: dict[str, int] = {}

    def forbidden(category: str) -> Callable[..., Any]:
        def reject(*_: Any, **__: Any) -> Any:
            attempts[category] = attempts.get(category, 0) + 1
            raise AssertionError(f"forbidden ASR startup review boundary: {category}")

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
    yield attempts
    assert not attempts, attempts
    assert _ffi_client.FfiClient._instance is None


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["listener", "provider-constructor"])
async def test_non_connect_timeout_preserves_original_exception_object(
    monkeypatch: pytest.MonkeyPatch,
    review_guard: dict[str, int],
    phase: str,
) -> None:
    error = TimeoutError(f"synthetic_{phase}_timeout")
    sdk = FakeSdk(fail_step=phase, error=cast(Any, error))
    sdk.install(monkeypatch)
    target = receiver()
    try:
        with pytest.raises(TimeoutError) as failure:
            await target.start()
        assert failure.value is error
        assert not review_guard
    finally:
        await target.close()
        # Baseline listener registration is outside its rollback try. This is
        # fixture disposal after the classification assertion, not evidence.
        for room in sdk.rooms:
            if room.disconnected == 0:
                await room.disconnect()


@pytest.mark.asyncio
async def test_connect_timeout_retains_existing_start_timeout_classification(
    monkeypatch: pytest.MonkeyPatch,
    review_guard: dict[str, int],
) -> None:
    error = TimeoutError("synthetic_connect_timeout")
    sdk = FakeSdk(fail_step="connect", error=cast(Any, error))
    sdk.install(monkeypatch)
    target = receiver()
    try:
        with pytest.raises(FunAsrError) as failure:
            await target.start()
        assert failure.value.code == "asr_start_timeout"
        assert failure.value.__cause__ is error
        assert not review_guard
    finally:
        await target.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("close_kind", ["match", "runtime", "receiver"])
async def test_terminal_close_during_pending_join_never_hands_off_provider_or_tracks(
    monkeypatch: pytest.MonkeyPatch,
    review_guard: dict[str, int],
    close_kind: str,
) -> None:
    sdk = FakeSdk(join_pending=True, prepublished_track=True)
    sdk.install(monkeypatch)
    runtime = AsrRuntime(settings=settings(), callbacks=AsyncMock())
    match_id = uuid4()
    startup = asyncio.create_task(runtime.ensure_match(match_id))
    closing: asyncio.Task[None] | None = None
    target = None
    try:
        for _ in range(100):
            if sdk.rooms:
                break
            await asyncio.sleep(0)
        assert len(sdk.rooms) == 1
        room = sdk.rooms[0]
        await asyncio.wait_for(room.join_entered.wait(), timeout=1)
        target = runtime._receivers[match_id]
        if close_kind == "match":
            closing = asyncio.create_task(runtime.close_match(match_id))
        elif close_kind == "runtime":
            closing = asyncio.create_task(runtime.close())
        else:
            closing = asyncio.create_task(target.close())
        # The pending SDK join owns the lifecycle lock. Allow terminal close to
        # run to its first await before releasing that exact old join.
        for _ in range(8):
            await asyncio.sleep(0)
        assert not closing.done()
        if close_kind == "match":
            assert match_id in runtime._closed_matches
            assert match_id not in runtime._receivers
        elif close_kind == "runtime":
            assert runtime._closed
            assert not runtime._receivers
        assert not sdk.connections
        assert not sdk.streams
        assert not room.join_cancelled.is_set()

        room.join_release.set()
        outcomes = await asyncio.wait_for(
            asyncio.gather(startup, closing, return_exceptions=True), timeout=1
        )
        # Assert product postconditions before any fixture disposal.
        assert not sdk.connections, {
            "provider_constructors": len(sdk.connections),
            "audio_streams": len(sdk.streams),
            "startup_result": type(outcomes[0]).__name__,
            "close_kind": close_kind,
        }
        assert not sdk.streams
        assert isinstance(outcomes[0], FunAsrError)
        assert outcomes[0].code == "asr_receiver_closed"
        assert outcomes[1] is None
        assert target._room is None
        assert target._connection is None
        assert not target._track_tasks
        assert room.disconnected == 1
        assert room.join_done.is_set()
        assert not room.join_cancelled.is_set()
        assert not review_guard
    finally:
        for room in sdk.rooms:
            room.join_release.set()
        for stream in sdk.streams:
            stream.release.set()
        pending: list[asyncio.Task[Any]] = [startup]
        if closing is not None:
            pending.append(closing)
        await asyncio.gather(*pending, return_exceptions=True)
        if target is not None:
            await target.close()
        await runtime.close()

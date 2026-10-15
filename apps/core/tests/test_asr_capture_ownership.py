"""ASR capture handoff releases its actual process-owned pool without relying on GC."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

import jx_core.data_capture.provider_writer as writer_module
from jx_core.asr.runtime import MatchAudioReceiver
from jx_core.asr.session import AsrSpeechSession
from jx_core.config import Settings
from jx_core.data_capture.provider_writer import (
    ProviderCaptureWriter,
    prepare_capture,
    submit_capture,
)
from jx_core.models import ExternalCall
from jx_core.runtime_identity import CallbackEnvelope


@pytest.fixture
async def capture_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[ProviderCaptureWriter, None]:
    writer = ProviderCaptureWriter("postgresql+psycopg://test:test@localhost/test")
    monkeypatch.setattr(writer, "_persist_batch", AsyncMock())
    monkeypatch.setattr(writer_module, "FLUSH_SECONDS", 0.001)
    await writer.start()
    try:
        yield writer
    finally:
        await writer.close()


def receiver(callbacks: Any) -> MatchAudioReceiver:
    return MatchAudioReceiver(
        match_id=uuid4(),
        settings=Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            app_env="test",
            database_url=SecretStr("postgresql+psycopg://test:test@localhost/test"),
        ),
        callbacks=callbacks,
    )


def speech_envelope(service: MatchAudioReceiver) -> CallbackEnvelope:
    return CallbackEnvelope(
        match_id=service.match_id,
        speech_id=uuid4(),
        attempt_no=1,
        generation_id=None,
        connection_epoch=1,
        context_version=1,
        opportunity_id=None,
        opportunity_generation=None,
    )


@pytest.mark.asyncio
async def test_session_without_capture_callback_releases_pool_and_buffer_owner_cycle(
    capture_writer: ProviderCaptureWriter,
) -> None:
    capture = capture_writer.new_capture()
    buffer = capture.event_buffer()
    buffer.append({"header": {"event": "task-finished"}})
    assert capture_writer.budget.items == 1 and capture_writer.budget.retained_bytes > 0
    session = AsrSpeechSession(
        speech_id=uuid4(),
        connection=cast(Any, SimpleNamespace()),
        on_interim=AsyncMock(),
        on_segment=AsyncMock(),
    )
    await session._emit_capture(uuid4(), capture)  # pyright: ignore[reportPrivateUsage]
    assert capture.reservation is None and len(buffer) == 0
    assert capture.buffers == [] and capture.request is capture.response is None
    assert capture_writer.budget.items == capture_writer.budget.retained_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["callback", "envelope"])
async def test_detached_receiver_capture_releases_pool_without_callback(
    capture_writer: ProviderCaptureWriter, missing: str
) -> None:
    callback = AsyncMock()
    service = receiver(
        SimpleNamespace()
        if missing == "callback"
        else SimpleNamespace(persist_asr_capture=callback)
    )
    if missing == "callback":
        service._speech_envelope = speech_envelope(service)  # pyright: ignore[reportPrivateUsage]
    capture = capture_writer.new_capture()
    capture.event_buffer().append({"header": {"event": "task-finished"}})
    assert capture_writer.budget.items == 1
    await service._on_capture(uuid4(), capture, envelope=service._speech_envelope)  # pyright: ignore[reportPrivateUsage]
    callback.assert_not_awaited()
    assert capture.reservation is None and capture.buffers == []
    assert capture_writer.budget.items == capture_writer.budget.retained_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["normal", "error", "cancel", "transferred"])
async def test_receiver_callback_finally_releases_only_untransferred_reservation(
    capture_writer: ProviderCaptureWriter, outcome: str
) -> None:
    service = receiver(SimpleNamespace())
    envelope = speech_envelope(service)
    service._speech_envelope = envelope  # pyright: ignore[reportPrivateUsage]
    capture = capture_writer.new_capture()
    capture.capture_request(transport="WSS", url="wss://synthetic.invalid", body={"qa": True})
    retained = capture_writer.budget.retained_bytes
    submission = None

    async def callback(**kwargs: Any) -> None:
        nonlocal submission
        assert kwargs["envelope"] == envelope and kwargs["provider_capture"] is capture
        if outcome == "error":
            raise RuntimeError("synthetic-callback-error")
        if outcome == "cancel":
            raise asyncio.CancelledError
        if outcome == "transferred":
            call = ExternalCall(id=uuid4(), call_kind="ASR", status="SUCCEEDED")
            submission = prepare_capture(capture_writer, call, capture)
            assert submission is not None
            assert submit_capture(capture_writer, submission)

    service._callbacks = cast(Any, SimpleNamespace(persist_asr_capture=callback))  # pyright: ignore[reportPrivateUsage]
    assert retained > 0
    if outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await service._on_capture(uuid4(), capture, envelope=envelope)  # pyright: ignore[reportPrivateUsage]
    else:
        await service._on_capture(uuid4(), capture, envelope=envelope)  # pyright: ignore[reportPrivateUsage]
    assert capture.reservation is None and capture.request is capture.response is None
    if outcome == "transferred":
        assert submission is not None and submission.owned and not submission.released
        assert capture_writer.budget.items == 1

        async def wait() -> None:
            while capture_writer.budget.items:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(wait(), 0.2)
        assert submission.released
    assert capture_writer.budget.items == capture_writer.budget.retained_bytes == 0

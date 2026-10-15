"""Actual native compression ownership and payload release without a database."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

import jx_core.data_capture.provider_writer as module
from jx_core.data_capture.provider import (
    ProviderCallCapture,
    capture_provider_payload,
    unavailable_provider_payload,
)
from jx_core.data_capture.provider_writer import CaptureSubmission, ProviderCaptureWriter
from jx_core.models import ExternalCall

# Private task/future ownership is the behavior under test.
# pyright: reportPrivateUsage=false

URL = "postgresql+psycopg://jx:change-me@127.0.0.1:5432/jx_debate_qa_agent_faults"


def prepared_capture(writer: ProviderCaptureWriter) -> CaptureSubmission:
    capture = writer.new_capture()
    capture.capture_request(
        transport="http", url="https://synthetic.invalid", body={"text": "a" * 1_900_000}
    )
    item = writer.prepare(
        ExternalCall(
            id=uuid4(),
            call_kind="LLM_SPEECH",
            provider="SYNTHETIC",
            operation="test",
            attempt_no=1,
            status="SUCCEEDED",
        ),
        capture,
    )
    assert item is not None and item.request.encoded is not None
    return item


async def wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(1.2):
        while not predicate():
            await asyncio.sleep(0.005)


class NoopContext:
    def __init__(self, value: Any = None) -> None:
        self.value = value

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, *_args: Any) -> None:
        return None


class NoopConnection:
    async def get_raw_connection(self) -> Any:
        return SimpleNamespace(driver_connection=object())


class NoopSession(NoopContext):
    def __init__(self, **_kwargs: Any) -> None:
        super().__init__(self)

    def begin(self) -> NoopContext:
        return NoopContext()

    def begin_nested(self) -> NoopContext:
        return NoopContext()


async def test_small_complete_and_gap_snapshots_prepare_during_actual_native_compression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = ProviderCaptureWriter(URL)
    entered, release = threading.Event(), threading.Event()
    original_compress = module._compress_batch

    def held_compression(batch: list[CaptureSubmission]) -> Any:
        entered.set()
        assert release.wait(3), "owned native test gate must be released"
        return original_compress(batch)

    def no_database_connect(_engine: Any) -> NoopContext:
        return NoopContext(NoopConnection())

    async def no_storage(*_args: Any) -> None:
        return None

    monkeypatch.setattr(module, "_compress_batch", held_compression)
    monkeypatch.setattr(type(writer._engine), "connect", no_database_connect)
    monkeypatch.setattr(module, "AsyncSession", NoopSession)
    monkeypatch.setattr(writer, "_persist_item", no_storage)
    await writer.start()
    large = prepared_capture(writer)
    small: CaptureSubmission | None = None
    try:
        assert writer.submit(large)
        await wait_until(entered.is_set)
        assert writer.stats()["workspace_bytes"] > 1024 * 1024
        assert writer._compression_future is not None and not writer._compression_future.done()
        # An unencoded legacy-compatible complete payload plus a known tiny
        # gap needs little space, even while the one fixed compressor owns a
        # large batch. Neither may reserve the maximum 7 MiB unnecessarily.
        capture = ProviderCallCapture(
            request=capture_provider_payload(
                transport="http", url="https://synthetic.invalid", body={"text": "tiny"}
            ),
            response=unavailable_provider_payload("provider_response_unavailable"),
        )
        small = writer.prepare(
            ExternalCall(
                id=uuid4(),
                call_kind="LLM_SPEECH",
                provider="SYNTHETIC",
                operation="tiny-during-native",
                attempt_no=1,
                status="SUCCEEDED",
            ),
            capture,
        )
        assert small is not None
        assert small.request.status == "COMPLETE" and small.request.encoded is not None
        assert small.request.payload["body"] == {"text": "tiny"}
        assert small.response.status == "UNAVAILABLE" and small.response.encoded is not None
        assert small.response.payload["capture"]["error_code"] == "provider_response_unavailable"
        assert writer.submit(small)
        release.set()
        await wait_until(lambda: writer.stats()["items"] == 0)
        assert writer.stats()["retained_bytes"] == writer.stats()["workspace_bytes"] == 0
    finally:
        release.set()
        if small is not None:
            small.close()
        await writer.close()


async def test_completed_batch_drops_raw_compressed_future_and_worker_batch_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = ProviderCaptureWriter(URL)

    # Real fixed executor/compression and lifecycle; only the DB boundary is a
    # no-op context. No socket is opened and no Provider request is performed.
    def no_database_connect(_engine: Any) -> NoopContext:
        return NoopContext(NoopConnection())

    monkeypatch.setattr(type(writer._engine), "connect", no_database_connect)
    monkeypatch.setattr(module, "AsyncSession", NoopSession)

    async def no_storage(*_args: Any) -> None:
        return None

    monkeypatch.setattr(writer, "_persist_item", no_storage)
    await writer.start()
    item = prepared_capture(writer)
    before = (
        item.request.status,
        item.request.original_bytes,
        item.request.stored_bytes,
        item.request.sha256,
    )
    try:
        assert writer.submit(item)
        await wait_until(lambda: writer.stats()["items"] == 0)
        assert item.request.encoded is item.response.encoded is None
        assert item.request._payload is item.response._payload is None
        assert (
            item.request.status,
            item.request.original_bytes,
            item.request.stored_bytes,
            item.request.sha256,
        ) == before
        assert writer._compression_future is None and writer._attempt is None
        assert writer._worker is not None and not writer._worker.done()
        assert writer._worker.get_stack()[0].f_locals["batch"] == []
        assert writer.stats()["retained_bytes"] == writer.stats()["workspace_bytes"] == 0
    finally:
        await writer.close()


async def test_cancelled_native_compression_keeps_leases_until_actual_thread_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = ProviderCaptureWriter(URL)
    entered, release = threading.Event(), threading.Event()
    original_compress = module._compress_batch
    seen_batch: list[CaptureSubmission] | None = None

    def held_compression(batch: list[CaptureSubmission]) -> Any:
        nonlocal seen_batch
        seen_batch = batch
        entered.set()
        assert release.wait(2), "owned native test gate must be released"
        return original_compress(batch)

    def forbidden_connect(*_args: Any) -> Any:
        raise AssertionError("cancelled native compression must never connect to PostgreSQL")

    monkeypatch.setattr(module, "_compress_batch", held_compression)
    monkeypatch.setattr(module, "BATCH_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(type(writer._engine), "connect", forbidden_connect)
    await writer.start()
    item = prepared_capture(writer)
    try:
        assert writer.submit(item)
        await wait_until(entered.is_set)
        await wait_until(lambda: bool(writer.stats()["quarantined"]))
        await wait_until(lambda: writer._attempt is None)
        native = writer._compression_future
        assert native is not None and not native.done()
        assert item.request.encoded is not None and not item.released
        assert writer.stats()["items"] == 1 and writer.stats()["retained_bytes"] > 0
        assert writer.stats()["workspace_bytes"] > 0
        assert seen_batch is not None and len(seen_batch) == 1
        assert writer._worker is not None
        assert writer._worker.get_stack() == []  # quarantined worker already retired
        release.set()
        await wait_until(lambda: writer.stats()["items"] == 0)
        assert writer._compression_future is None and writer._attempt is None
        assert item.request.encoded is item.response.encoded is None
        assert item.request._payload is item.response._payload is None
        assert len(seen_batch) == 1  # worker did not clear the native thread's list
        assert writer.stats()["retained_bytes"] == writer.stats()["workspace_bytes"] == 0
    finally:
        release.set()
        await writer.close()

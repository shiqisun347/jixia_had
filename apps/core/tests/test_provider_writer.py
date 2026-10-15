"""Reservations, bounded ownership and isolation of provider research capture."""

from __future__ import annotations

import asyncio
import json
from dataclasses import fields
from uuid import uuid4

import jx_core.data_capture.provider_writer as module
from jx_core.data_capture.provider import MAX_CAPTURE_BYTES, ProviderCallCapture
from jx_core.data_capture.provider_budget import RETAINED_BYTES, WORKSPACE_BYTES
from jx_core.data_capture.provider_writer import (
    CaptureIdentity,
    ProviderCaptureWriter,
    new_provider_capture,
    prepare_capture,
)
from jx_core.models import ExternalCall

URL = "postgresql+psycopg://jx:change-me@127.0.0.1:5432/jx_debate_qa_agent_faults"


def call(**kwargs) -> ExternalCall:
    return ExternalCall(
        id=uuid4(),
        call_kind="LLM_SPEECH",
        provider="SYNTHETIC",
        operation="test",
        model="synthetic",
        attempt_no=1,
        status="SUCCEEDED",
        **kwargs,
    )


def captured(writer: ProviderCaptureWriter) -> ProviderCallCapture:
    result = writer.new_capture()
    result.capture_request(transport="http", url="https://synthetic.invalid", body={"text": "甲"})
    result.capture_response(transport="http", url="https://synthetic.invalid", body={"text": "乙"})
    return result


async def wait_empty(writer: ProviderCaptureWriter) -> None:
    async with asyncio.timeout(1):
        while writer.stats()["items"]:
            await asyncio.sleep(0.005)


async def test_snapshots_own_only_finite_bytes_and_scalar_historical_identity() -> None:
    writer = ProviderCaptureWriter(URL)
    try:
        capture = captured(writer)
        row = call(match_id=uuid4(), context_version=3)
        item = writer.prepare(row, capture)
        assert item is not None
        assert isinstance(item.identity, CaptureIdentity)
        assert item.identity.external_call_id == row.id
        assert item.request.encoded and item.response.encoded
        assert item.request._payload is None and item.response._payload is None
        assert "context_version" in module.IDENTITY_FIELDS
        assert all(
            not isinstance(getattr(item, field.name), ExternalCall) for field in fields(item)
        )
        assert capture.request is None and capture.response is None
        capture.close()
        assert writer.stats()["items"] == 1
        item.close()  # rollback: no submit
        assert writer.stats()["items"] == 0
        assert row.capture_storage_mode == "ASYNC_V1"
    finally:
        await writer.close()


async def test_reserve_before_encoding_and_64_constructing_capture_slots(monkeypatch) -> None:
    writer = ProviderCaptureWriter(URL)
    captures = [writer.new_capture() for _ in range(64)]
    try:
        overflow = writer.new_capture()
        assert not overflow.can_capture

        def forbidden(**_kwargs):
            raise AssertionError("encoding must not run without a reservation")

        monkeypatch.setattr("jx_core.data_capture.provider.capture_provider_payload", forbidden)
        overflow.capture_request(
            transport="http", url="https://synthetic.invalid", body={"huge": "x"}
        )
        assert overflow.request.status == "UNAVAILABLE"
        assert writer.stats()["items"] == 64
        assert writer.stats()["retained_bytes"] <= RETAINED_BYTES
        overflow.close()
    finally:
        for capture in captures:
            capture.close()
        await writer.close()


async def test_adapter_buffers_release_original_objects_and_reject_sensitive_events() -> None:
    writer = ProviderCaptureWriter(URL)
    try:
        capture = writer.new_capture()
        events = capture.event_buffer()
        event = {"header": {"event": "task-finished"}, "payload": {"text": "完整数据"}}
        events.append(event)
        event["payload"]["text"] = "mutation after capture"
        capture.capture_response(transport="ws", url="wss://synthetic.invalid", events=events)
        assert capture.response.payload["events"][0]["payload"]["text"] == "完整数据"
        assert not events
        assert capture.response.encoded is not None
        sensitive = capture.event_buffer()
        sensitive.append({"api_key": "synthetic-key"})
        capture.capture_request(transport="ws", url="wss://synthetic.invalid", events=sensitive)
        assert capture.request.status == "REJECTED_SENSITIVE"
        assert b"synthetic-key" not in capture.request.encoded if capture.request.encoded else True
        capture.close()
        assert writer.stats()["items"] == 0
    finally:
        await writer.close()


async def test_shared_workspace_is_separate_and_capture_rejection_does_not_wait() -> None:
    writer = ProviderCaptureWriter(URL)
    try:
        capture = writer.new_capture()
        assert writer.budget.workspace(WORKSPACE_BYTES)
        capture.capture_request(
            transport="http", url="https://synthetic.invalid", body={"text": "test"}
        )
        assert capture.request.status == "UNAVAILABLE"
        assert writer.stats()["high_water_bytes"] <= RETAINED_BYTES + WORKSPACE_BYTES
        writer.budget.release_workspace(WORKSPACE_BYTES)
        capture.close()
    finally:
        await writer.close()


async def test_missing_writer_is_explicit_gap_without_synchronous_fallback() -> None:
    capture = new_provider_capture(None)
    assert not capture.can_capture
    capture.capture_request(
        transport="http", url="https://synthetic.invalid", body={"text": "test"}
    )
    row = call()
    assert prepare_capture(None, row, capture) is None
    assert row.capture_storage_mode == "ASYNC_V1"
    assert capture.request is None


async def test_diagnostic_retry_once_and_shutdown_is_idempotent(monkeypatch) -> None:
    writer = ProviderCaptureWriter(URL)
    attempts = 0

    async def fail(_batch):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("synthetic")

    monkeypatch.setattr(writer, "_persist_batch", fail)
    await writer.start()
    item = writer.prepare(call(), captured(writer))
    assert writer.submit(item)
    item.close()  # producer finally cannot release transferred bytes
    await wait_empty(writer)
    assert attempts == 2 and writer.stats()["persist_failed"] == 1
    await writer.close()
    await writer.close()
    rejected = writer.new_capture()
    assert not rejected.can_capture
    assert not writer.submit(writer.prepare(call(), rejected))


async def test_twenty_late_offers_do_not_replace_cancellation_resistant_writer(monkeypatch) -> None:
    writer = ProviderCaptureWriter(URL)
    entered = asyncio.Event()
    release = asyncio.Event()
    spawned = 0

    async def ignore_cancel(_batch):
        nonlocal spawned
        spawned += 1
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    monkeypatch.setattr(writer, "_persist_batch", ignore_cancel)
    monkeypatch.setattr(module, "BATCH_TIMEOUT_SECONDS", 0.03)
    await writer.start()
    assert writer.submit(writer.prepare(call(), captured(writer)))
    await entered.wait()
    await asyncio.sleep(0.04)
    assert writer.stats()["quarantined"]
    retained = writer.stats()["retained_bytes"]
    assert retained > 0  # ignored cancellation still owns its bytes
    for _ in range(20):
        capture = captured(writer)
        assert not writer.submit(writer.prepare(call(), capture))
    assert spawned == 1 and writer.stats()["items"] == 1
    assert writer.stats()["retained_bytes"] == retained
    release.set()
    await wait_empty(writer)
    await writer.close()


async def test_duplicate_submission_merges_and_keeps_first_complete_snapshot(monkeypatch) -> None:
    writer = ProviderCaptureWriter(URL)
    persisted = []

    async def save(batch):
        persisted.extend(batch)

    monkeypatch.setattr(writer, "_persist_batch", save)
    await writer.start()
    row = call()
    first = writer.prepare(row, captured(writer))
    duplicate = writer.prepare(row, captured(writer))
    assert writer.submit(first)
    assert not writer.submit(duplicate)
    await wait_empty(writer)
    assert len(persisted) == 1 and persisted[0] is first
    await writer.close()


async def test_full_sensitive_scan_in_tail_and_bounded_wrapper() -> None:
    writer = ProviderCaptureWriter(URL)
    try:
        capture = writer.new_capture()
        capture.capture_response(
            transport="http",
            url="https://synthetic.invalid",
            body={"text": "甲" * MAX_CAPTURE_BYTES, "late": {"password": "never-store"}},
        )
        assert capture.response.status == "REJECTED_SENSITIVE"
        assert "never-store" not in json.dumps(capture.response.payload)
        capture.capture_response(
            transport="http",
            url="https://synthetic.invalid",
            body={"text": "\\" * (MAX_CAPTURE_BYTES * 2)},
        )
        assert capture.response.status == "TRUNCATED"
        assert len(capture.response.encoded) <= MAX_CAPTURE_BYTES
        capture.close()
        assert writer.stats()["retained_bytes"] == 0
    finally:
        await writer.close()


async def test_failed_call_can_evict_queued_success_without_growing_queue(monkeypatch) -> None:
    writer = ProviderCaptureWriter(URL)
    hold = asyncio.Event()

    async def delayed(batch):
        await hold.wait()

    monkeypatch.setattr(writer, "_persist_batch", delayed)
    await writer.start()
    for _ in range(64):
        assert writer.submit(writer.prepare(call(), captured(writer)))
    # All slots are reserved; construct only a bounded gap for this failed call.
    overflow = writer.new_capture()
    assert not overflow.can_capture
    failed = call()
    failed.status = "FAILED"
    item = writer.prepare(failed, overflow)
    assert item is not None and item.priority == 2
    assert writer.submit(item)
    assert writer.stats()["items"] == 64
    assert writer.stats()["evicted_success"] == 1
    assert writer.stats()["high_water_items"] <= 64
    hold.set()
    await wait_empty(writer)
    await writer.close()

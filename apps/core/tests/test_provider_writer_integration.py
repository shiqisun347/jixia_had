"""Actual writer connection, lock release and immutable historical identity proofs.

Synthetic captures only. Each fixture deletes its own rows in the strict local
Agent-fault database; it never connects to a Provider or loads dotenv settings.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import jx_core.data_capture.provider_writer as module
from jx_core.data_capture.content import load_content_blob, store_content_blob
from jx_core.data_capture.content_gc import collect_unreferenced_content_blobs
from jx_core.data_capture.provider_budget import METADATA_BYTES, WORKSPACE_BYTES
from jx_core.data_capture.provider_writer import CaptureSubmission, ProviderCaptureWriter
from jx_core.models import CallContentBlob, ExternalCall
from jx_core.models import ProviderCallCapture as CaptureRow
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)

pytestmark = pytest.mark.integration
# These probes inspect the writer's actual private resource ownership, not mocks.
# pyright: reportPrivateUsage=false

PreparedSides = tuple[tuple[bytes, str], tuple[bytes, str]]
OBSERVATION_SECONDS = 1.2


async def wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(OBSERVATION_SECONDS):
        while not predicate():
            await asyncio.sleep(0.01)


@dataclass
class WriterDb:
    factory: async_sessionmaker[AsyncSession]
    writer: ProviderCaptureWriter
    call_ids: list[UUID] = field(default_factory=lambda: list[UUID]())
    digests: set[str] = field(default_factory=lambda: set[str]())
    releases: list[asyncio.Event] = field(default_factory=lambda: list[asyncio.Event]())
    connections: int = 0

    async def prepare(self) -> CaptureSubmission:
        call_id = uuid4()
        self.call_ids.append(call_id)
        capture = self.writer.new_capture()
        capture.capture_request(
            transport="http",
            url="https://synthetic.invalid/request",
            body={"probe_id": str(call_id), "input": "synthetic"},
        )
        capture.capture_response(
            transport="http",
            url="https://synthetic.invalid/response",
            body={"probe_id": str(call_id), "output": "synthetic"},
        )
        try:
            async with self.factory() as session, session.begin():
                row = ExternalCall(
                    id=call_id,
                    call_kind="LLM_SPEECH",
                    provider="SYNTHETIC",
                    operation="synthetic",
                    model="synthetic-model",
                    attempt_no=1,
                    status="SUCCEEDED",
                    source_kind="OPS_PROBE",
                    started_at=datetime.now(UTC),
                    completed_at=datetime.now(UTC),
                    completion_tokens=2,
                )
                session.add(row)
                await session.flush()
                item = self.writer.prepare(row, capture)
                assert item is not None
            for side in (item.request, item.response):
                assert side.encoded is not None
                self.digests.add(hashlib.sha256(side.encoded).hexdigest())
            return item
        finally:
            capture.close()

    async def capture(self, call_id: UUID) -> CaptureRow | None:
        async with self.factory() as session:
            return await session.scalar(
                select(CaptureRow).where(CaptureRow.external_call_id == call_id)
            )

    async def lock_call(self, call_id: UUID) -> None:
        async with self.factory() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout = '100ms'"))
            assert (
                await session.scalar(
                    select(ExternalCall.id).where(ExternalCall.id == call_id).with_for_update()
                )
                == call_id
            )


@pytest.fixture
async def writer_db(agent_fault_database_url: str) -> AsyncGenerator[WriterDb, None]:
    # Reused guard also supplies Settings(_env_file=None) to the migration loader.
    engine = create_async_engine(
        agent_fault_database_url,
        connect_args={"options": "-c statement_timeout=1500 -c lock_timeout=100"},
    )
    writer = ProviderCaptureWriter(agent_fault_database_url)
    probe = WriterDb(async_sessionmaker(engine, expire_on_commit=False), writer)

    def connected(_connection: Any, _record: Any) -> None:
        probe.connections += 1

    event.listen(writer._engine.sync_engine, "connect", connected)
    await writer.start()
    try:
        yield probe
    finally:
        for release in probe.releases:
            release.set()
        await writer.close()
        attempt = writer._attempt
        if attempt is not None and not attempt.done():
            done, _ = await asyncio.wait({attempt}, timeout=OBSERVATION_SECONDS)
            assert attempt in done, "owned synthetic storage boundary must release during teardown"
        # Done callbacks release the quarantined bytes and consume late errors.
        await asyncio.sleep(0)
        try:
            async with probe.factory() as session, session.begin():
                candidates = set(
                    await session.scalars(
                        select(CallContentBlob.id).where(CallContentBlob.sha256.in_(probe.digests))
                    )
                )
                await session.execute(
                    delete(ExternalCall).where(ExternalCall.id.in_(probe.call_ids))
                )
                await collect_unreferenced_content_blobs(session, candidates)
        finally:
            await engine.dispose()


@pytest.mark.parametrize("boundary", ["blob", "capture_fk"])
async def test_deadline_finishes_actual_connection_releases_locks_and_keeps_retained_bytes(
    writer_db: WriterDb,
    monkeypatch: pytest.MonkeyPatch,
    boundary: Literal["blob", "capture_fk"],
) -> None:
    probe, writer = writer_db, writer_db.writer
    entered, release = asyncio.Event(), asyncio.Event()
    probe.releases.append(release)
    backend_pid: int | None = None
    swallowed_cancels = 0
    original_blob = module._store_encoded_blob
    original_item = writer._persist_item

    async def hold_actual_transaction(session: AsyncSession) -> None:
        nonlocal backend_pid, swallowed_cancels
        backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
        assert backend_pid is not None
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                swallowed_cancels += 1

    async def hold_blob(
        session: AsyncSession, kind: str, raw: bytes, prepared: tuple[bytes, str]
    ) -> UUID:
        blob_id = await original_blob(session, kind, raw, prepared)
        if kind == "REQUEST":
            await hold_actual_transaction(session)
        return blob_id

    async def hold_capture_fk(
        session: AsyncSession, item: CaptureSubmission, prepared: PreparedSides
    ) -> None:
        await original_item(session, item, prepared)
        await hold_actual_transaction(session)

    if boundary == "blob":
        monkeypatch.setattr(module, "_store_encoded_blob", hold_blob)
    else:
        monkeypatch.setattr(writer, "_persist_item", hold_capture_fk)

    item = await probe.prepare()
    assert writer.submit(item)
    async with asyncio.timeout(OBSERVATION_SECONDS):
        await entered.wait()
    old_task, old_worker, driver = writer._attempt, writer._worker, writer._driver
    assert old_task is not None and not old_task.done() and driver is not None
    compression = writer._compression_future
    assert compression is not None and compression.done()
    assert probe.connections == 1
    raw_size = sum(len(side.encoded or b"") for side in (item.request, item.response))
    expected_workspace = min(WORKSPACE_BYTES, max(raw_size * 2, raw_size + 512 * 1024))
    assert 0 < expected_workspace < WORKSPACE_BYTES
    assert writer.stats()["workspace_bytes"] == expected_workspace
    original_retained = writer.stats()["retained_bytes"]
    assert original_retained == raw_size + METADATA_BYTES
    assert writer.stats()["items"] == 1

    # Non-key authority updates must commit while the actual FK KEY SHARE is held.
    async with probe.factory() as authority, authority.begin():
        await authority.execute(text("SET LOCAL lock_timeout = '100ms'"))
        await authority.execute(
            update(ExternalCall)
            .where(ExternalCall.id == item.identity.external_call_id)
            .values(completion_tokens=3, completed_latency_ms=7)
        )

    assert item.request.encoded is not None
    synthetic_payload = json.loads(item.request.encoded)

    async def same_address_write() -> UUID:
        async with probe.factory() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout = '100ms'"))
            return await store_content_blob(
                session, content_kind="REQUEST", payload=synthetic_payload
            )

    # Prove a real conflicting lock exists before the connection is aborted.
    with pytest.raises(DBAPIError) as blocked:
        if boundary == "capture_fk":
            await probe.lock_call(item.identity.external_call_id)
        else:
            await same_address_write()
    assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"

    await wait_until(lambda: bool(writer.stats()["quarantined"]))
    assert driver.closed, "deadline must finish the actual psycopg connection"

    async with asyncio.timeout(OBSERVATION_SECONDS):
        while True:
            async with probe.factory() as observer:
                state = await observer.scalar(
                    text("SELECT state FROM pg_stat_activity WHERE pid = :pid"),
                    {"pid": backend_pid},
                )
            if state is None:
                break
            await asyncio.sleep(0.01)
    assert not old_task.done(), "Python task must still own bytes after its backend is gone"
    assert swallowed_cancels >= 1
    await probe.lock_call(item.identity.external_call_id)
    shared_blob = await same_address_write()
    async with probe.factory() as session:
        assert await load_content_blob(session, shared_blob) == synthetic_payload

    for _ in range(20):
        rejected = writer.new_capture()
        assert not rejected.can_capture
        async with probe.factory() as session:
            row = await session.get(ExternalCall, item.identity.external_call_id)
            assert row is not None
            assert writer.prepare(row, rejected) is None
        rejected.close()
    assert writer._attempt is old_task and writer._worker is old_worker
    assert writer._compression_future is compression
    assert probe.connections == 1
    assert writer.stats()["items"] == 1
    assert writer.stats()["retained_bytes"] == original_retained
    assert writer.stats()["workspace_bytes"] == expected_workspace

    clock = asyncio.get_running_loop()
    before_close = clock.time()
    await writer.close()
    await writer.close()
    assert clock.time() - before_close <= module.SHUTDOWN_SECONDS + 0.2
    assert not old_task.done() and writer.stats()["items"] == 1
    assert writer.stats()["workspace_bytes"] == expected_workspace

    # Releasing the stale Python frame must not commit capture on the dead socket
    # or create an unhandled late task exception.
    unhandled: list[str] = []
    previous_handler = clock.get_exception_handler()

    def late_exception(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        unhandled.append(type(context.get("exception")).__name__)

    clock.set_exception_handler(late_exception)
    try:
        release.set()
        done, _ = await asyncio.wait({old_task}, timeout=OBSERVATION_SECONDS)
        assert old_task in done
        await wait_until(lambda: writer.stats()["items"] == 0)
        await asyncio.sleep(0)
        assert not unhandled
        assert writer.stats()["retained_bytes"] == writer.stats()["workspace_bytes"] == 0
        assert await probe.capture(item.identity.external_call_id) is None
        async with probe.factory() as session:
            row = await session.get(ExternalCall, item.identity.external_call_id)
            assert row is not None
            assert row.status == "SUCCEEDED" and row.completion_tokens == 3
            assert row.completed_latency_ms == 7 and row.capture_storage_mode == "ASYNC_V1"
    finally:
        clock.set_exception_handler(previous_handler)


IDENTITY_MUTATIONS: list[tuple[str, str | int | UUID]] = [
    ("call_kind", "LLM_DECISION"),
    ("provider", "SYNTHETIC_CHANGED"),
    ("operation", "changed"),
    ("model", "changed-model"),
    ("voice", "changed-voice"),
    ("attempt_no", 2),
    ("generation_id", uuid4()),
    ("decision_round_id", uuid4()),
    ("connection_epoch", 2),
    ("context_version", 2),
    ("source_kind", "CONFIG_TEST"),
    ("source_resource_id", "changed-resource"),
    ("logical_call_id", uuid4()),
]


@pytest.mark.parametrize(
    ("field_name", "changed"), [("model", "changed-mid-write"), ("context_version", 3)]
)
async def test_mid_write_identity_change_rejects_capture_without_orphans_or_losing_valid_batch_item(
    writer_db: WriterDb,
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    changed: str | int,
) -> None:
    probe, writer = writer_db, writer_db.writer
    entered, release = asyncio.Event(), asyncio.Event()
    probe.releases.append(release)
    rejected_item, valid_item = await probe.prepare(), await probe.prepare()
    expected_valid_request = valid_item.request.payload
    expected_valid_response = valid_item.response.payload
    rejected_digests = {
        hashlib.sha256(side.encoded or b"").hexdigest()
        for side in (rejected_item.request, rejected_item.response)
    }
    stored_rejected_ids: list[UUID] = []
    original_blob = module._store_encoded_blob

    async def pause_first_rejected_blob(
        session: AsyncSession, kind: str, raw: bytes, prepared: tuple[bytes, str]
    ) -> UUID:
        blob_id = await original_blob(session, kind, raw, prepared)
        if prepared[1] in rejected_digests:
            stored_rejected_ids.append(blob_id)
            if kind == "REQUEST":
                entered.set()
                await release.wait()
        return blob_id

    monkeypatch.setattr(module, "_store_encoded_blob", pause_first_rejected_blob)
    # Both queued before yielding, so the stale and valid items share one actual
    # writer transaction. Stale savepoint rollback must not lose the valid one.
    assert writer.submit(rejected_item) and writer.submit(valid_item)
    async with asyncio.timeout(OBSERVATION_SECONDS):
        await entered.wait()
    async with probe.factory() as authority, authority.begin():
        await authority.execute(text("SET LOCAL lock_timeout = '100ms'"))
        await authority.execute(
            update(ExternalCall)
            .where(ExternalCall.id == rejected_item.identity.external_call_id)
            .values({field_name: changed})
        )
    release.set()
    await wait_until(lambda: writer.stats()["items"] == 0)
    assert writer.stats()["stale"] == 1
    assert writer.stats().get("retry", 0) == 0
    assert len(stored_rejected_ids) == 2, "final fence must reject after both raw sides exist"
    assert await probe.capture(rejected_item.identity.external_call_id) is None
    valid_capture = await probe.capture(valid_item.identity.external_call_id)
    assert valid_capture is not None and valid_capture.request_capture_status == "COMPLETE"
    assert valid_capture.response_capture_status == "COMPLETE"
    async with probe.factory() as observer:
        assert (
            await observer.scalar(
                select(func.count())
                .select_from(CallContentBlob)
                .where(CallContentBlob.sha256.in_(rejected_digests))
            )
            == 0
        )
        for blob_id in stored_rejected_ids:
            assert await observer.get(CallContentBlob, blob_id) is None
        row = await observer.get(ExternalCall, rejected_item.identity.external_call_id)
        assert row is not None and getattr(row, field_name) == changed
        assert row.status == "SUCCEEDED" and row.completion_tokens == 2
        assert (
            valid_capture.request_blob_id is not None and valid_capture.response_blob_id is not None
        )
        assert (
            await load_content_blob(observer, valid_capture.request_blob_id)
            == expected_valid_request
        )
        assert (
            await load_content_blob(observer, valid_capture.response_blob_id)
            == expected_valid_response
        )
    assert valid_item.request.encoded is valid_item.response.encoded is None


@pytest.mark.parametrize(("field_name", "changed"), IDENTITY_MUTATIONS)
async def test_actual_historical_scalar_identity_change_rejects_accepted_capture(
    writer_db: WriterDb, field_name: str, changed: str | int | UUID
) -> None:
    probe, writer = writer_db, writer_db.writer
    item = await probe.prepare()
    async with probe.factory() as session, session.begin():
        await session.execute(
            update(ExternalCall)
            .where(ExternalCall.id == item.identity.external_call_id)
            .values({field_name: changed})
        )
    assert writer.submit(item)
    await wait_until(lambda: writer.stats()["items"] == 0)
    assert writer.stats()["stale"] == 1
    assert await probe.capture(item.identity.external_call_id) is None
    async with probe.factory() as session:
        row = await session.get(ExternalCall, item.identity.external_call_id)
        assert row is not None and getattr(row, field_name) == changed
        assert row.status == "SUCCEEDED" and row.completion_tokens == 2


async def test_actual_call_deletion_does_not_resurrect_queued_capture(writer_db: WriterDb) -> None:
    probe, writer = writer_db, writer_db.writer
    item = await probe.prepare()
    async with probe.factory() as session, session.begin():
        await session.execute(
            delete(ExternalCall).where(ExternalCall.id == item.identity.external_call_id)
        )
    assert writer.submit(item)
    await wait_until(lambda: writer.stats()["items"] == 0)
    assert writer.stats()["stale"] == 1
    assert await probe.capture(item.identity.external_call_id) is None
    async with probe.factory() as session:
        assert await session.get(ExternalCall, item.identity.external_call_id) is None


async def test_mutable_result_changes_do_not_invalidate_historical_capture(
    writer_db: WriterDb,
) -> None:
    probe, writer = writer_db, writer_db.writer
    item = await probe.prepare()
    async with probe.factory() as session, session.begin():
        await session.execute(
            update(ExternalCall)
            .where(ExternalCall.id == item.identity.external_call_id)
            .values(
                status="FAILED",
                error_code="synthetic-failure",
                completion_tokens=9,
                completed_at=datetime.now(UTC),
            )
        )
    assert writer.submit(item)
    await wait_until(lambda: writer.stats()["items"] == 0)
    capture = await probe.capture(item.identity.external_call_id)
    assert capture is not None and capture.request_capture_status == "COMPLETE"
    async with probe.factory() as session:
        row = await session.get(ExternalCall, item.identity.external_call_id)
        assert row is not None and row.status == "FAILED"
        assert row.completion_tokens == 9 and row.error_code == "synthetic-failure"


async def test_actual_unknown_commit_retry_is_idempotent_and_preserves_complete_capture(
    writer_db: WriterDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe, writer = writer_db, writer_db.writer
    original_batch = writer._persist_batch
    attempts = 0
    first_capture: CaptureRow | None = None

    async def lose_first_commit_ack(batch: list[CaptureSubmission]) -> None:
        nonlocal attempts, first_capture
        attempts += 1
        await original_batch(batch)
        if attempts == 1:
            first_capture = await probe.capture(batch[0].identity.external_call_id)
            assert first_capture is not None
            raise RuntimeError("synthetic_commit_ack_lost")

    monkeypatch.setattr(writer, "_persist_batch", lose_first_commit_ack)
    item = await probe.prepare()
    expected_request, expected_response = item.request.payload, item.response.payload
    assert writer.submit(item)
    await wait_until(lambda: writer.stats()["items"] == 0)
    assert attempts == 2 and writer.stats()["retry"] == 1
    assert writer.stats()["duplicate"] == 1 and first_capture is not None
    capture = await probe.capture(item.identity.external_call_id)
    assert capture is not None
    assert capture.id == first_capture.id and capture.revision == 1
    assert capture.request_blob_id == first_capture.request_blob_id
    assert capture.response_blob_id == first_capture.response_blob_id
    assert capture.request_sha256 == item.request.sha256
    assert capture.response_sha256 == item.response.sha256
    assert capture.request_blob_id is not None and capture.response_blob_id is not None
    async with probe.factory() as session:
        assert await load_content_blob(session, capture.request_blob_id) == expected_request
        assert await load_content_blob(session, capture.response_blob_id) == expected_response
    assert item.request.encoded is item.response.encoded is None

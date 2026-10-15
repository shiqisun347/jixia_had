"""Isolated, process-shared persistence of immutable optional provider captures.

No authoritative ORM/session/run object crosses the submission boundary. The
writer owns its single connection and compression thread; a missed deadline
quarantines it instead of spawning replacement tasks or borrowing Core locks.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import zlib
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

from ..models import CallContentBlob, CallContentBlobChunk, ExternalCall
from ..models import ProviderCallCapture as CaptureRow
from .content import CONTENT_CHUNK_BYTES, SERIALIZATION_VERSION
from .provider import (
    PROVIDER_CAPTURE_VERSION,
    CapturedProviderPayload,
    ProviderCallCapture,
    freeze_provider_payload,
    reserve_payload_encoding,
    unavailable_provider_payload,
)
from .provider_budget import (
    METADATA_BYTES,
    WORKSPACE_BYTES,
    CaptureBudget,
    Reservation,
)

BATCH_ITEMS = 8
BATCH_BYTES = 4 * 1024 * 1024 + 64 * 1024
BATCH_TIMEOUT_SECONDS = 1.0
SHUTDOWN_SECONDS = 3.0
FLUSH_SECONDS = 0.05
RETRY_SECONDS = 0.05
logger = logging.getLogger("jx-core.provider-capture")
# Association values are historical call identity, never the mutable Actor state,
# task lease or result status. Nullable identities must match too.
IDENTITY_FIELDS = (
    "call_kind",
    "provider",
    "operation",
    "model",
    "voice",
    "attempt_no",
    "match_id",
    "speech_id",
    "agent_generation_id",
    "agent_decision_id",
    "asr_segment_id",
    "judge_result_id",
    "generation_id",
    "decision_round_id",
    "opportunity_id",
    "connection_epoch",
    "context_version",
    "source_kind",
    "source_resource_id",
    "logical_call_id",
)


@dataclass(frozen=True, slots=True)
class CaptureIdentity:
    external_call_id: UUID
    associations: tuple[str | int | UUID | None, ...]


@dataclass(slots=True)
class CaptureSubmission:
    identity: CaptureIdentity
    request: CapturedProviderPayload
    response: CapturedProviderPayload
    captured_at: datetime
    provider_request_id: str | None
    error_code: str | None
    revision: int
    priority: int
    reservation: Reservation = field(repr=False)
    owned: bool = field(default=False, repr=False)
    released: bool = field(default=False, repr=False)

    @property
    def size(self) -> int:
        return self.reservation.size

    def close(self) -> None:
        if not self.owned:
            self.release()

    def release(self) -> None:
        if not self.released:
            self.released = True
            self.request = replace(self.request, _payload=None, encoded=None)
            self.response = replace(self.response, _payload=None, encoded=None)
            self.reservation.release()


class _StaleCapture(Exception):
    pass


class ProviderCaptureWriter:
    def __init__(self, database_url: str, *, service: str = "jx-core") -> None:
        self.service = service
        self.budget = CaptureBudget()
        self._engine = create_async_engine(
            database_url,
            pool_size=1,
            max_overflow=0,
            pool_timeout=1,
            connect_args={
                "connect_timeout": 1,
                "options": (
                    "-c statement_timeout=750 -c lock_timeout=100 "
                    "-c idle_in_transaction_session_timeout=1000"
                ),
            },
        )
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="provider-capture")
        self._queue: deque[CaptureSubmission] = deque()
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._attempt: asyncio.Task[None] | None = None
        self._compression_future: asyncio.Future[Any] | None = None
        self._connection: AsyncConnection | None = None
        self._driver: Any = None
        self._quarantined = False
        self._closing = False
        self._closed = False
        self._counters: Counter[str] = Counter()
        self._last_report = 0.0

    async def start(self) -> None:
        if self._worker is None and not self._closing:
            self._worker = asyncio.create_task(self._run(), name="provider-capture-writer")

    def new_capture(self) -> ProviderCallCapture:
        reservation = self.budget.reserve(METADATA_BYTES)
        if reservation is None:
            self._count("capture_capacity_exhausted")
        return ProviderCallCapture(budget=self.budget, reservation=reservation, budgeted=True)

    def prepare(self, call: ExternalCall, capture: ProviderCallCapture) -> CaptureSubmission | None:
        """Called only inside the producer's already-validated result transaction."""
        call.capture_storage_mode = "ASYNC_V1"
        if self._closing or self._quarantined:
            capture.close()
            self._count("writer_unavailable")
            return None
        reservation = capture.reservation
        request = capture.request or unavailable_provider_payload("provider_request_unavailable")
        response = capture.response or unavailable_provider_payload("provider_response_unavailable")
        # Budgeted adapters have encoded their payloads already. Compatibility
        # callers must reserve BEFORE any encoding, never build then reject.
        if reservation is None:
            # A missing reservation is already a bounded gap. Failed/cancelled
            # calls may evict queued success snapshots before taking a slot.
            size = METADATA_BYTES if capture.budgeted else 4 * 1024 * 1024
            reservation = self.budget.reserve(size)
            if call.status in {"FAILED", "CANCELLED"}:
                while reservation is None and self._evict_success():
                    reservation = self.budget.reserve(size)
        if reservation is None:
            capture.close()
            self._count("dropped")
            return None
        try:
            for side, payload in (("request", request), ("response", response)):
                if payload.encoded is not None:
                    continue
                workspace = reserve_payload_encoding(self.budget, payload)
                if workspace is None:
                    self._count("capture_workspace_exhausted")
                    return None
                try:
                    frozen = freeze_provider_payload(payload, workspace_bytes=workspace)
                finally:
                    self.budget.release_workspace(workspace)
                if side == "request":
                    request = frozen
                else:
                    response = frozen
            size = len(request.encoded or b"") + len(response.encoded or b"") + METADATA_BYTES
            if size > BATCH_BYTES or not reservation.resize(size):
                self._count("dropped")
                return None
            identity = CaptureIdentity(
                call.id,
                tuple(
                    cast(str | int | UUID | None, getattr(call, name)) for name in IDENTITY_FIELDS
                ),
            )
            submission = CaptureSubmission(
                identity,
                request,
                response,
                datetime.now(UTC),
                _bounded_scalar(capture.provider_request_id, 255),
                _bounded_scalar(capture.error_code, 100),
                1,
                2 if call.status in {"FAILED", "CANCELLED"} else 1,
                reservation,
            )
            capture.reservation = None
            return submission
        except (TypeError, ValueError, RecursionError):
            self._count("dropped")
            return None
        finally:
            # Transfer succeeded iff capture no longer owns its original lease.
            if capture.reservation is reservation:
                capture.reservation = None
                reservation.release()
            elif reservation is not capture.reservation and "submission" not in locals():
                reservation.release()
            capture.close()

    def submit(self, submission: CaptureSubmission | None) -> bool:
        """Nonblocking offer AFTER successful authority commit, transferring ownership."""
        if submission is None or submission.released or submission.owned:
            return False
        if self._closing or self._quarantined or self._worker is None:
            submission.close()
            self._count("writer_unavailable")
            return False
        key = (submission.identity.external_call_id, submission.revision)
        for queued in self._queue:
            if (queued.identity.external_call_id, queued.revision) == key:
                submission.close()
                self._count("duplicate")
                return False
        submission.owned = True
        self._queue.append(submission)
        self._count("accepted")
        self._wake.set()
        return True

    def _evict_success(self) -> bool:
        for item in self._queue:
            if item.priority < 2:
                self._queue.remove(item)
                item.release()
                self._count("evicted_success")
                return True
        return False

    def discard(self, submission: CaptureSubmission | None) -> None:
        if submission is not None:
            submission.close()

    def stats(self) -> dict[str, int | bool]:
        return {
            **self._counters,
            "items": self.budget.items,
            "retained_bytes": self.budget.retained_bytes,
            "workspace_bytes": self.budget.workspace_bytes,
            "high_water_bytes": self.budget.high_water_bytes,
            "high_water_items": self.budget.high_water_items,
            "queued": len(self._queue),
            "quarantined": self._quarantined,
        }

    def _count(self, reason: str, count: int = 1) -> None:
        self._counters[reason] += count
        # Finite reason vocabulary; report aggregates at most once/minute.
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            return
        if now - self._last_report >= 60:
            self._last_report = now
            logger.info(
                "provider capture watermarks",
                extra={
                    "service": self.service,
                    "capture_counters": dict(self._counters),
                    "capture_retained_bytes": self.budget.retained_bytes,
                    "capture_workspace_bytes": self.budget.workspace_bytes,
                },
            )

    async def _run(self) -> None:
        while not self._quarantined:
            if not self._queue:
                if self._closing:
                    return
                self._wake.clear()
                await self._wake.wait()
                continue
            if not self._closing:
                await asyncio.sleep(FLUSH_SECONDS)
            batch: list[CaptureSubmission] = []
            size = 0
            while self._queue and len(batch) < BATCH_ITEMS:
                item = self._queue[0]
                if batch and size + item.size > BATCH_BYTES:
                    break
                batch.append(self._queue.popleft())
                size += item.size
            try:
                for attempt_no in (1, 2):
                    outcome = await self._run_attempt(batch)
                    if outcome == "success" or outcome == "quarantined":
                        break
                    if attempt_no == 1:
                        self._count("retry")
                        await asyncio.sleep(RETRY_SECONDS)
                else:
                    self._count("persist_failed", len(batch))
            finally:
                if not self._quarantined:
                    for item in batch:
                        item.release()
                # Quarantine/native callbacks may still own the original list.
                # Drop this worker's reference; never mutate their shared batch.
                batch = []
            self._wake.set()
        self._drop_queue("writer_unavailable")

    async def _run_attempt(self, batch: list[CaptureSubmission]) -> str:
        task = asyncio.create_task(self._persist_batch(batch), name="provider-capture-batch")
        self._attempt = task
        try:
            done, _ = await asyncio.wait({task}, timeout=BATCH_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            task.cancel()
            self._abort_connection()
            self._quarantine(task, batch)
            raise
        if not done:
            task.cancel()
            self._abort_connection()
            # A timeout cannot safely put a possibly-live connection back into
            # service. Quarantine once even if the task later accepts cancel.
            self._quarantine(task, batch)
            return "quarantined"
        self._attempt = None
        try:
            task.result()
            return "success"
        except Exception:
            _consume(task)
            self._count("batch_failed")
            return "failed"
        except asyncio.CancelledError:
            return "failed"

    def _abort_connection(self) -> None:
        driver = self._driver
        if driver is not None:
            # psycopg's libpq finish is synchronous socket ownership release,
            # unlike awaiting session.rollback()/close() after cancellation.
            try:
                driver.pgconn.finish()
            except Exception:
                self._count("connection_abort_failed")

    def _quarantine(self, task: asyncio.Task[None], batch: list[CaptureSubmission]) -> None:
        self._quarantined = True
        self.budget.accepting = False
        self._count("persist_failed", len(batch))
        self._count("writer_unavailable")
        self._drop_queue("writer_unavailable")

        def release_batch(_future: asyncio.Future[Any] | None = None) -> None:
            for item in batch:
                item.release()

        def finished(completed: asyncio.Task[None]) -> None:
            _consume(completed)
            if self._attempt is completed:
                self._attempt = None
            compression = self._compression_future
            if compression is not None and not compression.done():
                compression.add_done_callback(release_batch)
            else:
                release_batch()

        task.add_done_callback(finished)

    def _drop_queue(self, reason: str) -> None:
        count = len(self._queue)
        while self._queue:
            self._queue.popleft().release()
        if count:
            self._count(reason, count)

    async def _persist_batch(self, batch: list[CaptureSubmission]) -> None:
        # Fixed dedicated executor, one submission at a time. Raw bytes remain
        # under retained reservations; compression/chunk copies own workspace.
        raw_size = sum(
            len(side.encoded or b"") for item in batch for side in (item.request, item.response)
        )
        workspace = min(WORKSPACE_BYTES, max(raw_size * 2, raw_size + 512 * 1024))
        if not self.budget.workspace(workspace):
            raise RuntimeError("capture_workspace_exhausted")
        future: asyncio.Future[Any] | None = None
        try:
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(self._executor, _compress_batch, batch)
            self._compression_future = future
            prepared = await asyncio.shield(future)
            async with self._engine.connect() as connection:
                self._connection = connection
                raw_connection = await connection.get_raw_connection()
                self._driver = raw_connection.driver_connection
                async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                    async with session.begin():
                        for item, sides in zip(batch, prepared, strict=True):
                            try:
                                async with session.begin_nested():
                                    await self._persist_item(session, item, sides)
                            except _StaleCapture:
                                pass
        finally:
            self._connection = None
            self._driver = None
            # A running compression job can retain compressed bytes after an
            # asyncio cancellation; release workspace only when it has ended.
            if future is not None and not future.done():
                future.add_done_callback(
                    lambda completed: self._release_workspace(completed, workspace)
                )
            else:
                self.budget.release_workspace(workspace)
                if self._compression_future is future:
                    self._compression_future = None

    def _release_workspace(self, future: asyncio.Future[Any], workspace: int) -> None:
        _consume(future)
        self.budget.release_workspace(workspace)
        if self._compression_future is future:
            self._compression_future = None

    async def _persist_item(
        self,
        session: AsyncSession,
        item: CaptureSubmission,
        prepared: tuple[tuple[bytes, str], tuple[bytes, str]],
    ) -> None:
        call = await session.get(ExternalCall, item.identity.external_call_id)
        if (
            call is None
            or tuple(getattr(call, name) for name in IDENTITY_FIELDS) != item.identity.associations
        ):
            self._count("stale")
            return
        existing = await session.scalar(
            select(CaptureRow).where(CaptureRow.external_call_id == item.identity.external_call_id)
        )
        if existing is not None:
            # Immutable revision: unknown-commit retries and late duplicates
            # cannot replace complete payload or resurrect a deleted call.
            self._count("duplicate")
            return
        values: dict[str, Any] = {
            "id": uuid4(),
            "external_call_id": item.identity.external_call_id,
            "revision": item.revision,
            "schema_version": PROVIDER_CAPTURE_VERSION,
            "captured_at": item.captured_at,
            "provider_request_id": item.provider_request_id,
            "capture_error_code": item.error_code,
        }
        for side, payload, compressed in zip(
            ("request", "response"), (item.request, item.response), prepared, strict=True
        ):
            blob_id = await _store_encoded_blob(
                session, side.upper(), payload.encoded or b"{}", compressed
            )
            values.update(
                {
                    f"{side}_blob_id": blob_id,
                    f"{side}_capture_status": payload.status,
                    f"{side}_original_bytes": payload.original_bytes,
                    f"{side}_stored_bytes": payload.stored_bytes,
                    f"{side}_sha256": payload.sha256,
                }
            )
        # Recheck immutable associations in the INSERT's MVCC snapshot too:
        # blob preparation/storage may have yielded after the first SELECT.
        # This never takes an authority row lock or checks a finished lease.
        historical_call = (
            select(ExternalCall.id)
            .where(
                ExternalCall.id == item.identity.external_call_id,
                *(
                    getattr(ExternalCall, name).is_not_distinct_from(value)
                    for name, value in zip(IDENTITY_FIELDS, item.identity.associations, strict=True)
                ),
            )
            .exists()
        )
        inserted = await session.scalar(
            insert(CaptureRow)
            .from_select(
                list(values),
                select(
                    *(
                        literal(value, type_=CaptureRow.__table__.c[name].type)
                        for name, value in values.items()
                    )
                ).where(historical_call),
            )
            .on_conflict_do_nothing(index_elements=("external_call_id",))
            .returning(CaptureRow.id)
        )
        if inserted is None:
            self._count("stale")
            raise _StaleCapture

    async def close(self) -> None:
        if self._closed:
            return
        self._closing = True
        self.budget.accepting = False
        self._wake.set()
        deadline = asyncio.get_running_loop().time() + SHUTDOWN_SECONDS
        worker = self._worker
        if worker is not None and not worker.done():
            done, _ = await asyncio.wait(
                {worker}, timeout=max(0, deadline - asyncio.get_running_loop().time())
            )
            if not done:
                worker.cancel()
                self._abort_connection()
                worker.add_done_callback(_consume)
        self._drop_queue("shutdown_drop")
        self._executor.shutdown(wait=False, cancel_futures=True)
        dispose = asyncio.create_task(self._engine.dispose(), name="provider-capture-dispose")
        done, _ = await asyncio.wait(
            {dispose}, timeout=max(0, deadline - asyncio.get_running_loop().time())
        )
        if not done:
            dispose.cancel()
            dispose.add_done_callback(_consume)
        self._closed = True


def _bounded_scalar(value: str | None, limit: int) -> str | None:
    # Metadata is adapter supplied, never log provider prose or credentials.
    if value is None:
        return None
    from .provider import contains_sensitive

    return None if contains_sensitive(value) else value[:limit]


def _compress_batch(
    batch: list[CaptureSubmission],
) -> list[tuple[tuple[bytes, str], tuple[bytes, str]]]:
    return [
        tuple(  # type: ignore[misc]
            (
                zlib.compress(side.encoded or b"{}", level=6),
                hashlib.sha256(side.encoded or b"{}").hexdigest(),
            )
            for side in (item.request, item.response)
        )
        for item in batch
    ]


async def _store_encoded_blob(
    session: AsyncSession, kind: str, raw: bytes, prepared: tuple[bytes, str]
) -> UUID:
    compressed, digest = prepared
    blob_id = uuid4()
    chunk_count = max(1, (len(compressed) + CONTENT_CHUNK_BYTES - 1) // CONTENT_CHUNK_BYTES)
    inserted = await session.scalar(
        insert(CallContentBlob)
        .values(
            id=blob_id,
            sha256=digest,
            content_kind=kind,
            serialization_version=SERIALIZATION_VERSION,
            compression="ZLIB",
            uncompressed_bytes=len(raw),
            compressed_bytes=len(compressed),
            chunk_count=chunk_count,
        )
        .on_conflict_do_nothing(index_elements=("sha256", "serialization_version", "content_kind"))
        .returning(CallContentBlob.id)
    )
    if inserted is None:
        existing = await session.scalar(
            select(CallContentBlob.id).where(
                CallContentBlob.sha256 == digest,
                CallContentBlob.serialization_version == SERIALIZATION_VERSION,
                CallContentBlob.content_kind == kind,
            )
        )
        if existing is None:
            raise RuntimeError("call_content_blob_conflict")
        return existing
    session.add_all(
        CallContentBlobChunk(
            blob_id=blob_id,
            chunk_no=index,
            payload=compressed[index * CONTENT_CHUNK_BYTES : (index + 1) * CONTENT_CHUNK_BYTES],
        )
        for index in range(chunk_count)
    )
    return blob_id


def _consume(task: asyncio.Future[Any]) -> None:
    try:
        error = task.exception()
        if error is not None:
            # Diagnostic traceback frames may retain raw/compressed payloads
            # even after leases end. No exception escapes this owned boundary.
            error.__traceback__ = None
    except BaseException:
        pass


def new_provider_capture(writer: ProviderCaptureWriter | None) -> ProviderCallCapture:
    return writer.new_capture() if writer is not None else ProviderCallCapture(budgeted=True)


def prepare_capture(
    writer: ProviderCaptureWriter | None, call: ExternalCall, capture: ProviderCallCapture
) -> CaptureSubmission | None:
    call.capture_storage_mode = "ASYNC_V1"
    if writer is None:
        capture.close()
        return None
    return writer.prepare(call, capture)


def submit_capture(
    writer: ProviderCaptureWriter | None, submission: CaptureSubmission | None
) -> bool:
    if writer is None:
        if submission is not None:
            submission.close()
        return False
    return writer.submit(submission)

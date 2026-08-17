"""Bounded, non-blocking persistence for redacted WARNING/ERROR logs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..logging import JsonFormatter
from ..models import SystemIncident, SystemLogEvent


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class DiagnosticRecord:
    level: str
    service: str
    logger_name: str
    message: str
    error_code: str | None
    request_id: str | None
    match_id: UUID | None
    speech_id: UUID | None
    generation_id: UUID | None
    decision_round_id: UUID | None
    connection_epoch: int | None
    incident_id: UUID | None
    happened_at: datetime


class DiagnosticHandler(logging.Handler):
    def __init__(self, writer: DiagnosticWriter) -> None:
        super().__init__(level=logging.WARNING)
        self._writer = writer
        self._formatter = JsonFormatter(writer.service)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = json.loads(self._formatter.format(record))
            happened_at = datetime.fromisoformat(str(payload["timestamp"]))
            self._writer.enqueue(
                DiagnosticRecord(
                    level=str(payload["level"]),
                    service=str(payload["service"]),
                    logger_name=record.name[:128],
                    message=str(payload["message"])[:1000],
                    error_code=(str(payload["error_code"]) if payload.get("error_code") else None),
                    request_id=(str(payload["request_id"]) if payload.get("request_id") else None),
                    match_id=_uuid(payload.get("match_id")),
                    speech_id=_uuid(payload.get("speech_id")),
                    generation_id=_uuid(payload.get("generation_id")),
                    decision_round_id=_uuid(payload.get("decision_round_id")),
                    connection_epoch=(
                        int(payload["connection_epoch"])
                        if payload.get("connection_epoch") is not None
                        else None
                    ),
                    incident_id=_uuid(payload.get("incident_id")),
                    happened_at=happened_at,
                )
            )
        except Exception:
            sys.stderr.write(
                json.dumps(
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "level": "ERROR",
                        "service": self._writer.service,
                        "message": "diagnostic record normalization failed",
                        "error_code": "diagnostic_normalization_failed",
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )


class DiagnosticWriter:
    def __init__(
        self,
        *,
        service: str,
        session_factory: async_sessionmaker[AsyncSession],
        queue_size: int = 1024,
        batch_size: int = 50,
        flush_interval_seconds: float = 0.25,
    ) -> None:
        self.service = service
        self._session_factory = session_factory
        self._queue = asyncio.Queue[DiagnosticRecord](maxsize=queue_size)
        self._batch_size = batch_size
        self._flush_interval_seconds = flush_interval_seconds
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._handler = DiagnosticHandler(self)
        self._dropped = 0

    @property
    def dropped_count(self) -> int:
        return self._dropped

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    async def flush(self) -> None:
        """Wait until all records accepted by the bounded queue are handled."""

        # Logging handlers can run on any thread and enqueue via
        # call_soon_threadsafe. Yield once so records already emitted are
        # visible before observing Queue.join().
        await asyncio.sleep(0)
        await self._queue.join()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        logging.getLogger().addHandler(self._handler)
        self._task = asyncio.create_task(self._run(), name="diagnostic-log-writer")

    def enqueue(self, record: DiagnosticRecord) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._enqueue_on_loop, record)

    def _enqueue_on_loop(self, record: DiagnosticRecord) -> None:
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped == 1 or self._dropped & (self._dropped - 1) == 0:
                sys.stderr.write(
                    json.dumps(
                        {
                            "timestamp": datetime.now(UTC).isoformat(),
                            "level": "ERROR",
                            "service": self.service,
                            "message": "diagnostic records dropped",
                            "error_code": "diagnostic_queue_full",
                            "dropped_count": self._dropped,
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )

    async def stop(self) -> None:
        logging.getLogger().removeHandler(self._handler)
        task, self._task = self._task, None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            self._loop = None

    async def _run(self) -> None:
        while self._task is not None or not self._queue.empty():
            batch: list[DiagnosticRecord] = []
            try:
                first = await asyncio.wait_for(
                    self._queue.get(), timeout=self._flush_interval_seconds
                )
                batch.append(first)
            except TimeoutError:
                continue
            while len(batch) < self._batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            try:
                await self._persist_with_retry(batch)
            finally:
                for _ in batch:
                    self._queue.task_done()

    async def _persist_with_retry(self, batch: list[DiagnosticRecord]) -> None:
        for attempt in (1, 2):
            try:
                async with self._session_factory() as session:
                    async with session.begin():
                        for item in batch:
                            fingerprint_source = (
                                f"{item.service}|{item.error_code or ''}|"
                                f"{item.logger_name}|{item.message}"
                            )
                            fingerprint = hashlib.sha256(fingerprint_source.encode()).hexdigest()[
                                :64
                            ]
                            incident = await session.scalar(
                                select(SystemIncident)
                                .where(SystemIncident.fingerprint == fingerprint)
                                .with_for_update()
                            )
                            if incident is None:
                                incident = SystemIncident(
                                    fingerprint=fingerprint,
                                    title=item.message[:256],
                                    severity=item.level,
                                    first_seen_at=item.happened_at,
                                    last_seen_at=item.happened_at,
                                )
                                session.add(incident)
                                await session.flush()
                            else:
                                incident.last_seen_at = max(incident.last_seen_at, item.happened_at)
                                incident.occurrence_count += 1
                                if (
                                    item.level == "CRITICAL"
                                    or incident.severity != "CRITICAL"
                                    and item.level == "ERROR"
                                ):
                                    incident.severity = item.level
                            if item.match_id is not None:
                                incident.affected_match_count += 1
                            session.add(
                                SystemLogEvent(
                                    level=item.level,
                                    service=item.service,
                                    logger_name=item.logger_name,
                                    message=item.message,
                                    error_code=item.error_code,
                                    request_id=item.request_id,
                                    match_id=item.match_id,
                                    speech_id=item.speech_id,
                                    generation_id=item.generation_id,
                                    decision_round_id=item.decision_round_id,
                                    connection_epoch=item.connection_epoch,
                                    incident_id=incident.id,
                                    happened_at=item.happened_at,
                                )
                            )
                return
            except Exception:
                if attempt == 1:
                    await asyncio.sleep(0)
                    continue
                self._dropped += len(batch)
                sys.stderr.write(
                    json.dumps(
                        {
                            "timestamp": datetime.now(UTC).isoformat(),
                            "level": "ERROR",
                            "service": self.service,
                            "message": "diagnostic batch persistence failed",
                            "error_code": "diagnostic_persist_failed",
                            "dropped_count": self._dropped,
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )


__all__ = ["DiagnosticHandler", "DiagnosticRecord", "DiagnosticWriter"]

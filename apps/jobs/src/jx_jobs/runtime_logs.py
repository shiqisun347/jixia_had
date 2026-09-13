"""Bounded persistence for redacted Jobs runtime logs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .logging import JsonFormatter


@dataclass(frozen=True, slots=True)
class RuntimeRecord:
    level: str
    logger_name: str
    message: str
    error_code: str | None
    happened_at: datetime


class RuntimeLogHandler(logging.Handler):
    def __init__(self, writer: RuntimeLogWriter) -> None:
        super().__init__(level=logging.INFO)
        self._writer = writer
        self._formatter = JsonFormatter("jx-jobs")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = json.loads(self._formatter.format(record))
            self._writer.enqueue(
                RuntimeRecord(
                    level=str(payload["level"]),
                    logger_name=record.name[:128],
                    message=str(payload["message"])[:1000],
                    error_code=(str(payload["error_code"]) if payload.get("error_code") else None),
                    happened_at=datetime.fromisoformat(str(payload["timestamp"])),
                )
            )
        except Exception:
            return


class RuntimeLogWriter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        queue_size: int = 1024,
    ) -> None:
        self._session_factory = session_factory
        self._queue = asyncio.Queue[RuntimeRecord](maxsize=queue_size)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._handler = RuntimeLogHandler(self)
        self.dropped_count = 0

    async def start(self) -> None:
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        logging.getLogger().addHandler(self._handler)
        self._task = asyncio.create_task(self._run(), name="jobs-runtime-log-writer")

    def enqueue(self, record: RuntimeRecord) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._enqueue_on_loop, record)

    def _enqueue_on_loop(self, record: RuntimeRecord) -> None:
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self.dropped_count += 1

    async def stop(self) -> None:
        logging.getLogger().removeHandler(self._handler)
        task, self._task = self._task, None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=2)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._loop = None

    async def _run(self) -> None:
        while self._task is not None or not self._queue.empty():
            try:
                record = await asyncio.wait_for(self._queue.get(), timeout=0.25)
            except TimeoutError:
                continue
            try:
                await self._persist(record)
            except Exception:
                self.dropped_count += 1
                sys.stderr.write("jobs runtime log persistence failed\n")
            finally:
                self._queue.task_done()

    async def _persist(self, record: RuntimeRecord) -> None:
        incident_id = None
        async with self._session_factory() as session:
            async with session.begin():
                if record.level in {"WARNING", "ERROR", "CRITICAL"}:
                    fingerprint = hashlib.sha256(
                        (
                            f"jx-jobs|{record.error_code or ''}|"
                            f"{record.logger_name}|{record.message}"
                        ).encode()
                    ).hexdigest()[:64]
                    incident_id = await session.scalar(
                        text(
                            """
                            INSERT INTO system_incidents
                              (id, fingerprint, title, severity, status, first_seen_at,
                               last_seen_at, occurrence_count, affected_match_count,
                               affected_user_count, created_at, updated_at)
                            VALUES (:id, :fingerprint, :title, :severity, 'OPEN', :at,
                                    :at, 1, 0, 0, now(), now())
                            ON CONFLICT (fingerprint) DO UPDATE SET
                              last_seen_at=GREATEST(
                                system_incidents.last_seen_at, EXCLUDED.last_seen_at
                              ),
                              occurrence_count=system_incidents.occurrence_count+1,
                              severity=CASE WHEN EXCLUDED.severity='CRITICAL' THEN 'CRITICAL'
                                WHEN EXCLUDED.severity='ERROR'
                                  AND system_incidents.severity='WARNING'
                                THEN 'ERROR' ELSE system_incidents.severity END
                            RETURNING id
                            """
                        ),
                        {
                            "id": uuid4(),
                            "fingerprint": fingerprint,
                            "title": record.message[:256],
                            "severity": record.level,
                            "at": record.happened_at,
                        },
                    )
                await session.execute(
                    text(
                        """
                        INSERT INTO system_log_events
                          (id, level, service, logger_name, message, error_code,
                           incident_id, details, happened_at, created_at)
                        VALUES (:id, :level, 'jx-jobs', :logger_name, :message,
                                :error_code, :incident_id, '{}'::jsonb, :at, now())
                        """
                    ),
                    {
                        "id": uuid4(),
                        "level": record.level,
                        "logger_name": record.logger_name,
                        "message": record.message,
                        "error_code": record.error_code,
                        "incident_id": incident_id,
                        "at": record.happened_at,
                    },
                )


__all__ = ["RuntimeLogWriter", "RuntimeRecord"]

"""No-op task runner used until a later slice adds PostgreSQL task claims."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from .database import DATABASE_STARTUP_TIMEOUT_SECONDS


class DatabaseProbe(Protocol):
    async def ping(self) -> bool: ...

    async def dispose(self) -> None: ...


DatabaseFactory = Callable[[str], DatabaseProbe]
TaskProcessor = Callable[[], Awaitable[bool]]


async def run_jobs(
    *,
    once: bool,
    database: DatabaseProbe,
    stop_event: asyncio.Event | None = None,
    logger: logging.Logger | None = None,
    database_timeout_seconds: float = DATABASE_STARTUP_TIMEOUT_SECONDS,
    task_processor: TaskProcessor | None = None,
) -> int:
    """Check configuration/database, then idle without claiming work.

    ``once`` is intentionally a no-op success path.  It proves the process
    boundary and health checks without creating a jobs table or calling a
    model.  The caller owns the supplied database and it is always disposed.
    """

    if database_timeout_seconds <= 0:
        raise ValueError("database_timeout_seconds must be positive")
    resolved_logger = logger or logging.getLogger("jx-jobs")
    try:
        try:
            async with asyncio.timeout(database_timeout_seconds):
                healthy = await database.ping()
        except Exception:
            healthy = False
        if not healthy:
            resolved_logger.error(
                "jobs database is unavailable",
                extra={"error_code": "database_unavailable"},
            )
            return 1
        if once:
            if task_processor is not None:
                await task_processor()
            resolved_logger.info("jobs configuration check complete")
            return 0

        resolved_logger.info("jobs process started")
        event = stop_event or asyncio.Event()
        while not event.is_set():
            processed = await task_processor() if task_processor is not None else False
            if not processed:
                try:
                    await asyncio.wait_for(event.wait(), timeout=0.5)
                except TimeoutError:
                    continue
        return 0
    finally:
        with contextlib.suppress(Exception):
            await database.dispose()

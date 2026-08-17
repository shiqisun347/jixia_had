"""Lifecycle composition for the foundation core service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from .config import CORE_INSTANCE_LOCK_KEY, Settings
from .database import DATABASE_READY_TIMEOUT_SECONDS, Database
from .lease import LEASE_ACQUIRE_TIMEOUT_SECONDS, InstanceLease, LeaseUnavailable

DATABASE_CLEANUP_TIMEOUT_SECONDS = 2.0


class CoreStartupError(RuntimeError):
    """Safe startup error whose message can be shown by a supervisor."""


@dataclass(frozen=True, slots=True)
class Readiness:
    ready: bool
    error_code: str | None = None


class DatabaseProtocol(Protocol):
    @property
    def engine(self) -> AsyncEngine: ...

    @property
    def session_factory(self) -> object: ...

    async def ping(self) -> bool: ...

    async def dispose(self) -> None: ...


class LeaseProtocol(Protocol):
    @property
    def healthy(self) -> bool: ...

    @property
    def acquired(self) -> bool: ...

    async def acquire(self) -> None: ...

    async def release(self) -> None: ...


DatabaseFactory = Callable[[str], DatabaseProtocol]


class CoreRuntime:
    """Own the database engine and advisory-lock lease for one process."""

    def __init__(
        self,
        settings: Settings,
        *,
        database: DatabaseProtocol | None = None,
        lease: LeaseProtocol | None = None,
        database_factory: DatabaseFactory = Database,
        lock_key: int = CORE_INSTANCE_LOCK_KEY,
        startup_timeout_seconds: float = LEASE_ACQUIRE_TIMEOUT_SECONDS,
        ready_timeout_seconds: float = DATABASE_READY_TIMEOUT_SECONDS,
        cleanup_timeout_seconds: float = DATABASE_CLEANUP_TIMEOUT_SECONDS,
        logger: logging.Logger | None = None,
    ) -> None:
        if startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive")
        if ready_timeout_seconds <= 0:
            raise ValueError("ready_timeout_seconds must be positive")
        if cleanup_timeout_seconds <= 0:
            raise ValueError("cleanup_timeout_seconds must be positive")
        self.settings = settings
        self.logger = logger or logging.getLogger("jx-core.runtime")
        self._startup_timeout_seconds = startup_timeout_seconds
        self._ready_timeout_seconds = ready_timeout_seconds
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        try:
            self.database = database or database_factory(settings.database_url_value)
            self.lease = lease or InstanceLease(
                self.database.engine,
                lock_key,
                logger=self.logger,
            )
        except Exception:
            self.logger.error(
                "core database initialization failed",
                extra={"error_code": "database_unavailable"},
            )
            raise CoreStartupError("database_unavailable") from None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        try:
            async with asyncio.timeout(self._startup_timeout_seconds):
                await self.lease.acquire()
        except LeaseUnavailable:
            await self._dispose_database()
            self.logger.error(
                "core instance lock unavailable",
                extra={"error_code": "instance_lock_unavailable"},
            )
            raise CoreStartupError("instance_lock_unavailable") from None
        except Exception:
            await self._dispose_database()
            self.logger.error(
                "core database startup failed",
                extra={"error_code": "database_unavailable"},
            )
            raise CoreStartupError("database_unavailable") from None
        self._started = True

    async def stop(self) -> None:
        if not self._started and not self.lease.acquired:
            return
        try:
            # Bound the complete shutdown sequence, rather than giving lease
            # release and engine disposal separate full windows.  This keeps
            # the process-level lease-loss exit within the foundation limit.
            async with asyncio.timeout(self._cleanup_timeout_seconds):
                with contextlib.suppress(Exception):
                    await self.lease.release()
                await self.database.dispose()
        except Exception:
            # Shutdown is already on the failure path; the supervisor owns
            # the final process exit and must not wait indefinitely here.
            pass
        self._started = False

    async def readiness(self) -> Readiness:
        if not self.lease.healthy:
            return Readiness(False, "instance_lock_unavailable")
        try:
            async with asyncio.timeout(self._ready_timeout_seconds):
                if not await self.database.ping():
                    return Readiness(False, "database_unavailable")
        except (SQLAlchemyError, OSError, TimeoutError):
            return Readiness(False, "database_unavailable")
        except Exception:
            # Do not turn an implementation/vendor exception into a response.
            return Readiness(False, "database_unavailable")
        return Readiness(True)

    async def _dispose_database(self) -> None:
        with contextlib.suppress(Exception):
            async with asyncio.timeout(self._cleanup_timeout_seconds):
                await self.database.dispose()

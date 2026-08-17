"""PostgreSQL advisory-lock lease for the single ``jx-core`` instance."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import signal
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

LeaseShutdown = Callable[[], Awaitable[None] | None]

LEASE_ACQUIRE_TIMEOUT_SECONDS = 10.0
LEASE_HEARTBEAT_INTERVAL_SECONDS = 1.0
LEASE_HEARTBEAT_TIMEOUT_SECONDS = 2.0
LEASE_CLEANUP_TIMEOUT_SECONDS = 2.0


class LeaseUnavailable(RuntimeError):
    """Raised when another core process owns the configured lock."""


class InstanceLease:
    """Hold a dedicated PostgreSQL connection for the process lifetime.

    The connection is deliberately not returned to the pool.  If a heartbeat
    query fails, the lease is marked unhealthy and the shutdown callback is
    invoked so a supervisor can restart the process instead of allowing two
    competing orchestrators to run.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        lock_key: int,
        *,
        acquire_timeout_seconds: float = LEASE_ACQUIRE_TIMEOUT_SECONDS,
        heartbeat_seconds: float = LEASE_HEARTBEAT_INTERVAL_SECONDS,
        heartbeat_timeout_seconds: float = LEASE_HEARTBEAT_TIMEOUT_SECONDS,
        cleanup_timeout_seconds: float = LEASE_CLEANUP_TIMEOUT_SECONDS,
        on_connection_lost: LeaseShutdown | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if acquire_timeout_seconds <= 0:
            raise ValueError("acquire_timeout_seconds must be positive")
        if heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be positive")
        if heartbeat_timeout_seconds <= 0:
            raise ValueError("heartbeat_timeout_seconds must be positive")
        if cleanup_timeout_seconds <= 0:
            raise ValueError("cleanup_timeout_seconds must be positive")
        self._engine = engine
        self._lock_key = lock_key
        self._acquire_timeout_seconds = acquire_timeout_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._on_connection_lost = on_connection_lost or request_process_shutdown
        self._logger = logger or logging.getLogger("jx-core.lease")
        self._connection: AsyncConnection | None = None
        self._backend_pid: int | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._healthy = False
        self._callback_started = False

    @property
    def healthy(self) -> bool:
        return self._healthy

    @property
    def acquired(self) -> bool:
        return self._connection is not None and self._healthy

    @property
    def backend_pid(self) -> int | None:
        """Return the lease connection PID for internal diagnostics/tests."""

        return self._backend_pid

    async def acquire(self) -> None:
        if self._connection is not None:
            return
        connection = self._engine.connect()
        try:
            async with asyncio.timeout(self._acquire_timeout_seconds):
                # AsyncConnection.start() is the explicit form of the context
                # manager enter operation and retains this dedicated lease.
                await connection.start()
                result = await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_key)"),
                    {"lock_key": self._lock_key},
                )
                if result is not True:
                    raise LeaseUnavailable("jx-core instance lock is already held")
                backend_pid = await connection.scalar(text("SELECT pg_backend_pid()"))
                if not isinstance(backend_pid, int):
                    raise RuntimeError("lease backend pid is unavailable")
                # The advisory lock is session-scoped, so ending the implicit
                # transaction keeps the lock while avoiding a lifetime-long snapshot.
                await connection.commit()
        except BaseException:
            await self._close_connection(connection)
            raise

        self._connection = connection
        self._backend_pid = backend_pid
        self._healthy = True
        self._monitor_task = asyncio.create_task(
            self._monitor(), name="jx-core-advisory-lock-heartbeat"
        )

    async def release(self) -> None:
        monitor = self._monitor_task
        self._monitor_task = None
        if monitor is not None:
            monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor

        connection = self._connection
        self._connection = None
        self._backend_pid = None
        self._healthy = False
        if connection is None:
            return
        try:
            async with asyncio.timeout(self._cleanup_timeout_seconds):
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": self._lock_key},
                )
                await connection.commit()
        except Exception:
            # A lost connection cannot be unlocked; PostgreSQL releases the
            # lock when it observes the disconnect.
            self._logger.warning(
                "advisory lock release skipped",
                extra={"error_code": "lease_release_unavailable"},
            )
        finally:
            await self._close_connection(connection)

    async def _monitor(self) -> None:
        connection = self._connection
        assert connection is not None
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            try:
                async with asyncio.timeout(self._heartbeat_timeout_seconds):
                    await connection.scalar(text("SELECT 1"))
                    await connection.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._healthy = False
                self._logger.error(
                    "advisory lock connection lost",
                    extra={"error_code": "lease_connection_lost"},
                )
                await self._notify_connection_lost()
                return

    async def _notify_connection_lost(self) -> None:
        if self._callback_started:
            return
        self._callback_started = True
        callback_result = self._on_connection_lost()
        if inspect.isawaitable(callback_result):
            try:
                async with asyncio.timeout(self._cleanup_timeout_seconds):
                    await callback_result
            except Exception:
                self._logger.error(
                    "lease shutdown callback failed",
                    extra={"error_code": "lease_shutdown_callback_failed"},
                )

    async def _close_connection(self, connection: AsyncConnection) -> None:
        with contextlib.suppress(Exception):
            async with asyncio.timeout(self._cleanup_timeout_seconds):
                await connection.close()


def request_process_shutdown() -> None:
    """Ask the process supervisor to restart after lease loss."""

    os.kill(os.getpid(), signal.SIGTERM)

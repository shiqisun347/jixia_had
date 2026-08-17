from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import pytest

from jx_core.config import Settings
from jx_core.logging import configure_logging
from jx_core.runtime import CoreRuntime, CoreStartupError


class FakeDatabase:
    def __init__(self, *, ping_hangs: bool = False, dispose_hangs: bool = False) -> None:
        self.ping_hangs = ping_hangs
        self.dispose_hangs = dispose_hangs
        self.disposed = False
        self.engine = cast(Any, object())

    async def ping(self) -> bool:
        if self.ping_hangs:
            await asyncio.Event().wait()
        return True

    async def dispose(self) -> None:
        if self.dispose_hangs:
            await asyncio.Event().wait()
        self.disposed = True


class FakeLease:
    def __init__(self, *, acquire_hangs: bool = False, release_delay: float = 0) -> None:
        self.acquire_hangs = acquire_hangs
        self.release_delay = release_delay
        self.healthy = True
        self.acquired = False

    async def acquire(self) -> None:
        if self.acquire_hangs:
            await asyncio.Event().wait()
        self.acquired = True

    async def release(self) -> None:
        if self.release_delay:
            await asyncio.sleep(self.release_delay)
        self.acquired = False
        self.healthy = False


def settings() -> Settings:
    return Settings(database_url="postgresql+psycopg://jx:secret@127.0.0.1:5432/jx_debate")


def test_database_construction_failure_is_normalized_and_redacted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("jx-core")

    def fail(database_url: str) -> FakeDatabase:
        raise RuntimeError(f"cannot construct {database_url} password=hunter2")

    with pytest.raises(CoreStartupError, match="database_unavailable"):
        CoreRuntime(settings(), database_factory=fail)

    output = capsys.readouterr().out
    assert '"error_code":"database_unavailable"' in output
    assert "secret" not in output
    assert "hunter2" not in output
    assert "postgresql" not in output
    assert "Traceback" not in output


@pytest.mark.asyncio
async def test_startup_timeout_is_bounded_and_disposes_database() -> None:
    database = FakeDatabase()
    runtime = CoreRuntime(
        settings(),
        database=database,
        lease=FakeLease(acquire_hangs=True),
        startup_timeout_seconds=0.01,
    )

    with pytest.raises(CoreStartupError, match="database_unavailable"):
        await runtime.start()

    assert database.disposed is True


@pytest.mark.asyncio
async def test_readiness_timeout_returns_normalized_failure() -> None:
    runtime = CoreRuntime(
        settings(),
        database=FakeDatabase(ping_hangs=True),
        lease=FakeLease(),
        ready_timeout_seconds=0.01,
        logger=logging.getLogger("test-runtime"),
    )

    state = await runtime.readiness()

    assert state.ready is False
    assert state.error_code == "database_unavailable"


@pytest.mark.asyncio
async def test_stop_bounds_lease_release_and_database_disposal_together() -> None:
    runtime = CoreRuntime(
        settings(),
        database=FakeDatabase(dispose_hangs=True),
        lease=FakeLease(release_delay=0.04),
        cleanup_timeout_seconds=0.05,
    )
    await runtime.start()

    async with asyncio.timeout(0.075):
        await runtime.stop()

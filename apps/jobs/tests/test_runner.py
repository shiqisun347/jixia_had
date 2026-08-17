from __future__ import annotations

import asyncio

from jx_jobs.runner import run_jobs


class FakeDatabase:
    def __init__(self, healthy: bool, *, ping_hangs: bool = False) -> None:
        self.healthy = healthy
        self.ping_hangs = ping_hangs
        self.disposed = False
        self.ping_calls = 0

    async def ping(self) -> bool:
        self.ping_calls += 1
        if self.ping_hangs:
            await asyncio.Event().wait()
        return self.healthy

    async def dispose(self) -> None:
        self.disposed = True


def test_once_checks_database_and_exits_without_work() -> None:
    database = FakeDatabase(True)
    result = asyncio.run(run_jobs(once=True, database=database))

    assert result == 0
    assert database.ping_calls == 1
    assert database.disposed is True


def test_once_returns_nonzero_on_database_failure() -> None:
    database = FakeDatabase(False)
    result = asyncio.run(run_jobs(once=True, database=database))

    assert result == 1
    assert database.disposed is True


def test_noop_mode_waits_for_stop_event_then_exits() -> None:
    database = FakeDatabase(True)
    stop_event = asyncio.Event()
    stop_event.set()
    result = asyncio.run(run_jobs(once=False, database=database, stop_event=stop_event))

    assert result == 0
    assert database.disposed is True


def test_database_timeout_is_bounded_and_returns_nonzero() -> None:
    database = FakeDatabase(True, ping_hangs=True)
    result = asyncio.run(
        run_jobs(
            once=True,
            database=database,
            database_timeout_seconds=0.01,
        )
    )

    assert result == 1
    assert database.disposed is True

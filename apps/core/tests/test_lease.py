from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from jx_core.lease import InstanceLease, LeaseUnavailable


class FakeConnection:
    def __init__(
        self,
        *,
        lock_result: bool = True,
        start_hangs: bool = False,
    ) -> None:
        self.lock_result = lock_result
        self.start_hangs = start_hangs
        self.closed = False
        self.heartbeat_error = False
        self.heartbeat_hangs = False
        self.commit_error = False
        self.unlock_calls = 0
        self.commit_calls = 0
        self._lock_checked = False

    async def start(self) -> None:
        if self.start_hangs:
            await asyncio.Event().wait()
        return None

    async def scalar(self, statement: Any, params: Any = None) -> Any:
        text_value = str(statement)
        if "pg_try_advisory_lock" in text_value:
            self._lock_checked = True
            return self.lock_result
        if "pg_backend_pid" in text_value:
            return 42
        if self.heartbeat_hangs:
            await asyncio.Event().wait()
        if self.heartbeat_error:
            raise OSError("simulated connection loss")
        return 1

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.unlock_calls += 1
        return True

    async def commit(self) -> None:
        if self.commit_error:
            raise OSError("simulated commit failure")
        self.commit_calls += 1

    async def close(self) -> None:
        self.closed = True


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def connect(self) -> FakeConnection:
        return self.connection


@pytest.mark.asyncio
async def test_advisory_lock_rejects_second_owner() -> None:
    connection = FakeConnection(lock_result=False)
    lease = InstanceLease(cast(Any, FakeEngine(connection)), 123)

    with pytest.raises(LeaseUnavailable):
        await lease.acquire()

    assert connection.closed is True
    assert lease.healthy is False


@pytest.mark.asyncio
async def test_connection_loss_marks_lease_unhealthy_and_requests_shutdown() -> None:
    connection = FakeConnection()
    lost = asyncio.Event()

    async def shutdown() -> None:
        lost.set()

    lease = InstanceLease(
        cast(Any, FakeEngine(connection)),
        123,
        heartbeat_seconds=0.001,
        on_connection_lost=shutdown,
    )
    await lease.acquire()
    assert lease.backend_pid == 42
    assert connection.commit_calls == 1
    connection.heartbeat_error = True

    await asyncio.wait_for(lost.wait(), timeout=1)
    assert lease.healthy is False
    await lease.release()
    assert connection.closed is True


@pytest.mark.asyncio
async def test_heartbeat_and_release_end_their_transactions() -> None:
    connection = FakeConnection()
    lease = InstanceLease(
        cast(Any, FakeEngine(connection)),
        123,
        heartbeat_seconds=0.001,
    )

    await lease.acquire()
    await asyncio.sleep(0.01)
    heartbeat_commits = connection.commit_calls
    assert heartbeat_commits >= 2

    await lease.release()
    assert connection.unlock_calls == 1
    assert connection.commit_calls == heartbeat_commits + 1


@pytest.mark.asyncio
async def test_heartbeat_commit_failure_marks_lease_unhealthy() -> None:
    connection = FakeConnection()
    lost = asyncio.Event()
    lease = InstanceLease(
        cast(Any, FakeEngine(connection)),
        123,
        heartbeat_seconds=0.001,
        on_connection_lost=lost.set,
    )

    await lease.acquire()
    connection.commit_error = True
    await asyncio.wait_for(lost.wait(), timeout=1)

    assert lease.healthy is False
    await lease.release()


@pytest.mark.asyncio
async def test_acquire_timeout_closes_partial_connection() -> None:
    connection = FakeConnection(start_hangs=True)
    lease = InstanceLease(
        cast(Any, FakeEngine(connection)),
        123,
        acquire_timeout_seconds=0.01,
        cleanup_timeout_seconds=0.05,
    )

    with pytest.raises(TimeoutError):
        await lease.acquire()

    assert connection.closed is True
    assert lease.healthy is False


@pytest.mark.asyncio
async def test_heartbeat_timeout_marks_lease_unhealthy() -> None:
    connection = FakeConnection()
    lost = asyncio.Event()
    lease = InstanceLease(
        cast(Any, FakeEngine(connection)),
        123,
        heartbeat_seconds=0.001,
        heartbeat_timeout_seconds=0.01,
        on_connection_lost=lost.set,
    )
    await lease.acquire()
    connection.heartbeat_hangs = True

    await asyncio.wait_for(lost.wait(), timeout=1)

    assert lease.healthy is False
    await lease.release()

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import subprocess
import sys
import time

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import text

from jx_core.app import create_app
from jx_core.config import CORE_INSTANCE_LOCK_KEY, Settings
from jx_core.database import Database
from jx_core.lease import InstanceLease, LeaseUnavailable
from jx_core.runtime import CoreRuntime

pytestmark = pytest.mark.integration


def _integration_settings() -> Settings:
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("set RUN_DATABASE_INTEGRATION=1 to run real PostgreSQL tests")
    database_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.fail("DATABASE_URL is required for PostgreSQL integration tests")
    try:
        return Settings(database_url=database_url, app_env="test")
    except ValidationError:
        pytest.fail(
            "DATABASE_URL must be a valid postgresql+psycopg URL",
            pytrace=False,
        )


def _unique_lock_key() -> int:
    return secrets.randbits(63)


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _advisory_lock_parts(lock_key: int) -> tuple[int, int]:
    unsigned = lock_key & ((1 << 64) - 1)
    return unsigned >> 32, unsigned & 0xFFFF_FFFF


async def _wait_until_live(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 5.0
    async with AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout is not None else ""
                pytest.fail(f"jx-core exited before live check: {output}", pytrace=False)
            try:
                response = await client.get("/health/live", timeout=0.25)
            except Exception:
                await asyncio.sleep(0.05)
                continue
            if response.status_code == 200:
                return
            await asyncio.sleep(0.05)
    pytest.fail("jx-core did not become live within 5 seconds", pytrace=False)


@pytest.mark.asyncio
async def test_real_postgres_ping_and_ready_endpoint() -> None:
    settings = _integration_settings()
    runtime = CoreRuntime(settings, lock_key=_unique_lock_key())
    app = create_app(settings, runtime=runtime)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "service": "jx-core"}


@pytest.mark.asyncio
async def test_real_postgres_rejects_second_lease_owner() -> None:
    settings = _integration_settings()
    first_database = Database(settings.database_url_value)
    second_database = Database(settings.database_url_value)
    lock_key = _unique_lock_key()
    first_lease = InstanceLease(first_database.engine, lock_key)
    second_lease = InstanceLease(second_database.engine, lock_key)

    try:
        await first_lease.acquire()
        backend_pid = first_lease.backend_pid
        assert backend_pid is not None
        await asyncio.sleep(1.1)
        async with second_database.engine.connect() as connection:
            state = await connection.scalar(
                text("SELECT state FROM pg_stat_activity WHERE pid = :backend_pid"),
                {"backend_pid": backend_pid},
            )
        assert state == "idle"
        with pytest.raises(LeaseUnavailable):
            await second_lease.acquire()
    finally:
        await second_lease.release()
        await first_lease.release()
        await second_database.dispose()
        await first_database.dispose()


@pytest.mark.asyncio
async def test_real_core_process_rejects_second_instance_with_fixed_lock() -> None:
    settings = _integration_settings()
    first_port = _available_port()
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "CORE_HOST": "127.0.0.1",
        "CORE_PORT": str(first_port),
        "CORS_ORIGINS": "http://localhost:3000",
        "DATABASE_URL": settings.database_url_value,
        "PYTHONUNBUFFERED": "1",
    }
    first_process = subprocess.Popen(
        [sys.executable, "-m", "jx_core.cli"],
        cwd=os.getcwd(),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        await _wait_until_live(first_port, first_process)
        second_port = _available_port()
        while second_port == first_port:
            second_port = _available_port()
        second_environment = {**environment, "CORE_PORT": str(second_port)}
        second_process = subprocess.Popen(
            [sys.executable, "-m", "jx_core.cli"],
            cwd=os.getcwd(),
            env=second_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            second_exit = await asyncio.to_thread(second_process.wait, 5.0)
            second_output = (
                second_process.stdout.read() if second_process.stdout is not None else ""
            )
            assert second_exit != 0
            assert "instance_lock_unavailable" in second_output
            assert settings.database_url_value not in second_output
            assert "Traceback" not in second_output
        finally:
            if second_process.poll() is None:
                second_process.terminate()
                await asyncio.to_thread(second_process.wait, 2.0)
            if second_process.stdout is not None:
                second_process.stdout.close()
    finally:
        if first_process.poll() is None:
            first_process.terminate()
            try:
                await asyncio.to_thread(first_process.wait, 2.0)
            except subprocess.TimeoutExpired:
                first_process.kill()
                await asyncio.to_thread(first_process.wait, 2.0)
        if first_process.stdout is not None:
            first_process.stdout.close()


@pytest.mark.asyncio
async def test_terminating_real_lease_backend_marks_runtime_unhealthy() -> None:
    settings = _integration_settings()
    database = Database(settings.database_url_value)
    killer_database = Database(settings.database_url_value)
    connection_lost = asyncio.Event()
    lease = InstanceLease(
        database.engine,
        _unique_lock_key(),
        heartbeat_seconds=0.05,
        heartbeat_timeout_seconds=2.0,
        on_connection_lost=connection_lost.set,
    )
    runtime = CoreRuntime(settings, database=database, lease=lease)

    try:
        await runtime.start()
        backend_pid = lease.backend_pid
        assert backend_pid is not None
        async with asyncio.timeout(2.0):
            async with killer_database.engine.connect() as connection:
                terminated = await connection.scalar(
                    text("SELECT pg_terminate_backend(:backend_pid)"),
                    {"backend_pid": backend_pid},
                )
        assert terminated is True

        await asyncio.wait_for(connection_lost.wait(), timeout=5.0)
        state = await runtime.readiness()

        assert lease.healthy is False
        assert state.ready is False
        assert state.error_code == "instance_lock_unavailable"
    finally:
        await runtime.stop()
        await killer_database.dispose()


@pytest.mark.asyncio
async def test_terminating_lease_backend_exits_real_core_process_within_five_seconds() -> None:
    settings = _integration_settings()
    port = _available_port()
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "CORE_HOST": "127.0.0.1",
        "CORE_PORT": str(port),
        "CORS_ORIGINS": "http://localhost:3000",
        "DATABASE_URL": settings.database_url_value,
        "PYTHONUNBUFFERED": "1",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "jx_core.cli"],
        cwd=os.getcwd(),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    killer_database = Database(settings.database_url_value)

    try:
        await _wait_until_live(port, process)
        class_id, object_id = _advisory_lock_parts(CORE_INSTANCE_LOCK_KEY)
        async with asyncio.timeout(2.0):
            async with killer_database.engine.connect() as connection:
                backend_pid = await connection.scalar(
                    text(
                        "SELECT pid FROM pg_locks "
                        "WHERE locktype = 'advisory' AND granted "
                        "AND classid = :class_id AND objid = :object_id AND objsubid = 1 "
                        "AND database = (SELECT oid FROM pg_database "
                        "WHERE datname = current_database())"
                    ),
                    {"class_id": class_id, "object_id": object_id},
                )
                assert isinstance(backend_pid, int)
                terminated = await connection.scalar(
                    text("SELECT pg_terminate_backend(:backend_pid)"),
                    {"backend_pid": backend_pid},
                )
        assert terminated is True

        exit_code = await asyncio.to_thread(process.wait, 5.0)
        output = process.stdout.read() if process.stdout is not None else ""

        assert exit_code != 0
        assert "lease_connection_lost" in output
        assert settings.database_url_value not in output
        assert "Traceback" not in output
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, 2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, 2.0)
        if process.stdout is not None:
            process.stdout.close()
        await killer_database.dispose()

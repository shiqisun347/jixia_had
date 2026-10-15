"""Exercise provider cleanup against real loopback WebSocket transports."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.protocol import State

from jx_core.agent.tts import QwenTtsConnection
from jx_core.asr.protocol import FunAsrConnection
from jx_core.provider_cleanup import close_provider_socket_bounded
from jx_jobs.tts import DashScopeTTSClient  # pyright: ignore[reportMissingTypeStubs]

_PATHS = ("tts_close", "tts_discard", "asr_close", "asr_invalidate", "jobs_close")


@asynccontextmanager
async def _stalled_close_server() -> AsyncGenerator[str, None]:
    """Complete the actual upgrade handshake, then never respond to Close."""

    release = asyncio.Event()
    handlers: set[asyncio.Task[Any]] = set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        handlers.add(task)
        try:
            request = (await reader.readuntil(b"\r\n\r\n")).decode()
            key = next(
                line.split(":", 1)[1].strip()
                for line in request.split("\r\n")
                if line.lower().startswith("sec-websocket-key:")
            )
            accept = base64.b64encode(
                hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
            ).decode()
            writer.write(
                (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            await release.wait()
        finally:
            writer.close()
            await writer.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        release.set()
        server.close()
        await server.wait_closed()
        if handlers:
            await asyncio.wait_for(asyncio.gather(*handlers), timeout=1.0)


def _retirement(
    path: str,
    socket: ClientConnection,
    monkeypatch: pytest.MonkeyPatch,
    *,
    replacement: ClientConnection | None = None,
) -> tuple[Callable[[], Coroutine[Any, Any, None]], Callable[[], object | None]]:
    if path.startswith("tts_"):
        tts = QwenTtsConnection(url="ws://synthetic.invalid", api_key="test")
        tts._socket = replacement or socket
        monkeypatch.setattr(QwenTtsConnection, "_CLOSE_TIMEOUT_SECONDS", 0.05)
        if path == "tts_close":
            return tts.close, lambda: tts._socket
        return lambda: tts._discard_socket(socket), lambda: tts._socket
    if path.startswith("asr_"):
        asr = FunAsrConnection(url="ws://synthetic.invalid", api_key="test")
        asr._socket = replacement or socket
        monkeypatch.setattr(FunAsrConnection, "_CLOSE_TIMEOUT_SECONDS", 0.05)
        if path == "asr_close":
            return asr.close, lambda: asr._socket
        return lambda: asr._invalidate_socket(socket), lambda: asr._socket
    jobs = DashScopeTTSClient(websocket_url="ws://synthetic.invalid", api_key="test", workspace="")
    jobs._connection = socket
    monkeypatch.setattr(DashScopeTTSClient, "_CLOSE_TIMEOUT_SECONDS", 0.05)
    return jobs.close, lambda: jobs._connection


async def _wait_until_closing(socket: ClientConnection) -> None:
    while socket.state is State.OPEN:
        await asyncio.sleep(0)


@pytest.mark.parametrize("path", _PATHS)
async def test_retired_websocket_timeout_releases_real_fd(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _stalled_close_server() as url:
        socket = await connect(url, proxy=None)
        actual_socket = socket.transport.get_extra_info("socket")
        retire, current_socket = _retirement(path, socket, monkeypatch)
        try:
            await asyncio.wait_for(retire(), timeout=0.5)
            await asyncio.wait_for(socket.wait_closed(), timeout=0.2)
            assert actual_socket.fileno() == -1
            assert socket.state is State.CLOSED
            assert current_socket() is None
        finally:
            socket.transport.abort()
            await socket.wait_closed()


@pytest.mark.parametrize("path", _PATHS)
async def test_retired_websocket_outer_cancel_releases_real_fd_and_propagates(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _stalled_close_server() as url:
        socket = await connect(url, proxy=None)
        actual_socket = socket.transport.get_extra_info("socket")
        retire, current_socket = _retirement(path, socket, monkeypatch)
        cleanup = asyncio.create_task(retire())
        try:
            await asyncio.wait_for(_wait_until_closing(socket), timeout=0.2)
            cleanup.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(cleanup, timeout=0.2)
            await asyncio.wait_for(socket.wait_closed(), timeout=0.2)
            assert actual_socket.fileno() == -1
            assert current_socket() is None
        finally:
            cleanup.cancel()
            socket.transport.abort()
            await asyncio.gather(cleanup, socket.wait_closed(), return_exceptions=True)


@pytest.mark.parametrize("path", _PATHS)
async def test_normal_websocket_close_releases_real_fd_without_abort(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def handle(socket: ServerConnection) -> None:
        await socket.wait_closed()

    async with serve(handle, "127.0.0.1", 0) as server:
        socket = await connect(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", proxy=None)
        actual_socket = socket.transport.get_extra_info("socket")
        retire, current_socket = _retirement(path, socket, monkeypatch)
        with patch.object(socket.transport, "abort", wraps=socket.transport.abort) as abort:
            await asyncio.wait_for(retire(), timeout=0.5)
            await asyncio.wait_for(socket.wait_closed(), timeout=0.2)
            assert actual_socket.fileno() == -1
            assert current_socket() is None
            abort.assert_not_called()


@pytest.mark.parametrize("path", ("tts_discard", "asr_invalidate"))
async def test_discarded_socket_abort_does_not_close_replacement(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _stalled_close_server() as url:
        old_socket = await connect(url, proxy=None)
        replacement = await connect(url, proxy=None)
        old_fd = old_socket.transport.get_extra_info("socket")
        replacement_fd = replacement.transport.get_extra_info("socket")
        retire, current_socket = _retirement(path, old_socket, monkeypatch, replacement=replacement)
        try:
            await asyncio.wait_for(retire(), timeout=0.5)
            await asyncio.wait_for(old_socket.wait_closed(), timeout=0.2)
            assert old_fd.fileno() == -1
            assert current_socket() is replacement
            assert replacement_fd.fileno() >= 0
            assert replacement.state is State.OPEN
        finally:
            old_socket.transport.abort()
            replacement.transport.abort()
            await asyncio.gather(old_socket.wait_closed(), replacement.wait_closed())


@pytest.mark.parametrize("error", (RuntimeError("close failed"), asyncio.CancelledError()))
async def test_failed_close_aborts_retired_transport(error: BaseException) -> None:
    class Transport:
        aborted = False

        def abort(self) -> None:
            self.aborted = True

    class Socket:
        transport = Transport()

        async def close(self) -> None:
            raise error

    socket = Socket()
    await close_provider_socket_bounded(socket, timeout_seconds=0.01, task_name="test-close")
    assert socket.transport.aborted is True


async def test_close_without_abort_remains_bounded_and_consumes_late_failure() -> None:
    release = asyncio.Event()
    closing: asyncio.Task[None] | None = None
    unhandled: list[dict[str, Any]] = []

    class Socket:
        async def close(self) -> None:
            nonlocal closing
            closing = asyncio.current_task()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            raise RuntimeError("late close failure")

    loop = asyncio.get_running_loop()
    original_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        await asyncio.wait_for(
            close_provider_socket_bounded(Socket(), timeout_seconds=0.01, task_name="test-close"),
            timeout=0.2,
        )
        assert closing is not None and not closing.done()
        release.set()
        await asyncio.wait({closing}, timeout=0.2)
        await asyncio.sleep(0)
        assert closing.done()
        assert unhandled == []
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        loop.set_exception_handler(original_handler)


async def test_abort_failure_does_not_mask_caller_cancellation() -> None:
    started = asyncio.Event()

    class Transport:
        def abort(self) -> None:
            raise RuntimeError("abort failed")

    class Socket:
        transport = Transport()

        async def close(self) -> None:
            started.set()
            await asyncio.Future()

    cleanup = asyncio.create_task(
        close_provider_socket_bounded(Socket(), timeout_seconds=2.0, task_name="test-close")
    )
    await started.wait()
    cleanup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(cleanup, timeout=0.2)
    await asyncio.sleep(0)

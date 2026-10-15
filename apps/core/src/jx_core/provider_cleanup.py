"""Bounded cleanup for retired provider WebSocket connections."""

from __future__ import annotations

import asyncio
from typing import Protocol


class ClosableProviderSocket(Protocol):
    async def close(self) -> None: ...


def _abort_transport(socket: ClosableProviderSocket) -> None:
    """Abort when supported; test and native interfaces may only expose close."""

    # Pinned websockets 17.0.1 exposes its asyncio transport. abort() is the
    # public WriteTransport operation, not a high-level WebSocket API.
    abort = getattr(getattr(socket, "transport", None), "abort", None)
    if callable(abort):
        try:
            abort()
        except Exception:
            # Failed cleanup must not replace the original provider error or
            # caller cancellation. There is no stronger portable operation.
            pass


def _consume_close_result(completed: asyncio.Task[None]) -> None:
    try:
        completed.exception()
    except BaseException:
        # Detached cleanup must not produce an unhandled task exception.
        pass


async def close_provider_socket_bounded(
    socket: ClosableProviderSocket, *, timeout_seconds: float, task_name: str
) -> None:
    """Close a retired socket and abort its transport when close cannot finish."""

    close_task = asyncio.create_task(socket.close(), name=task_name)
    try:
        done, _ = await asyncio.wait({close_task}, timeout=timeout_seconds)
    except asyncio.CancelledError:
        # websockets' close timeout performs this abort itself, but canceling
        # close before that timeout bypasses it. Do not await before aborting.
        _abort_transport(socket)
        close_task.cancel()
        close_task.add_done_callback(_consume_close_result)
        raise
    if close_task in done:
        try:
            close_task.result()
        except BaseException:
            _abort_transport(socket)
    else:
        _abort_transport(socket)
        close_task.cancel()
        close_task.add_done_callback(_consume_close_result)

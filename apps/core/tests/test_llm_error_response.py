"""Non-success provider responses share the absolute first-token budget."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic
from typing import Any, cast

import httpx
import pytest

from jx_core.agent.llm import (
    LLM_MAX_ERROR_BODY_BYTES,
    LlmProviderError,
    OpenAIStreamingClient,
    close_llm_client_bounded,
)
from jx_core.data_capture.provider import ProviderCallCapture


class ErrorBody(httpx.AsyncByteStream):
    def __init__(self, *, chunks: list[bytes] | None = None, trickle: bool = False) -> None:
        self.chunks = chunks
        self.trickle = trickle
        self.reads = 0
        self.closed = False
        self.entered = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.entered.set()
        if self.chunks is not None:
            for chunk in self.chunks:
                self.reads += 1
                yield chunk
        elif self.trickle:
            while True:
                await asyncio.sleep(0.005)
                self.reads += 1
                yield b"x"
        else:
            await asyncio.Future()

    async def aclose(self) -> None:
        self.closed = True


@asynccontextmanager
async def _adapter(
    body: ErrorBody, *, header_delay: float = 0.0, first_timeout: float = 0.04
) -> AsyncGenerator[OpenAIStreamingClient, None]:
    async def handler(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(header_delay)
        return httpx.Response(429, headers={"x-request-id": "synthetic-id"}, stream=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        yield OpenAIStreamingClient(
            base_url="http://synthetic.invalid",
            api_key="test",
            model="synthetic",
            client=http_client,
            first_token_timeout_seconds=first_timeout,
        )


async def _call(adapter: OpenAIStreamingClient, capture: ProviderCallCapture | None) -> None:
    async def unexpected_delta(_: str) -> None:
        raise AssertionError("An HTTP error response must not deliver a delta")

    await adapter.stream_chat(
        messages=[{"role": "user", "content": "synthetic"}],
        max_tokens=16,
        generation_params={},
        on_delta=unexpected_delta,
        capture=capture,
    )


@pytest.mark.parametrize("trickle", (False, True))
async def test_error_body_silence_and_slow_bytes_cannot_extend_deadline(trickle: bool) -> None:
    body = ErrorBody(trickle=trickle)
    capture = ProviderCallCapture()
    async with _adapter(body) as adapter:
        with pytest.raises(LlmProviderError, match="llm_provider_failed"):
            await asyncio.wait_for(_call(adapter, capture), timeout=0.2)
    assert body.closed
    assert capture.provider_request_id == "synthetic-id"
    assert capture.response is not None and capture.response.status == "UNAVAILABLE"
    assert capture.response.payload["capture"]["error_code"] == "provider_error_body_timeout"
    if trickle:
        assert body.reads > 0


async def test_response_header_latency_consumes_error_body_remaining_budget() -> None:
    body = ErrorBody()
    capture = ProviderCallCapture()
    async with _adapter(body, header_delay=0.14, first_timeout=0.2) as adapter:
        started = monotonic()
        with pytest.raises(LlmProviderError, match="llm_provider_failed"):
            await asyncio.wait_for(_call(adapter, capture), timeout=0.3)
        assert monotonic() - started < 0.3
    assert body.closed
    assert capture.response is not None and capture.response.status == "UNAVAILABLE"


async def test_expired_first_token_budget_does_not_read_even_complete_error_body() -> None:
    body = ErrorBody(chunks=[b'{"error":"synthetic"}'])
    capture = ProviderCallCapture()
    async with _adapter(body, header_delay=0.04, first_timeout=0.02) as adapter:
        with pytest.raises(LlmProviderError, match="llm_provider_failed"):
            await _call(adapter, capture)
    assert body.reads == 0 and body.closed
    assert capture.response is not None and capture.response.status == "UNAVAILABLE"


async def test_complete_error_body_is_captured_without_changing_provider_error() -> None:
    body = ErrorBody(chunks=[b'{"error":"synthetic"}'])
    capture = ProviderCallCapture()
    async with _adapter(body) as adapter:
        with pytest.raises(LlmProviderError, match="llm_provider_failed"):
            await _call(adapter, capture)
    assert body.closed
    assert capture.response is not None and capture.response.status == "COMPLETE"
    assert capture.response.payload["body"] == {"error": "synthetic"}


async def test_error_body_keeps_existing_64kib_capture_limit() -> None:
    body = ErrorBody(chunks=[b"x" * (LLM_MAX_ERROR_BODY_BYTES + 1), b"unread"])
    capture = ProviderCallCapture()
    async with _adapter(body) as adapter:
        with pytest.raises(LlmProviderError, match="llm_provider_failed"):
            await _call(adapter, capture)
    assert body.reads == 1 and body.closed
    assert capture.response is not None
    assert capture.response.payload["body"] == {
        "truncated": True,
        "bytes": LLM_MAX_ERROR_BODY_BYTES,
    }


async def test_error_response_without_capture_fails_without_reading_body() -> None:
    body = ErrorBody()
    async with _adapter(body, first_timeout=10.0) as adapter:
        with pytest.raises(LlmProviderError, match="llm_provider_failed"):
            await asyncio.wait_for(_call(adapter, None), timeout=0.2)
    assert body.reads == 0 and not body.entered.is_set() and body.closed


async def test_caller_cancel_during_error_body_propagates_and_closes_response() -> None:
    body = ErrorBody()
    capture = ProviderCallCapture()
    async with _adapter(body, first_timeout=10.0) as adapter:
        task = asyncio.create_task(_call(adapter, capture))
        try:
            await asyncio.wait_for(body.entered.wait(), timeout=0.2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=0.2)
            assert body.closed
            assert capture.response is not None
            assert capture.response.payload["capture"]["error_code"] == "provider_call_cancelled"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@asynccontextmanager
async def _loopback_error_server(
    *, trickle: bool
) -> AsyncGenerator[tuple[str, asyncio.Event, asyncio.Event], None]:
    ready, peer_closed = asyncio.Event(), asyncio.Event()
    handlers: set[asyncio.Task[Any]] = set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        current = asyncio.current_task()
        assert current is not None
        handlers.add(current)
        sender: asyncio.Task[None] | None = None
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode()
            length = int(
                next(
                    line.split(":", 1)[1].strip()
                    for line in head.split("\r\n")
                    if line.lower().startswith("content-length:")
                )
            )
            await reader.readexactly(length)
            writer.write(
                b"HTTP/1.1 429 Too Many Requests\r\nContent-Type: application/json\r\n"
                b"Transfer-Encoding: chunked\r\nConnection: keep-alive\r\n\r\n"
            )
            await writer.drain()
            if trickle:

                async def send_bytes() -> None:
                    while True:
                        await asyncio.sleep(0.005)
                        writer.write(b"1\r\nx\r\n")
                        await writer.drain()

                sender = asyncio.create_task(send_bytes())
            ready.set()
            try:
                assert await reader.read(1) == b""
            except ConnectionResetError:
                # Closing a stream with unread response bytes may reset TCP
                # instead of sending FIN. Both prove the peer closed its FD.
                pass
            peer_closed.set()
        finally:
            if sender is not None:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionResetError:
                pass
            handlers.discard(current)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", ready, peer_closed
    finally:
        server.close()
        await server.wait_closed()
        if handlers:
            for handler in handlers:
                handler.cancel()
            await asyncio.gather(*handlers, return_exceptions=True)


@pytest.mark.parametrize("trickle", (False, True))
async def test_real_error_response_deadline_releases_fd_before_cleanup_returns(
    trickle: bool,
) -> None:
    async with _loopback_error_server(trickle=trickle) as (url, ready, peer_closed):
        adapter = OpenAIStreamingClient(
            base_url=url, api_key="test", model="synthetic", first_token_timeout_seconds=0.1
        )
        capture = ProviderCallCapture()
        task = asyncio.create_task(_call(adapter, capture))
        try:
            await asyncio.wait_for(ready.wait(), timeout=0.2)
            # Inspect the actual pinned httpx/httpcore stream's socket object,
            # rather than inferring physical cleanup from an empty registry.
            pool = cast(Any, adapter)._client._transport._pool
            actual_socket = pool.connections[0]._connection._network_stream.get_extra_info("socket")
            assert actual_socket.fileno() >= 0
            with pytest.raises(LlmProviderError, match="llm_provider_failed"):
                await asyncio.wait_for(task, timeout=0.3)
            assert actual_socket.fileno() == -1
            await close_llm_client_bounded(adapter)
            assert actual_socket.fileno() == -1 and len(pool.connections) == 0
            await asyncio.wait_for(peer_closed.wait(), timeout=0.2)
            assert capture.response is not None and capture.response.status == "UNAVAILABLE"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await adapter.close()

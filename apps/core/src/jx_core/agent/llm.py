"""OpenAI-compatible streaming text adapter with bounded timing semantics."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from time import monotonic
from typing import Any, cast

import httpx

from ..data_capture.provider import ProviderCallCapture, unavailable_provider_payload


class LlmProviderError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# Product failure semantics: connection/first token and an idle streaming gap
# each have a 10-second budget. Capacity acquisition is intentionally separate
# and remains a short bounded queue wait.
LLM_FIRST_TOKEN_TIMEOUT_SECONDS = 10.0
LLM_IDLE_TIMEOUT_SECONDS = 10.0
LLM_CONNECTION_TIMEOUT_SECONDS = 10.0
LLM_FAST_DECISION_TIMEOUT_SECONDS = 3.0
LLM_MAX_COMPLETION_TOKENS = 32_768
LLM_MAX_TEXT_CHARS = 256_000
LLM_MAX_ERROR_BODY_BYTES = 64 * 1024
LLM_CLIENT_CLOSE_TIMEOUT_SECONDS = 0.5

logger = logging.getLogger("jx-core.llm")


@dataclass(frozen=True, slots=True)
class LlmStreamResult:
    text: str
    first_token_latency_ms: int
    completed_latency_ms: int
    completion_tokens: int | None


DeltaCallback = Callable[[str], Awaitable[None]]


class LlmCapacityLimiter:
    def __init__(self, global_limit: int = 50) -> None:
        self._global = asyncio.Semaphore(global_limit)
        self._models: dict[str, asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self, model_key: str, model_limit: int, timeout_seconds: float = 3.0
    ) -> tuple[asyncio.Semaphore, asyncio.Semaphore]:
        try:
            # Registry and both capacity waits share one queue budget. Busy
            # model waiters must not reserve global slots from other models.
            async with asyncio.timeout(timeout_seconds):
                async with self._lock:
                    model = self._models.setdefault(model_key, asyncio.Semaphore(model_limit))
                await model.acquire()
                try:
                    await self._global.acquire()
                except BaseException:
                    # CancelledError is a BaseException. Return the already
                    # acquired model slot before propagating cancellation.
                    model.release()
                    raise
        except TimeoutError as error:
            raise LlmProviderError("llm_capacity_full") from error
        return self._global, model

    @staticmethod
    def release(leases: tuple[asyncio.Semaphore, asyncio.Semaphore]) -> None:
        global_slot, model_slot = leases
        model_slot.release()
        global_slot.release()


class OpenAIStreamingClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        connection_timeout_seconds: float = LLM_CONNECTION_TIMEOUT_SECONDS,
        first_token_timeout_seconds: float = LLM_FIRST_TOKEN_TIMEOUT_SECONDS,
        idle_timeout_seconds: float = LLM_IDLE_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._connection_timeout_seconds = connection_timeout_seconds
        self._first_token_timeout_seconds = first_token_timeout_seconds
        self._idle_timeout_seconds = idle_timeout_seconds
        self._client = client or httpx.AsyncClient(timeout=None)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def stream_chat(
        self,
        *,
        messages: list[dict[str, str]],
        max_tokens: int,
        generation_params: dict[str, Any],
        on_delta: DeltaCallback,
        capture: ProviderCallCapture | None = None,
    ) -> LlmStreamResult:
        started = monotonic()
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            **generation_params,
            # Runtime duration and model capability determine the hard output
            # budget.  Provider tuning parameters must not override it.
            "max_tokens": min(max(1, int(max_tokens)), LLM_MAX_COMPLETION_TOKENS),
        }
        body.setdefault("enable_thinking", False)
        url = f"{self._base_url}/chat/completions"
        if capture is not None:
            capture.capture_request(
                transport="SSE",
                url=url,
                headers={"content-type": "application/json"},
                body=body,
            )
        key_events = capture.event_buffer() if capture is not None else []
        consumer_error: Exception | None = None
        first_token_deadline = started + self._first_token_timeout_seconds
        stream_completed = False
        try:
            # Enter the response stream under the connection budget only.  The
            # response body has its own absolute first-token/idle deadlines;
            # wrapping the entire stream would incorrectly cap long replies.
            async with AsyncExitStack() as stack:
                try:
                    async with asyncio.timeout(self._connection_timeout_seconds):
                        response = await stack.enter_async_context(
                            self._client.stream(
                                "POST",
                                url,
                                headers={"Authorization": f"Bearer {self._api_key}"},
                                json=body,
                                timeout=httpx.Timeout(
                                    None, connect=self._connection_timeout_seconds
                                ),
                            )
                        )
                except TimeoutError as error:
                    raise LlmProviderError("llm_provider_failed") from error
                if capture is not None:
                    capture.provider_request_id = _provider_request_id(response.headers)
                if response.status_code < 200 or response.status_code >= 300:
                    if capture is not None and capture.can_capture:
                        try:
                            remaining = first_token_deadline - monotonic()
                            if remaining <= 0:
                                raise TimeoutError
                            async with asyncio.timeout(remaining):
                                error_body = await _bounded_error_body(response)
                        except TimeoutError:
                            capture.response = unavailable_provider_payload(
                                "provider_error_body_timeout"
                            )
                        else:
                            capture.capture_response(
                                transport="SSE",
                                url=url,
                                headers=response.headers,
                                body=error_body,
                                events=[],
                            )
                    raise LlmProviderError("llm_provider_failed")
                lines = response.aiter_lines()
                text_parts: list[str] = []
                text_length = 0
                first_token_latency_ms: int | None = None
                completion_tokens: int | None = None
                last_token_at: float | None = None
                while True:
                    now = monotonic()
                    if first_token_latency_ms is None:
                        timeout = first_token_deadline - now
                        timeout_code = "llm_first_token_timeout"
                    else:
                        assert last_token_at is not None
                        timeout = self._idle_timeout_seconds - (now - last_token_at)
                        timeout_code = "llm_stream_stalled"
                    if timeout <= 0:
                        raise LlmProviderError(timeout_code)
                    try:
                        line = await asyncio.wait_for(anext(lines), timeout=timeout)
                    except StopAsyncIteration:
                        break
                    except TimeoutError as error:
                        raise LlmProviderError(timeout_code) from error
                    payload = _sse_payload(line)
                    if payload is None:
                        continue
                    if payload == "[DONE]":
                        stream_completed = True
                        break
                    event = _json_event(payload)
                    if capture is not None and capture.can_capture:
                        if len(key_events) < 2:
                            key_events.append(event)
                        else:
                            key_events[1] = event
                    usage_value = event.get("usage")
                    usage = (
                        cast(dict[str, Any], usage_value) if isinstance(usage_value, dict) else {}
                    )
                    if isinstance(usage.get("completion_tokens"), int):
                        completion_tokens = int(usage["completion_tokens"])
                    delta = _content_delta(event)
                    if not delta:
                        continue
                    if first_token_latency_ms is None:
                        first_token_latency_ms = int((monotonic() - started) * 1000)
                    if text_length + len(delta) > LLM_MAX_TEXT_CHARS:
                        raise LlmProviderError("llm_output_too_large")
                    text_parts.append(delta)
                    text_length += len(delta)
                    last_token_at = monotonic()
                    try:
                        await on_delta(delta)
                    except Exception as error:
                        consumer_error = error
                        raise
        except asyncio.CancelledError:
            if capture is not None and capture.response is None:
                capture.response = unavailable_provider_payload("provider_call_cancelled")
            raise
        except LlmProviderError:
            if capture is not None and capture.response is None:
                capture.response = unavailable_provider_payload("provider_stream_failed")
            raise
        except Exception as error:
            if error is consumer_error:
                if capture is not None:
                    capture.error_code = "provider_consumer_failed"
                    if capture.response is None:
                        capture.response = unavailable_provider_payload("provider_consumer_failed")
                raise
            if capture is not None and capture.response is None:
                capture.response = unavailable_provider_payload("provider_transport_failed")
            raise LlmProviderError("llm_provider_failed") from error
        # A provider may accept the request and close an otherwise successful
        # SSE response without sending a first token. Treat that boundary as
        # the same bounded first-token failure as a timed-out stream so the
        # retry and UI can explain the failure consistently.
        if first_token_latency_ms is None:
            raise LlmProviderError("llm_first_token_timeout")
        # OpenAI-compatible streams are explicitly terminated by [DONE].  A
        # body EOF after text (including one carrying finish_reason) is a
        # truncated provider response and must never be committed as a full
        # Agent draft.
        if not stream_completed:
            raise LlmProviderError("llm_stream_interrupted")
        result = LlmStreamResult(
            text="".join(text_parts),
            first_token_latency_ms=first_token_latency_ms,
            completed_latency_ms=int((monotonic() - started) * 1000),
            completion_tokens=completion_tokens,
        )
        if capture is not None:
            capture.capture_response(
                transport="SSE",
                url=url,
                body={
                    "text": result.text,
                    "completion_tokens": result.completion_tokens,
                },
                events=key_events,
            )
        return result


def _consume_client_close(task: asyncio.Task[None]) -> None:
    try:
        task.exception()
    except BaseException:
        pass


async def close_llm_client_bounded(client: OpenAIStreamingClient) -> None:
    """Bound best-effort cleanup without masking a completed provider result."""

    task = asyncio.create_task(client.close(), name="llm-client-close")
    try:
        done, _ = await asyncio.wait({task}, timeout=LLM_CLIENT_CLOSE_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        task.cancel()
        task.add_done_callback(_consume_client_close)
        raise
    if not done:
        task.cancel()
        task.add_done_callback(_consume_client_close)
        logger.warning(
            "LLM client cleanup timed out",
            extra={"error_code": "llm_client_cleanup_timeout"},
        )
        return
    try:
        task.result()
    except BaseException as error:
        logger.warning(
            "LLM client cleanup failed",
            extra={
                "error_code": "llm_client_cleanup_failed",
                "exception_type": type(error).__name__,
            },
        )


def _provider_request_id(headers: httpx.Headers) -> str | None:
    for name in ("x-request-id", "x-dashscope-request-id", "request-id"):
        value = headers.get(name)
        if value:
            return value[:256]
    return None


def _response_json_or_text(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"text": raw.decode("utf-8", errors="replace")}


async def _bounded_error_body(response: httpx.Response) -> Any:
    """Read only a small provider error body for diagnostics."""
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        remaining = LLM_MAX_ERROR_BODY_BYTES - len(buffer)
        if len(chunk) > remaining:
            return {"truncated": True, "bytes": LLM_MAX_ERROR_BODY_BYTES}
        buffer.extend(chunk)
    return _response_json_or_text(bytes(buffer))


def _sse_payload(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(":") or not stripped.startswith("data:"):
        return None
    return stripped[5:].strip()


def _json_event(payload: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise LlmProviderError("llm_provider_failed") from error
    if not isinstance(value, dict):
        raise LlmProviderError("llm_provider_failed")
    return cast(dict[str, Any], value)


def _content_delta(event: dict[str, Any]) -> str:
    choices_value = event.get("choices")
    if not isinstance(choices_value, list) or not choices_value:
        return ""
    choices = cast(list[Any], choices_value)
    first_value = choices[0]
    if not isinstance(first_value, dict):
        return ""
    first = cast(dict[str, Any], first_value)
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return ""
    content = cast(dict[str, Any], delta).get("content")
    return content if isinstance(content, str) else ""


async def iter_text_queue(queue: asyncio.Queue[str | None]) -> AsyncIterator[str]:
    while True:
        value = await queue.get()
        try:
            if value is None:
                return
            yield value
        finally:
            queue.task_done()


__all__ = [
    "LlmCapacityLimiter",
    "LlmProviderError",
    "LlmStreamResult",
    "OpenAIStreamingClient",
    "close_llm_client_bounded",
    "iter_text_queue",
]

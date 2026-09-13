"""OpenAI-compatible streaming text adapter with bounded timing semantics."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
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
        async with self._lock:
            model = self._models.setdefault(model_key, asyncio.Semaphore(model_limit))
        try:
            await asyncio.wait_for(self._global.acquire(), timeout=timeout_seconds)
            try:
                await asyncio.wait_for(model.acquire(), timeout=timeout_seconds)
            except Exception:
                self._global.release()
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
            "max_tokens": max_tokens,
            **generation_params,
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
        key_events: list[dict[str, Any]] = []
        last_event: dict[str, Any] | None = None
        try:
            async with self._client.stream(
                "POST",
                url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=body,
                timeout=httpx.Timeout(None, connect=self._connection_timeout_seconds),
            ) as response:
                if capture is not None:
                    capture.provider_request_id = _provider_request_id(response.headers)
                if response.status_code < 200 or response.status_code >= 300:
                    if capture is not None:
                        capture.capture_response(
                            transport="SSE",
                            url=url,
                            headers=response.headers,
                            body=_response_json_or_text(await response.aread()),
                            events=[],
                        )
                    raise LlmProviderError("llm_provider_failed")
                lines = response.aiter_lines()
                text_parts: list[str] = []
                first_token_latency_ms: int | None = None
                completion_tokens: int | None = None
                while True:
                    timeout = (
                        self._first_token_timeout_seconds
                        if first_token_latency_ms is None
                        else self._idle_timeout_seconds
                    )
                    try:
                        line = await asyncio.wait_for(anext(lines), timeout=timeout)
                    except StopAsyncIteration:
                        break
                    except TimeoutError as error:
                        code = (
                            "llm_first_token_timeout"
                            if first_token_latency_ms is None
                            else "llm_stream_stalled"
                        )
                        raise LlmProviderError(code) from error
                    payload = _sse_payload(line)
                    if payload is None:
                        continue
                    if payload == "[DONE]":
                        break
                    event = _json_event(payload)
                    if not key_events:
                        key_events.append(event)
                    last_event = event
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
                    text_parts.append(delta)
                    await on_delta(delta)
        except asyncio.CancelledError:
            if capture is not None and capture.response is None:
                capture.response = unavailable_provider_payload("provider_call_cancelled")
            raise
        except LlmProviderError:
            if capture is not None and capture.response is None:
                capture.response = unavailable_provider_payload("provider_stream_failed")
            raise
        except Exception as error:
            if capture is not None and capture.response is None:
                capture.response = unavailable_provider_payload("provider_transport_failed")
            raise LlmProviderError("llm_provider_failed") from error
        if first_token_latency_ms is None:
            raise LlmProviderError("llm_provider_failed")
        result = LlmStreamResult(
            text="".join(text_parts),
            first_token_latency_ms=first_token_latency_ms,
            completed_latency_ms=int((monotonic() - started) * 1000),
            completion_tokens=completion_tokens,
        )
        if capture is not None:
            if last_event is not None and (not key_events or last_event is not key_events[-1]):
                key_events.append(last_event)
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
    "iter_text_queue",
]

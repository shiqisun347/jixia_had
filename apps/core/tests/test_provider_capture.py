from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from jx_core.data_capture.provider import (
    MAX_CAPTURE_BYTES,
    ProviderCallCapture,
    capture_provider_payload,
)
from jx_core.data_capture.provider_persistence import persist_provider_capture


def test_provider_capture_keeps_actual_json_and_allowlisted_headers() -> None:
    result = capture_provider_payload(
        transport="sse",
        url="https://provider.example/v1/chat?signature=hidden",
        headers={
            "Authorization": "Bearer hidden",
            "Content-Type": "application/json",
            "X-Request-Id": "request-1",
        },
        body={"model": "qwen", "messages": [{"role": "user", "content": "你好"}]},
        events=[{"event": "done"}],
    )

    assert result.status == "COMPLETE"
    assert result.payload["url"] == {
        "origin": "https://provider.example",
        "path": "/v1/chat",
    }
    assert result.payload["headers"] == {
        "content-type": "application/json",
        "x-request-id": "request-1",
    }
    assert result.payload["body"]["messages"][0]["content"] == "你好"
    assert "hidden" not in json.dumps(result.payload, ensure_ascii=False)


def test_provider_capture_rejects_sensitive_body_without_persisting_value() -> None:
    result = capture_provider_payload(
        transport="http",
        url="https://provider.example/v1/test",
        body={"nested": {"api_key": "should-never-be-stored"}},
    )

    assert result.status == "REJECTED_SENSITIVE"
    assert result.payload["body"] is None
    assert "should-never-be-stored" not in json.dumps(result.payload)


def test_provider_capture_counts_utf8_bytes_and_hashes_original_json() -> None:
    body = {"text": "辩论"}
    content = {
        "transport": "HTTP",
        "url": {"origin": "https://provider.example", "path": "/v1/test"},
        "headers": {},
        "body": body,
        "events": [],
        "binary_summary": None,
    }
    encoded = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    result = capture_provider_payload(
        transport="http", url="https://provider.example/v1/test", body=body
    )

    assert result.original_bytes == len(encoded)
    assert result.sha256 == hashlib.sha256(encoded).hexdigest()
    assert result.stored_bytes == result.payload["capture"]["stored_bytes"]


def test_provider_capture_truncates_above_two_mib_with_head_tail_and_hash() -> None:
    result = capture_provider_payload(
        transport="websocket",
        url="wss://provider.example/ws",
        body={"text": "甲" * (MAX_CAPTURE_BYTES // 2)},
    )

    assert result.status == "TRUNCATED"
    assert result.original_bytes > MAX_CAPTURE_BYTES
    assert result.stored_bytes <= MAX_CAPTURE_BYTES
    assert result.payload["body"] is None
    assert result.payload["truncated_json"]["head"]
    assert result.payload["truncated_json"]["tail"]
    assert len(result.sha256 or "") == 64


@pytest.mark.asyncio
async def test_provider_capture_persistence_failure_does_not_escape(monkeypatch) -> None:
    class Savepoint:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Session:
        def begin_nested(self) -> Savepoint:
            return Savepoint()

    async def fail_store(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(
        "jx_core.data_capture.provider_persistence.store_content_blob",
        fail_store,
    )
    call = SimpleNamespace(capture_error_code=None)
    capture = ProviderCallCapture()

    persisted = await persist_provider_capture(Session(), call, capture)  # type: ignore[arg-type]

    assert persisted is False
    assert call.capture_error_code == "capture_persistence_failed"

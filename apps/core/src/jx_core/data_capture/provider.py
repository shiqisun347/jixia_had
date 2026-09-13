"""Bounded, fail-closed capture of provider request and response JSON."""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

PROVIDER_CAPTURE_VERSION = 1
MAX_CAPTURE_BYTES = 2 * 1024 * 1024
FRAGMENT_BYTES = 896 * 1024

_ALLOWED_HEADERS = {
    "content-type",
    "request-id",
    "x-request-id",
    "x-dashscope-request-id",
}
_SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credentials",
    "password",
    "private_key",
    "proxy_authorization",
    "secret",
    "signature",
    "token",
}
_SENSITIVE_VALUES = (
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b"),
)


@dataclass(frozen=True, slots=True)
class CapturedProviderPayload:
    payload: dict[str, Any]
    status: str
    original_bytes: int
    stored_bytes: int
    sha256: str | None


@dataclass(slots=True)
class ProviderCallCapture:
    """Adapter-owned capture result, persisted later by the caller's transaction."""

    request: CapturedProviderPayload | None = None
    response: CapturedProviderPayload | None = None
    provider_request_id: str | None = None
    error_code: str | None = None

    def capture_request(self, **payload: Any) -> None:
        self.request = capture_provider_payload(**payload)

    def capture_response(self, **payload: Any) -> None:
        self.response = capture_provider_payload(**payload)


def capture_provider_payload(
    *,
    transport: str,
    url: str,
    headers: Mapping[str, str] | None = None,
    body: Any = None,
    events: list[dict[str, Any]] | None = None,
    binary_summary: dict[str, Any] | None = None,
) -> CapturedProviderPayload:
    content: dict[str, Any] = {
        "transport": transport.upper(),
        "url": _safe_url(url),
        "headers": _safe_headers(headers or {}),
        "body": body,
        "events": events or [],
        "binary_summary": binary_summary,
    }
    if _contains_sensitive(content):
        return _terminal_capture(
            content=content,
            status="REJECTED_SENSITIVE",
            error_code="sensitive_content_detected",
        )
    try:
        serialized = _serialize_bounded(content)
    except (TypeError, ValueError, RecursionError):
        return _terminal_capture(
            content=content,
            status="UNAVAILABLE",
            error_code="provider_payload_not_json",
        )
    if serialized.complete is not None:
        payload: dict[str, Any] = {
            "capture_schema_version": PROVIDER_CAPTURE_VERSION,
            **content,
            "capture": {
                "status": "COMPLETE",
                "original_bytes": serialized.original_bytes,
                "stored_bytes": serialized.original_bytes,
                "sha256": serialized.sha256,
            },
        }
        stored_bytes = _apply_stored_bytes(payload)
        return CapturedProviderPayload(
            payload=payload,
            status="COMPLETE",
            original_bytes=serialized.original_bytes,
            stored_bytes=stored_bytes,
            sha256=serialized.sha256,
        )
    payload: dict[str, Any] = {
        "capture_schema_version": PROVIDER_CAPTURE_VERSION,
        "transport": content["transport"],
        "url": content["url"],
        "headers": content["headers"],
        "body": None,
        "events": [],
        "binary_summary": binary_summary,
        "truncated_json": {
            "encoding": "utf-8-json-fragments",
            "head": serialized.head.decode("utf-8", errors="replace"),
            "tail": serialized.tail.decode("utf-8", errors="replace"),
        },
        "capture": {
            "status": "TRUNCATED",
            "original_bytes": serialized.original_bytes,
            "stored_bytes": 0,
            "sha256": serialized.sha256,
        },
    }
    stored_bytes = _apply_stored_bytes(payload)
    return CapturedProviderPayload(
        payload=payload,
        status="TRUNCATED",
        original_bytes=serialized.original_bytes,
        stored_bytes=stored_bytes,
        sha256=serialized.sha256,
    )


def unavailable_provider_payload(reason: str) -> CapturedProviderPayload:
    return _terminal_capture(content={}, status="UNAVAILABLE", error_code=reason)


def _terminal_capture(
    *, content: dict[str, Any], status: str, error_code: str
) -> CapturedProviderPayload:
    payload: dict[str, Any] = {
        "capture_schema_version": PROVIDER_CAPTURE_VERSION,
        "transport": content.get("transport"),
        "url": content.get("url"),
        "headers": content.get("headers", {}),
        "body": None,
        "events": [],
        "binary_summary": None,
        "capture": {
            "status": status,
            "original_bytes": 0,
            "stored_bytes": 0,
            "sha256": None,
            "error_code": error_code,
        },
    }
    stored_bytes = _apply_stored_bytes(payload)
    return CapturedProviderPayload(payload, status, 0, stored_bytes, None)


@dataclass(frozen=True, slots=True)
class _SerializedPayload:
    complete: bytes | None
    head: bytes
    tail: bytes
    original_bytes: int
    sha256: str


def _serialize_bounded(value: Any) -> _SerializedPayload:
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256()
    complete = bytearray()
    head = bytearray()
    tail_chunks: deque[bytes] = deque()
    tail_bytes = 0
    total = 0
    overflowed = False
    for text in encoder.iterencode(value):
        chunk = text.encode("utf-8")
        digest.update(chunk)
        total += len(chunk)
        if not overflowed and len(complete) + len(chunk) <= MAX_CAPTURE_BYTES:
            complete.extend(chunk)
        else:
            overflowed = True
            complete.clear()
        if len(head) < FRAGMENT_BYTES:
            head.extend(chunk[: FRAGMENT_BYTES - len(head)])
        tail_chunks.append(chunk)
        tail_bytes += len(chunk)
        while tail_bytes > FRAGMENT_BYTES and tail_chunks:
            excess = tail_bytes - FRAGMENT_BYTES
            first = tail_chunks[0]
            if len(first) <= excess:
                tail_chunks.popleft()
                tail_bytes -= len(first)
            else:
                tail_chunks[0] = first[excess:]
                tail_bytes -= excess
    return _SerializedPayload(
        complete=bytes(complete) if not overflowed else None,
        head=bytes(head),
        tail=b"".join(tail_chunks),
        original_bytes=total,
        sha256=digest.hexdigest(),
    )


def _contains_sensitive(value: Any) -> bool:
    pending: list[Any] = [value]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            if any(pattern.search(current) for pattern in _SENSITIVE_VALUES):
                return True
            continue
        if isinstance(current, Mapping):
            current_mapping = cast(Mapping[Any, Any], current)
            identity = id(current_mapping)
            if identity in seen:
                continue
            seen.add(identity)
            for key, nested in current_mapping.items():
                normalized = str(key).strip().lower().replace("-", "_")
                if normalized in _SENSITIVE_KEYS:
                    return True
                pending.append(nested)
            continue
        if isinstance(current, (list, tuple)):
            current_sequence = cast(list[Any] | tuple[Any, ...], current)
            identity = id(current_sequence)
            if identity in seen:
                continue
            seen.add(identity)
            pending.extend(current_sequence)
    return False


def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items() if key.lower() in _ALLOWED_HEADERS}


def _safe_url(url: str) -> dict[str, str | None]:
    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else None
    return {"origin": origin, "path": parsed.path or "/"}


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _apply_stored_bytes(payload: dict[str, Any]) -> int:
    capture = cast(dict[str, Any], payload["capture"])
    previous = -1
    current = _json_size(payload)
    while current != previous:
        previous = current
        capture["stored_bytes"] = current
        current = _json_size(payload)
    return current


__all__ = [
    "CapturedProviderPayload",
    "MAX_CAPTURE_BYTES",
    "PROVIDER_CAPTURE_VERSION",
    "ProviderCallCapture",
    "capture_provider_payload",
    "unavailable_provider_payload",
]

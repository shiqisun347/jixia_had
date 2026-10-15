"""Bounded, fail-closed capture of provider request and response JSON."""

from __future__ import annotations

import hashlib
import json
import re
from codecs import getincrementaldecoder
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, cast, overload
from urllib.parse import urlsplit

from .provider_budget import (
    COUNTING_WORKSPACE_BYTES,
    ENCODING_WORKSPACE_BYTES,
    METADATA_BYTES,
    SMALL_ENCODING_BASE_BYTES,
    CaptureBudget,
    Reservation,
)

PROVIDER_CAPTURE_VERSION = 1
MAX_CAPTURE_BYTES = 2 * 1024 * 1024
FRAGMENT_BYTES = 896 * 1024
SORT_KEY_WORKSPACE_BYTES = 1024 * 1024
# A list slot plus TimSort's temporary key references, rounded conservatively.
SORT_KEY_REFERENCE_BYTES = 16

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
_MAX_SENSITIVE_KEY_CHARS = max(map(len, _SENSITIVE_KEYS))
_SENSITIVE_VALUES = (
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_.-]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b"),
)


@dataclass(frozen=True, slots=True)
class CapturedProviderPayload:
    _payload: dict[str, Any] | None
    status: str
    original_bytes: int
    stored_bytes: int
    sha256: str | None
    encoded: bytes | None = None

    @property
    def payload(self) -> dict[str, Any]:
        # Legacy consumers only. The asynchronous writer never decodes snapshots.
        if self._payload is not None:
            fragments = self._payload.get("truncated_json")
            fragment_map = cast(dict[str, Any], fragments) if isinstance(fragments, dict) else {}
            if any(isinstance(part, _Utf8Fragment) for part in fragment_map.values()):
                # Compatibility only: async freeze reads _payload directly and
                # streams these bytes, without allocating wide whole strings.
                return {
                    **self._payload,
                    "truncated_json": {
                        key: part.raw.decode("utf-8", errors="replace")
                        if isinstance(part, _Utf8Fragment)
                        else part
                        for key, part in fragment_map.items()
                    },
                }
            return self._payload
        return cast(dict[str, Any], json.loads(self.encoded or b"{}"))

    @property
    def encoding_payload(self) -> dict[str, Any]:
        """Internal JSON view retaining streaming fragments, before freeze."""
        return self._payload if self._payload is not None else self.payload


@dataclass(slots=True)
class ProviderCallCapture:
    """Adapter-owned capture result, persisted later by the caller's transaction."""

    request: CapturedProviderPayload | None = None
    response: CapturedProviderPayload | None = None
    provider_request_id: str | None = None
    error_code: str | None = None
    budget: CaptureBudget | None = field(default=None, repr=False)
    reservation: Reservation | None = field(default=None, repr=False)
    budgeted: bool = field(default=False, repr=False)
    buffers: list[CaptureEventBuffer] = field(
        default_factory=lambda: list[CaptureEventBuffer](), repr=False
    )

    @property
    def can_capture(self) -> bool:
        return not self.budgeted or self.reservation is not None

    def event_buffer(self) -> list[dict[str, Any]]:
        if not self.budgeted:
            return []
        buffer = CaptureEventBuffer(self)
        self.buffers.append(buffer)
        return cast(list[dict[str, Any]], cast(object, buffer))

    def close(self) -> None:
        if self.reservation is not None:
            self.reservation.release()
            self.reservation = None
        self.request = None
        self.response = None
        for buffer in self.buffers:
            buffer.clear()
        self.buffers.clear()

    def _capture(self, **payload: Any) -> CapturedProviderPayload:
        if not self.budgeted:
            return capture_provider_payload(**payload)
        if self.error_code == "sensitive_content_detected":
            return _terminal_capture(
                content={}, status="REJECTED_SENSITIVE", error_code=self.error_code
            )
        if self.error_code and self.error_code.startswith("capture_"):
            return unavailable_provider_payload(self.error_code)
        if self.reservation is None or self.budget is None:
            return unavailable_provider_payload("capture_capacity_exhausted")
        content = _capture_content(**payload)
        try:
            plan = _plan_with_budget(
                self.budget, content, retained_limit=self.budget.available_retained_bytes
            )
        except _CountingCapacityExceeded:
            return unavailable_provider_payload("capture_capacity_exhausted")
        if plan is None:
            return unavailable_provider_payload("capture_workspace_exhausted")
        if not self.reservation.resize(self.reservation.size + plan.encoded_limit):
            return unavailable_provider_payload("capture_capacity_exhausted")
        if not self.budget.workspace(plan.workspace_bytes):
            return unavailable_provider_payload("capture_workspace_exhausted")
        try:
            result = freeze_provider_payload(
                capture_provider_payload(**payload, _encoding_limit=plan.encoded_limit),
                workspace_bytes=plan.workspace_bytes,
            )

            # Event buffers consumed into this immutable snapshot no longer
            # retain a second copy of their raw capture bytes.
            def consumed(value: Any) -> None:
                if isinstance(value, CaptureEventBuffer):
                    value.clear()
                elif isinstance(value, dict):
                    for item in cast(dict[str, Any], value).values():
                        consumed(item)

            for value in payload.values():
                consumed(value)
            return result
        finally:
            self.budget.release_workspace(plan.workspace_bytes)

    def capture_request(self, **payload: Any) -> None:
        self.request = self._capture(**payload)
        self.adjust_reservation()

    def capture_response(self, **payload: Any) -> None:
        self.response = self._capture(**payload)
        self.adjust_reservation()

    def adjust_reservation(self) -> None:
        if self.reservation is not None:
            self.reservation.resize(
                METADATA_BYTES
                + sum(
                    len(side.encoded or b"")
                    for side in (self.request, self.response)
                    if side is not None
                )
                + sum(buffer.byte_count for buffer in self.buffers)
            )

    def __del__(self) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class RawProviderEvent:
    encoded: bytes
    event_name: str | None = None


@dataclass(frozen=True, slots=True)
class _Utf8Fragment:
    """Internal UTF-8 fragment: stream JSON escaping without wide whole strings."""

    raw: bytes

    def text_chunks(self) -> Iterator[str]:
        decoder = getincrementaldecoder("utf-8")(errors="replace")
        for offset in range(0, len(self.raw), 1024):
            end = offset + 1024
            yield decoder.decode(self.raw[offset:end], final=end >= len(self.raw))

    def __len__(self) -> int:
        return sum(len(chunk) for chunk in self.text_chunks())

    def __getitem__(self, index: slice) -> _Utf8Fragment:
        start, stop, step = index.indices(len(self))
        if step != 1:
            raise ValueError("capture_fragment_slice_invalid")
        result = bytearray()
        position = 0
        for chunk in self.text_chunks():
            end = position + len(chunk)
            if end > start and position < stop:
                result.extend(chunk[max(0, start - position) : stop - position].encode("utf-8"))
            position = end
            if position >= stop:
                break
        return _Utf8Fragment(bytes(result))


@dataclass(frozen=True, slots=True)
class _EncodingPlan:
    encoded_limit: int
    workspace_bytes: int


class _CountingOverflow(Exception):
    pass


class _CountingCapacityExceeded(Exception):
    pass


class _EncodingReservationExceeded(ValueError):
    pass


def _encoding_plan(value: Any, *, retained_limit: int | None = None) -> _EncodingPlan:
    """Count without copying trees, sorting keys, or encoding whole strings.

    The mutable ancestor set is bounded by 128 levels; unlike the encoder this
    inspector never retains a frozenset copy for every recursive level. At the
    ordinary side limit it stops counting and uses the existing worst-case
    allocation reservation. New retained owners may reject once the unchanged
    admission ceiling cannot fit; existing owners omit retained_limit. This
    never replaces the full sensitive scan for a payload that can be stored.
    """
    total = 0
    max_depth = 0
    max_sort_refs = 0
    ancestors: set[int] = set()
    # A side with at least MAX_CAPTURE_BYTES available retains the original
    # overflow policy. Below that, total + the original 1 KiB wrapper allowance
    # must fit. Rejecting this lower bound never drops an otherwise admitted side.
    capacity_ceiling = (
        retained_limit - 1024
        if retained_limit is not None and retained_limit < MAX_CAPTURE_BYTES
        else None
    )

    def check_capacity(minimum: int) -> None:
        if capacity_ceiling is not None and minimum > capacity_ceiling:
            raise _CountingCapacityExceeded

    def add(size: int) -> None:
        nonlocal total
        total += size
        check_capacity(total)
        if total > MAX_CAPTURE_BYTES:
            raise _CountingOverflow

    def string_size(text: str) -> None:
        # Every Unicode character occupies at least one UTF-8/JSON byte. This
        # O(1) lower bound avoids slicing a whole large source already rejected
        # by retained capacity; escaping and actual sizing still follow for
        # every potentially admissible string.
        check_capacity(total + len(text) + 2)
        add(2)
        for offset in range(0, len(text), 1024):
            piece = json.dumps(text[offset : offset + 1024], ensure_ascii=False)[1:-1]
            add(len(piece.encode("utf-8")))

    def visit(item: Any, *, depth: int = 0, sort_refs: int = 0) -> None:
        nonlocal max_depth, max_sort_refs
        if isinstance(item, RawProviderEvent):
            add(len(item.encoded))
        elif isinstance(item, _Utf8Fragment):
            add(2)
            for chunk in item.text_chunks():
                add(len(json.dumps(chunk, ensure_ascii=False)[1:-1].encode("utf-8")))
        elif isinstance(item, str):
            string_size(item)
        elif isinstance(item, (Mapping, list, tuple, CaptureEventBuffer)):
            identity = id(cast(object, item))
            if identity in ancestors or depth >= 128:
                raise ValueError("provider_payload_not_json")
            max_depth = max(max_depth, depth + 1)
            ancestors.add(identity)
            try:
                container = cast(Mapping[Any, Any] | Sequence[Any], item)
                add(2 + max(0, len(container) - 1))
                if isinstance(item, Mapping):
                    mapping = cast(Mapping[Any, Any], item)
                    nested_sort_refs = sort_refs + len(mapping) * SORT_KEY_REFERENCE_BYTES
                    max_sort_refs = max(max_sort_refs, nested_sort_refs)
                    for key in mapping:
                        if not isinstance(key, str):
                            raise TypeError("provider_payload_not_json")
                        string_size(key)
                        add(1)
                        visit(mapping[key], depth=depth + 1, sort_refs=nested_sort_refs)
                else:
                    for child in cast(Sequence[Any], item):
                        visit(child, depth=depth + 1, sort_refs=sort_refs)
            finally:
                ancestors.remove(identity)
        else:
            # JSON's ordinary numeric scalars stay small; oversized Python ints
            # use the existing worst-case path without first allocating str(int).
            if isinstance(item, int) and item.bit_length() > 16_384:
                raise _CountingOverflow
            add(len(json.dumps(item, allow_nan=False, separators=(",", ":")).encode("utf-8")))

    if isinstance(value, CapturedProviderPayload):
        value = (
            RawProviderEvent(value.encoded) if value.encoded is not None else value.encoding_payload
        )
    try:
        check_capacity(0)
        visit(value)
    except (_CountingOverflow, TypeError, ValueError, RecursionError):
        return _EncodingPlan(MAX_CAPTURE_BYTES, ENCODING_WORKSPACE_BYTES)
    # Schema/capture metadata and decimal length fields add well under 1 KiB.
    # Three byte ceilings cover bytearray/bytes overlap; 1.5 MiB covers bounded
    # recursion, key references, and transient 1024-character JSON pieces.
    ceiling = min(MAX_CAPTURE_BYTES, total + 1024)
    workspace = min(ENCODING_WORKSPACE_BYTES, SMALL_ENCODING_BASE_BYTES + 3 * ceiling)
    if max_depth > 32 or max_sort_refs > SORT_KEY_WORKSPACE_BYTES // 4:
        workspace = ENCODING_WORKSPACE_BYTES
    return _EncodingPlan(ceiling, workspace)


def _plan_with_budget(
    budget: CaptureBudget, payload: Any, *, retained_limit: int | None = None
) -> _EncodingPlan | None:
    if not budget.workspace(COUNTING_WORKSPACE_BYTES):
        return None
    try:
        return _encoding_plan(payload, retained_limit=retained_limit)
    finally:
        budget.release_workspace(COUNTING_WORKSPACE_BYTES)


def reserve_payload_encoding(budget: CaptureBudget, payload: Any) -> int | None:
    """Reserve finite inspection then encoding; caller releases returned bytes.

    Pass CapturedProviderPayload directly to avoid materializing legacy fragment
    strings. Already encoded payloads need no decode. No retained ownership moves.
    """
    plan = _plan_with_budget(budget, payload)
    if plan is None or not budget.workspace(plan.workspace_bytes):
        return None
    return plan.workspace_bytes


class CaptureEventBuffer(Sequence[RawProviderEvent]):
    """Capture-only events: reserve before encoding, retain bytes rather than JSON trees."""

    def __init__(self, capture: ProviderCallCapture) -> None:
        self.capture = capture
        self.items: list[RawProviderEvent] = []
        self.byte_count = 0

    def __len__(self) -> int:
        return len(self.items)

    @overload
    def __getitem__(self, index: int) -> RawProviderEvent: ...
    @overload
    def __getitem__(self, index: slice) -> list[RawProviderEvent]: ...
    def __getitem__(self, index: int | slice) -> RawProviderEvent | list[RawProviderEvent]:
        return self.items[index]

    def __setitem__(self, index: int, value: dict[str, Any]) -> None:
        old = self.items[index]
        del self.items[index]
        self.byte_count -= len(old.encoded)
        self.append(value)

    def append(self, value: dict[str, Any]) -> None:
        owner = self.capture
        reservation = owner.reservation
        budget = owner.budget
        if reservation is None or budget is None or not owner.can_capture:
            return
        try:
            plan = _plan_with_budget(budget, value, retained_limit=budget.available_retained_bytes)
        except _CountingCapacityExceeded:
            owner.error_code = "capture_event_capacity_exhausted"
            return
        if plan is None:
            owner.error_code = "capture_workspace_exhausted"
            return
        old_size = reservation.size
        if not reservation.resize(old_size + plan.encoded_limit):
            owner.error_code = "capture_event_capacity_exhausted"
            return
        if not budget.workspace(plan.workspace_bytes):
            reservation.resize(old_size)
            owner.error_code = "capture_workspace_exhausted"
            return
        try:
            if contains_sensitive(value):
                owner.error_code = "sensitive_content_detected"
                return
            try:
                serialized = _serialize_bounded(
                    value, sort_keys=True, allocation_limit=plan.encoded_limit
                )
            except _SortKeyBudgetExceeded:
                owner.error_code = "capture_sort_keys_exhausted"
                return
            except _EncodingReservationExceeded:
                owner.error_code = "capture_size_changed"
                return
            except (TypeError, ValueError, RecursionError):
                owner.error_code = "capture_event_not_json"
                return
            encoded = serialized.complete
            if encoded is None or self.byte_count + len(encoded) > MAX_CAPTURE_BYTES:
                owner.error_code = "capture_event_bytes_exhausted"
                return
            # Existing adapter policy retains first half and latest half.
            if len(self.items) >= 128:
                removed = self.items.pop(64)
                self.byte_count -= len(removed.encoded)
            header = value.get("header")
            event_name = (
                cast(dict[str, Any], header).get("event") if isinstance(header, dict) else None
            )
            self.items.append(
                RawProviderEvent(
                    encoded,
                    event_name if isinstance(event_name, str) and len(event_name) < 100 else None,
                )
            )
            self.byte_count += len(encoded)
        finally:
            budget.release_workspace(plan.workspace_bytes)
            owner.adjust_reservation()

    def clear(self) -> None:
        self.items.clear()
        self.byte_count = 0


def _capture_content(
    *,
    transport: str,
    url: str,
    headers: Mapping[str, str] | None = None,
    body: Any = None,
    events: list[dict[str, Any]] | None = None,
    binary_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "transport": transport.upper(),
        "url": _safe_url(url),
        "headers": _safe_headers(headers or {}),
        "body": body,
        "events": events or [],
        "binary_summary": binary_summary,
    }


def capture_provider_payload(
    *,
    transport: str,
    url: str,
    headers: Mapping[str, str] | None = None,
    body: Any = None,
    events: list[dict[str, Any]] | None = None,
    binary_summary: dict[str, Any] | None = None,
    _encoding_limit: int | None = None,
) -> CapturedProviderPayload:
    content = _capture_content(
        transport=transport,
        url=url,
        headers=headers,
        body=body,
        events=events,
        binary_summary=binary_summary,
    )
    if contains_sensitive(content):
        return _terminal_capture(
            content={},
            status="REJECTED_SENSITIVE",
            error_code="sensitive_content_detected",
        )
    try:
        serialized = _serialize_bounded(content, allocation_limit=_encoding_limit)
    except _EncodingReservationExceeded:
        return unavailable_provider_payload("capture_size_changed")
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
            _payload=payload,
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
            "head": _Utf8Fragment(serialized.head),
            "tail": _Utf8Fragment(serialized.tail),
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
        _payload=payload,
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


class _SortKeyBudgetExceeded(ValueError):
    pass


@dataclass(slots=True)
class _SizeObservation:
    """Local eligibility facts collected during an existing size traversal."""

    capture: dict[str, Any] | None = None
    capture_occurrences: int = 0
    stable: bool = True


_DICT_TYPE: type[dict[Any, Any]] = dict
_LIST_TYPE: type[list[Any]] = list
_TUPLE_TYPE: type[tuple[Any, ...]] = tuple


@dataclass(slots=True)
class _SortKeyBudget:
    used: int = 0

    def reserve(self, count: int) -> int:
        size = count * SORT_KEY_REFERENCE_BYTES
        if self.used + size > SORT_KEY_WORKSPACE_BYTES:
            raise _SortKeyBudgetExceeded
        self.used += size
        return size


class _TailBuffer:
    """A fixed byte ring, including when JSON yields millions of tiny chunks."""

    def __init__(self) -> None:
        self.storage = bytearray(FRAGMENT_BYTES)
        self.position = 0
        self.length = 0

    def append(self, data: bytes | memoryview) -> None:
        view = memoryview(data)
        if len(view) >= FRAGMENT_BYTES:
            self.storage[:] = view[-FRAGMENT_BYTES:]
            self.position = 0
            self.length = FRAGMENT_BYTES
            return
        first = min(len(view), FRAGMENT_BYTES - self.position)
        self.storage[self.position : self.position + first] = view[:first]
        rest = len(view) - first
        if rest:
            self.storage[:rest] = view[first:]
        self.position = (self.position + len(view)) % FRAGMENT_BYTES
        self.length = min(FRAGMENT_BYTES, self.length + len(view))

    def bytes(self) -> bytes:
        view = memoryview(self.storage)
        if self.length < FRAGMENT_BYTES:
            return bytes(view[: self.length])
        if self.position == 0:
            return bytes(view)
        result = bytearray(FRAGMENT_BYTES)
        first = FRAGMENT_BYTES - self.position
        result[:first] = view[self.position :]
        result[first:] = view[: self.position]
        return bytes(result)


def _serialize_bounded(
    value: Any, *, sort_keys: bool = False, allocation_limit: int | None = None
) -> _SerializedPayload:
    digest = hashlib.sha256()
    complete = bytearray()
    head = bytearray()
    tail: _TailBuffer | None = None
    total = 0
    overflowed = False
    for text in _json_chunks(value, sort_keys=sort_keys):
        chunk = text.encode("utf-8")
        digest.update(chunk)
        total += len(chunk)
        if (
            allocation_limit is not None
            and allocation_limit < MAX_CAPTURE_BYTES
            and total > allocation_limit
        ):
            raise _EncodingReservationExceeded("capture_size_changed")
        if not overflowed:
            if len(complete) + len(chunk) <= MAX_CAPTURE_BYTES:
                complete.extend(chunk)
                continue
            # Split only after crossing the cap; normal payloads retain a
            # single complete buffer, never three parallel copies.
            overflowed = True
            head.extend(memoryview(complete)[:FRAGMENT_BYTES])
            tail = _TailBuffer()
            tail.append(memoryview(complete)[-FRAGMENT_BYTES:])
            complete.clear()
        if len(head) < FRAGMENT_BYTES:
            head.extend(chunk[: FRAGMENT_BYTES - len(head)])
        assert tail is not None
        tail.append(chunk)
    return _SerializedPayload(
        complete=bytes(complete) if not overflowed else None,
        head=bytes(head),
        tail=tail.bytes() if tail is not None else b"",
        original_bytes=total,
        sha256=digest.hexdigest(),
    )


def _json_chunks(
    value: Any,
    *,
    ancestors: frozenset[int] = frozenset(),
    sort_keys: bool = False,
    sort_budget: _SortKeyBudget | None = None,
    size_observation: _SizeObservation | None = None,
) -> Iterator[str]:
    if size_observation is not None:
        if size_observation.capture is not None and value is size_observation.capture:
            size_observation.capture_occurrences += 1
        value_type = type(cast(object, value))
        if not (
            value_type is _DICT_TYPE
            or value_type is _LIST_TYPE
            or value_type is _TUPLE_TYPE
            or value_type is str
            or value_type is int
            or value_type is float
            or value_type is bool
            or value is None
        ):
            if value_type is _Utf8Fragment:
                if type(value.raw) is not bytes:
                    size_observation.stable = False
            elif value_type is RawProviderEvent:
                if type(value.encoded) is not bytes:
                    size_observation.stable = False
            else:
                # Custom containers/scalars can change their values on each
                # read. Observe without adding a preflight read or rejecting
                # input; retain the old repeated measurements for these trees.
                size_observation.stable = False
    if sort_keys and sort_budget is None:
        sort_budget = _SortKeyBudget()
    if isinstance(value, _Utf8Fragment):
        yield '"'
        for chunk in value.text_chunks():
            yield json.dumps(chunk, ensure_ascii=False)[1:-1]
        yield '"'
    elif isinstance(value, RawProviderEvent):
        decoder = getincrementaldecoder("utf-8")()
        for offset in range(0, len(value.encoded), 1024):
            end = offset + 1024
            yield decoder.decode(value.encoded[offset:end], final=end >= len(value.encoded))
    elif isinstance(value, str):
        yield '"'
        for offset in range(0, len(value), 1024):
            yield json.dumps(value[offset : offset + 1024], ensure_ascii=False)[1:-1]
        yield '"'
    elif isinstance(value, (Mapping, list, tuple, CaptureEventBuffer)):
        if id(cast(object, value)) in ancestors or len(ancestors) >= 128:
            raise ValueError("provider_payload_not_json")
        nested = ancestors | {id(cast(object, value))}
        if isinstance(value, Mapping):
            yield "{"
            mapping = cast(Mapping[Any, Any], value)
            reservation = 0
            try:
                if sort_keys:
                    assert sort_budget is not None
                    reservation = sort_budget.reserve(len(mapping))
                    keys = sorted(mapping)
                else:
                    keys = mapping
                for index, key in enumerate(keys):
                    if not isinstance(key, str):
                        raise TypeError("provider_payload_not_json")
                    if index:
                        yield ","
                    yield from _json_chunks(
                        key,
                        ancestors=nested,
                        sort_keys=sort_keys,
                        sort_budget=sort_budget,
                        size_observation=size_observation,
                    )
                    yield ":"
                    yield from _json_chunks(
                        mapping[key],
                        ancestors=nested,
                        sort_keys=sort_keys,
                        sort_budget=sort_budget,
                        size_observation=size_observation,
                    )
            finally:
                if sort_budget is not None:
                    sort_budget.used -= reservation
            yield "}"
        else:
            yield "["
            for index, item in enumerate(cast(list[Any] | tuple[Any, ...], value)):
                if index:
                    yield ","
                yield from _json_chunks(
                    item,
                    ancestors=nested,
                    sort_keys=sort_keys,
                    sort_budget=sort_budget,
                    size_observation=size_observation,
                )
            yield "]"
    else:
        yield json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _is_sensitive_key(key: str) -> bool:
    # Normalizing a huge ASCII-plus-emoji key can allocate multiple wide Unicode
    # copies. Inspect trim boundaries without copying, then normalize only the
    # finite number of characters which can equal a known sensitive key.
    start, end = 0, len(key)
    while start < end and key[start].isspace():
        start += 1
    while end > start and key[end - 1].isspace():
        end -= 1
    if end - start > _MAX_SENSITIVE_KEY_CHARS:
        return False
    return key[start:end].lower().replace("-", "_") in _SENSITIVE_KEYS


def contains_sensitive(value: Any, *, ancestors: frozenset[int] = frozenset()) -> bool:
    if isinstance(value, RawProviderEvent):
        return False  # Full sensitive scan happened before it was encoded.
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in _SENSITIVE_VALUES)
    if isinstance(value, (Mapping, list, tuple, CaptureEventBuffer)):
        if id(cast(object, value)) in ancestors:
            return False
        if len(ancestors) >= 128:
            return True  # Fail closed without constructing an unbounded traversal stack.
        nested = ancestors | {id(cast(object, value))}
        if isinstance(value, Mapping):
            for key, item in cast(Mapping[Any, Any], value).items():
                if (isinstance(key, str) and _is_sensitive_key(key)) or contains_sensitive(
                    key, ancestors=nested
                ):
                    return True
                if contains_sensitive(item, ancestors=nested):
                    return True
        else:
            for item in cast(list[Any] | tuple[Any, ...], value):
                if contains_sensitive(item, ancestors=nested):
                    return True
    return False


def freeze_provider_payload(
    value: CapturedProviderPayload, *, workspace_bytes: int | None = None
) -> CapturedProviderPayload:
    if value.encoded is not None:
        return value
    payload = value.encoding_payload
    # JSON fragments can expand when escaped a second time. Count bounded
    # chunks and shrink only this oversized wrapper before allocating bytes.
    size_observation = _SizeObservation()
    current_size = _json_size(payload, size_observation=size_observation)
    if current_size > MAX_CAPTURE_BYTES:
        fragments = payload.get("truncated_json")
        if not isinstance(fragments, dict):
            return freeze_provider_payload(
                unavailable_provider_payload("capture_wrapper_too_large")
            )
        fragments = cast(dict[str, str | _Utf8Fragment], fragments)
        minimum_payload = {
            **payload,
            "truncated_json": {**fragments, "head": "", "tail": ""},
        }
        if _json_size(minimum_payload) > MAX_CAPTURE_BYTES:
            return freeze_provider_payload(
                unavailable_provider_payload("capture_wrapper_too_large")
            )
        while (
            current_size if size_observation.stable else _json_size(payload)
        ) > MAX_CAPTURE_BYTES:
            previous_lengths = (len(fragments["head"]), len(fragments["tail"]))
            fragments["head"] = fragments["head"][: len(fragments["head"]) // 2]
            fragments["tail"] = fragments["tail"][len(fragments["tail"]) // 2 :]
            if previous_lengths == (len(fragments["head"]), len(fragments["tail"])):
                return freeze_provider_payload(
                    unavailable_provider_payload("capture_wrapper_too_large")
                )
            current_size = _apply_stored_bytes(payload)
    encoded_buffer = bytearray()
    allocation_limit = (
        (workspace_bytes - SMALL_ENCODING_BASE_BYTES) // 3
        if workspace_bytes is not None and workspace_bytes < ENCODING_WORKSPACE_BYTES
        else MAX_CAPTURE_BYTES
    )
    try:
        for chunk in _json_chunks(payload, sort_keys=True):
            encoded_chunk = chunk.encode("utf-8")
            if len(encoded_buffer) + len(encoded_chunk) > allocation_limit:
                return freeze_provider_payload(unavailable_provider_payload("capture_size_changed"))
            encoded_buffer.extend(encoded_chunk)
    except _SortKeyBudgetExceeded:
        return freeze_provider_payload(unavailable_provider_payload("capture_sort_keys_exhausted"))
    encoded = bytes(encoded_buffer)
    return replace(value, _payload=None, encoded=encoded, stored_bytes=len(encoded))


def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items() if key.lower() in _ALLOWED_HEADERS}


def _safe_url(url: str) -> dict[str, str | None]:
    parsed = urlsplit(url)
    host = parsed.hostname
    port = f":{parsed.port}" if parsed.port is not None else ""
    origin = f"{parsed.scheme}://{host}{port}" if parsed.scheme and host else None
    return {"origin": origin, "path": parsed.path or "/"}


def _json_size(value: Any, *, size_observation: _SizeObservation | None = None) -> int:
    return sum(
        len(chunk.encode("utf-8"))
        for chunk in _json_chunks(value, size_observation=size_observation)
    )


def _apply_stored_bytes(payload: dict[str, Any]) -> int:
    capture = cast(dict[str, Any], payload["capture"])
    size_observation = _SizeObservation(capture=capture)
    previous = -1
    current = _json_size(payload, size_observation=size_observation)
    stored = capture.get("stored_bytes") if type(capture) is dict else None
    if (
        type(payload) is dict
        and type(capture) is dict
        and type(stored) is int
        and stored >= 0
        and size_observation.stable
        and size_observation.capture_occurrences == 1
    ):
        # Exactly one ordinary scalar changes, so every subsequent full size
        # differs only by its decimal width. Keep the original first size and
        # assignment sequence: adjacent decimal fixed points are not equivalent.
        base = current - len(str(stored))
        while current != previous:
            previous = current
            capture["stored_bytes"] = current
            current = base + len(str(current))
        return current
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

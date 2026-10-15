"""Encoding workspace bounds, including high-cardinality JSON and tiny chunks.

Original synthetic business objects are built before tracing: assertions measure
only additional research-capture allocations, including serialization and freeze.
"""

from __future__ import annotations

import hashlib
import json
import tracemalloc
from collections.abc import Callable
from typing import Literal

import pytest

import jx_core.data_capture.provider as module
from jx_core.data_capture.content import canonical_payload_bytes
from jx_core.data_capture.provider import ProviderCallCapture, capture_provider_payload
from jx_core.data_capture.provider_budget import (
    ENCODING_WORKSPACE_BYTES,
    METADATA_BYTES,
    RETAINED_BYTES,
    WORKSPACE_BYTES,
    CaptureBudget,
)

# The ring and recursive sorting budget are deliberate white-box safety probes.
# pyright: reportPrivateUsage=false


def budgeted_capture() -> tuple[ProviderCallCapture, CaptureBudget]:
    budget = CaptureBudget()
    reservation = budget.reserve(METADATA_BYTES)
    assert reservation is not None
    return ProviderCallCapture(budget=budget, reservation=reservation, budgeted=True), budget


def peak_allocation(operation: Callable[[], None]) -> int:
    assert not tracemalloc.is_tracing()
    tracemalloc.start()
    try:
        operation()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def assert_budget_reusable(budget: CaptureBudget) -> None:
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0
    lease = budget.reserve(METADATA_BYTES)
    assert lease is not None
    lease.release()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


@pytest.mark.parametrize("path", ["event", "response"])
def test_high_cardinality_sorting_rejects_before_copy_and_within_workspace(
    path: Literal["event", "response"],
) -> None:
    # The source still fits the ordinary per-side JSON byte ceiling. Refusal is
    # specifically the finite key-reference workspace, not arbitrary truncation.
    count = module.SORT_KEY_WORKSPACE_BYTES // module.SORT_KEY_REFERENCE_BYTES + 1
    original = {str(index): 0 for index in range(count)}
    assert len(canonical_payload_bytes(original)) < module.MAX_CAPTURE_BYTES
    capture, budget = budgeted_capture()
    events = capture.event_buffer()

    def encode() -> None:
        if path == "event":
            events.append(original)
            capture.capture_response(transport="ws", url="wss://synthetic.invalid", events=events)
            # Gap metadata takes the small legacy-compatible branch. Include
            # the writer's eventual freeze in this allocation measurement too.
            assert capture.response is not None
            capture.response = module.freeze_provider_payload(capture.response)
            capture.adjust_reservation()
        else:
            capture.capture_response(
                transport="http", url="https://synthetic.invalid", body=original
            )

    try:
        peak = peak_allocation(encode)
        assert peak <= ENCODING_WORKSPACE_BYTES
        assert capture.response is not None and capture.response.status == "UNAVAILABLE"
        assert capture.response.payload["capture"]["error_code"] == "capture_sort_keys_exhausted"
        assert not events and capture.response.encoded is not None
        assert budget.high_water_bytes <= RETAINED_BYTES + WORKSPACE_BYTES
    finally:
        capture.close()
    assert_budget_reusable(budget)


@pytest.mark.parametrize("path", ["event", "response"])
def test_sorting_workspace_refusal_does_not_skip_sensitive_tail_scan(
    path: Literal["event", "response"],
) -> None:
    count = module.SORT_KEY_WORKSPACE_BYTES // module.SORT_KEY_REFERENCE_BYTES + 1
    original: dict[str, int | str] = {str(index): 0 for index in range(count)}
    original["password"] = "synthetic-must-not-be-retained"
    capture, budget = budgeted_capture()
    events = capture.event_buffer()
    try:
        if path == "event":
            events.append(original)
            capture.capture_response(transport="ws", url="wss://synthetic.invalid", events=events)
        else:
            capture.capture_response(
                transport="http", url="https://synthetic.invalid", body=original
            )
        assert capture.response is not None and capture.response.status == "REJECTED_SENSITIVE"
        frozen = module.freeze_provider_payload(capture.response)
        assert frozen.encoded is not None
        assert b"synthetic-must-not-be-retained" not in frozen.encoded
        assert not events
    finally:
        capture.close()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


def test_freeze_many_tiny_json_chunks_keeps_complete_data_within_workspace() -> None:
    # Each zero and comma is a separate generator chunk. Joining the generator
    # first used to retain hundreds of thousands of independent bytes objects.
    original = [0] * 200_000
    capture, budget = budgeted_capture()
    try:
        peak = peak_allocation(
            lambda: capture.capture_response(
                transport="http", url="https://synthetic.invalid", body=original
            )
        )
        assert peak <= ENCODING_WORKSPACE_BYTES
        assert capture.response is not None and capture.response.status == "COMPLETE"
        assert capture.response.payload["body"] == original
        assert capture.response.encoded is not None
        assert capture.response.encoded == canonical_payload_bytes(capture.response.payload)
        assert budget.high_water_bytes <= RETAINED_BYTES + WORKSPACE_BYTES
    finally:
        capture.close()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


def test_nested_event_utf8_decode_does_not_expand_entire_payload_to_wide_unicode() -> None:
    # One emoji makes a whole-string UTF-8 decode select four-byte Unicode for
    # almost two million ASCII characters. Incremental decoding keeps that
    # expansion confined to the current 1024-byte block.
    original = {"text": "a" * 1_900_000 + "🙂"}
    capture, budget = budgeted_capture()
    events = capture.event_buffer()

    def encode() -> None:
        events.append(original)
        capture.capture_response(transport="ws", url="wss://synthetic.invalid", events=events)

    try:
        peak = peak_allocation(encode)
        assert peak <= ENCODING_WORKSPACE_BYTES
        assert capture.response is not None and capture.response.status == "COMPLETE"
        assert capture.response.payload["events"] == [original]
        assert not events and budget.workspace_bytes == 0
        assert budget.high_water_bytes <= RETAINED_BYTES + WORKSPACE_BYTES
    finally:
        capture.close()
    assert_budget_reusable(budget)


def test_overflow_many_tiny_json_chunks_keeps_fixed_tail_and_freeze_workspace() -> None:
    # Actual 2 MiB crossing, with millions of one-character JSON chunks. A
    # byte-limited deque used to retain tens of MiB of node/bytes overhead here.
    original = [0] * (module.MAX_CAPTURE_BYTES // 2 + 10)
    capture, budget = budgeted_capture()
    try:
        peak = peak_allocation(
            lambda: capture.capture_response(
                transport="http", url="https://synthetic.invalid", body=original
            )
        )
        assert peak <= ENCODING_WORKSPACE_BYTES
        assert capture.response is not None and capture.response.status == "TRUNCATED"
        assert capture.response.encoded is not None
        assert len(capture.response.encoded) <= module.MAX_CAPTURE_BYTES
        fragments = capture.response.payload["truncated_json"]
        assert fragments["head"] and fragments["tail"]
        assert budget.high_water_bytes <= RETAINED_BYTES + WORKSPACE_BYTES
    finally:
        capture.close()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


def test_nested_sorting_uses_one_shared_key_reference_budget() -> None:
    count = module.SORT_KEY_WORKSPACE_BYTES // module.SORT_KEY_REFERENCE_BYTES // 2 + 1
    child = {str(index): 0 for index in range(count)}
    original: dict[str, object] = {str(index): 0 for index in range(count)}
    original[""] = child  # Nested sort happens while the outer keys are retained.
    assert len(canonical_payload_bytes(original)) < module.MAX_CAPTURE_BYTES
    capture, budget = budgeted_capture()
    try:
        capture.capture_response(transport="http", url="https://synthetic.invalid", body=original)
        assert capture.response is not None and capture.response.status == "UNAVAILABLE"
        assert capture.response.payload["capture"]["error_code"] == "capture_sort_keys_exhausted"
    finally:
        capture.close()
    assert_budget_reusable(budget)


@pytest.mark.parametrize("fault", ["type", "cycle", "number"])
def test_event_serialization_errors_remain_optional_and_release_budget(
    fault: Literal["type", "cycle", "number"],
) -> None:
    original: dict[str, object] = {}
    if fault == "type":
        original["payload"] = object()
    elif fault == "cycle":
        original["self"] = original
    else:
        original["number"] = 10**5_000
    capture, budget = budgeted_capture()
    events = capture.event_buffer()
    try:
        events.append(original)  # No exception may escape into the Provider stream.
        assert not events and capture.error_code == "capture_event_not_json"
        assert budget.workspace_bytes == 0
        capture.capture_response(transport="ws", url="wss://synthetic.invalid", events=events)
        assert capture.response is not None and capture.response.status == "UNAVAILABLE"
        frozen = module.freeze_provider_payload(capture.response)
        assert frozen.encoded is not None and len(frozen.encoded) < METADATA_BYTES
        assert frozen.payload["capture"]["error_code"] == "capture_event_not_json"
    finally:
        capture.close()
    assert_budget_reusable(budget)


@pytest.mark.parametrize("path", ["request", "response", "event"])
def test_synthetic_dotted_provider_key_is_rejected_without_sensitive_key_name(
    path: Literal["request", "response", "event"],
) -> None:
    synthetic_key = "sk-ws-H.abcdefghijk.abcdef"
    capture, budget = budgeted_capture()
    try:
        if path == "request":
            capture.capture_request(
                transport="http",
                url="https://synthetic.invalid",
                body={"hint": synthetic_key},
            )
            captured = capture.request
        elif path == "response":
            capture.capture_response(
                transport="http",
                url="https://synthetic.invalid",
                body={"hint": synthetic_key},
            )
            captured = capture.response
        else:
            events = capture.event_buffer()
            events.append({"hint": synthetic_key})
            capture.capture_response(transport="ws", url="wss://synthetic.invalid", events=events)
            captured = capture.response
            assert not events
        assert captured is not None and captured.status == "REJECTED_SENSITIVE"
        frozen = module.freeze_provider_payload(captured)
        assert frozen.encoded is not None and synthetic_key.encode() not in frozen.encoded
    finally:
        capture.close()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


def test_ring_preserves_exact_fragments_original_length_and_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "MAX_CAPTURE_BYTES", 128)
    monkeypatch.setattr(module, "FRAGMENT_BYTES", 32)
    original = {"text": '甲🙂\\"' * 50}
    raw = json.dumps(original, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    serialized = module._serialize_bounded(original)
    assert serialized.complete is None
    assert serialized.head == raw[:32]
    assert serialized.tail == raw[-32:]
    assert serialized.original_bytes == len(raw)
    assert serialized.sha256 == hashlib.sha256(raw).hexdigest()


def test_normal_canonical_raw_capture_schema_and_hash_are_preserved() -> None:
    captured = capture_provider_payload(
        transport="http",
        url="https://synthetic.invalid/path",
        headers={"content-type": "application/json", "x-request-id": "synthetic-id"},
        body={"z": ["甲", "🙂", "\\"], "a": {"y": 2, "x": 1}},
    )
    before = canonical_payload_bytes(captured.payload)
    frozen = module.freeze_provider_payload(captured)
    assert frozen.encoded == before
    assert frozen.status == "COMPLETE"
    assert frozen.sha256 == captured.sha256
    assert frozen.original_bytes == captured.original_bytes
    assert frozen.payload["capture_schema_version"] == module.PROVIDER_CAPTURE_VERSION

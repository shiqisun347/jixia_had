"""Real finite encoder reservations: normal concurrent sizes and hostile overflow.

These are pure memory/encoding tests, not provider calls or a database load test.
"""

from __future__ import annotations

import json
import signal
import tracemalloc
from collections.abc import Callable, Iterator, Mapping
from typing import Any, SupportsIndex

import pytest

import jx_core.data_capture.provider as module
from jx_core.data_capture.provider import MAX_CAPTURE_BYTES, ProviderCallCapture
from jx_core.data_capture.provider_budget import (
    COUNTING_WORKSPACE_BYTES,
    ENCODING_WORKSPACE_BYTES,
    METADATA_BYTES,
    RETAINED_BYTES,
    WORKSPACE_BYTES,
    CaptureBudget,
)

# Size/shape and character-halving checks intentionally inspect private helpers.
# pyright: reportPrivateUsage=false


def captured(budget: CaptureBudget) -> ProviderCallCapture:
    reservation = budget.reserve(METADATA_BYTES)
    assert reservation is not None
    return ProviderCallCapture(budget=budget, reservation=reservation, budgeted=True)


class ReadObservedText(str):
    """Count actual source slices without replacing capture's finite encoder."""

    def __init__(self, value: str) -> None:
        self.sliced_characters = 0

    def __getitem__(self, index: SupportsIndex | slice) -> str:
        result = super().__getitem__(index)
        if isinstance(index, slice):
            self.sliced_characters += len(result)
        return result


def run_capture_entry(capture: ProviderCallCapture, entry: str, body: Any) -> None:
    if entry == "event":
        events = capture.event_buffer()
        events.append(body)
        assert not events
    else:
        method = capture.capture_request if entry == "request" else capture.capture_response
        method(transport="HTTP", url="https://synthetic.invalid", body=body)


def assert_capacity_gap(capture: ProviderCallCapture, entry: str) -> None:
    if entry == "event":
        assert capture.error_code == "capture_event_capacity_exhausted"
    else:
        result = capture.request if entry == "request" else capture.response
        assert result is not None and result.status == "UNAVAILABLE"
        assert result.payload["capture"]["error_code"] == "capture_capacity_exhausted"


@pytest.mark.parametrize("entry", ("request", "response", "event"))
def test_refused_large_ascii_source_is_not_sliced_when_retained_cannot_fit(entry: str) -> None:
    text = ReadObservedText("a" * (MAX_CAPTURE_BYTES + 64))
    budget = CaptureBudget()
    capture = captured(budget)
    remaining = 16 * 1024
    other = budget.reserve(RETAINED_BYTES - budget.retained_bytes - remaining)
    assert other is not None
    retained_before = budget.retained_bytes
    try:
        run_capture_entry(capture, entry, {"text": text})
        assert_capacity_gap(capture, entry)
        assert text.sliced_characters == 0
        assert budget.retained_bytes == retained_before and budget.workspace_bytes == 0
    finally:
        capture.close()
        other.release()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


@pytest.mark.parametrize("entry", ("request", "response", "event"))
def test_refused_combination_stops_source_reads_at_remaining_retained_ceiling(entry: str) -> None:
    # Each string fits; only their combination exceeds available retained. ASCII
    # makes source character reads equal UTF-8 bytes without tracing internals.
    parts = [ReadObservedText("a" * 1024) for _ in range(128)]
    budget = CaptureBudget()
    capture = captured(budget)
    remaining = 16 * 1024
    other = budget.reserve(RETAINED_BYTES - budget.retained_bytes - remaining)
    assert other is not None
    retained_before = budget.retained_bytes
    try:
        run_capture_entry(capture, entry, {"parts": parts})
        assert_capacity_gap(capture, entry)
        assert 0 < sum(part.sliced_characters for part in parts) <= remaining + 1024
        assert budget.retained_bytes == retained_before and budget.workspace_bytes == 0
    finally:
        capture.close()
        other.release()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


@pytest.mark.parametrize("entry", ("request", "response", "event"))
def test_admissible_raw_still_scans_sensitive_value_at_end(entry: str) -> None:
    budget = CaptureBudget()
    capture = captured(budget)
    remaining = 64 * 1024
    other = budget.reserve(RETAINED_BYTES - budget.retained_bytes - remaining)
    assert other is not None
    body = {"ordinary": "a" * (16 * 1024), "last": "Bearer synthetic-test-token-000000"}
    try:
        run_capture_entry(capture, entry, body)
        if entry == "event":
            assert capture.error_code == "sensitive_content_detected"
        else:
            result = capture.request if entry == "request" else capture.response
            assert result is not None and result.status == "REJECTED_SENSITIVE"
            assert result.encoded is not None
            assert b"synthetic-test-token-000000" not in result.encoded
        assert budget.workspace_bytes == 0
    finally:
        capture.close()
        other.release()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


def test_legacy_freeze_with_owned_retained_uses_workspace_only_at_full_pool() -> None:
    budget = CaptureBudget()
    owned = budget.reserve(4 * 1024 * 1024)
    other = budget.reserve(4 * 1024 * 1024)
    assert owned is not None and other is not None
    legacy = module.capture_provider_payload(
        transport="HTTP", url="https://synthetic.invalid", body={"text": "legacy complete"}
    )
    assert legacy.status == "COMPLETE" and legacy.encoded is None
    workspace = module.reserve_payload_encoding(budget, legacy)
    try:
        assert workspace is not None
        frozen = module.freeze_provider_payload(legacy, workspace_bytes=workspace)
        assert frozen.status == "COMPLETE" and frozen.encoded is not None
        assert frozen.payload["body"] == {"text": "legacy complete"}
        assert budget.retained_bytes == RETAINED_BYTES
    finally:
        if workspace is not None:
            budget.release_workspace(workspace)
        owned.release()
        other.release()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


def test_fifty_paper_requests_and_fixed_free_responses_keep_complete_pairs() -> None:
    budget = CaptureBudget()
    captures: list[ProviderCallCapture] = []
    history = "甲" * 24_000
    try:
        for _ in range(50):
            capture = captured(budget)
            captures.append(capture)
            capture.capture_request(
                transport="SSE",
                url="https://synthetic.invalid/v1/chat/completions",
                body={"messages": [{"role": "user", "content": history}]},
            )
        for index, capture in enumerate(captures):
            text = "乙" * (306 if index % 2 == 0 else 102)
            events = capture.event_buffer()
            events.append({"choices": [{"delta": {"content": text}}]})
            events.append({"usage": {"completion_tokens": len(text)}})
            capture.capture_response(
                transport="SSE",
                url="https://synthetic.invalid/v1/chat/completions",
                body={"text": text, "completion_tokens": len(text)},
                events=events,
            )
        assert len(captures) == 50
        assert all(
            capture.request is not None
            and capture.response is not None
            and capture.request.status == capture.response.status == "COMPLETE"
            for capture in captures
        )
        assert budget.items == 50 and budget.retained_bytes < RETAINED_BYTES
        assert budget.workspace_bytes == 0
        for index, capture in enumerate(captures):
            assert capture.request is not None and capture.response is not None
            assert capture.request.payload["body"]["messages"][0]["content"] == history
            assert capture.response.payload["body"]["text"] == "乙" * (
                306 if index % 2 == 0 else 102
            )
            assert not capture.buffers[0]
    finally:
        for capture in captures:
            capture.close()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


def nested_source(levels: int = 120) -> object:
    value: object = "末尾"
    for _ in range(levels):
        value = [value]
    return value


@pytest.mark.parametrize(
    "source_factory",
    (
        lambda: {"text": "短文本🙂"},
        lambda: {str(index): index for index in range(8_000)},
        lambda: [0] * 50_000,
        nested_source,
        lambda: {"text": "a" * 1_900_000 + "🙂"},
        lambda: {"text": "\x00" * 350_000},
    ),
    ids=("tiny", "many-keys", "tiny-chunks", "deep", "wide-unicode", "escaped-overflow"),
)
def test_actual_extra_peak_stays_below_adaptive_workspace_for_different_shapes(
    source_factory: Callable[[], Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = source_factory()
    budget = CaptureBudget()
    capture = captured(budget)
    requests: list[int] = []
    original_workspace = budget.workspace

    def tracked_workspace(size: int) -> bool:
        requests.append(size)
        return original_workspace(size)

    monkeypatch.setattr(budget, "workspace", tracked_workspace)
    tracemalloc.start()
    try:
        capture.capture_response(transport="HTTP", url="https://synthetic.invalid", body=original)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    try:
        assert capture.response is not None
        assert capture.response.status in {"COMPLETE", "TRUNCATED"}
        assert requests and peak <= max(requests) <= ENCODING_WORKSPACE_BYTES
        assert budget.workspace_bytes == 0
    finally:
        capture.close()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


def test_deep_counting_uses_bounded_mutable_ancestors_before_any_sorting() -> None:
    # Only the inspection itself is traced. The later encoder has the original
    # 7 MiB conservative reservation for a deep recursive shape.
    original = nested_source(127)
    budget = CaptureBudget()
    tracemalloc.start()
    try:
        plan = module._plan_with_budget(budget, original)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert plan is not None and plan.workspace_bytes == ENCODING_WORKSPACE_BYTES
    assert peak < COUNTING_WORKSPACE_BYTES
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


@pytest.mark.parametrize("text", ("甲🙂" * 700_000, '"\x00🙂' * 500_000))
def test_streamed_fragment_json_preserves_original_utf8_replacement_and_character_halving(
    text: str,
) -> None:
    value = {"text": text}
    # Legacy output remains normal public JSON. Its first/last fragments and
    # post-escape halving must match the original decoded-character algorithm.
    capture = module.capture_provider_payload(
        transport="HTTP", url="https://synthetic.invalid", body=value
    )
    assert capture.status == "TRUNCATED"
    expected = capture.payload
    assert expected["truncated_json"]["head"] and expected["truncated_json"]["tail"]
    while module._json_size(expected) > MAX_CAPTURE_BYTES:
        fragments = expected["truncated_json"]
        fragments["head"] = fragments["head"][: len(fragments["head"]) // 2]
        fragments["tail"] = fragments["tail"][len(fragments["tail"]) // 2 :]
        module._apply_stored_bytes(expected)
    frozen = module.freeze_provider_payload(capture)
    assert frozen.encoded == json.dumps(
        expected, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert frozen.payload == expected


def test_source_growth_after_count_cannot_outgrow_its_finite_reservation() -> None:
    class ChangingMapping(Mapping[str, str]):
        def __init__(self) -> None:
            self.reads = 0
            self.large = "a" * MAX_CAPTURE_BYTES

        def __getitem__(self, key: str) -> str:
            assert key == "text"
            self.reads += 1
            return "small" if self.reads == 1 else self.large

        def __iter__(self) -> Iterator[str]:
            yield "text"

        def __len__(self) -> int:
            return 1

    original = ChangingMapping()
    budget = CaptureBudget()
    capture = captured(budget)
    try:
        capture.capture_response(transport="HTTP", url="https://synthetic.invalid", body=original)
        assert original.reads > 1
        assert capture.response is not None and capture.response.status == "UNAVAILABLE"
        assert capture.response.payload["capture"]["error_code"] == "capture_size_changed"
        assert budget.workspace_bytes == 0 and budget.retained_bytes < METADATA_BYTES + 1024
    finally:
        capture.close()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


@pytest.mark.parametrize("metadata_field", ("headers", "binary_summary"))
def test_oversized_wrapper_metadata_returns_gap_instead_of_nonprogressing_halving(
    metadata_field: str,
) -> None:
    original = {"text": "a" * (MAX_CAPTURE_BYTES + 1024)}
    metadata = "b" * (MAX_CAPTURE_BYTES + 1024)
    kwargs = (
        {"headers": {"content-type": metadata}}
        if metadata_field == "headers"
        else {"binary_summary": {"description": metadata}}
    )
    budget = CaptureBudget()
    capture = captured(budget)

    def timeout(_number: int, _frame: object) -> None:
        raise TimeoutError("fragment halving must make progress within five seconds")

    previous_handler = signal.signal(signal.SIGALRM, timeout)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 5)
    tracemalloc.start()
    try:
        capture.capture_response(
            transport="HTTP", url="https://synthetic.invalid", body=original, **kwargs
        )
        peak = tracemalloc.get_traced_memory()[1]
        assert capture.response is not None and capture.response.status == "UNAVAILABLE"
        assert capture.response.payload["capture"]["error_code"] == "capture_wrapper_too_large"
        assert peak <= ENCODING_WORKSPACE_BYTES
        assert budget.workspace_bytes == 0
    finally:
        tracemalloc.stop()
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)
        capture.close()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


@pytest.mark.parametrize("sensitive", (False, True), ids=("wide-key", "whitespace-password"))
def test_sensitive_key_detection_does_not_copy_huge_unicode_key(sensitive: bool) -> None:
    key = (
        " " * MAX_CAPTURE_BYTES + "password" + " " * MAX_CAPTURE_BYTES
        if sensitive
        else "a" * (MAX_CAPTURE_BYTES + 64) + "🙂"
    )
    original = {key: "synthetic-sensitive-field-value" if sensitive else "ordinary"}
    budget = CaptureBudget()
    capture = captured(budget)
    tracemalloc.start()
    try:
        capture.capture_response(transport="HTTP", url="https://synthetic.invalid", body=original)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    try:
        assert capture.response is not None
        assert capture.response.status == ("REJECTED_SENSITIVE" if sensitive else "TRUNCATED")
        assert capture.response.encoded is not None
        if sensitive:
            assert b"synthetic-sensitive-field-value" not in capture.response.encoded
        assert peak <= ENCODING_WORKSPACE_BYTES
    finally:
        capture.close()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


def test_tiny_event_at_six_point_three_mib_retained_does_not_need_two_mib() -> None:
    budget = CaptureBudget()
    other = budget.reserve(int(6.3 * 1024 * 1024))
    assert other is not None
    capture = captured(budget)
    try:
        events = capture.event_buffer()
        event = {"choices": [{"delta": {"content": "有限事件"}}]}
        events.append(event)
        assert len(events) == 1 and capture.error_code is None
        capture.capture_response(transport="SSE", url="https://synthetic.invalid", events=events)
        assert capture.response is not None and capture.response.status == "COMPLETE"
        assert capture.response.payload["events"] == [event]
        assert budget.retained_bytes < RETAINED_BYTES and budget.workspace_bytes == 0
    finally:
        capture.close()
        other.release()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


def test_small_payload_can_coexist_with_more_than_one_mib_native_workspace() -> None:
    budget = CaptureBudget()
    native_workspace = 1280 * 1024
    assert budget.workspace(native_workspace)
    capture = captured(budget)
    try:
        for method in (capture.capture_request, capture.capture_response):
            method(transport="HTTP", url="https://synthetic.invalid", body={"text": "小请求"})
        assert capture.request is not None and capture.response is not None
        assert capture.request.status == capture.response.status == "COMPLETE"
        assert budget.workspace_bytes == native_workspace
    finally:
        capture.close()
        budget.release_workspace(native_workspace)
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


def test_real_workspace_exhaustion_still_rejects_without_waiting() -> None:
    budget = CaptureBudget()
    assert budget.workspace(WORKSPACE_BYTES)
    capture = captured(budget)
    try:
        capture.capture_request(
            transport="HTTP", url="https://synthetic.invalid", body={"text": "有限请求"}
        )
        assert capture.request is not None and capture.request.status == "UNAVAILABLE"
        assert capture.request.payload["capture"]["error_code"] == "capture_workspace_exhausted"
        assert budget.workspace_bytes == WORKSPACE_BYTES
        assert budget.retained_bytes == METADATA_BYTES
    finally:
        capture.close()
        budget.release_workspace(WORKSPACE_BYTES)
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0


def test_emoji_ascii_overflow_does_not_allocate_two_wide_unicode_fragments() -> None:
    original = {"text": "🙂" + "a" * (MAX_CAPTURE_BYTES + 64) + "🙂"}
    budget = CaptureBudget()
    capture = captured(budget)
    tracemalloc.start()
    try:
        capture.capture_response(transport="HTTP", url="https://synthetic.invalid", body=original)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    try:
        assert capture.response is not None and capture.response.status == "TRUNCATED"
        assert capture.response.encoded is not None
        assert len(capture.response.encoded) <= MAX_CAPTURE_BYTES
        fragments = capture.response.payload["truncated_json"]
        assert fragments["head"].startswith('{"transport"')
        assert "🙂" in fragments["head"] and "🙂" in fragments["tail"]
        assert peak <= ENCODING_WORKSPACE_BYTES
        assert budget.high_water_bytes <= RETAINED_BYTES + WORKSPACE_BYTES
    finally:
        capture.close()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0

"""Independent JSON oracles and actual encoding-read budgets for Spec 300.

These are pure synthetic capture tests. They do not load settings, open a
database, call a provider, or replace a product size helper with a mock.
"""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import hashlib
import json
from abc import ABCMeta
from collections.abc import Iterator, Mapping
from copy import deepcopy
from typing import Any, SupportsIndex, cast

import pytest

import jx_core.data_capture.provider as module
from jx_core.data_capture.provider import CapturedProviderPayload, ProviderCallCapture
from jx_core.data_capture.provider_budget import METADATA_BYTES, CaptureBudget

_STANDARD_DUMPS = json.dumps


def compact_bytes(value: Any, *, sorted_keys: bool = False) -> bytes:
    """The independent standard JSON oracle never uses product encoding helpers."""

    def mapping_json(item: Any) -> dict[str, Any]:
        if isinstance(item, Mapping):
            return dict(cast(Mapping[str, Any], item))
        raise TypeError("synthetic_non_json_input")

    return _STANDARD_DUMPS(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=sorted_keys,
        default=mapping_json,
    ).encode("utf-8")


def old_apply_oracle(payload: dict[str, Any]) -> tuple[int, list[int]]:
    """Original assignment order, with full independent JSON on every round."""
    previous = -1
    current = len(compact_bytes(payload))
    measurements = [current]
    while current != previous:
        previous = current
        payload["capture"]["stored_bytes"] = current
        current = len(compact_bytes(payload))
        measurements.append(current)
    return current, measurements


def public_content(body: Any) -> dict[str, Any]:
    return {
        "transport": "HTTP",
        "url": {"origin": "https://synthetic.invalid", "path": "/result"},
        "headers": {},
        "body": body,
        "events": [],
        "binary_summary": None,
    }


def padded_payload(initial: Any, initial_encoded_size: int) -> dict[str, Any]:
    payload: dict[str, Any] = {"capture": {"stored_bytes": initial}, "text": ""}
    count = initial_encoded_size - len(compact_bytes(payload))
    assert count >= 0
    payload["text"] = "p" * count
    assert len(compact_bytes(payload)) == initial_encoded_size
    return payload


@pytest.mark.parametrize("initial_size", [98, 99, 998, 999, 9998, 9999])
def test_decimal_boundaries_preserve_original_assignment_sequence_and_json(
    initial_size: int,
) -> None:
    actual = padded_payload(0, initial_size)
    expected = deepcopy(actual)
    expected_size, _ = old_apply_oracle(expected)
    assert module._apply_stored_bytes(actual) == expected_size
    assert actual == expected and compact_bytes(actual) == compact_bytes(expected)


@pytest.mark.parametrize("initial, final_size", [(0, 99), (100, 100)])
def test_adjacent_fixed_points_keep_original_initial_value(initial: int, final_size: int) -> None:
    actual = padded_payload(0, 98)  # 97 non-digit bytes; both 99 and 100 are fixed points.
    actual["capture"]["stored_bytes"] = initial
    expected = deepcopy(actual)
    expected_size, steps = old_apply_oracle(expected)
    assert steps == ([98, 99, 99] if initial == 0 else [100, 100])
    assert expected_size == final_size
    assert module._apply_stored_bytes(actual) == final_size
    assert compact_bytes(actual) == compact_bytes(expected)


def observed_plain_characters(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any], text: str
) -> tuple[int, int]:
    reads = 0

    def dumps(value: Any, *args: Any, **kwargs: Any) -> str:
        nonlocal reads
        if type(value) is str and value == text:
            reads += len(value)
        return _STANDARD_DUMPS(value, *args, **kwargs)

    with monkeypatch.context() as observe:
        observe.setattr(json, "dumps", dumps)
        result = module._apply_stored_bytes(payload)
    return result, reads


@pytest.mark.parametrize("field", [True, -1, 1.5, "stale", None, "missing"])
def test_nonordinary_or_missing_stored_bytes_retains_full_measurement_fallback(
    monkeypatch: pytest.MonkeyPatch,
    field: Any,
) -> None:
    text = "Q" * 32
    actual: dict[str, Any] = {"body": {"text": text}, "capture": {}}
    if field != "missing":
        actual["capture"]["stored_bytes"] = field
    expected = deepcopy(actual)
    size, steps = old_apply_oracle(expected)
    actual_size, characters = observed_plain_characters(monkeypatch, actual, text)
    assert actual_size == size and compact_bytes(actual) == compact_bytes(expected)
    assert characters == len(text) * len(steps)


def test_aliased_capture_metadata_retains_full_measurement_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "Q" * 128
    metadata = {"stored_bytes": 0}
    actual = {"body": {"text": text, "alias": metadata}, "capture": metadata}
    expected = deepcopy(actual)
    assert expected["body"]["alias"] is expected["capture"]
    size, steps = old_apply_oracle(expected)
    actual_size, characters = observed_plain_characters(monkeypatch, actual, text)
    assert actual["body"]["alias"] is actual["capture"]
    assert actual_size == size and compact_bytes(actual) == compact_bytes(expected)
    assert characters == len(text) * len(steps)


class ScriptedMapping(Mapping[str, str]):
    def __init__(self) -> None:
        self.reads = 0

    def __iter__(self) -> Iterator[str]:
        return iter(("text",))

    def __len__(self) -> int:
        return 1

    def __getitem__(self, key: str) -> str:
        if key != "text":
            raise KeyError(key)
        self.reads += 1
        return "a" if self.reads == 1 else "b" * 400


def test_nested_custom_mapping_keeps_original_read_sequence_and_fixed_point() -> None:
    expected_source, actual_source = ScriptedMapping(), ScriptedMapping()
    expected = {"body": {"nested": expected_source}, "capture": {"stored_bytes": 0}}
    actual = {"body": {"nested": actual_source}, "capture": {"stored_bytes": 0}}
    expected_size, steps = old_apply_oracle(expected)
    assert expected_source.reads == len(steps)
    assert module._apply_stored_bytes(actual) == expected_size
    assert actual_source.reads == len(steps)
    assert compact_bytes(actual) == compact_bytes(expected)


def test_custom_mapping_metaclass_equality_cannot_impersonate_plain_json_type() -> None:
    class EqualityMeta(ABCMeta):
        def __eq__(cls, _: object) -> bool:
            return True

        __hash__ = type.__hash__

    class EqualityMapping(ScriptedMapping, metaclass=EqualityMeta):
        pass

    expected_source, actual_source = EqualityMapping(), EqualityMapping()
    expected = {"body": expected_source, "capture": {"stored_bytes": 0}}
    actual = {"body": actual_source, "capture": {"stored_bytes": 0}}
    expected_size, steps = old_apply_oracle(expected)
    assert module._apply_stored_bytes(actual) == expected_size
    assert actual_source.reads == len(steps)
    assert compact_bytes(actual) == compact_bytes(expected)


class ObservedText(str):
    characters: int = 0

    def __getitem__(self, index: SupportsIndex | slice) -> str:
        part = super().__getitem__(index)
        if isinstance(index, slice):
            self.characters += len(part)
        return part


class DictSubclass(dict[str, Any]):
    pass


class ListSubclass(list[Any]):
    pass


class IntSubclass(int):
    pass


@pytest.mark.parametrize(
    "kind",
    ["nested-dict", "nested-list", "nested-str", "nested-int", "capture-dict", "capture-int"],
)
def test_nested_and_metadata_subclasses_keep_complete_read_fallback(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    text = "Q" * 32
    nested: Any = {"text": text}
    capture: dict[str, Any] = {"stored_bytes": 0}
    if kind == "nested-dict":
        nested = DictSubclass(nested)
    elif kind == "nested-list":
        nested = {"items": ListSubclass([text])}
    elif kind == "nested-str":
        nested = {"text": ObservedText(text)}
    elif kind == "nested-int":
        nested = {"text": text, "revision": IntSubclass(7)}
    elif kind == "capture-int":
        capture["stored_bytes"] = IntSubclass(0)
    else:
        capture = DictSubclass(capture)
    actual: dict[str, Any] = {"body": {"nested": nested}, "capture": capture}
    expected = deepcopy(actual)
    size, steps = old_apply_oracle(expected)
    actual_size, characters = observed_plain_characters(monkeypatch, actual, text)
    assert actual_size == size and compact_bytes(actual) == compact_bytes(expected)
    assert characters == len(text) * len(steps)
    if kind == "nested-str":
        assert nested["text"].characters == len(text) * len(steps)


def test_complete_utf8_control_escaping_matches_independent_canonical_golden() -> None:
    body = {"text": '中🙂\n\t\x00\x1f"\\', "fraction": -1.25, "nested": [True, None]}
    content = public_content(body)
    original = compact_bytes(content)
    expected = {
        "capture_schema_version": 1,
        **content,
        "capture": {
            "status": "COMPLETE",
            "original_bytes": len(original),
            "stored_bytes": len(original),
            "sha256": hashlib.sha256(original).hexdigest(),
        },
    }
    expected_size, _ = old_apply_oracle(expected)
    captured = module.capture_provider_payload(
        transport="http", url="https://synthetic.invalid/result", body=body
    )
    frozen = module.freeze_provider_payload(captured)
    assert frozen.status == "COMPLETE" and frozen.original_bytes == len(original)
    assert frozen.sha256 == hashlib.sha256(original).hexdigest()
    assert frozen.encoded == compact_bytes(expected, sorted_keys=True)
    assert frozen.stored_bytes == expected_size


def test_truncated_utf8_fragments_match_independent_original_and_wrapper_golden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "MAX_CAPTURE_BYTES", 2048)
    monkeypatch.setattr(module, "FRAGMENT_BYTES", 128)
    body = {"text": '中🙂\n"\\\x01' * 800}
    content = public_content(body)
    original = compact_bytes(content)
    assert len(original) > 2048
    expected: dict[str, Any] = {
        "capture_schema_version": 1,
        **{**content, "body": None},
        "truncated_json": {
            "encoding": "utf-8-json-fragments",
            "head": original[:128].decode("utf-8", errors="replace"),
            "tail": original[-128:].decode("utf-8", errors="replace"),
        },
        "capture": {
            "status": "TRUNCATED",
            "original_bytes": len(original),
            "stored_bytes": 0,
            "sha256": hashlib.sha256(original).hexdigest(),
        },
    }
    size, _ = old_apply_oracle(expected)
    result = module.capture_provider_payload(
        transport="http", url="https://synthetic.invalid/result", body=body
    )
    frozen = module.freeze_provider_payload(result)
    assert frozen.status == "TRUNCATED" and frozen.original_bytes == len(original)
    assert frozen.sha256 == hashlib.sha256(original).hexdigest()
    assert frozen.encoded == compact_bytes(expected, sorted_keys=True)
    assert frozen.stored_bytes == size <= 2048
    assert type(frozen.payload["truncated_json"]["head"]) is str
    assert type(frozen.payload["truncated_json"]["tail"]) is str


def truncated_wrapper(head: str, tail: str) -> dict[str, Any]:
    return {
        "capture_schema_version": 1,
        "transport": "HTTP",
        "url": None,
        "headers": {},
        "body": None,
        "events": [],
        "binary_summary": None,
        "truncated_json": {"encoding": "utf-8-json-fragments", "head": head, "tail": tail},
        "capture": {
            "status": "TRUNCATED",
            "original_bytes": 123456,
            "stored_bytes": 0,
            "sha256": "a" * 64,
        },
    }


def captured_wrapper(payload: dict[str, Any]) -> CapturedProviderPayload:
    return CapturedProviderPayload(payload, "TRUNCATED", 123456, 0, "a" * 64)


def test_freeze_multiple_character_halvings_match_old_json_oracle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "MAX_CAPTURE_BYTES", 768)
    payload = truncated_wrapper('中🙂\n"\\\x01' * 1000, '尾🙂\t"\\' * 1001)
    expected = deepcopy(payload)
    fragments = expected["truncated_json"]
    halves = 0
    while len(compact_bytes(expected)) > 768:
        fragments["head"] = fragments["head"][: len(fragments["head"]) // 2]
        fragments["tail"] = fragments["tail"][len(fragments["tail"]) // 2 :]
        old_apply_oracle(expected)
        halves += 1
    assert halves >= 3
    frozen = module.freeze_provider_payload(captured_wrapper(payload))
    assert frozen.encoded == compact_bytes(expected, sorted_keys=True)
    assert frozen.stored_bytes == len(frozen.encoded or b"") <= 768
    assert frozen.original_bytes == 123456 and frozen.sha256 == "a" * 64


class NoProgressText(str):
    def __getitem__(self, _: SupportsIndex | slice) -> str:
        return self


@pytest.mark.parametrize("case", ["missing-fragments", "minimum-too-large", "no-progress"])
def test_freeze_refusal_metadata_uses_original_canonical_error_wrapper(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    monkeypatch.setattr(module, "MAX_CAPTURE_BYTES", 768)
    payload = truncated_wrapper("x" * 1000, "y" * 1000)
    if case == "missing-fragments":
        del payload["truncated_json"]
        payload["body"] = "z" * 1000
    elif case == "minimum-too-large":
        payload["headers"] = {"fixed": "z" * 1000}
    else:
        payload["truncated_json"]["head"] = NoProgressText("x" * 1000)
        payload["truncated_json"]["tail"] = NoProgressText("y" * 1000)
    frozen = module.freeze_provider_payload(captured_wrapper(payload))
    expected: dict[str, Any] = {
        "capture_schema_version": 1,
        "transport": None,
        "url": None,
        "headers": {},
        "body": None,
        "events": [],
        "binary_summary": None,
        "capture": {
            "status": "UNAVAILABLE",
            "original_bytes": 0,
            "stored_bytes": 0,
            "sha256": None,
            "error_code": "capture_wrapper_too_large",
        },
    }
    old_apply_oracle(expected)
    assert frozen.status == "UNAVAILABLE"
    assert frozen.original_bytes == 0 and frozen.sha256 is None
    assert frozen.encoded == compact_bytes(expected, sorted_keys=True)


def test_freeze_preserves_stale_embedded_size_without_repairing_legacy_payload() -> None:
    payload: dict[str, Any] = {"capture": {"stored_bytes": 999999}, "body": {"text": "stable"}}
    expected = compact_bytes(payload, sorted_keys=True)
    frozen = module.freeze_provider_payload(
        CapturedProviderPayload(payload, "COMPLETE", 7, 2, None)
    )
    assert frozen.encoded == expected and frozen.stored_bytes == len(expected)
    assert frozen.payload["capture"]["stored_bytes"] == 999999


def test_custom_source_growth_still_hits_final_allocation_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "MAX_CAPTURE_BYTES", 768)

    class GrowingMapping(ScriptedMapping):
        def __getitem__(self, key: str) -> str:
            if key != "text":
                raise KeyError(key)
            self.reads += 1
            return "small" if self.reads == 1 else "z" * 4000

    source = GrowingMapping()
    payload: dict[str, Any] = {"capture": {"stored_bytes": 0}, "body": {"nested": source}}
    frozen = module.freeze_provider_payload(
        CapturedProviderPayload(payload, "COMPLETE", 0, 0, None)
    )
    assert source.reads >= 2 and frozen.status == "UNAVAILABLE"
    assert frozen.original_bytes == 0 and frozen.sha256 is None
    assert frozen.payload["capture"]["error_code"] == "capture_size_changed"
    assert len(frozen.encoded or b"") <= 768


def test_public_budgeted_complete_capture_does_not_reencode_plain_body_for_fixed_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body_text = "Q" * (64 * 1024)
    assert type(body_text) is str  # Subclasses deliberately keep compatibility fallback.
    body = {"text": body_text}
    original = compact_bytes(
        {
            "transport": "HTTP",
            "url": {"origin": "https://synthetic.invalid", "path": "/result"},
            "headers": {},
            "body": body,
            "events": [],
            "binary_summary": None,
        }
    )
    processed_characters = 0

    def observed_dumps(value: Any, *args: Any, **kwargs: Any) -> str:
        nonlocal processed_characters
        # Observe the real encoder's ordinary pieces, not a custom str's hooks.
        if type(value) is str and value and value == "Q" * len(value):
            processed_characters += len(value)
        return _STANDARD_DUMPS(value, *args, **kwargs)

    budget = CaptureBudget()
    reservation = budget.reserve(METADATA_BYTES)
    assert reservation is not None
    capture = ProviderCallCapture(budget=budget, reservation=reservation, budgeted=True)
    try:
        with monkeypatch.context() as observing:
            observing.setattr(json, "dumps", observed_dumps)
            capture.capture_response(
                transport="http", url="https://synthetic.invalid/result", body=body
            )
        result = capture.response
        assert result is not None and result.status == "COMPLETE" and result.encoded is not None
        assert result.original_bytes == len(original)
        assert result.sha256 == hashlib.sha256(original).hexdigest()
        assert result.encoded == compact_bytes(result.payload, sorted_keys=True)
        assert (
            result.stored_bytes == len(result.encoded) == result.payload["capture"]["stored_bytes"]
        )
        assert budget.workspace_bytes == 0
        assert budget.retained_bytes == METADATA_BYTES + len(result.encoded)
        observation = {
            "body_characters": len(body_text),
            "actual_plain_piece_characters": processed_characters,
            "allowed_plain_piece_characters": 5 * len(body_text),
            "original_bytes": result.original_bytes,
            "stored_bytes": result.stored_bytes,
        }
        print(_STANDARD_DUMPS(observation, sort_keys=True))
        # Keep initial reservation measurement, original serialization, first
        # fixed-point size read, independent freeze size read and final encode.
        assert processed_characters <= 5 * len(body_text), observation
    finally:
        capture.close()
    assert budget.items == budget.retained_bytes == budget.workspace_bytes == 0

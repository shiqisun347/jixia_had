from __future__ import annotations

from uuid import uuid4

import pytest

from jx_core.runtime_identity import CallbackEnvelope, CallbackEnvelopeError


def _envelope(**overrides: object) -> CallbackEnvelope:
    values: dict[str, object] = {
        "match_id": uuid4(),
        "speech_id": uuid4(),
        "attempt_no": 1,
        "generation_id": uuid4(),
        "connection_epoch": 2,
        "context_version": 0,
        "opportunity_id": uuid4(),
        "opportunity_generation": 1,
    }
    values.update(overrides)
    return CallbackEnvelope(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("attempt_no", 0, "callback_attempt_invalid"),
        ("context_version", -1, "callback_context_version_invalid"),
        ("connection_epoch", 0, "callback_connection_epoch_invalid"),
        (
            "opportunity_generation",
            0,
            "callback_opportunity_generation_invalid",
        ),
    ],
)
def test_callback_envelope_rejects_invalid_numeric_identity(
    field: str, value: int, error: str
) -> None:
    with pytest.raises(CallbackEnvelopeError, match=error):
        _envelope(**{field: value})


def test_callback_envelope_requires_applicable_resource() -> None:
    envelope = _envelope(generation_id=None, opportunity_id=None)

    envelope.require("speech_id", "connection_epoch")
    with pytest.raises(CallbackEnvelopeError, match="callback_generation_id_required"):
        envelope.require("generation_id")
    with pytest.raises(CallbackEnvelopeError, match="callback_opportunity_id_required"):
        envelope.require("opportunity_id")


def test_callback_envelope_serializes_only_identity_fields() -> None:
    envelope = _envelope()

    assert envelope.to_log_details() == {
        "match_id": str(envelope.match_id),
        "speech_id": str(envelope.speech_id),
        "attempt_no": 1,
        "generation_id": str(envelope.generation_id),
        "connection_epoch": 2,
        "context_version": 0,
        "opportunity_id": str(envelope.opportunity_id),
        "opportunity_generation": 1,
    }


def test_callback_envelope_compares_selected_fields_only() -> None:
    envelope = _envelope()
    same_task = _envelope(
        match_id=envelope.match_id,
        speech_id=envelope.speech_id,
        generation_id=envelope.generation_id,
        context_version=envelope.context_version,
        attempt_no=2,
    )

    assert envelope.matches(same_task, fields=("match_id", "generation_id"))
    assert not envelope.matches(same_task, fields=("match_id", "attempt_no"))

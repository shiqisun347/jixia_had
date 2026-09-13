"""Immutable identity carried by asynchronous runtime callbacks."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID


class CallbackEnvelopeError(ValueError):
    """Raised when a callback identity is malformed or incomplete."""


@dataclass(frozen=True, slots=True)
class CallbackEnvelope:
    """The identity captured when an asynchronous task/session is created.

    Resource identifiers are nullable only for callback classes that do not own
    that resource. The producer must keep this object unchanged until callback
    delivery; consumers must not reconstruct it from mutable match state.
    """

    match_id: UUID
    speech_id: UUID | None
    attempt_no: int
    generation_id: UUID | None
    connection_epoch: int | None
    context_version: int
    opportunity_id: UUID | None
    opportunity_generation: int | None

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.match_id), UUID):
            raise CallbackEnvelopeError("callback_match_id_invalid")
        if self.attempt_no <= 0:
            raise CallbackEnvelopeError("callback_attempt_invalid")
        if self.context_version < 0:
            raise CallbackEnvelopeError("callback_context_version_invalid")
        if self.connection_epoch is not None and self.connection_epoch <= 0:
            raise CallbackEnvelopeError("callback_connection_epoch_invalid")
        if self.opportunity_generation is not None and self.opportunity_generation <= 0:
            raise CallbackEnvelopeError("callback_opportunity_generation_invalid")

    def require(self, *fields: str) -> None:
        """Require resource fields for a callback class at its boundary."""

        for field in fields:
            if not hasattr(self, field):
                raise CallbackEnvelopeError("callback_field_unknown")
            if getattr(self, field) is None:
                raise CallbackEnvelopeError(f"callback_{field}_required")

    def to_log_details(self) -> dict[str, Any]:
        """Return a stable, non-secret representation for diagnostics."""

        return {
            "match_id": str(self.match_id),
            "speech_id": str(self.speech_id) if self.speech_id else None,
            "attempt_no": self.attempt_no,
            "generation_id": str(self.generation_id) if self.generation_id else None,
            "connection_epoch": self.connection_epoch,
            "context_version": self.context_version,
            "opportunity_id": str(self.opportunity_id) if self.opportunity_id else None,
            "opportunity_generation": self.opportunity_generation,
        }

    def matches(self, other: CallbackEnvelope, *, fields: Iterable[str]) -> bool:
        """Compare only the identity fields relevant to a callback boundary."""

        return all(getattr(self, field) == getattr(other, field, object()) for field in fields)


__all__ = ["CallbackEnvelope", "CallbackEnvelopeError"]

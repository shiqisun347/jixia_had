"""Shared raw-provider projection for legacy, async and missing captures."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ExternalCall, ProviderCallCapture
from .provider import PROVIDER_CAPTURE_VERSION

CAPTURE_RAW_FIELDS = (
    "capture_version",
    "captured_at",
    "provider_request_id",
    "request_blob_id",
    "response_blob_id",
    "request_capture_status",
    "response_capture_status",
    "request_original_bytes",
    "response_original_bytes",
    "request_stored_bytes",
    "response_stored_bytes",
    "request_sha256",
    "response_sha256",
    "capture_error_code",
)


def project_capture(call: ExternalCall, capture: ProviderCallCapture | None) -> dict[str, Any]:
    """Project scalar metadata without loading or modifying content blobs."""

    if capture is not None:
        projected = {
            field: getattr(capture, field)
            for field in CAPTURE_RAW_FIELDS
            if field != "capture_version"
        }
        projected["capture_version"] = capture.schema_version
        projected["capture_revision"] = capture.revision
        return projected
    if call.capture_storage_mode == "ASYNC_V1":
        # ExternalCall may contain complete authoritative business blobs.
        # Their existence must never turn a missing raw capture into a body.
        return {
            **dict.fromkeys(CAPTURE_RAW_FIELDS),
            "capture_version": PROVIDER_CAPTURE_VERSION,
            "capture_revision": None,
            "request_capture_status": "UNAVAILABLE",
            "response_capture_status": "UNAVAILABLE",
            "capture_error_code": "capture_not_persisted",
        }
    return {
        **{field: getattr(call, field) for field in CAPTURE_RAW_FIELDS},
        "capture_revision": None,
    }


async def capture_projections(
    session: AsyncSession, calls: Sequence[ExternalCall]
) -> dict[UUID, dict[str, Any]]:
    """Read new captures once in the caller's MVCC snapshot, with no row locks."""

    if not calls:
        return {}
    captures = {
        row.external_call_id: row
        for row in await session.scalars(
            select(ProviderCallCapture).where(
                ProviderCallCapture.external_call_id.in_([call.id for call in calls])
            )
        )
    }
    return {call.id: project_capture(call, captures.get(call.id)) for call in calls}


async def capture_projection(session: AsyncSession, call: ExternalCall) -> dict[str, Any]:
    """Read one compatible raw projection; body loading remains on demand."""

    return (await capture_projections(session, [call]))[call.id]

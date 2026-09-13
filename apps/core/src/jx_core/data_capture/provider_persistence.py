"""Persist bounded provider-adapter captures on an existing ExternalCall."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ExternalCall
from .content import store_content_blob
from .provider import (
    PROVIDER_CAPTURE_VERSION,
    CapturedProviderPayload,
    ProviderCallCapture,
    unavailable_provider_payload,
)


async def persist_provider_capture(
    session: AsyncSession,
    call: ExternalCall,
    capture: ProviderCallCapture,
) -> bool:
    """Persist capture inside a savepoint without changing the business transaction outcome."""

    try:
        async with session.begin_nested():
            call.capture_version = PROVIDER_CAPTURE_VERSION
            call.captured_at = datetime.now(UTC)
            call.provider_request_id = capture.provider_request_id
            call.capture_error_code = capture.error_code
            await _persist_side(
                session,
                call,
                "request",
                capture.request or unavailable_provider_payload("provider_request_not_observed"),
            )
            await _persist_side(
                session,
                call,
                "response",
                capture.response or unavailable_provider_payload("provider_response_not_observed"),
            )
    except Exception:
        call.capture_error_code = "capture_persistence_failed"
        return False
    return True


async def _persist_side(
    session: AsyncSession,
    call: ExternalCall,
    side: str,
    captured: CapturedProviderPayload | None,
) -> None:
    if captured is None:
        return
    blob_id = await store_content_blob(
        session,
        content_kind="REQUEST" if side == "request" else "RESPONSE",
        payload=captured.payload,
    )
    setattr(call, f"{side}_blob_id", blob_id)
    setattr(call, f"{side}_capture_status", captured.status)
    setattr(call, f"{side}_original_bytes", captured.original_bytes)
    setattr(call, f"{side}_stored_bytes", captured.stored_bytes)
    setattr(call, f"{side}_sha256", captured.sha256)


__all__ = ["persist_provider_capture"]

"""Signed LiveKit webhook boundary for match media presence."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Request, Response
from livekit import api
from sqlalchemy import select

from .models import Match, RoomMember
from .presence import MatchPresenceCoordinator

router = APIRouter()
logger = logging.getLogger(__name__)
ROOM_RE = re.compile(r"^jx-match-([0-9a-f-]{36})$")
IDENTITY_RE = re.compile(r"^jx-human-([0-9a-f-]{36})-([0-9a-f-]{36})-(\d+)-")
WEBHOOK_PROCESSING_TIMEOUT_SECONDS = 5.0


def _event_value(event: Any, name: str, default: str = "") -> str:
    value = getattr(event, name, None)
    normalized = str(value).strip() if value is not None else ""
    return normalized or default


def _parse_human_identity(identity: str) -> tuple[UUID, UUID, int] | None:
    parsed = IDENTITY_RE.match(identity)
    if parsed is None:
        return None
    try:
        match_id = UUID(parsed.group(1))
        user_id = UUID(parsed.group(2))
        epoch = int(parsed.group(3))
    except (TypeError, ValueError):
        return None
    if epoch < 1:
        return None
    return match_id, user_id, epoch


@router.post("/api/livekit/webhook", include_in_schema=False)
async def livekit_webhook(request: Request) -> Response:
    settings = request.app.state.settings
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        return Response(status_code=503)
    authorization = request.headers.get("authorization")
    if not authorization:
        return Response(status_code=401)
    try:
        body = (await request.body()).decode("utf-8")
        event = api.WebhookReceiver(
            api.TokenVerifier(
                settings.livekit_api_key.get_secret_value(),
                settings.livekit_api_secret.get_secret_value(),
            )
        ).receive(body, authorization)
    except Exception:
        return Response(status_code=401)
    event_type = _event_value(event, "event")
    if event_type not in {
        "participant_joined",
        "participant_left",
        "participant_connection_aborted",
    }:
        return Response(status_code=204)
    room = getattr(event, "room", None)
    participant = getattr(event, "participant", None)
    match = ROOM_RE.match(_event_value(room, "name"))
    identity = _event_value(participant, "identity")
    if match is None:
        return Response(status_code=204)
    try:
        match_id = UUID(match.group(1))
    except ValueError:
        return Response(status_code=204)
    parsed_identity = _parse_human_identity(identity)
    if parsed_identity is None:
        return Response(status_code=204)
    identity_match_id, user_id, epoch = parsed_identity
    if identity_match_id != match_id:
        return Response(status_code=204)
    runtime_manager = request.app.state.match_runtime_manager
    coordinator = cast(
        MatchPresenceCoordinator | None, getattr(request.app.state, "presence", None)
    )
    if coordinator is None or runtime_manager is None:
        return Response(status_code=503)
    runtime = request.app.state.runtime
    try:
        async with asyncio.timeout(WEBHOOK_PROCESSING_TIMEOUT_SECONDS):
            async with runtime.database.session_factory() as session:
                member = await session.scalar(
                    select(RoomMember)
                    .join(Match, Match.room_id == RoomMember.room_id)
                    .where(
                        Match.id == match_id,
                        RoomMember.user_id == user_id,
                        RoomMember.member_role == "DEBATER",
                        RoomMember.left_at.is_(None),
                    )
                )
            if member is None:
                return Response(status_code=204)
            sid = _event_value(participant, "sid", identity)
            event_id = _event_value(event, "id", f"{event_type}:{sid}:{epoch}")
            await coordinator.media_event(
                match_id=match_id,
                user_id=user_id,
                epoch=epoch,
                sid=sid,
                event_id=event_id,
                joined=event_type == "participant_joined",
            )
    except Exception as error:
        logger.warning(
            "LiveKit presence webhook processing failed",
            extra={
                "error_code": "livekit_presence_webhook_failed",
                "match_id": str(match_id),
                "connection_epoch": epoch,
                "details": {"exception_type": type(error).__name__},
            },
        )
        return Response(status_code=503)
    return Response(status_code=204)


__all__ = ["router"]

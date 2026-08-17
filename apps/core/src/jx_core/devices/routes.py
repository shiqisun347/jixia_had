"""Issue narrowly scoped LiveKit tokens for preparation-stage network probes."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from livekit import api
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import (
    get_changed_password_auth,
    get_database_session,
    require_browser_origin,
)
from ..auth.errors import APIError
from ..auth.session import AuthContext
from ..config import Settings
from ..models import User

router = APIRouter()
DEVICE_PROBE_ROOM = "jx-device-probe"
DEVICE_PROBE_TOKEN_TTL_SECONDS = 120


class LiveKitProbeTokenResponse(BaseModel):
    server_url: str
    participant_token: str
    room_name: str
    expires_in_seconds: int = DEVICE_PROBE_TOKEN_TTL_SECONDS


def build_livekit_probe_token(settings: Settings, user: User) -> LiveKitProbeTokenResponse:
    assert settings.livekit_url is not None
    assert settings.livekit_api_key is not None
    assert settings.livekit_api_secret is not None
    identity = f"device-{user.id}-{uuid4().hex[:8]}"
    participant_token = (
        api.AccessToken(
            settings.livekit_api_key.get_secret_value(),
            settings.livekit_api_secret.get_secret_value(),
        )
        .with_identity(identity)
        .with_name(user.real_name)
        .with_ttl(timedelta(seconds=DEVICE_PROBE_TOKEN_TTL_SECONDS))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=DEVICE_PROBE_ROOM,
                can_publish=True,
                can_publish_sources=["microphone"],
                can_subscribe=False,
                can_publish_data=False,
                hidden=True,
            )
        )
        .to_jwt()
    )
    return LiveKitProbeTokenResponse(
        server_url=settings.livekit_url,
        participant_token=participant_token,
        room_name=DEVICE_PROBE_ROOM,
    )


@router.post(
    "/api/device/livekit-token",
    response_model=LiveKitProbeTokenResponse,
    tags=["devices"],
    dependencies=[Depends(require_browser_origin)],
)
async def create_livekit_probe_token(
    request: Request,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> LiveKitProbeTokenResponse:
    settings: Settings = request.app.state.settings
    if (
        settings.livekit_url is None
        or settings.livekit_api_key is None
        or settings.livekit_api_secret is None
    ):
        raise APIError("livekit_not_configured")
    user = await database_session.get(User, context.user_id)
    if user is None or user.status != "ACTIVE":
        raise APIError("not_authenticated")
    return build_livekit_probe_token(settings, user)


__all__ = ["router"]

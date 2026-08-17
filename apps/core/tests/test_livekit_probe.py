from __future__ import annotations

from uuid import uuid4

import jwt
import pytest
from pydantic import ValidationError

from jx_core.config import Settings
from jx_core.devices.routes import DEVICE_PROBE_ROOM, build_livekit_probe_token
from jx_core.models import User

PROBE_SECRET = "probe-secret-with-at-least-thirty-two-bytes"


def livekit_settings() -> Settings:
    return Settings(
        database_url="postgresql+psycopg://jx:secret@127.0.0.1:5432/jx_debate",
        livekit_url="wss://rtc.example.test",
        livekit_api_key="probe-key",
        livekit_api_secret=PROBE_SECRET,
    )


def test_livekit_settings_require_complete_credentials() -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+psycopg://jx:secret@127.0.0.1:5432/jx_debate",
            livekit_url="wss://rtc.example.test",
        )


def test_device_probe_token_only_allows_microphone_publish() -> None:
    settings = livekit_settings()
    user = User(
        id=uuid4(),
        username="probe-user",
        username_normalized="probe-user",
        real_name="测试辩手",
        password_hash="argon2id-probe",
    )
    response = build_livekit_probe_token(settings, user)
    claims = jwt.decode(
        response.participant_token,
        PROBE_SECRET,
        algorithms=["HS256"],
        options={"verify_aud": False},
    )

    assert response.server_url == "wss://rtc.example.test"
    assert response.room_name == DEVICE_PROBE_ROOM
    assert claims["sub"].startswith(f"device-{user.id}-")
    assert claims["video"] == {
        "roomJoin": True,
        "room": DEVICE_PROBE_ROOM,
        "canPublish": True,
        "canSubscribe": False,
        "canPublishData": False,
        "canPublishSources": ["microphone"],
        "hidden": True,
    }

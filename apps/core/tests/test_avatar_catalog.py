from io import BytesIO

import pytest
from PIL import Image
from pydantic import ValidationError

from jx_core.rules.schemas import VoiceProfileCreate, VoiceProfileUpdate
from jx_core.users.avatar import AvatarService
from jx_core.users.avatar_catalog import (
    AGENT_AVATAR_KEYS,
    AGENT_SELECTABLE_AVATAR_KEYS,
    HUMAN_AVATAR_KEYS,
    avatar_asset_path,
    avatar_keys,
    is_avatar_key,
    random_avatar_key,
)


def test_avatar_catalogs_are_disjoint_and_allow_listed() -> None:
    assert len(HUMAN_AVATAR_KEYS) == 16
    assert len(AGENT_AVATAR_KEYS) == 12
    assert set(HUMAN_AVATAR_KEYS).isdisjoint(AGENT_AVATAR_KEYS)
    assert is_avatar_key("human-16", "HUMAN") is True
    assert is_avatar_key("agent-12", "AGENT") is True
    assert is_avatar_key("agent-01", "HUMAN") is False
    assert is_avatar_key("human-16", "AGENT") is True
    assert avatar_keys("AGENT") == AGENT_SELECTABLE_AVATAR_KEYS
    assert len(AGENT_SELECTABLE_AVATAR_KEYS) == 28
    assert len(set(AGENT_SELECTABLE_AVATAR_KEYS)) == 28
    assert avatar_asset_path("human-01") == "/assets/avatars/human-01.webp"
    assert avatar_asset_path("../../secret") is None


def test_random_avatar_assignment_stays_within_kind() -> None:
    assert random_avatar_key("HUMAN") in HUMAN_AVATAR_KEYS
    assert random_avatar_key("AGENT") in AGENT_AVATAR_KEYS


def test_agent_random_default_pool_stays_at_twelve(monkeypatch: pytest.MonkeyPatch) -> None:
    selected_from = []

    def choose(keys):
        selected_from.append(keys)
        return keys[-1]

    monkeypatch.setattr("jx_core.users.avatar_catalog.secrets.choice", choose)
    assert random_avatar_key("AGENT") == "agent-12"
    assert selected_from == [AGENT_AVATAR_KEYS]


@pytest.mark.parametrize("key", AGENT_SELECTABLE_AVATAR_KEYS)
def test_agent_voice_create_and_update_accept_existing_selectable_presets(key: str) -> None:
    payload = {"name": "头像选择", "kind": "AGENT", "provider_voice": "voice", "avatar_key": key}
    assert VoiceProfileCreate(**payload).avatar_key == key
    assert VoiceProfileUpdate(**payload).avatar_key == key
    assert avatar_asset_path(key) == f"/assets/avatars/{key}.webp"


@pytest.mark.parametrize(
    "key", ["agent-00", "agent-13", "human-00", "human-17", "human-1", "../human-01"]
)
def test_voice_schema_rejects_out_of_catalog_avatar_keys(key: str) -> None:
    with pytest.raises(ValidationError):
        VoiceProfileCreate(name="头像选择", kind="AGENT", provider_voice="voice", avatar_key=key)
    assert is_avatar_key(key, "AGENT") is False


def test_host_avatar_remains_null_and_agent_still_requires_a_choice() -> None:
    assert VoiceProfileCreate(name="主持", kind="HOST", provider_voice="host").avatar_key is None
    for key in ("agent-01", "human-16"):
        with pytest.raises(ValidationError):
            VoiceProfileCreate(name="主持", kind="HOST", provider_voice="host", avatar_key=key)
    with pytest.raises(ValidationError):
        VoiceProfileCreate(name="Agent", kind="AGENT", provider_voice="voice")


def test_all_human_presets_are_real_bounded_webp_assets() -> None:
    for key in HUMAN_AVATAR_KEYS:
        payload = AvatarService.preset_bytes(key)
        assert 1_000 < len(payload) < 100_000
        with Image.open(BytesIO(payload)) as image:
            assert image.format == "WEBP"
            assert image.size == (256, 256)

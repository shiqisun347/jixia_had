from io import BytesIO

from PIL import Image

from jx_core.users.avatar import AvatarService
from jx_core.users.avatar_catalog import (
    AGENT_AVATAR_KEYS,
    HUMAN_AVATAR_KEYS,
    avatar_asset_path,
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
    assert avatar_asset_path("human-01") == "/assets/avatars/human-01.webp"
    assert avatar_asset_path("../../secret") is None


def test_random_avatar_assignment_stays_within_kind() -> None:
    assert random_avatar_key("HUMAN") in HUMAN_AVATAR_KEYS
    assert random_avatar_key("AGENT") in AGENT_AVATAR_KEYS


def test_all_human_presets_are_real_bounded_webp_assets() -> None:
    for key in HUMAN_AVATAR_KEYS:
        payload = AvatarService.preset_bytes(key)
        assert 1_000 < len(payload) < 100_000
        with Image.open(BytesIO(payload)) as image:
            assert image.format == "WEBP"
            assert image.size == (256, 256)

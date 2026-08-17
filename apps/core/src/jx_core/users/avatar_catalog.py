"""Allow-listed, stable avatar preset catalog for people and Agents."""

from __future__ import annotations

import secrets
from typing import Literal

HUMAN_AVATAR_KEYS: tuple[str, ...] = tuple(f"human-{index:02d}" for index in range(1, 17))
AGENT_AVATAR_KEYS: tuple[str, ...] = tuple(f"agent-{index:02d}" for index in range(1, 13))
AvatarKind = Literal["HUMAN", "AGENT"]


def avatar_keys(kind: AvatarKind) -> tuple[str, ...]:
    return HUMAN_AVATAR_KEYS if kind == "HUMAN" else AGENT_AVATAR_KEYS


def random_avatar_key(kind: AvatarKind) -> str:
    return secrets.choice(avatar_keys(kind))


def is_avatar_key(value: str, kind: AvatarKind) -> bool:
    return value in avatar_keys(kind)


def avatar_asset_path(key: str) -> str | None:
    kind = "human" if key.startswith("human-") else "agent" if key.startswith("agent-") else ""
    if not kind or key not in avatar_keys("HUMAN" if kind == "human" else "AGENT"):
        return None
    return f"/assets/avatars/{key}.webp"


__all__ = [
    "AGENT_AVATAR_KEYS",
    "HUMAN_AVATAR_KEYS",
    "avatar_asset_path",
    "avatar_keys",
    "is_avatar_key",
    "random_avatar_key",
]

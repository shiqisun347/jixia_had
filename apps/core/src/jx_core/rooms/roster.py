"""Conference defaults use real voice identities; flexible seating remains available."""

from __future__ import annotations

import secrets
from typing import Any
from uuid import UUID

from ..auth.errors import AuthError

CONFERENCE_ROLES = (
    ("AFFIRMATIVE", 2, "执锋 Agent", "longhua_v3.1"),
    ("AFFIRMATIVE", 4, "博闻 Agent", "anmingyuan_v3.1"),
    ("NEGATIVE", 2, "睿断 Agent", "huozhuoshi_v3.1"),
    ("NEGATIVE", 4, "明辨 Agent", "xiaxiaochen_v3.1"),
)


def default_agent_assignments(
    agents: list[dict[str, Any]],
    available_ids: list[UUID],
    *,
    conference: bool,
    all_agent: bool,
) -> dict[tuple[str, int], UUID]:
    available = set(available_ids)
    by_voice = {
        item.get("voice", {}).get("provider_voice"): item
        for item in agents
        if UUID(item["id"]) in available
    }
    assignments: dict[tuple[str, int], UUID] = {}
    for side, seat, name, voice in CONFERENCE_ROLES:
        candidate = by_voice.get(voice)
        if candidate is not None and (not conference or candidate.get("name") == name):
            assignments[(side, seat)] = UUID(candidate["id"])
        elif conference:
            raise AuthError("conference_agents_unavailable")
    if conference and not all_agent:
        return assignments
    remaining = list(available - set(assignments.values()))
    secrets.SystemRandom().shuffle(remaining)
    for side in ("AFFIRMATIVE", "NEGATIVE"):
        for seat in range(1, 5):
            if (side, seat) not in assignments and remaining:
                assignments[(side, seat)] = remaining.pop()
    return assignments

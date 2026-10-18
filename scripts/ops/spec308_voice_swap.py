"""Atomic Spec308 resource operation; preserves identities and existing uniqueness.

The caller owns the transaction and must stop room-creating services before a
production apply. This module never operates on live match state or model keys.
"""

from __future__ import annotations

import json
import subprocess
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jx_core.audit.service import AuditService
from jx_core.models import AgentProfile, Match, Room, VoiceProfile
from jx_core.rules.agent_pool import lock_agent_pool, sync_agent_pools
from jx_core.rules.host_voice import is_host_reserved_voice

ZHIFENG_ID = UUID("60ba43a6-3136-43aa-a602-2e14d9a2018e")
JIEWEN_ID = UUID("25743aa0-e101-4b11-8a0d-f28da6452586")
ORIGINAL = {ZHIFENG_ID: "longhua_v3.1", JIEWEN_ID: "longwan_v3.1"}
DESIRED = {ZHIFENG_ID: "longwan_v3.1", JIEWEN_ID: "longhua_v3.1"}
NAMES = {ZHIFENG_ID: "执锋 Agent", JIEWEN_ID: "诘问 Agent"}


class SwapConflict(RuntimeError):
    """A safe, closed reason code without provider or database diagnostics."""


def require_services_stopped() -> None:
    """Require systemd's exact inactive state, fail closed on unknown or errors."""
    for service in ("jx-core", "jx-jobs"):
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 3 or result.stdout.strip() != "inactive":
            raise SwapConflict("room_creating_services_must_be_inactive")


def _agent_fingerprint(agents: list[AgentProfile]) -> str:
    # Include all persisted preferences, identities and legacy prompt fields.
    return json.dumps(
        [
            {column.key: getattr(agent, column.key) for column in agent.__table__.columns}
            for agent in agents
        ],
        default=str,
        sort_keys=True,
    )


async def swap_voices(
    session: AsyncSession,
    *,
    rollback: bool = False,
    actor_user_id: UUID | None = None,
) -> dict[str, object]:
    """Join one transaction. The caller must roll it back on every exception.

    Explicit before/after maps make retries idempotent rather than toggling.
    Production execution additionally requires require_services_stopped().
    """
    if not session.in_transaction():
        raise SwapConflict("transaction_required")
    await lock_agent_pool(session)
    if await session.scalar(
        select(func.count()).select_from(Room).where(Room.status.not_in(("FINISHED", "TERMINATED")))
    ) or await session.scalar(
        select(func.count())
        .select_from(Match)
        .where(Match.status.not_in(("FINISHED", "TERMINATED")))
    ):
        raise SwapConflict("nonterminal_room_or_match")
    voices = list(
        (
            await session.scalars(
                select(VoiceProfile)
                .where(VoiceProfile.id.in_(ORIGINAL))
                .order_by(VoiceProfile.id)
                .with_for_update()
            )
        ).all()
    )
    if len(voices) != 2 or any(
        voice.kind != "AGENT"
        or voice.name != NAMES[voice.id]
        or is_host_reserved_voice(voice.provider_voice)
        for voice in voices
    ):
        raise SwapConflict("voice_identity_conflict")
    before, after = (DESIRED, ORIGINAL) if rollback else (ORIGINAL, DESIRED)
    observed = {voice.id: voice.provider_voice for voice in voices}
    if observed == after:
        return {"status": "already_applied", "rollback": rollback}
    if observed != before:
        raise SwapConflict("voice_provider_conflict")
    agents = list(
        (
            await session.scalars(select(AgentProfile).order_by(AgentProfile.id).with_for_update())
        ).all()
    )
    identities = _agent_fingerprint(agents)
    invariant = {
        voice.id: (
            voice.name,
            voice.kind,
            voice.avatar_key,
            voice.rate,
            voice.playback_gain,
            voice.status,
        )
        for voice in voices
    }
    first, second = voices
    first.provider_voice = "spec308-uncommitted-" + uuid4().hex
    await session.flush()
    second.provider_voice = after[second.id]
    await session.flush()
    first.provider_voice = after[first.id]
    for voice in voices:
        voice.chars_per_second = None
        voice.calibration_status = "NOT_CALIBRATED"
        voice.calibrated_at = None
    await session.flush()
    for voice in voices:
        if await sync_agent_pools(
            session, voice_id=voice.id, refresh_voice=True, actor_user_id=actor_user_id
        ):
            raise SwapConflict("unexpected_agent_creation")
        if invariant[voice.id] != (
            voice.name,
            voice.kind,
            voice.avatar_key,
            voice.rate,
            voice.playback_gain,
            voice.status,
        ):
            raise SwapConflict("voice_preferences_changed")
    if _agent_fingerprint(agents) != identities:
        raise SwapConflict("agent_preferences_changed")
    AuditService().record(
        session,
        actor_user_id=actor_user_id,
        action="admin.spec308.voice_providers_swapped",
        target_type="voice_profile",
        target_id=str(ZHIFENG_ID),
        details={
            "rollback": rollback,
            "providers": [
                {"id": str(item), "before": before[item], "after": after[item]}
                for item in sorted(before)
            ],
        },
    )
    await session.flush()
    return {"status": "applied", "rollback": rollback, "agent_rows_preserved": len(agents)}

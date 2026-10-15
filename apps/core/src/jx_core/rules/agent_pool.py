"""Voice identity synchronization that preserves each rule's Agent preferences."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit.service import AuditService
from ..models import AgentProfile, Rule, VoiceProfile
from .host_voice import is_host_reserved_voice


async def lock_agent_pool(session: AsyncSession) -> None:
    """Serialize catalog mutations before taking any voice/rule/Agent row locks."""

    await session.execute(select(func.pg_advisory_xact_lock(263_001)))


async def sync_agent_pools(
    session: AsyncSession,
    *,
    voice_id: UUID | None = None,
    rule_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    refresh_voice: bool = False,
) -> int:
    """Join the caller's transaction; fill missing pairs without resetting preferences."""

    await lock_agent_pool(session)
    await session.flush()
    rule_query = select(Rule).where(
        Rule.side_size == 4,
        Rule.historical_read_only.is_(False),
        Rule.status != "ARCHIVED",
        Rule.default_agent_model_profile_id.is_not(None),
    )
    if rule_id is not None:
        rule_query = rule_query.where(Rule.id == rule_id)
    rules = list((await session.scalars(rule_query.order_by(Rule.id).with_for_update())).all())
    voice_query = select(VoiceProfile).where(VoiceProfile.kind == "AGENT")
    voice_query = (
        voice_query.where(VoiceProfile.id == voice_id)
        if voice_id is not None
        else voice_query.where(VoiceProfile.status == "ENABLED")
    )
    voices = [
        voice
        for voice in (await session.scalars(voice_query.order_by(VoiceProfile.id))).all()
        if not is_host_reserved_voice(voice.provider_voice)
    ]
    if not rules or not voices:
        return 0
    existing = list(
        (
            await session.scalars(
                select(AgentProfile)
                .where(
                    AgentProfile.rule_id.in_([rule.id for rule in rules]),
                    AgentProfile.voice_profile_id.in_([voice.id for voice in voices]),
                )
                .order_by(AgentProfile.id)
                .with_for_update()
            )
        ).all()
    )
    pairs = {(agent.rule_id, agent.voice_profile_id): agent for agent in existing}
    added = 0
    changed_rules: list[str] = []
    for rule in rules:
        changed = False
        for voice in voices:
            agent = pairs.get((rule.id, voice.id))
            if agent is None:
                if voice.status != "ENABLED":
                    continue
                assert rule.default_agent_model_profile_id is not None
                agent = AgentProfile(
                    rule_id=rule.id,
                    name=voice.name,
                    model_profile_id=rule.default_agent_model_profile_id,
                    voice_profile_id=voice.id,
                    status="ENABLED",
                )
                session.add(agent)
                added += 1
                changed = True
            else:
                if agent.name != voice.name:
                    agent.name = voice.name
                    changed = True
                changed = changed or refresh_voice
        if changed:
            rule.config_revision += 1
            changed_rules.append(str(rule.id))
    if changed_rules:
        AuditService().record(
            session,
            actor_user_id=actor_user_id,
            action="admin.rule_agent_pool.synced",
            target_type="voice_profile" if voice_id is not None else "rule",
            target_id=str(voice_id or rule_id) if voice_id or rule_id else None,
            details={"added_agents": added, "rule_ids": changed_rules},
        )
    await session.flush()
    return added

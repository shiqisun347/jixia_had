"""Host role and asset changes use the real transactional catalog boundaries."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from jx_core.auth.errors import AuthError
from jx_core.models import HostAudioAsset, Rule, RuleAudioGeneration, VoiceProfile
from jx_core.rules.schemas import VoiceProfileUpdate
from jx_core.rules.service import CatalogService
from test_rule_audio_lifecycle_integration import audio_pool as _audio_pool
from test_rule_audio_lifecycle_integration import generate
from test_voice_rule_agent_sync_integration import pool as _pool

audio_pool = _audio_pool
pool = _pool

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_host_sound_change_starts_new_frozen_generation(audio_pool: Any) -> None:
    factory, rule_id, admin_id, _, _, config, _ = audio_pool
    old = await generate(factory, rule_id, admin_id, config)
    async with factory() as session:
        rule = await session.get(Rule, rule_id)
        host_id = rule.host_voice_profile_id
        old_revision = rule.config_revision
    async with factory() as session:
        await CatalogService().update_voice(
            session,
            voice_id=host_id,
            actor_user_id=admin_id,
            audio_configuration=config,
            payload=VoiceProfileUpdate(
                name="本场主持", kind="HOST", provider_voice="xuyanchu_v3.1", rate=1.1
            ),
        )
    async with factory() as session:
        rule = await session.get(Rule, rule_id)
        previous = await session.get(RuleAudioGeneration, old.id)
        current = await session.get(RuleAudioGeneration, rule.current_audio_generation_id)
        assets = list(
            (
                await session.scalars(
                    select(HostAudioAsset).where(HostAudioAsset.generation_id == current.id)
                )
            ).all()
        )
        assert current.id != old.id
        assert previous.is_current is False
        assert rule.config_revision == old_revision + 1
        assert current.review_status != "APPROVED"
        assert {item.input_snapshot["rate"] for item in assets} == {1.1}
        assert {item.input_snapshot["provider_voice"] for item in assets} == {"xuyanchu_v3.1"}


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["HOST", "AGENT"])
async def test_catalog_cannot_convert_voice_role_in_either_direction(
    pool: Any, source: str
) -> None:
    factory, context, _, host_id, voice_id, _ = pool
    identity = host_id if source == "HOST" else voice_id
    async with factory() as session:
        row = await session.get(VoiceProfile, identity)
        provider_voice = row.provider_voice
    async with factory() as session:
        with pytest.raises(AuthError, match="voice_kind_immutable"):
            await CatalogService().update_voice(
                session,
                voice_id=identity,
                actor_user_id=context.user_id,
                payload=VoiceProfileUpdate(
                    name="不能改角色",
                    kind="AGENT" if source == "HOST" else "HOST",
                    provider_voice=provider_voice,
                    avatar_key="agent-01" if source == "HOST" else None,
                ),
            )

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from jx_core.admin_bulk_routes import BulkRequest, create_bulk
from jx_core.auth.errors import AuthError
from jx_core.auth.passwords import PasswordService
from jx_core.auth.session import AuthContext
from jx_core.models import (
    AgentProfile,
    AgentPromptOverride,
    BulkJob,
    BulkJobItem,
    ModelProfile,
    Rule,
    RuleStage,
    User,
    VoiceProfile,
)
from jx_core.rooms.service import RoomService
from jx_core.rules.agent_pool import lock_agent_pool, sync_agent_pools
from jx_core.rules.routes import list_rule_agents, update_catalog_status
from jx_core.rules.schemas import (
    CatalogStatusUpdate,
    RuleBasicUpdate,
    RuleCreate,
    VoiceProfileUpdate,
)
from jx_core.rules.service import CatalogService, RuleService

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def pool():
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("requires explicitly enabled independent PostgreSQL")
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or url == os.environ.get("DATABASE_URL"):
        pytest.fail("TEST_DATABASE_URL must be an independent test database")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex[:10]
    async with factory() as session:
        async with session.begin():
            admin = (await session.scalars(select(User).where(User.role == "ADMIN"))).first()
            created_admin = admin is None
            if admin is None:
                admin = User(
                    username=f"sync-admin-{suffix}",
                    username_normalized=f"sync-admin-{suffix}",
                    real_name="同步集成管理员",
                    role="ADMIN",
                    password_hash=PasswordService().hash(uuid4().hex),
                )
                session.add(admin)
                await session.flush()
            model = ModelProfile(name=f"sync-model-{suffix}", config_ref=f"sync-{suffix}")
            host = VoiceProfile(name=f"sync-host-{suffix}", kind="HOST", provider_voice=suffix)
            voice = VoiceProfile(
                name=f"sync-voice-{suffix}",
                kind="AGENT",
                provider_voice=f"agent-{suffix}",
                avatar_key="agent-01",
                status="DISABLED",
            )
            session.add_all([model, host, voice])
            await session.flush()
            rules = [
                Rule(
                    rule_key=f"sync-rule-{suffix}-{index}",
                    version=1,
                    name=f"sync-rule-{index}",
                    side_size=4,
                    estimated_seconds=10,
                    status="READY",
                    created_by=admin.id,
                    host_voice_profile_id=host.id,
                    default_agent_model_profile_id=model.id,
                )
                for index in range(2)
            ]
            session.add_all(rules)
            await session.flush()
        context = AuthContext(admin.id, "ADMIN", uuid4(), False)
    try:
        yield factory, context, model.id, host.id, voice.id, [rule.id for rule in rules]
    finally:
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    delete(BulkJob).where(
                        BulkJob.id.in_(
                            select(BulkJobItem.job_id).where(BulkJobItem.target_id == voice.id)
                        )
                    )
                )
                await session.execute(delete(Rule).where(Rule.id.in_([r.id for r in rules])))
                await session.execute(
                    delete(AgentProfile).where(AgentProfile.voice_profile_id == voice.id)
                )
                await session.execute(
                    delete(VoiceProfile).where(VoiceProfile.id.in_([voice.id, host.id]))
                )
                await session.execute(delete(ModelProfile).where(ModelProfile.id == model.id))
                if created_admin:
                    await session.execute(delete(User).where(User.id == context.user_id))
        await engine.dispose()


async def _voice_status(factory, context, voice_id, status):
    async with factory() as session:
        await update_catalog_status(
            "voices", voice_id, CatalogStatusUpdate(status=status), context, session
        )


@pytest.mark.asyncio
async def test_reenable_backfills_every_rule_and_preserves_local_preferences(pool):
    factory, context, model_id, _, voice_id, rule_ids = pool
    await _voice_status(factory, context, voice_id, "ENABLED")
    async with factory() as session:
        agents = list(
            (
                await session.scalars(
                    select(AgentProfile).where(
                        AgentProfile.voice_profile_id == voice_id,
                        AgentProfile.rule_id.in_(rule_ids),
                    )
                )
            ).all()
        )
        assert len(agents) == 2
        first = next(a for a in agents if a.rule_id == rule_ids[0])
        second = next(a for a in agents if a.rule_id == rule_ids[1])
        ids = {a.id for a in agents}
        first.generation_params = {"temperature": 0.42}
        stage = RuleStage(
            rule_id=rule_ids[0], position=1, name="个性 Prompt", stage_kind="FIXED_SPEECH"
        )
        session.add(stage)
        await session.flush()
        override = AgentPromptOverride(
            agent_profile_id=first.id,
            stage_id=stage.id,
            purpose="SPEECH",
            template_text="已保存的个性 Prompt",
        )
        session.add(override)
        await session.flush()
        override_id = override.id
        await session.commit()
        await RuleService().set_agent_status(
            session,
            rule_id=rule_ids[0],
            agent_id=first.id,
            status="DISABLED",
            actor_user_id=context.user_id,
        )
        provider_voice = (await session.get(VoiceProfile, voice_id)).provider_voice
        await session.commit()
        await CatalogService().update_voice(
            session,
            voice_id=voice_id,
            payload=VoiceProfileUpdate(
                name="改名后仍保留赛制选择",
                rate=1.1,
                kind="AGENT",
                provider_voice=provider_voice,
                avatar_key="agent-01",
            ),
            actor_user_id=context.user_id,
        )
    await _voice_status(factory, context, voice_id, "DISABLED")
    async with factory() as session:
        assert second.id not in await RoomService().available_agent_ids(
            session, rule_id=rule_ids[1]
        )
        rows = await list_rule_agents(rule_ids[1], context, session)
        response = next(row for row in rows if row.id == second.id)
        assert response.status == "ENABLED" and not response.available
        assert response.unavailable_reason == "VOICE_DISABLED"
    await _voice_status(factory, context, voice_id, "ENABLED")
    async with factory() as session:
        async with session.begin():
            for rule_id in rule_ids:
                assert await sync_agent_pools(session, voice_id=voice_id, rule_id=rule_id) == 0
        rows = await list_rule_agents(rule_ids[0], context, session)
        first_response = next(row for row in rows if row.id == first.id)
        assert first_response.status == "DISABLED" and not first_response.available
        assert first_response.model_profile_id == model_id
        assert first_response.generation_params == {"temperature": 0.42}
        assert first_response.prompt_override_count == 1
        assert (
            await session.get(AgentPromptOverride, override_id)
        ).template_text == "已保存的个性 Prompt"
        assert first_response.name == "改名后仍保留赛制选择"
        second_response = next(
            row
            for row in await list_rule_agents(rule_ids[1], context, session)
            if row.id == second.id
        )
        assert second_response.available
        found = set(
            (
                await session.scalars(
                    select(AgentProfile.id).where(
                        AgentProfile.voice_profile_id == voice_id,
                        AgentProfile.rule_id.in_(rule_ids),
                    )
                )
            ).all()
        )
        assert found == ids


@pytest.mark.asyncio
async def test_bulk_voice_status_preserves_rule_switch_and_rejects_cross_rule_edit(pool):
    factory, context, _, _, voice_id, rule_ids = pool
    await _voice_status(factory, context, voice_id, "ENABLED")
    async with factory() as session:
        agent = (
            await session.scalars(
                select(AgentProfile).where(
                    AgentProfile.voice_profile_id == voice_id, AgentProfile.rule_id == rule_ids[0]
                )
            )
        ).one()
        agent_id = agent.id
        await session.commit()
        await RuleService().set_agent_status(
            session,
            rule_id=rule_ids[0],
            agent_id=agent_id,
            status="DISABLED",
            actor_user_id=context.user_id,
        )
        with pytest.raises(AuthError):
            await RuleService().set_agent_status(
                session,
                rule_id=rule_ids[1],
                agent_id=agent_id,
                status="ENABLED",
                actor_user_id=context.user_id,
            )
    for operation in ("DISABLE", "ENABLE"):
        async with factory() as session:
            result = await create_bulk(
                BulkRequest(resource="voice", operation=operation, target_ids=[voice_id]),
                Request({"type": "http"}),
                context,
                None,
                session,
            )
            assert result["succeeded_items"] == 1
    async with factory() as session:
        assert (await session.get(AgentProfile, agent_id)).status == "DISABLED"


@pytest.mark.asyncio
async def test_concurrent_enable_and_rule_creation_cannot_miss_voice(pool):
    factory, context, model_id, host_id, voice_id, rule_ids = pool

    async def configure_rule():
        async with factory() as session:
            async with session.begin():
                await lock_agent_pool(session)
                rule = await session.get(Rule, rule_ids[1])
                rule.default_agent_model_profile_id = None
            await RuleService().update_rule_basic(
                session,
                rule_id=rule_ids[1],
                actor_user_id=context.user_id,
                payload=RuleBasicUpdate(
                    name="同步默认模型",
                    host_voice_profile_id=host_id,
                    default_agent_model_profile_id=model_id,
                ),
            )

    await asyncio.gather(configure_rule(), _voice_status(factory, context, voice_id, "ENABLED"))
    async with factory() as session:
        assert (
            len(
                list(
                    (
                        await session.scalars(
                            select(AgentProfile).where(
                                AgentProfile.voice_profile_id == voice_id,
                                AgentProfile.rule_id.in_(rule_ids),
                            )
                        )
                    ).all()
                )
            )
            == 2
        )


@pytest.mark.asyncio
async def test_sync_is_atomic_and_skips_missing_model_or_archived_rules(pool):
    factory, context, _, _, voice_id, rule_ids = pool
    async with factory() as session:
        async with session.begin():
            await lock_agent_pool(session)
            voice = await session.get(VoiceProfile, voice_id)
            voice.status = "ENABLED"
            (await session.get(Rule, rule_ids[0])).default_agent_model_profile_id = None
            (await session.get(Rule, rule_ids[1])).status = "ARCHIVED"
            for rule_id in rule_ids:
                assert await sync_agent_pools(session, voice_id=voice_id, rule_id=rule_id) == 0
        await session.rollback()
    async with factory() as session:
        with pytest.raises(RuntimeError):
            async with session.begin():
                (await session.get(Rule, rule_ids[1])).status = "READY"
                assert await sync_agent_pools(session, voice_id=voice_id, rule_id=rule_ids[1]) == 1
                raise RuntimeError("rollback test")
    async with factory() as session:
        assert not list(
            (
                await session.scalars(
                    select(AgentProfile).where(
                        AgentProfile.voice_profile_id == voice_id,
                        AgentProfile.rule_id.in_(rule_ids),
                    )
                )
            ).all()
        )


@pytest.mark.asyncio
async def test_host_and_historical_rules_do_not_receive_agent_pairs(pool):
    factory, context, _, host_id, voice_id, rule_ids = pool
    async with factory() as session:
        async with session.begin():
            await lock_agent_pool(session)
            assert await sync_agent_pools(session, voice_id=host_id) == 0
            (await session.get(Rule, rule_ids[1])).historical_read_only = True
    await _voice_status(factory, context, voice_id, "ENABLED")
    async with factory() as session:
        agents = list(
            (
                await session.scalars(
                    select(AgentProfile).where(
                        AgentProfile.voice_profile_id == voice_id,
                        AgentProfile.rule_id.in_(rule_ids),
                    )
                )
            ).all()
        )
        assert len(agents) == 1 and agents[0].rule_id == rule_ids[0]


@pytest.mark.asyncio
async def test_concurrent_new_rule_and_voice_enable_are_complete(pool):
    factory, context, model_id, host_id, voice_id, _ = pool

    async def create_rule():
        async with factory() as session:
            rule = await RuleService().create_rule(
                session,
                creator_user_id=context.user_id,
                payload=RuleCreate.model_validate(
                    {
                        "host_voice_profile_id": str(host_id),
                        "default_agent_model_profile_id": str(model_id),
                        "draft": {
                            "name": "并发创建规则",
                            "side_size": 4,
                            "stages": [
                                {
                                    "name": "准备",
                                    "stage_kind": "PREPARATION",
                                    "duration_seconds": 1,
                                },
                                {"name": "结束", "stage_kind": "END"},
                            ],
                        },
                    }
                ),
            )
            return rule.id

    rule_id, _ = await asyncio.gather(
        create_rule(), _voice_status(factory, context, voice_id, "ENABLED")
    )
    try:
        async with factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(AgentProfile).where(
                            AgentProfile.rule_id == rule_id,
                            AgentProfile.voice_profile_id == voice_id,
                        )
                    )
                ).all()
            )
            assert len(rows) == 1 and rows[0].status == "ENABLED"
    finally:
        async with factory() as session:
            async with session.begin():
                await session.execute(delete(Rule).where(Rule.id == rule_id))

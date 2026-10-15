"""PostgreSQL regressions for Spec 264's identity and convergence boundaries."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jx_core.auth.errors import AuthError
from jx_core.models import (
    BackgroundTask,
    HostAudioAsset,
    Rule,
    RuleAudioGeneration,
    RuleStage,
    User,
    VoiceProfile,
)
from jx_core.rules.audio import AudioConfiguration, aggregate, create_generation, repair_generation
from jx_core.rules.schemas import RuleStageCreate, RuleStageReorder
from jx_core.rules.service import RuleService

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def audio_pool(tmp_path: Path):
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("requires explicitly enabled independent PostgreSQL")
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or url == os.environ.get("DATABASE_URL"):
        pytest.fail("TEST_DATABASE_URL must be independent")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex[:10]
    async with factory() as session:
        async with session.begin():
            user = User(
                username=f"audio-{suffix}",
                username_normalized=f"audio-{suffix}",
                real_name="音频集成测试",
                password_hash="unused-test-hash",
            )
            host = VoiceProfile(name=f"host-{suffix}", kind="HOST", provider_voice="xuyanchu_v3.1")
            session.add_all([user, host])
            await session.flush()
            rule = Rule(
                rule_key=f"audio-{suffix}",
                version=1,
                name="音频生命周期",
                side_size=4,
                estimated_seconds=10,
                created_by=user.id,
                host_voice_profile_id=host.id,
                lifecycle_status="DISABLED",
            )
            session.add(rule)
            await session.flush()
            first = RuleStage(
                rule_id=rule.id,
                position=1,
                name="准备",
                stage_kind="PREPARATION",
                duration_seconds=10,
                start_host_text="欢迎来到稷下。",
            )
            end = RuleStage(
                rule_id=rule.id,
                position=2,
                name="结束",
                stage_kind="END",
                end_host_text="比赛结束。",
            )
            session.add_all([first, end])
    config = AudioConfiguration(storage_root=str(tmp_path))
    try:
        yield factory, rule.id, user.id, first.id, end.id, config, tmp_path
    finally:
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    delete(BackgroundTask).where(
                        BackgroundTask.payload["rule_id"].astext == str(rule.id)
                    )
                )
                await session.execute(delete(Rule).where(Rule.id == rule.id))
                await session.execute(delete(VoiceProfile).where(VoiceProfile.id == host.id))
                await session.execute(delete(User).where(User.id == user.id))
        await engine.dispose()


async def generate(factory, rule_id, user_id, config):
    async with factory() as session:
        return await RuleService(config).generate_audio(
            session, rule_id=rule_id, actor_user_id=user_id
        )


@pytest.mark.asyncio
async def test_last_success_converges_and_only_current_manifest_can_be_approved(audio_pool):
    factory, rule_id, user_id, _, _, config, root = audio_pool
    generation = await generate(factory, rule_id, user_id, config)
    async with factory() as session:
        async with session.begin():
            rule = await session.get(Rule, rule_id)
            current = await session.get(RuleAudioGeneration, generation.id)
            assets = list(
                (
                    await session.scalars(
                        select(HostAudioAsset).where(HostAudioAsset.generation_id == generation.id)
                    )
                ).all()
            )
            for asset in assets:
                path = root / f"{asset.id}.ogg"
                path.write_bytes(b"fake-provider-fixture")
                asset.storage_path, asset.status, asset.duration_ms = path.name, "READY", 1000
            await aggregate(session, rule, current)
            assert current.status == "SUCCEEDED" and current.review_status == "PENDING"
            assert rule.status == "READY" and rule.audio_reviewed_at is None
    async with factory() as session:
        with pytest.raises(AuthError):
            await RuleService(config).review_audio(
                session,
                rule_id=rule_id,
                actor_user_id=user_id,
                generation_id=uuid4(),
                manifest_hash=generation.manifest_hash,
            )
        rule = await session.get(Rule, rule_id)
        assert rule.audio_reviewed_at is None
        await session.rollback()
        await RuleService(config).review_audio(
            session,
            rule_id=rule_id,
            actor_user_id=user_id,
            generation_id=generation.id,
            manifest_hash=generation.manifest_hash,
        )
        assert (await session.get(RuleAudioGeneration, generation.id)).review_status == "APPROVED"


@pytest.mark.asyncio
async def test_missing_asset_is_failed_and_retry_repairs_only_missing_asset(audio_pool):
    factory, rule_id, user_id, _, _, config, _ = audio_pool
    generation = await generate(factory, rule_id, user_id, config)
    async with factory() as session:
        async with session.begin():
            assets = list(
                (
                    await session.scalars(
                        select(HostAudioAsset).where(HostAudioAsset.generation_id == generation.id)
                    )
                ).all()
            )
            keep, missing = assets
            keep.status, keep.duration_ms, keep.storage_path = "READY", 1000, "keep.ogg"
            keep_id = keep.id
            await session.delete(missing)
            rule = await session.get(Rule, rule_id)
            await aggregate(session, rule, await session.get(RuleAudioGeneration, generation.id))
            assert (await session.get(RuleAudioGeneration, generation.id)).status == "FAILED"
    async with factory() as session:
        await RuleService(config).retry_audio(session, rule_id=rule_id, actor_user_id=user_id)
        assets = list(
            (
                await session.scalars(
                    select(HostAudioAsset).where(HostAudioAsset.generation_id == generation.id)
                )
            ).all()
        )
        assert len(assets) == 2
        assert (await session.get(HostAudioAsset, keep_id)).status == "READY"
        assert sum(asset.status == "PENDING" for asset in assets) == 1


@pytest.mark.asyncio
async def test_exhausted_crash_lease_converges_to_failure(audio_pool):
    factory, rule_id, user_id, _, _, config, _ = audio_pool
    generation = await generate(factory, rule_id, user_id, config)
    async with factory() as session:
        async with session.begin():
            assets = list(
                (
                    await session.scalars(
                        select(HostAudioAsset).where(HostAudioAsset.generation_id == generation.id)
                    )
                ).all()
            )
            for asset in assets:
                task = await session.get(BackgroundTask, asset.active_task_id)
                task.attempts, task.status = task.max_attempts, "RUNNING"
                task.lease_until = datetime.now(UTC) - timedelta(seconds=1)
                asset.status = "RUNNING"
            current = await repair_generation(session, rule=await session.get(Rule, rule_id))
            assert current.status == "FAILED" and current.failed_count == 2
            assert all(asset.status == "FAILED" for asset in assets)


@pytest.mark.asyncio
async def test_superseded_aggregation_cannot_overwrite_current_batch(audio_pool):
    factory, rule_id, user_id, first_id, _, config, _ = audio_pool
    old = await generate(factory, rule_id, user_id, config)
    async with factory() as session:
        async with session.begin():
            rule = await session.get(Rule, rule_id)
            (await session.get(RuleStage, first_id)).start_host_text = "新的主持词。"
            current = await create_generation(
                session, rule=rule, actor_user_id=user_id, configuration=config
            )
            previous = await session.get(RuleAudioGeneration, old.id)
            await aggregate(session, rule, previous)
            assert previous.status == "SUPERSEDED"
            assert rule.current_audio_generation_id == current.id
            assert current.status == "QUEUED" and rule.status == "GENERATING_AUDIO"


@pytest.mark.asyncio
async def test_stage_operations_keep_end_last_and_rollback_invalid_structure(audio_pool):
    factory, rule_id, user_id, first_id, end_id, config, _ = audio_pool
    async with factory() as session:
        with pytest.raises(AuthError):
            await RuleService(config).reorder_rule_stages(
                session,
                rule_id=rule_id,
                actor_user_id=user_id,
                payload=RuleStageReorder(stage_ids=[end_id, first_id]),
            )
        with pytest.raises(AuthError):
            await RuleService(config).create_rule_stage(
                session,
                rule_id=rule_id,
                actor_user_id=user_id,
                payload=RuleStageCreate(name="无动作", stage_kind="FIXED_SPEECH"),
            )
        stage = await RuleService(config).create_rule_stage(
            session,
            rule_id=rule_id,
            actor_user_id=user_id,
            payload=RuleStageCreate(name="新增准备", stage_kind="PREPARATION", duration_seconds=20),
        )
        rows = list(
            (
                await session.scalars(
                    select(RuleStage)
                    .where(RuleStage.rule_id == rule_id)
                    .order_by(RuleStage.position)
                )
            ).all()
        )
        stage_id = stage.id
        assert [row.id for row in rows] == [first_id, stage_id, end_id]
        await session.rollback()
        await RuleService(config).delete_rule_stage(
            session, rule_id=rule_id, stage_id=stage_id, actor_user_id=user_id
        )
        assert (await session.get(Rule, rule_id)).estimated_seconds == 10

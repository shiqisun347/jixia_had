from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from unittest.mock import Mock
from uuid import UUID, uuid4

import av
import pytest
import pytest_asyncio
from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, select

from jx_core.models import (
    BackgroundTask,
    ExternalCall,
    HostAudioAsset,
    Rule,
    RuleAudioGeneration,
    RuleStage,
    User,
    VoiceProfile,
)
from jx_core.rules.audio import AudioConfiguration, create_generation, fingerprint
from jx_jobs.config import Settings  # pyright: ignore[reportMissingTypeStubs]
from jx_jobs.database import Database  # pyright: ignore[reportMissingTypeStubs]
from jx_jobs.host_tts_worker import process_one_host_tts  # pyright: ignore[reportMissingTypeStubs]


class FakeTTSClient:
    model = "qwen-audio-3.1-tts-flash"

    def __init__(self) -> None:
        self.calls = 0

    async def synthesize_to_file(
        self, *, text: str, voice: str, rate: float, output_path: Path
    ) -> None:
        self.calls += 1
        assert text == "主持测试文本。"
        assert voice == "host-probe"
        assert rate == 1.0
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with av.open(str(output_path), mode="w", format="ogg") as container:
            stream = container.add_stream(  # pyright: ignore[reportUnknownMemberType]
                "libopus", rate=48_000
            )
            stream.layout = "mono"
            for _ in range(5):
                frame = av.AudioFrame(format="s16", layout="mono", samples=960)
                frame.sample_rate = 48_000
                frame.planes[0].update(bytes(960 * 2))
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)


pytestmark = pytest.mark.integration


def _integration_settings() -> Settings:
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("set RUN_DATABASE_INTEGRATION=1 to run real PostgreSQL tests")
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.fail("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    if database_url == os.environ.get("DATABASE_URL"):
        pytest.fail("TEST_DATABASE_URL must be independent of DATABASE_URL")
    try:
        settings_factory = cast(Callable[..., Settings], Settings)
        return settings_factory(
            database_url=SecretStr(database_url), app_env="test", _env_file=None
        )
    except ValidationError:
        pytest.fail(
            "TEST_DATABASE_URL must be a valid postgresql+psycopg URL",
            pytrace=False,
        )


@pytest.mark.asyncio
async def test_jobs_database_ping_uses_real_postgres() -> None:
    settings = _integration_settings()
    database = Database(settings.database_url_value)

    try:
        assert await database.ping() is True
    finally:
        await database.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("configuration", ["missing", "same"])
async def test_integration_database_guard_rejects_before_connect(
    monkeypatch: pytest.MonkeyPatch, configuration: str
) -> None:
    url = "postgresql+psycopg://test:test@synthetic.invalid/unsafe"
    monkeypatch.setenv("RUN_DATABASE_INTEGRATION", "1")
    monkeypatch.setenv("DATABASE_URL", url)
    if configuration == "missing":
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("TEST_DATABASE_URL", url)
    database = Mock(side_effect=AssertionError("database construction must not occur"))
    monkeypatch.setattr(sys.modules[__name__], "Database", database)
    with pytest.raises(pytest.fail.Exception, match="TEST_DATABASE_URL"):
        await test_jobs_database_ping_uses_real_postgres()
    database.assert_not_called()


@dataclass(frozen=True)
class HostAudioTaskFixture:
    database: Database
    storage_root: Path
    rule_id: UUID
    generation_id: UUID
    asset_id: UUID
    task_id: UUID


@pytest_asyncio.fixture
async def host_audio_task(tmp_path: Path) -> AsyncIterator[HostAudioTaskFixture]:
    settings = _integration_settings()
    database = Database(settings.database_url_value)
    user_id, voice_id, rule_id = (uuid4() for _ in range(3))
    asset_id: UUID | None = None
    task_id: UUID | None = None
    suffix = uuid4().hex[:12]
    try:
        async with database.session_factory() as session:
            async with session.begin():
                session.add_all(
                    [
                        User(
                            id=user_id,
                            username=f"jobs-{suffix}",
                            username_normalized=f"jobs-{suffix}",
                            real_name="任务管理员",
                            password_hash="unused-test-hash",
                        ),
                        VoiceProfile(
                            id=voice_id,
                            name=f"主持-{suffix}",
                            kind="HOST",
                            provider_voice="host-probe",
                            rate=1.0,
                            status="ENABLED",
                        ),
                    ]
                )
                await session.flush()
                rule = Rule(
                    id=rule_id,
                    rule_key=f"jobs-{suffix}",
                    version=1,
                    name="任务规则",
                    side_size=4,
                    estimated_seconds=60,
                    created_by=user_id,
                    host_voice_profile_id=voice_id,
                    lifecycle_status="DISABLED",
                    config_revision=3,
                )
                session.add(rule)
                await session.flush()
                session.add_all(
                    [
                        RuleStage(
                            rule_id=rule_id,
                            position=1,
                            name="准备",
                            stage_kind="PREPARATION",
                            duration_seconds=60,
                            start_host_text="主持测试文本。",
                        ),
                        RuleStage(
                            rule_id=rule_id,
                            position=2,
                            name="结束",
                            stage_kind="END",
                        ),
                    ]
                )
                await session.flush()
                generation = await create_generation(
                    session,
                    rule=rule,
                    actor_user_id=user_id,
                    configuration=AudioConfiguration(
                        model=FakeTTSClient.model,
                        websocket_url="wss://synthetic.invalid/tts",
                        storage_root=str(tmp_path),
                    ),
                )
                asset = (
                    await session.scalars(
                        select(HostAudioAsset).where(HostAudioAsset.generation_id == generation.id)
                    )
                ).one()
                asset_id, task_id = asset.id, asset.active_task_id
                assert task_id is not None
                task = await session.get(BackgroundTask, task_id)
                assert task is not None
                assert generation.is_current and rule.current_audio_generation_id == generation.id
                assert generation.manifest_hash == fingerprint(generation.manifest)
                assert asset.input_hash == fingerprint(asset.input_snapshot)
                assert task.payload == {
                    "rule_id": str(rule_id),
                    "generation_id": str(generation.id),
                    "asset_id": str(asset_id),
                    "manifest_hash": generation.manifest_hash,
                    "config_revision": 3,
                }
        yield HostAudioTaskFixture(database, tmp_path, rule_id, generation.id, asset_id, task_id)
    finally:
        async with database.session_factory() as session:
            async with session.begin():
                if asset_id is not None:
                    await session.execute(
                        delete(ExternalCall).where(
                            ExternalCall.source_kind == "HOST_AUDIO",
                            ExternalCall.source_resource_id == str(asset_id),
                        )
                    )
                if task_id is not None:
                    await session.execute(
                        delete(BackgroundTask).where(BackgroundTask.id == task_id)
                    )
                await session.execute(delete(Rule).where(Rule.id == rule_id))
                await session.execute(delete(VoiceProfile).where(VoiceProfile.id == voice_id))
                await session.execute(delete(User).where(User.id == user_id))
        await database.dispose()


@pytest.mark.asyncio
async def test_host_tts_task_is_claimed_and_published_with_real_postgres(
    host_audio_task: HostAudioTaskFixture,
) -> None:
    case = host_audio_task
    client = FakeTTSClient()
    assert await process_one_host_tts(
        case.database.session_factory, client=client, storage_root=case.storage_root
    )
    assert client.calls == 1
    async with case.database.session_factory() as session:
        asset = await session.get(HostAudioAsset, case.asset_id)
        task = await session.get(BackgroundTask, case.task_id)
        generation = await session.get(RuleAudioGeneration, case.generation_id)
        rule = await session.get(Rule, case.rule_id)
        assert (
            asset is not None and task is not None and generation is not None and rule is not None
        )
        assert asset.status == "READY" and asset.error_code is None
        assert asset.duration_ms is not None and asset.duration_ms > 0
        assert asset.byte_count is not None and asset.byte_count > 0
        assert asset.checksum is not None and len(asset.checksum) == 64
        assert asset.storage_path is not None
        assert (case.storage_root / asset.storage_path).read_bytes().startswith(b"OggS")
        assert task.status == "SUCCEEDED" and task.error_code is None
        assert task.attempts == 1 and task.lease_until is None
        assert generation.status == "SUCCEEDED" and generation.review_status == "PENDING"
        assert (generation.total_count, generation.ready_count, generation.failed_count) == (
            1,
            1,
            0,
        )
        assert generation.completed_at is not None and generation.approved_at is None
        assert rule.current_audio_generation_id == generation.id
        assert rule.status == "READY" and rule.lifecycle_status == "DISABLED"
        assert rule.audio_reviewed_at is None


@pytest.mark.asyncio
async def test_host_tts_missing_generation_is_rejected_without_provider_call(
    host_audio_task: HostAudioTaskFixture,
) -> None:
    case = host_audio_task
    async with case.database.session_factory() as session:
        async with session.begin():
            task = await session.get(BackgroundTask, case.task_id)
            assert task is not None
            task.payload = {
                key: value for key, value in task.payload.items() if key != "generation_id"
            }
    client = FakeTTSClient()
    assert await process_one_host_tts(
        case.database.session_factory, client=client, storage_root=case.storage_root
    )
    assert client.calls == 0
    assert not list(case.storage_root.rglob("*.ogg"))
    async with case.database.session_factory() as session:
        task = await session.get(BackgroundTask, case.task_id)
        asset = await session.get(HostAudioAsset, case.asset_id)
        generation = await session.get(RuleAudioGeneration, case.generation_id)
        assert task is not None and asset is not None and generation is not None
        assert task.status == "FAILED" and task.error_code == "tts_generation_stale"
        assert task.attempts == 1 and task.lease_until is None
        assert asset.status == "PENDING" and asset.storage_path is None
        assert generation.status == "QUEUED" and generation.review_status == "PENDING"
        assert generation.ready_count == 0
        assert (
            await session.scalar(
                select(ExternalCall.id).where(
                    ExternalCall.source_kind == "HOST_AUDIO",
                    ExternalCall.source_resource_id == str(case.asset_id),
                )
            )
            is None
        )
    assert not await process_one_host_tts(
        case.database.session_factory, client=client, storage_root=case.storage_root
    )
    assert client.calls == 0
    async with case.database.session_factory() as session:
        task = await session.get(BackgroundTask, case.task_id)
        asset = await session.get(HostAudioAsset, case.asset_id)
        generation = await session.get(RuleAudioGeneration, case.generation_id)
        assert task is not None and asset is not None and generation is not None
        assert task.status == "FAILED" and task.error_code == "tts_generation_stale"
        assert task.attempts == 1 and task.lease_until is None
        assert asset.status == "FAILED" and asset.error_code == "tts_generation_stale"
        assert asset.storage_path is None
        assert generation.status == "FAILED" and generation.review_status == "PENDING"
        assert generation.last_error_code == "tts_generation_stale"
        assert (generation.total_count, generation.ready_count, generation.failed_count) == (
            1,
            0,
            1,
        )
        assert generation.completed_at is not None and generation.approved_at is None

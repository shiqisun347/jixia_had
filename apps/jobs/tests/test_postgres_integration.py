from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from jx_jobs.config import Settings
from jx_jobs.database import Database
from jx_jobs.host_tts_worker import process_one_host_tts


class FakeTTSClient:
    async def synthesize_to_file(
        self, *, text: str, voice: str, rate: float, output_path: Path
    ) -> None:
        assert text == "主持测试文本。"
        assert voice == "host-probe"
        assert rate == 1.0
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"OggS-probe")


pytestmark = pytest.mark.integration


def _integration_settings() -> Settings:
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("set RUN_DATABASE_INTEGRATION=1 to run real PostgreSQL tests")
    database_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.fail("DATABASE_URL is required for PostgreSQL integration tests")
    try:
        return Settings(database_url=database_url, app_env="test")
    except ValidationError:
        pytest.fail(
            "DATABASE_URL must be a valid postgresql+psycopg URL",
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
async def test_host_tts_task_is_claimed_and_published_with_real_postgres() -> None:
    settings = _integration_settings()
    database = Database(settings.database_url_value)
    user_id, voice_id, rule_id, asset_id, task_id = (uuid4() for _ in range(5))
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO users (id, username, username_normalized, real_name, "
                        "password_hash) VALUES (:id, 'jobsadmin', 'jobsadmin', "
                        "'任务管理员', 'probe')"
                    ),
                    {"id": user_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO voice_profiles (id, name, kind, provider_voice, rate, status) "
                        "VALUES (:id, '主持', 'HOST', 'host-probe', 1.0, 'ENABLED')"
                    ),
                    {"id": voice_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO rules (id, rule_key, version, name, side_size, "
                        "estimated_seconds, status, created_by) VALUES "
                        "(:id, 'jobs-rule', 1, '任务规则', 1, 60, 'GENERATING_AUDIO', :user_id)"
                    ),
                    {"id": rule_id, "user_id": user_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO host_audio_assets "
                        "(id, rule_id, segment_key, text, text_hash, voice_profile_id, status) "
                        "VALUES (:id, :rule_id, 'start', '主持测试文本。', :hash, "
                        ":voice_id, 'PENDING')"
                    ),
                    {"id": asset_id, "rule_id": rule_id, "hash": "a" * 64, "voice_id": voice_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO background_tasks (id, task_type, payload, status, attempts, "
                        "max_attempts) VALUES (:id, 'HOST_TTS', CAST(:payload AS jsonb), "
                        "'PENDING', 0, 2)"
                    ),
                    {
                        "id": task_id,
                        "payload": json.dumps({"asset_id": str(asset_id), "rule_id": str(rule_id)}),
                    },
                )
        with TemporaryDirectory() as directory:
            assert await process_one_host_tts(
                database.session_factory,
                client=FakeTTSClient(),
                storage_root=Path(directory),
            )
            async with database.session_factory() as session:
                row = (
                    (
                        await session.execute(
                            text(
                                "SELECT a.status AS asset_status, a.storage_path, "
                                "t.status AS task_status "
                                "FROM host_audio_assets a JOIN background_tasks t "
                                "ON t.id = :task_id "
                                "WHERE a.id = :asset_id"
                            ),
                            {"task_id": task_id, "asset_id": asset_id},
                        )
                    )
                    .mappings()
                    .one()
                )
            assert row["asset_status"] == "READY"
            assert row["task_status"] == "SUCCEEDED"
            assert (Path(directory) / row["storage_path"]).read_bytes() == b"OggS-probe"
    finally:
        async with database.session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "TRUNCATE background_tasks, host_audio_assets, rules, voice_profiles, "
                        "users CASCADE"
                    )
                )
        await database.dispose()

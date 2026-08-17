from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jx_core.admin_cli import create_admin as create_admin_with_cli
from jx_core.app import create_app
from jx_core.auth.errors import AuthError
from jx_core.auth.service import AuthService
from jx_core.auth.session import SessionService
from jx_core.config import Settings
from jx_core.legal.terms import get_current_human_participation_terms
from jx_core.matches.domain import MatchCommand
from jx_core.matches.service import MatchRuntimeManager
from jx_core.models import (
    BackgroundTask,
    HostAudioAsset,
    Match,
    MatchEvent,
    Room,
    RoomMember,
    Rule,
    Seat,
    Speech,
    User,
)
from jx_core.room_connections import RoomConnectionService
from jx_core.rooms.schemas import (
    DeviceCheckRequest,
    RoleChangeRequest,
    RoomCreateRequest,
    RoomJoinRequest,
    SeatSelectRequest,
)
from jx_core.rooms.service import RoomService
from jx_core.rules.schemas import (
    AgentProfileCreate,
    AgentProfileUpdate,
    ModelProfileCreate,
    RuleCreate,
    TopicCreate,
    VoiceProfileCreate,
)
from jx_core.rules.service import CatalogService, RuleService
from jx_core.runtime import CoreRuntime

pytestmark = pytest.mark.integration


def _test_database_url() -> str:
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("set RUN_DATABASE_INTEGRATION=1 to run real PostgreSQL tests")
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.fail("TEST_DATABASE_URL is required for auth migration tests")
    if value == os.environ.get("DATABASE_URL"):
        pytest.fail("TEST_DATABASE_URL must not equal DATABASE_URL")
    return value


def _run_alembic(database_url: str, *arguments: str) -> None:
    environment = {**os.environ, "DATABASE_URL": database_url}
    completed = subprocess.run(
        ["uv", "run", "--package", "jx-core", "alembic", *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != 0:
        raise AssertionError("Alembic command failed without exposing its captured output")


@pytest.mark.asyncio
async def test_0020_makes_voice_avatar_authoritative_and_removes_agent_copy(
    auth_database_url: str,
) -> None:
    _run_alembic(auth_database_url, "downgrade", "0018_profile_avatar_catalog")
    engine = create_async_engine(auth_database_url)
    voice_id = uuid4()
    model_id = uuid4()
    agent_id = uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO voice_profiles "
                    "(id, name, kind, provider_voice, rate, status) "
                    "VALUES (:id, '龙涟霓蓉', 'AGENT', :provider, 1.0, 'ENABLED')"
                ),
                {"id": voice_id, "provider": f"migration-voice-{voice_id}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO model_profiles (id, name, config_ref, status) "
                    "VALUES (:id, :name, :config, 'ENABLED')"
                ),
                {
                    "id": model_id,
                    "name": f"migration-model-{model_id}",
                    "config": f"migration-config-{model_id}",
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO agent_profiles "
                    "(id, name, model_profile_id, voice_profile_id, avatar_key, status) "
                    "VALUES (:id, :name, :model_id, :voice_id, 'agent-12', 'ENABLED')"
                ),
                {
                    "id": agent_id,
                    "name": f"migration-agent-{agent_id}",
                    "model_id": model_id,
                    "voice_id": voice_id,
                },
            )
        await engine.dispose()
        _run_alembic(auth_database_url, "upgrade", "head")
        engine = create_async_engine(auth_database_url)
        async with engine.begin() as connection:
            avatar_key = await connection.scalar(
                text("SELECT avatar_key FROM voice_profiles WHERE id = :id"),
                {"id": voice_id},
            )
            agent_avatar_column_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'agent_profiles' AND column_name = 'avatar_key'"
                )
            )
            assert avatar_key == "agent-08"
            assert agent_avatar_column_count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_agent_catalog_duplicate_and_edit(auth_database_url: str) -> None:
    engine = create_async_engine(auth_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    catalog = CatalogService()
    try:
        async with factory() as session:
            voice = await catalog.create_voice(
                session,
                payload=VoiceProfileCreate(
                    name="编辑测试音色",
                    kind="AGENT",
                    provider_voice="agent-edit-voice",
                    avatar_key="agent-08",
                ),
            )
            model = await catalog.create_model(
                session,
                payload=ModelProfileCreate(name="编辑测试模型", config_ref="agent-edit-model"),
            )
            agent = await catalog.create_agent(
                session,
                payload=AgentProfileCreate(
                    name="  编辑前  ",
                    model_profile_id=model.id,
                    voice_profile_id=voice.id,
                ),
            )
            assert agent.name == "编辑前"
            assert voice.avatar_key == "agent-08"

            with pytest.raises(AuthError, match="agent_name_taken"):
                await catalog.create_agent(
                    session,
                    payload=AgentProfileCreate(
                        name="编辑前",
                        model_profile_id=model.id,
                        voice_profile_id=voice.id,
                    ),
                )

            updated = await catalog.update_agent(
                session,
                agent_id=agent.id,
                payload=AgentProfileUpdate(
                    name="编辑后",
                    model_profile_id=model.id,
                    voice_profile_id=voice.id,
                    system_prompt="系统提示",
                    debater_prompt="辩手提示",
                    generation_params={"temperature": 0.8},
                ),
            )
            assert updated.name == "编辑后"
            assert updated.system_prompt == "系统提示"
            assert updated.generation_params == {"temperature": 0.8}
    finally:
        await engine.dispose()


@pytest.fixture
async def auth_database_url() -> AsyncGenerator[str, None]:
    database_url = _test_database_url()
    _run_alembic(database_url, "upgrade", "head")
    yield database_url
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE match_events, speeches, matches, background_tasks, device_checks, "
                    "seats, room_members, rooms, "
                    "host_audio_assets, stage_actions, rule_stages, rules, topics, "
                    "agent_profiles, model_profiles, voice_profiles, audit_logs, "
                    "room_connections, user_consents, sessions, users CASCADE"
                )
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_0023_free_debate_decision_schema_is_complete(
    auth_database_url: str,
) -> None:
    engine = create_async_engine(auth_database_url)
    try:
        async with engine.connect() as connection:
            columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = 'public' "
                            "AND table_name = 'agent_free_debate_decisions'"
                        )
                    )
                ).scalars()
            )
            constraints = set(
                (
                    await connection.execute(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE conrelid = 'agent_free_debate_decisions'::regclass"
                        )
                    )
                ).scalars()
            )
        assert {
            "match_id",
            "decision_round_id",
            "agent_profile_id",
            "should_speak",
            "willingness",
            "duration_ms",
            "final_queue_rank",
            "human_hand_at_result",
            "human_hand_at_lock",
            "selected",
            "fallback",
        }.issubset(columns)
        assert {
            "uq_agent_free_debate_decisions_round_agent",
            "ck_agent_free_debate_decisions_status",
            "ck_agent_free_debate_decisions_willingness",
        }.issubset(constraints)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rule_catalog_creation_queues_audio_and_requires_review(
    auth_database_url: str,
) -> None:
    engine = create_async_engine(auth_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    catalog = CatalogService()
    rules = RuleService()
    try:
        async with factory() as database_session:
            admin = await AuthService().create_admin(
                database_session,
                username="rule-admin",
                real_name="规则管理员",
                password="rule-admin-password-123",
            )
            host_voice = await catalog.create_voice(
                database_session,
                payload=VoiceProfileCreate(
                    name="主持音色",
                    kind="HOST",
                    provider_voice="host-probe",
                    rate=1.0,
                ),
            )
            agent_voice = await catalog.create_voice(
                database_session,
                payload=VoiceProfileCreate(
                    name="Agent 音色",
                    kind="AGENT",
                    provider_voice="agent-probe",
                    rate=1.0,
                    avatar_key="agent-01",
                ),
            )
            model = await catalog.create_model(
                database_session,
                payload=ModelProfileCreate(name="测试模型", config_ref="model-probe"),
            )
            agent = await catalog.create_agent(
                database_session,
                payload=AgentProfileCreate(
                    name="测试 Agent",
                    model_profile_id=model.id,
                    voice_profile_id=agent_voice.id,
                ),
            )
            topic = await catalog.create_topic(
                database_session,
                creator_user_id=admin.id,
                payload=TopicCreate(
                    title="效率与公平",
                    affirmative_text="更应重视效率",
                    negative_text="更应重视公平",
                ),
            )
            rule = await rules.create_rule(
                database_session,
                creator_user_id=admin.id,
                payload=RuleCreate.model_validate(
                    {
                        "host_voice_profile_id": str(host_voice.id),
                        "draft": {
                            "name": "一对一测试规则",
                            "side_size": 1,
                            "stages": [
                                {
                                    "name": "正方立论",
                                    "stage_kind": "FIXED_SPEECH",
                                    "start_host_text": "正方开始立论。",
                                    "actions": [
                                        {
                                            "side": "AFFIRMATIVE",
                                            "seat_no": 1,
                                            "duration_seconds": 60,
                                        }
                                    ],
                                },
                                {"name": "结束", "stage_kind": "END"},
                            ],
                        },
                    }
                ),
            )
            assert rule.status == "GENERATING_AUDIO"
            assert agent.status == "ENABLED"
            assert topic.version == 1
            assert (
                await database_session.scalar(
                    select(func.count())
                    .select_from(HostAudioAsset)
                    .where(HostAudioAsset.rule_id == rule.id)
                )
                == 1
            )
            assert (
                await database_session.scalar(
                    select(func.count())
                    .select_from(BackgroundTask)
                    .where(BackgroundTask.task_type == "HOST_TTS")
                )
                == 1
            )
            await database_session.commit()
            async with database_session.begin():
                await database_session.execute(
                    update(HostAudioAsset)
                    .where(HostAudioAsset.rule_id == rule.id)
                    .values(status="READY", storage_path="rules/probe.opus", duration_ms=600)
                )
            reviewed = await rules.review_audio(database_session, rule_id=rule.id)
            assert reviewed.status == "READY"
            enabled = await rules.enable_rule(database_session, rule_id=rule.id)
            assert enabled.status == "ENABLED"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_enabling_new_rule_version_disables_only_older_enabled_version(
    auth_database_url: str,
) -> None:
    engine = create_async_engine(auth_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            admin = await AuthService().create_admin(
                session,
                username="rule-version-admin",
                real_name="规则版本管理员",
                password="rule-version-admin-password-123",
            )
            reviewed_at = datetime.now(UTC)
            async with session.begin():
                old_version = Rule(
                    rule_key="versioned-rule",
                    version=1,
                    name="版本规则 v1",
                    description="",
                    side_size=1,
                    estimated_seconds=60,
                    status="ENABLED",
                    created_by=admin.id,
                    audio_reviewed_at=reviewed_at,
                )
                new_version = Rule(
                    rule_key="versioned-rule",
                    version=2,
                    name="版本规则 v2",
                    description="",
                    side_size=1,
                    estimated_seconds=60,
                    status="READY",
                    created_by=admin.id,
                    audio_reviewed_at=reviewed_at,
                )
                unrelated = Rule(
                    rule_key="unrelated-rule",
                    version=1,
                    name="其他规则",
                    description="",
                    side_size=1,
                    estimated_seconds=60,
                    status="ENABLED",
                    created_by=admin.id,
                    audio_reviewed_at=reviewed_at,
                )
                session.add_all([old_version, new_version, unrelated])
            await RuleService().enable_rule(session, rule_id=new_version.id)
            await session.refresh(old_version)
            await session.refresh(new_version)
            await session.refresh(unrelated)
            assert old_version.status == "DISABLED"
            assert new_version.status == "ENABLED"
            assert unrelated.status == "ENABLED"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rule_delete_is_reference_safe_and_draft_without_host_text_is_editable(
    auth_database_url: str,
) -> None:
    engine = create_async_engine(auth_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    catalog = CatalogService()
    rules = RuleService()
    try:
        async with factory() as session:
            admin = await AuthService().create_admin(
                session,
                username="rule-delete-admin",
                real_name="规则删除管理员",
                password="rule-delete-admin-password-123",
            )
            host = await catalog.create_voice(
                session,
                payload=VoiceProfileCreate(
                    name="删除测试主持", kind="HOST", provider_voice="delete-host"
                ),
            )
            rule = await rules.create_rule(
                session,
                creator_user_id=admin.id,
                payload=RuleCreate.model_validate(
                    {
                        "rule_key": "delete-safe-rule",
                        "host_voice_profile_id": str(host.id),
                        "draft": {
                            "name": "无主持词规则",
                            "side_size": 1,
                            "stages": [{"name": "结束", "stage_kind": "END"}],
                        },
                    }
                ),
            )
            assert rule.status == "READY"
            await rules.delete_rule(session, rule_id=rule.id, actor_user_id=admin.id)
            assert await session.get(Rule, rule.id) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_match_runtime_commits_events_and_speech_before_actor_updates(
    auth_database_url: str,
) -> None:
    engine = create_async_engine(auth_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    organizer_id, opponent_id, rule_id, room_id = (uuid4() for _ in range(4))
    rule_snapshot = {
        "name": "005 数据库线性赛制",
        "side_size": 1,
        "host_audio": [],
        "stages": [
            {
                "position": 1,
                "stage_kind": "FIXED_SPEECH",
                "actions": [
                    {
                        "position": 1,
                        "side": "AFFIRMATIVE",
                        "seat_no": 1,
                        "duration_seconds": 30,
                    },
                    {
                        "position": 2,
                        "side": "NEGATIVE",
                        "seat_no": 1,
                        "duration_seconds": 30,
                    },
                ],
            }
        ],
    }
    async with factory() as session:
        async with session.begin():
            session.add_all(
                [
                    User(
                        id=organizer_id,
                        username="runtime-organizer",
                        username_normalized="runtime-organizer",
                        real_name="运行时组织者",
                        password_hash="probe",
                    ),
                    User(
                        id=opponent_id,
                        username="runtime-opponent",
                        username_normalized="runtime-opponent",
                        real_name="运行时对手",
                        password_hash="probe",
                    ),
                ]
            )
            await session.flush()
            session.add(
                Rule(
                    id=rule_id,
                    rule_key="runtime-rule",
                    version=1,
                    name="005 数据库线性赛制",
                    side_size=1,
                    estimated_seconds=60,
                    status="ENABLED",
                    created_by=organizer_id,
                )
            )
            await session.flush()
            session.add(
                Room(
                    id=room_id,
                    code="RT005A",
                    title="005 运行时集成",
                    label="训练赛",
                    topic_snapshot={"title": "测试辩题"},
                    rule_id=rule_id,
                    rule_snapshot=rule_snapshot,
                    organizer_user_id=organizer_id,
                    status="START_PENDING_RUNTIME",
                )
            )
            session.add_all(
                [
                    RoomMember(
                        room_id=room_id,
                        user_id=organizer_id,
                        member_role="ORGANIZER",
                        online=True,
                        ready=True,
                    ),
                    RoomMember(
                        room_id=room_id,
                        user_id=opponent_id,
                        member_role="DEBATER",
                        online=True,
                        ready=True,
                    ),
                    Seat(
                        room_id=room_id,
                        side="AFFIRMATIVE",
                        seat_no=1,
                        occupant_type="HUMAN",
                        user_id=organizer_id,
                    ),
                    Seat(
                        room_id=room_id,
                        side="NEGATIVE",
                        seat_no=1,
                        occupant_type="HUMAN",
                        user_id=opponent_id,
                    ),
                ]
            )

    manager = MatchRuntimeManager(factory)
    try:
        async with factory() as session:
            state = await manager.start_room_match(
                session,
                room_id=room_id,
                actor_user_id=organizer_id,
                actor_role="USER",
            )
        assert state.status == "START_COUNTDOWN"
        async with factory() as session:
            retried = await manager.start_room_match(
                session,
                room_id=room_id,
                actor_user_id=organizer_id,
                actor_role="USER",
            )
            match_count = await session.scalar(
                select(func.count()).select_from(Match).where(Match.room_id == room_id)
            )
        assert retried.match_id == state.match_id
        assert match_count == 1
        actor = await manager.get_actor(state.match_id)
        await actor.submit(MatchCommand(type="countdown.elapsed", message_id="db-countdown"))
        speaking = await actor.submit(
            MatchCommand(
                type="speech.start",
                message_id="db-speech-start",
                actor_user_id=organizer_id,
            )
        )
        speech_id = speaking.state.current_speech_id
        assert speech_id is not None
        sequence_before_view = actor.state.sequence
        async with factory() as session:
            live_view = await manager.snapshot_view(session, state.match_id)
        assert live_view.state.sequence == sequence_before_view
        assert live_view.speech_remaining_ms is not None
        assert 0 < live_view.speech_remaining_ms <= 30_000
        assert live_view.countdown_remaining_ms is None
        assert actor.state.sequence == sequence_before_view
        await actor.submit(
            MatchCommand(
                type="speech.finish",
                message_id="db-speech-finish",
                actor_user_id=organizer_id,
            )
        )
        await actor.submit(
            MatchCommand(
                type="asr.finalized",
                message_id="db-asr-finalized",
                payload={
                    "speech_id": str(speech_id),
                    "final_text": "数据库测试发言",
                    "reason": "EARLY",
                },
            )
        )

        async with factory() as session:
            match = await session.get(Match, state.match_id)
            events = list(
                (
                    await session.scalars(
                        select(MatchEvent)
                        .where(MatchEvent.match_id == state.match_id)
                        .order_by(MatchEvent.sequence)
                    )
                ).all()
            )
            speech = await session.get(Speech, speech_id)
        assert match is not None
        assert match.sequence == actor.state.sequence
        assert [event.sequence for event in events] == list(range(1, match.sequence + 1))
        assert speech is not None
        assert speech.status == "FINALIZED"
        assert speech.finish_reason == "EARLY"
    finally:
        await manager.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_match_runtime_recovery_keeps_frozen_timing_snapshot(
    auth_database_url: str,
) -> None:
    engine = create_async_engine(auth_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    organizer_id, opponent_id, rule_id, room_id = (uuid4() for _ in range(4))
    rule_snapshot = {
        "name": "017c 恢复计时赛制",
        "side_size": 1,
        "host_audio": [],
        "stages": [
            {
                "position": 1,
                "stage_kind": "FIXED_SPEECH",
                "actions": [
                    {
                        "position": 1,
                        "side": "AFFIRMATIVE",
                        "seat_no": 1,
                        "duration_seconds": 30,
                    }
                ],
            }
        ],
    }
    async with factory() as session:
        async with session.begin():
            session.add_all(
                [
                    User(
                        id=organizer_id,
                        username="runtime-recovery-organizer",
                        username_normalized="runtime-recovery-organizer",
                        real_name="恢复组织者",
                        password_hash="probe",
                    ),
                    User(
                        id=opponent_id,
                        username="runtime-recovery-opponent",
                        username_normalized="runtime-recovery-opponent",
                        real_name="恢复对手",
                        password_hash="probe",
                    ),
                ]
            )
            await session.flush()
            session.add(
                Rule(
                    id=rule_id,
                    rule_key="runtime-recovery-rule",
                    version=1,
                    name="017c 恢复计时赛制",
                    side_size=1,
                    estimated_seconds=30,
                    status="ENABLED",
                    created_by=organizer_id,
                )
            )
            await session.flush()
            session.add(
                Room(
                    id=room_id,
                    code="RT017C",
                    title="017c 恢复计时",
                    label="训练赛",
                    topic_snapshot={"title": "恢复计时测试"},
                    rule_id=rule_id,
                    rule_snapshot=rule_snapshot,
                    organizer_user_id=organizer_id,
                    status="START_PENDING_RUNTIME",
                )
            )
            session.add_all(
                [
                    RoomMember(
                        room_id=room_id,
                        user_id=organizer_id,
                        member_role="ORGANIZER",
                        online=True,
                        ready=True,
                    ),
                    RoomMember(
                        room_id=room_id,
                        user_id=opponent_id,
                        member_role="DEBATER",
                        online=True,
                        ready=True,
                    ),
                    Seat(
                        room_id=room_id,
                        side="AFFIRMATIVE",
                        seat_no=1,
                        occupant_type="HUMAN",
                        user_id=organizer_id,
                    ),
                    Seat(
                        room_id=room_id,
                        side="NEGATIVE",
                        seat_no=1,
                        occupant_type="HUMAN",
                        user_id=opponent_id,
                    ),
                ]
            )

    first = MatchRuntimeManager(factory)
    second = MatchRuntimeManager(factory)
    try:
        async with factory() as session:
            state = await first.start_room_match(
                session,
                room_id=room_id,
                actor_user_id=organizer_id,
                actor_role="USER",
            )
        actor = await first.get_actor(state.match_id)
        await actor.submit(MatchCommand(type="countdown.elapsed", message_id="recovery-countdown"))
        await actor.submit(
            MatchCommand(
                type="speech.start",
                message_id="recovery-speech-start",
                actor_user_id=organizer_id,
            )
        )
        await first.close()

        assert await second.recover_unfinished() == 1
        recovered = await second.get_actor(state.match_id)
        assert recovered.state.status == "SYSTEM_RECOVERY"
        assert recovered.state.action_state == "RECOVERY_REQUIRED"
        async with factory() as session:
            view = await second.snapshot_view(session, state.match_id)
            persisted = await session.get(Match, state.match_id)
        assert view.countdown_remaining_ms is None
        assert view.speech_remaining_ms == 30_000
        assert view.state.sequence == recovered.state.sequence
        assert persisted is not None
        assert persisted.status == "SYSTEM_RECOVERY"
        assert persisted.runtime_snapshot["speech_remaining_ms"] == 30_000
    finally:
        await first.close()
        await second.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_room_creation_join_seat_ready_and_start_pending_runtime(
    auth_database_url: str,
) -> None:
    engine = create_async_engine(auth_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    catalog = CatalogService()
    rule_service = RuleService()
    room_service = RoomService()
    try:
        async with factory() as session:
            admin = await session.scalar(select(User).where(User.role == "ADMIN"))
            if admin is None:
                await session.commit()
                admin = await AuthService().create_admin(
                    session,
                    username="room-flow-admin",
                    real_name="房间流程管理员",
                    password="room-flow-admin-password-123",
                )
            host = await catalog.create_voice(
                session,
                payload=VoiceProfileCreate(
                    name="房间主持",
                    kind="HOST",
                    provider_voice="room-host",
                ),
            )
            agent_voice = await catalog.create_voice(
                session,
                payload=VoiceProfileCreate(
                    name="房间 Agent 音色",
                    kind="AGENT",
                    provider_voice="room-agent",
                    avatar_key="agent-02",
                ),
            )
            model = await catalog.create_model(
                session,
                payload=ModelProfileCreate(name="房间模型", config_ref="room-model"),
            )
            room_agents = []
            for index in range(4):
                room_agents.append(
                    await catalog.create_agent(
                        session,
                        payload=AgentProfileCreate(
                            name=f"房间补位 Agent {index + 1}",
                            model_profile_id=model.id,
                            voice_profile_id=agent_voice.id,
                        ),
                    )
                )
            rule = await rule_service.create_rule(
                session,
                creator_user_id=admin.id,
                payload=RuleCreate.model_validate(
                    {
                        "host_voice_profile_id": str(host.id),
                        "draft": {
                            "name": "房间规则",
                            "side_size": 2,
                            "stages": [
                                {
                                    "name": "开场",
                                    "stage_kind": "FIXED_SPEECH",
                                    "actions": [
                                        {
                                            "side": "AFFIRMATIVE",
                                            "seat_no": 1,
                                            "duration_seconds": 30,
                                        },
                                        {
                                            "side": "NEGATIVE",
                                            "seat_no": 1,
                                            "duration_seconds": 30,
                                        },
                                    ],
                                },
                                {"name": "结束", "stage_kind": "END"},
                            ],
                        },
                    }
                ),
            )
            await session.commit()
            async with session.begin():
                await session.execute(
                    update(HostAudioAsset)
                    .where(HostAudioAsset.rule_id == rule.id)
                    .values(status="READY", storage_path="rules/room-host.opus")
                )
            await rule_service.review_audio(session, rule_id=rule.id)
            await rule_service.enable_rule(session, rule_id=rule.id)
            rule_id = rule.id
            user_one = await AuthService().register(
                session,
                username="room-user-one",
                real_name="房间用户一",
                password="room-user-one-password",
                platform_terms_version="platform-terms-v1",
                avatar_key="human-16",
            )
            assert user_one.user.default_avatar_key == "human-16"
            user_two = await AuthService().register(
                session,
                username="room-user-two",
                real_name="房间用户二",
                password="room-user-two-password",
                platform_terms_version="platform-terms-v1",
            )
            user_three = await AuthService().register(
                session,
                username="room-user-three",
                real_name="房间观众三",
                password="room-user-three-password",
                platform_terms_version="platform-terms-v1",
            )
            user_four = await AuthService().register(
                session,
                username="room-user-four",
                real_name="房间竞态用户四",
                password="room-user-four-password",
                platform_terms_version="platform-terms-v1",
            )
            user_one_id = user_one.user.id
            user_two_id = user_two.user.id
            user_three_id = user_three.user.id
            user_four_id = user_four.user.id
            terms_version = get_current_human_participation_terms().version
            room = await room_service.create_room(
                session,
                organizer_user_id=user_one_id,
                organizer_role="USER",
                payload=RoomCreateRequest.model_validate(
                    {
                        "title": "房间生命周期测试",
                        "label": "训练赛",
                        "rule_id": str(rule_id),
                        "custom_topic_title": "效率与公平",
                        "affirmative_text": "重视效率",
                        "negative_text": "重视公平",
                        "human_participation_terms_version": terms_version,
                    }
                ),
            )
            room_id = room.id
            room_code = room.code
            created_room, created_members, created_seats = await room_service.snapshot(
                session, room_id=room_id
            )
            assert created_room.auto_fill_agents is True
            assert all(seat.occupant_type == "AGENT" for seat in created_seats)
            assert len({seat.agent_profile_id for seat in created_seats}) == len(created_seats)
            negative_first_agent_id = next(
                seat.configured_agent_profile_id
                for seat in created_seats
                if seat.side == "NEGATIVE" and seat.seat_no == 1
            )
            assert negative_first_agent_id is not None
            creator_member = next(
                member for member in created_members if member.user_id == user_one_id
            )
            assert creator_member.member_role == "DEBATER"
            assert all(seat.user_id != user_one_id for seat in created_seats)
            await session.commit()
            with pytest.raises(AuthError) as unseated_start:
                await room_service.start_room(
                    session,
                    actor_user_id=user_one_id,
                    actor_role="USER",
                    room_id=room_id,
                )
            assert unseated_start.value.code == "room_debater_unseated"
            await room_service.select_seat(
                session,
                user_id=user_one_id,
                room_id=room_id,
                payload=SeatSelectRequest(
                    side="AFFIRMATIVE",
                    seat_no=1,
                    human_participation_terms_version=terms_version,
                ),
            )
            await room_service.join_room(
                session,
                user_id=user_two_id,
                room_id=room_id,
                payload=RoomJoinRequest(
                    member_role="DEBATER",
                    human_participation_terms_version=terms_version,
                ),
            )
            await room_service.select_seat(
                session,
                user_id=user_two_id,
                room_id=room_id,
                payload=SeatSelectRequest(
                    side="NEGATIVE",
                    seat_no=1,
                    human_participation_terms_version=terms_version,
                ),
            )
            await room_service.leave_room(
                session,
                user_id=user_two_id,
                room_id=room_id,
            )
            _, _, seats_after_leave = await room_service.snapshot(session, room_id=room_id)
            restored = next(
                seat for seat in seats_after_leave if seat.side == "NEGATIVE" and seat.seat_no == 1
            )
            assert restored.occupant_type == "AGENT"
            assert restored.agent_profile_id == negative_first_agent_id
            assert restored.configured_agent_profile_id == negative_first_agent_id
            await session.commit()
            rejoined = await room_service.join_room(
                session,
                user_id=user_two_id,
                room_id=room_id,
                payload=RoomJoinRequest(
                    member_role="DEBATER",
                    human_participation_terms_version=terms_version,
                ),
            )
            assert rejoined.left_at is None
            await room_service.select_seat(
                session,
                user_id=user_two_id,
                room_id=room_id,
                payload=SeatSelectRequest(
                    side="NEGATIVE",
                    seat_no=1,
                    human_participation_terms_version=terms_version,
                ),
            )
            for user_id in (user_one_id, user_two_id):
                saved_check = await room_service.save_device_check(
                    session,
                    user_id=user_id,
                    room_id=room_id,
                    payload=DeviceCheckRequest(
                        check_version=1_750_000_000_000,
                        status="PASS",
                        details={"microphone": "pass", "speaker": "pass"},
                    ),
                )
                assert saved_check.check_version == 1
                ready_version = 1
                if user_id == user_one_id:
                    second_check = await room_service.save_device_check(
                        session,
                        user_id=user_id,
                        room_id=room_id,
                        payload=DeviceCheckRequest(
                            status="PASS",
                            details={"microphone": "pass", "speaker": "pass"},
                        ),
                    )
                    assert second_check.check_version == 2
                    ready_version = 2
                await room_service.ready(
                    session,
                    user_id=user_id,
                    room_id=room_id,
                    check_version=ready_version,
                )
            await room_service.select_seat(
                session,
                user_id=user_two_id,
                room_id=room_id,
                payload=SeatSelectRequest(
                    side="NEGATIVE",
                    seat_no=2,
                    human_participation_terms_version=terms_version,
                ),
            )
            _, members_after_switch, seats_after_switch = await room_service.snapshot(
                session, room_id=room_id
            )
            switched_member = next(
                member for member in members_after_switch if member.user_id == user_two_id
            )
            assert switched_member.ready is True
            assert (
                next(
                    seat
                    for seat in seats_after_switch
                    if seat.side == "NEGATIVE" and seat.seat_no == 1
                ).occupant_type
                == "AGENT"
            )
            await session.commit()

            for contender_id in (user_three_id, user_four_id):
                await room_service.join_room(
                    session,
                    user_id=contender_id,
                    room_id=room_id,
                    payload=RoomJoinRequest(
                        member_role="DEBATER",
                        human_participation_terms_version=terms_version,
                    ),
                )

            async def claim_affirmative_second(user_id: UUID) -> str:
                async with factory() as contender_session:
                    try:
                        await room_service.select_seat(
                            contender_session,
                            user_id=user_id,
                            room_id=room_id,
                            payload=SeatSelectRequest(
                                side="AFFIRMATIVE",
                                seat_no=2,
                                human_participation_terms_version=terms_version,
                            ),
                        )
                    except AuthError as error:
                        return error.code
                    return "selected"

            race_results = await asyncio.gather(
                claim_affirmative_second(user_three_id),
                claim_affirmative_second(user_four_id),
            )
            assert sorted(race_results) == ["seat_human_occupied", "selected"]

            async def leave_contender(user_id: UUID) -> None:
                async with factory() as contender_session:
                    await room_service.leave_room(
                        contender_session,
                        user_id=user_id,
                        room_id=room_id,
                    )

            await asyncio.gather(
                leave_contender(user_three_id),
                leave_contender(user_four_id),
            )
            await room_service.invalidate_device_check(
                session, user_id=user_two_id, room_id=room_id
            )
            invalidated = await room_service.viewer_context(
                session, room_id=room_id, user_id=user_two_id
            )
            assert invalidated.member is not None and invalidated.member.ready is False
            assert invalidated.latest_device_check is not None
            assert invalidated.latest_device_check.valid_until <= datetime.now(UTC)
            await session.commit()
            replacement_check = await room_service.save_device_check(
                session,
                user_id=user_two_id,
                room_id=room_id,
                payload=DeviceCheckRequest(
                    status="PASS", details={"microphone": "pass", "speaker": "tone_played"}
                ),
            )
            await room_service.ready(
                session,
                user_id=user_two_id,
                room_id=room_id,
                check_version=replacement_check.check_version,
            )
            started = await room_service.start_room(
                session,
                actor_user_id=user_one_id,
                actor_role="USER",
                room_id=room_id,
            )
            assert started.status == "START_PENDING_RUNTIME"
            late_spectator = await room_service.join_room(
                session,
                user_id=user_three_id,
                room_id=room_id,
                payload=RoomJoinRequest(member_role="SPECTATOR"),
            )
            assert late_spectator.member_role == "SPECTATOR"
            viewer = await room_service.viewer_context(
                session, room_id=room_id, user_id=user_two_id
            )
            assert viewer.member is not None and viewer.member.left_at is None
            assert viewer.latest_device_check is not None
            assert viewer.match_id is None
            snapshot_room, members, seats = await room_service.snapshot(session, room_id=room_id)
            assert snapshot_room.code == room_code
            assert len(members) == 3
            assert all(seat.occupant_type != "EMPTY" for seat in seats)
            assert sum(seat.occupant_type == "HUMAN" for seat in seats) == 2
            await session.commit()
            await room_service.terminate_room(
                session,
                actor_user_id=user_one_id,
                actor_role="USER",
                room_id=room_id,
            )
            replacement = await room_service.create_room(
                session,
                organizer_user_id=user_one_id,
                organizer_role="USER",
                payload=RoomCreateRequest.model_validate(
                    {
                        "title": "终止后重新建房",
                        "label": "训练赛",
                        "rule_id": str(rule_id),
                        "custom_topic_title": "新辩题",
                        "affirmative_text": "新正方",
                        "negative_text": "新反方",
                        "human_participation_terms_version": terms_version,
                    }
                ),
            )
            assert replacement.status == "WAITING"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_room_capacity_guards_serialize_spectators_and_fifth_match(
    auth_database_url: str,
) -> None:
    engine = create_async_engine(auth_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    catalog = CatalogService()
    rules = RuleService()
    rooms = RoomService()
    try:
        async with factory() as session:
            admin = await AuthService().create_admin(
                session,
                username="capacity-admin",
                real_name="容量管理员",
                password="capacity-admin-password-123",
            )
            admin_id = admin.id
            host_voice = await catalog.create_voice(
                session,
                payload=VoiceProfileCreate(
                    name="容量主持音色",
                    kind="HOST",
                    provider_voice="capacity-host",
                ),
            )
            agent_voice = await catalog.create_voice(
                session,
                payload=VoiceProfileCreate(
                    name="容量 Agent 音色",
                    kind="AGENT",
                    provider_voice="capacity-agent",
                    avatar_key="agent-03",
                ),
            )
            model = await catalog.create_model(
                session,
                payload=ModelProfileCreate(name="容量模型", config_ref="capacity-model"),
            )
            for index in range(2):
                await catalog.create_agent(
                    session,
                    payload=AgentProfileCreate(
                        name=f"容量 Agent {index + 1}",
                        model_profile_id=model.id,
                        voice_profile_id=agent_voice.id,
                    ),
                )
            rule = await rules.create_rule(
                session,
                creator_user_id=admin_id,
                payload=RuleCreate.model_validate(
                    {
                        "host_voice_profile_id": str(host_voice.id),
                        "draft": {
                            "name": "容量一对一规则",
                            "side_size": 1,
                            "stages": [
                                {
                                    "name": "自由辩论",
                                    "stage_kind": "FREE_DEBATE",
                                    "duration_seconds": 60,
                                },
                                {"name": "结束", "stage_kind": "END"},
                            ],
                        },
                    }
                ),
            )
            await rules.review_audio(session, rule_id=rule.id)
            await rules.enable_rule(session, rule_id=rule.id)
            room_ids: list[UUID] = []
            for index in range(6):
                room = await rooms.create_room(
                    session,
                    organizer_user_id=admin_id,
                    organizer_role="ADMIN",
                    payload=RoomCreateRequest.model_validate(
                        {
                            "title": f"容量比赛 {index + 1}",
                            "label": "实验场",
                            "rule_id": str(rule.id),
                            "custom_topic_title": f"容量辩题 {index + 1}",
                            "affirmative_text": "正方",
                            "negative_text": "反方",
                            "is_all_agent": True,
                        }
                    ),
                )
                room_ids.append(room.id)
            (
                first_all_agent_room,
                first_all_agent_members,
                first_all_agent_seats,
            ) = await rooms.snapshot(session, room_id=room_ids[0])
            assert first_all_agent_room.is_all_agent is True
            assert all(member.member_role == "ORGANIZER" for member in first_all_agent_members)
            assert all(seat.occupant_type == "AGENT" for seat in first_all_agent_seats)
            assert len({seat.agent_profile_id for seat in first_all_agent_seats}) == 2
            await session.commit()
            spectator_room_id = room_ids[5]
            spectator_ids: list[UUID] = []
            for index in range(11):
                registered = await AuthService().register(
                    session,
                    username=f"capacity-spectator-{index}",
                    real_name=f"容量观众{index}",
                    password=f"capacity-spectator-password-{index}",
                    platform_terms_version="platform-terms-v1",
                )
                spectator_ids.append(registered.user.id)
            role_switcher = await AuthService().register(
                session,
                username="capacity-role-switcher",
                real_name="容量身份切换用户",
                password="capacity-role-switcher-password",
                platform_terms_version="platform-terms-v1",
            )
            role_switch_room = await rooms.create_room(
                session,
                organizer_user_id=role_switcher.user.id,
                organizer_role="USER",
                payload=RoomCreateRequest.model_validate(
                    {
                        "title": "容量身份切换房间",
                        "label": "训练赛",
                        "rule_id": str(rule.id),
                        "custom_topic_title": "身份切换辩题",
                        "affirmative_text": "正方",
                        "negative_text": "反方",
                        "human_participation_terms_version": (
                            get_current_human_participation_terms().version
                        ),
                    }
                ),
            )
            role_switcher_id = role_switcher.user.id
            role_switch_room_id = role_switch_room.id

            with pytest.raises(AuthError) as all_agent_debater_join:
                await rooms.join_room(
                    session,
                    user_id=spectator_ids[0],
                    room_id=spectator_room_id,
                    payload=RoomJoinRequest(
                        member_role="DEBATER",
                        human_participation_terms_version=get_current_human_participation_terms().version,
                    ),
                )
            assert all_agent_debater_join.value.code == "forbidden"

        async def join_spectator(user_id: UUID) -> str:
            async with factory() as session:
                try:
                    await rooms.join_room(
                        session,
                        user_id=user_id,
                        room_id=spectator_room_id,
                        payload=RoomJoinRequest(member_role="SPECTATOR"),
                    )
                except AuthError as error:
                    return error.code
                return "joined"

        spectator_results = await asyncio.gather(
            *(join_spectator(user_id) for user_id in spectator_ids)
        )
        assert spectator_results.count("joined") == 10
        assert spectator_results.count("spectator_capacity_full") == 1

        async with factory() as session:
            with pytest.raises(AuthError) as all_agent_role_change:
                await rooms.change_role(
                    session,
                    user_id=spectator_ids[0],
                    room_id=spectator_room_id,
                    payload=RoleChangeRequest(
                        member_role="DEBATER",
                        human_participation_terms_version=get_current_human_participation_terms().version,
                    ),
                )
            assert all_agent_role_change.value.code == "forbidden"
            with pytest.raises(AuthError) as all_agent_seat_select:
                await rooms.select_seat(
                    session,
                    user_id=spectator_ids[0],
                    room_id=spectator_room_id,
                    payload=SeatSelectRequest(
                        side="AFFIRMATIVE",
                        seat_no=1,
                        human_participation_terms_version=get_current_human_participation_terms().version,
                    ),
                )
            assert all_agent_seat_select.value.code == "forbidden"
            with pytest.raises(AuthError) as spectator_role_capacity:
                await rooms.change_role(
                    session,
                    user_id=role_switcher_id,
                    room_id=role_switch_room_id,
                    payload=RoleChangeRequest(member_role="SPECTATOR"),
                )
            assert spectator_role_capacity.value.code == "spectator_capacity_full"
            unchanged_member = await session.scalar(
                select(RoomMember).where(
                    RoomMember.room_id == role_switch_room_id,
                    RoomMember.user_id == role_switcher_id,
                )
            )
            assert unchanged_member is not None
            assert unchanged_member.member_role == "DEBATER"

        for room_id in room_ids[:4]:
            async with factory() as session:
                await rooms.start_room(
                    session,
                    actor_user_id=admin_id,
                    actor_role="ADMIN",
                    room_id=room_id,
                )

        async def start_candidate(room_id: UUID) -> str:
            async with factory() as session:
                try:
                    await rooms.start_room(
                        session,
                        actor_user_id=admin_id,
                        actor_role="ADMIN",
                        room_id=room_id,
                    )
                except AuthError as error:
                    return error.code
                return "started"

        start_results = await asyncio.gather(
            start_candidate(room_ids[4]), start_candidate(room_ids[5])
        )
        assert start_results.count("started") == 1
        assert start_results.count("match_capacity_full") == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_auth_migration_constraints_and_defaults(auth_database_url: str) -> None:
    engine = create_async_engine(auth_database_url)
    user_id = uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, username, username_normalized, real_name, "
                    "password_hash) "
                    "VALUES (:id, :username, :normalized, :real_name, :password_hash)"
                ),
                {
                    "id": user_id,
                    "username": "ProbeUser",
                    "normalized": "probeuser",
                    "real_name": "探针用户",
                    "password_hash": "argon2id-probe",
                },
            )
            row = (
                await connection.execute(
                    text(
                        "SELECT role, status, must_change_password, failed_login_count, "
                        "avatar_version "
                        "FROM users "
                        "WHERE users.id = :id"
                    ),
                    {"id": user_id},
                )
            ).one()
            assert row.role == "USER"
            assert row.status == "ACTIVE"
            assert row.must_change_password is False
            assert row.failed_login_count == 0
            assert row.avatar_version == 0

            await connection.execute(
                text(
                    "INSERT INTO audit_logs (id, action, target_type, result) "
                    "VALUES (:id, 'probe', 'user', 'SUCCESS')"
                ),
                {"id": uuid4()},
            )
            audit_details = (
                await connection.execute(
                    text("SELECT details FROM audit_logs WHERE action = 'probe'")
                )
            ).scalar_one()
            assert audit_details == {}

        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO users (id, username, username_normalized, real_name, "
                        "password_hash) "
                        "VALUES (:id, :username, :normalized, :real_name, :password_hash)"
                    ),
                    {
                        "id": uuid4(),
                        "username": "probe-user-2",
                        "normalized": "probeuser",
                        "real_name": "另一个用户",
                        "password_hash": "argon2id-probe",
                    },
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_sessions_roll_revoke_and_honor_disabled_users(
    auth_database_url: str,
) -> None:
    engine = create_async_engine(auth_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    service = SessionService(ttl_seconds=60, rolling_refresh_seconds=10)
    now = datetime(2030, 1, 1, tzinfo=UTC)
    user_id = uuid4()
    try:
        async with session_factory() as database_session:
            database_session.add(
                User(
                    id=user_id,
                    username="session-probe",
                    username_normalized="session-probe",
                    real_name="会话探针",
                    password_hash="argon2id-probe",
                )
            )
            await database_session.commit()

            first = service.create(database_session, user_id, now=now)
            second = service.create(database_session, user_id, now=now)
            await database_session.commit()

            initial = await service.validate(
                database_session, first.token, now=now + timedelta(seconds=5)
            )
            assert initial.context is not None
            assert initial.refresh_cookie is False

            rolled = await service.validate(
                database_session, first.token, now=now + timedelta(seconds=11)
            )
            assert rolled.context is not None
            assert rolled.refresh_cookie is True
            await database_session.commit()

            assert (
                await service.revoke_current(
                    database_session, first.token, now=now + timedelta(seconds=12)
                )
                is True
            )
            assert (
                await service.revoke_all(database_session, user_id, now=now + timedelta(seconds=13))
                == 1
            )
            await database_session.commit()

            expired = await service.validate(
                database_session, first.token, now=now + timedelta(seconds=14)
            )
            assert expired.context is None
            assert expired.reason == "session_expired"

            user = await database_session.get(User, user_id)
            assert user is not None
            user.status = "DISABLED"
            replacement = service.create(database_session, user_id, now=now)
            await database_session.commit()
            disabled = await service.validate(database_session, replacement.token, now=now)
            assert disabled.context is None
            assert disabled.reason == "account_disabled"
            assert second.token != replacement.token
    finally:
        await engine.dispose()


def _auth_settings(database_url: str, *, avatar_storage_dir: str = "./data/avatars") -> Settings:
    return Settings(
        database_url=database_url,
        app_env="test",
        cors_origins="http://localhost:3000",
        avatar_storage_dir=avatar_storage_dir,
    )


def _encoded_image(format_name: str) -> bytes:
    image = Image.new("RGB", (640, 480), (40, 90, 150))
    output = BytesIO()
    image.save(output, format=format_name)
    return output.getvalue()


@pytest.mark.asyncio
async def test_real_auth_api_register_me_logout_and_origin_guard(
    auth_database_url: str,
) -> None:
    settings = _auth_settings(auth_database_url)
    runtime = CoreRuntime(settings, lock_key=uuid4().int & ((1 << 63) - 1))
    app = create_app(settings, runtime=runtime)
    headers = {"Origin": "http://localhost:3000"}

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
        ) as client:
            terms = await client.get("/api/legal/platform-terms/current")
            assert terms.status_code == 200
            terms_version = terms.json()["version"]

            rejected = await client.post(
                "/api/auth/register",
                headers={"Origin": "https://attacker.test"},
                json={
                    "username": "api-probe-bad-origin",
                    "real_name": "来源探针",
                    "password": "safe-password-123",
                    "platform_terms_version": terms_version,
                },
            )
            assert rejected.status_code == 403
            assert rejected.json()["error"]["code"] == "csrf_origin_rejected"

            invalid = await client.post("/api/auth/register", json={})
            assert invalid.status_code == 422
            assert invalid.json()["error"]["code"] == "validation_error"
            assert "password" in invalid.json()["error"]["field_errors"]

            registered = await client.post(
                "/api/auth/register",
                json={
                    "username": "ApiProbe",
                    "real_name": "接口探针",
                    "password": "safe-password-123",
                    "platform_terms_version": terms_version,
                },
            )
            assert registered.status_code == 201
            assert registered.json()["user"]["real_name"] == "接口探针"
            cookie = registered.headers["set-cookie"]
            assert "jx_session=" in cookie
            assert "HttpOnly" in cookie
            assert "SameSite=lax" in cookie
            assert "Secure" not in cookie

            current = await client.get("/api/auth/me")
            assert current.status_code == 200
            assert current.json()["user"]["username"] == "ApiProbe"

            duplicate = await client.post(
                "/api/auth/register",
                json={
                    "username": "apiprobe",
                    "real_name": "重复探针",
                    "password": "safe-password-123",
                    "platform_terms_version": terms_version,
                },
            )
            assert duplicate.status_code == 409
            assert duplicate.json()["error"]["code"] == "username_taken"

            logged_out = await client.post("/api/auth/logout")
            assert logged_out.status_code == 200
            assert logged_out.json() == {"status": "logged_out"}
            unauthenticated = await client.get("/api/auth/me")
            assert unauthenticated.status_code == 401
            assert unauthenticated.json()["error"]["code"] == "not_authenticated"


@pytest.mark.asyncio
async def test_real_auth_api_lockout_and_password_rotation(
    auth_database_url: str,
) -> None:
    settings = _auth_settings(auth_database_url)
    runtime = CoreRuntime(settings, lock_key=uuid4().int & ((1 << 63) - 1))
    app = create_app(settings, runtime=runtime)
    headers = {"Origin": "http://localhost:3000"}

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
        ) as first_client:
            terms = await first_client.get("/api/legal/platform-terms/current")
            terms_version = terms.json()["version"]
            registered = await first_client.post(
                "/api/auth/register",
                json={
                    "username": "PasswordProbe",
                    "real_name": "密码探针",
                    "password": "old-password-123",
                    "platform_terms_version": terms_version,
                },
            )
            assert registered.status_code == 201

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
                headers=headers,
            ) as second_client:
                second_login = await second_client.post(
                    "/api/auth/login",
                    json={"username": "passwordprobe", "password": "old-password-123"},
                )
                assert second_login.status_code == 200

                changed = await first_client.post(
                    "/api/auth/change-password",
                    json={
                        "current_password": "old-password-123",
                        "new_password": "new-password-456",
                    },
                )
                assert changed.status_code == 200
                assert changed.headers["x-other-sessions-revoked"] == "true"
                assert (await second_client.get("/api/auth/me")).status_code == 401

                old_login = await second_client.post(
                    "/api/auth/login",
                    json={"username": "passwordprobe", "password": "old-password-123"},
                )
                assert old_login.status_code == 401
                new_login = await second_client.post(
                    "/api/auth/login",
                    json={"username": "passwordprobe", "password": "new-password-456"},
                )
                assert new_login.status_code == 200

            for attempt in range(5):
                failed = await first_client.post(
                    "/api/auth/login",
                    json={"username": "passwordprobe", "password": "wrong-password"},
                )
                expected = 423 if attempt == 4 else 401
                assert failed.status_code == expected
            frozen = await first_client.post(
                "/api/auth/login",
                json={"username": "passwordprobe", "password": "new-password-456"},
            )
            assert frozen.status_code == 423


@pytest.mark.asyncio
async def test_real_avatar_api_reencodes_caches_and_deletes(
    auth_database_url: str,
) -> None:
    with TemporaryDirectory(prefix="jx-avatar-api-") as avatar_dir:
        settings = _auth_settings(
            auth_database_url,
            avatar_storage_dir=avatar_dir,
        )
        runtime = CoreRuntime(settings, lock_key=uuid4().int & ((1 << 63) - 1))
        app = create_app(settings, runtime=runtime)
        headers = {"Origin": "http://localhost:3000"}

        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
                headers=headers,
            ) as client:
                terms_version = (await client.get("/api/legal/platform-terms/current")).json()[
                    "version"
                ]
                registered = await client.post(
                    "/api/auth/register",
                    json={
                        "username": "AvatarProbe",
                        "real_name": "头像探针",
                        "password": "avatar-password-123",
                        "platform_terms_version": terms_version,
                        "avatar_key": "human-16",
                    },
                )
                assert registered.status_code == 201
                assert registered.json()["user"]["default_avatar_key"] == "human-16"
                user_id = registered.json()["user"]["id"]

                uploaded = await client.put(
                    "/api/users/me/avatar",
                    files={"file": ("avatar.fake", _encoded_image("PNG"), "image/svg+xml")},
                )
                assert uploaded.status_code == 200
                assert uploaded.json()["user"]["avatar_version"] == 1

                fetched = await client.get(f"/api/users/{user_id}/avatar")
                assert fetched.status_code == 200
                assert fetched.headers["content-type"] == "image/webp"
                with Image.open(BytesIO(fetched.content)) as image:
                    assert image.size == (256, 256)
                    assert image.format == "WEBP"
                etag = fetched.headers["etag"]
                cached = await client.get(
                    f"/api/users/{user_id}/avatar",
                    headers={"If-None-Match": etag},
                )
                assert cached.status_code == 304

                invalid = await client.put(
                    "/api/users/me/avatar",
                    files={"file": ("avatar.gif", _encoded_image("GIF"), "image/gif")},
                )
                assert invalid.status_code == 422
                assert invalid.json()["error"]["code"] == "avatar_type_invalid"

                oversized = await client.put(
                    "/api/users/me/avatar",
                    files={"file": ("large.png", b"x" * (2 * 1024 * 1024 + 1), "image/png")},
                )
                assert oversized.status_code == 413

                deleted = await client.delete("/api/users/me/avatar")
                assert deleted.status_code == 200
                assert deleted.json()["user"]["avatar_version"] == 2
                default_avatar = await client.get(f"/api/users/{user_id}/avatar")
                assert default_avatar.status_code == 200
                assert default_avatar.headers["etag"].startswith('"preset-human-')


@pytest.mark.asyncio
async def test_admin_temporary_password_is_one_time_visible_and_restricts_user(
    auth_database_url: str,
) -> None:
    settings = _auth_settings(auth_database_url)
    runtime = CoreRuntime(settings, lock_key=uuid4().int & ((1 << 63) - 1))
    app = create_app(settings, runtime=runtime)
    headers = {"Origin": "http://localhost:3000"}

    async with app.router.lifespan_context(app):
        async with runtime.database.session_factory() as database_session:
            admin = await AuthService().create_admin(
                database_session,
                username="root-admin",
                real_name="管理员",
                password="admin-password-123",
            )
        async with (
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
                headers=headers,
            ) as target_client,
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
                headers=headers,
            ) as admin_client,
        ):
            terms_version = (await target_client.get("/api/legal/platform-terms/current")).json()[
                "version"
            ]
            registered = await target_client.post(
                "/api/auth/register",
                json={
                    "username": "reset-target",
                    "real_name": "待重置用户",
                    "password": "target-password-123",
                    "platform_terms_version": terms_version,
                },
            )
            target_id = registered.json()["user"]["id"]
            admin_login = await admin_client.post(
                "/api/auth/login",
                json={"username": "root-admin", "password": "admin-password-123"},
            )
            assert admin_login.status_code == 200

            reset = await admin_client.post(f"/api/admin/users/{target_id}/temporary-password")
            assert reset.status_code == 200
            temporary_password = reset.json()["temporary_password"]
            assert reset.headers["cache-control"] == "no-store"
            assert len(temporary_password) >= 32
            assert (await target_client.get("/api/auth/me")).status_code == 401

            temporary_login = await target_client.post(
                "/api/auth/login",
                json={"username": "reset-target", "password": temporary_password},
            )
            assert temporary_login.status_code == 200
            assert temporary_login.json()["user"]["must_change_password"] is True
            forbidden_profile = await target_client.patch(
                "/api/users/me",
                json={"real_name": "不应修改"},
            )
            assert forbidden_profile.status_code == 403
            assert forbidden_profile.json()["error"]["code"] == "password_change_required"

            changed = await target_client.post(
                "/api/auth/change-password",
                json={
                    "current_password": temporary_password,
                    "new_password": "target-new-password-123",
                },
            )
            assert changed.status_code == 200
            assert changed.json()["user"]["must_change_password"] is False

        async with runtime.database.session_factory() as database_session:
            audit_details = (
                await database_session.execute(
                    text("SELECT details FROM audit_logs WHERE action = 'password.reset'")
                )
            ).scalar_one()
            assert "temporary_password" not in audit_details
            assert str(admin.id) not in str(audit_details)


@pytest.mark.asyncio
async def test_admin_cli_service_requires_current_migration_and_audits(
    auth_database_url: str,
) -> None:
    settings = _auth_settings(auth_database_url)
    user_id, stored_username = await create_admin_with_cli(
        settings,
        username="cli-admin",
        real_name="命令行管理员",
        password="cli-admin-password-123",
    )
    assert stored_username == "cli-admin"

    engine = create_async_engine(auth_database_url)
    try:
        async with engine.connect() as connection:
            role = await connection.scalar(
                text("SELECT role FROM users WHERE id = :id"),
                {"id": UUID(user_id)},
            )
            details = await connection.scalar(
                text("SELECT details FROM audit_logs WHERE action = 'admin.created'")
            )
        assert role == "ADMIN"
        assert details == {"username_normalized": "cli-admin"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_room_connection_replacement_and_stale_release(
    auth_database_url: str,
) -> None:
    engine = create_async_engine(auth_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = RoomConnectionService()
    user_id, first_room, second_room = uuid4(), uuid4(), uuid4()
    first_id, second_id = uuid4(), uuid4()
    try:
        async with factory() as database_session:
            async with database_session.begin():
                database_session.add(
                    User(
                        id=user_id,
                        username="LeaseUser",
                        username_normalized="leaseuser",
                        real_name="租约用户",
                        password_hash="argon2id-probe",
                    )
                )
                await database_session.flush()
                database_session.add(
                    Rule(
                        id=uuid4(),
                        rule_key="lease-rule",
                        version=1,
                        name="租约测试规则",
                        side_size=1,
                        estimated_seconds=60,
                        created_by=user_id,
                    )
                )
                await database_session.flush()
                rule_id = (
                    await database_session.execute(
                        text("SELECT id FROM rules WHERE rule_key = 'lease-rule'")
                    )
                ).scalar_one()
                database_session.add_all(
                    [
                        Room(
                            id=first_room,
                            code="LSE001",
                            title="租约测试一",
                            label="TEST",
                            topic_snapshot={},
                            rule_id=rule_id,
                            rule_snapshot={},
                            organizer_user_id=user_id,
                        ),
                        Room(
                            id=second_room,
                            code="LSE002",
                            title="租约测试二",
                            label="TEST",
                            topic_snapshot={},
                            rule_id=rule_id,
                            rule_snapshot={},
                            organizer_user_id=user_id,
                        ),
                    ]
                )
            first = await service.acquire(
                database_session,
                user_id=user_id,
                room_id=first_room,
                connection_id=first_id,
            )
            second = await service.acquire(
                database_session,
                user_id=user_id,
                room_id=second_room,
                connection_id=second_id,
            )
            assert first.connection_epoch == 1
            assert second.connection_epoch == 2
            assert second.replaced_connection_id == first_id
            assert (
                await service.release(
                    database_session,
                    user_id=user_id,
                    connection_id=first_id,
                    connection_epoch=1,
                )
                is False
            )
            assert (
                await service.heartbeat(
                    database_session,
                    user_id=user_id,
                    connection_id=second_id,
                    connection_epoch=2,
                )
                is True
            )
            assert (
                await service.release(
                    database_session,
                    user_id=user_id,
                    connection_id=second_id,
                    connection_epoch=2,
                )
                is True
            )
    finally:
        await engine.dispose()

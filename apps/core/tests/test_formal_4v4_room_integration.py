from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jx_core.auth.service import AuthService
from jx_core.legal.terms import get_current_human_participation_terms
from jx_core.models import User, VoiceProfile
from jx_core.rooms.schemas import (
    DeviceCheckRequest,
    RoomCreateRequest,
    RoomJoinRequest,
    SeatSelectRequest,
)
from jx_core.rooms.service import RoomService
from jx_core.rules.schemas import ModelProfileCreate, RuleCreate, VoiceProfileCreate
from jx_core.rules.service import CatalogService, RuleService

pytestmark = pytest.mark.integration


def _database_url() -> str:
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("set RUN_DATABASE_INTEGRATION=1 to run real PostgreSQL tests")
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.fail("TEST_DATABASE_URL is required for formal 4v4 room integration")
    if value == os.environ.get("DATABASE_URL"):
        pytest.fail("TEST_DATABASE_URL must not equal DATABASE_URL")
    return value


@pytest.mark.asyncio
async def test_formal_4v4_room_service_assigns_four_humans_and_starts() -> None:
    engine = create_async_engine(_database_url())
    connection = await engine.connect()
    transaction = await connection.begin()
    factory = async_sessionmaker(
        connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    catalog = CatalogService()
    rules = RuleService()
    rooms = RoomService()
    suffix = uuid4().hex[:10]
    try:
        async with factory() as session:
            host = await session.scalar(
                select(VoiceProfile).where(
                    VoiceProfile.kind == "HOST", VoiceProfile.status == "ENABLED"
                )
            )
            await session.commit()
            if host is None:
                host = await catalog.create_voice(
                    session,
                    payload=VoiceProfileCreate(
                        name=f"integration-host-{suffix}",
                        kind="HOST",
                        provider_voice=f"integration-host-{suffix}",
                    ),
                )
            for index in range(8):
                await catalog.create_voice(
                    session,
                    payload=VoiceProfileCreate(
                        name=f"integration-agent-{suffix}-{index}",
                        kind="AGENT",
                        provider_voice=f"integration-agent-{suffix}-{index}",
                        avatar_key=f"agent-{(index % 8) + 1:02d}",
                    ),
                )
            model = await catalog.create_model(
                session,
                payload=ModelProfileCreate(
                    name=f"integration-model-{suffix}", config_ref=f"integration-{suffix}"
                ),
            )
            admin = await session.scalar(select(User).where(User.role == "ADMIN"))
            if admin is None:
                admin = User(
                    username=f"integration-admin-{suffix}",
                    username_normalized=f"integration-admin-{suffix}",
                    real_name="正式赛集成管理员",
                    password_hash="unusable-test-hash",
                    role="ADMIN",
                )
                session.add(admin)
                await session.flush()
            await session.commit()
            rule = await rules.create_rule(
                session,
                creator_user_id=admin.id,
                payload=RuleCreate.model_validate(
                    {
                        "host_voice_profile_id": str(host.id),
                        "default_agent_model_profile_id": str(model.id),
                        "draft": {
                            "name": f"正式 4v4 集成规则 {suffix}",
                            "side_size": 4,
                            "stages": [
                                {
                                    "name": "固定发言",
                                    "stage_kind": "FIXED_SPEECH",
                                    "actions": [
                                        {
                                            "side": "AFFIRMATIVE",
                                            "seat_no": 1,
                                            "duration_seconds": 5,
                                        },
                                        {"side": "NEGATIVE", "seat_no": 1, "duration_seconds": 5},
                                    ],
                                },
                                {"name": "结束", "stage_kind": "END"},
                            ],
                        },
                    }
                ),
            )
            await rules.review_audio(session, rule_id=rule.id)
            await rules.enable_rule(session, rule_id=rule.id)
            terms = get_current_human_participation_terms().version
            registered = []
            for index in range(4):
                result = await AuthService().register(
                    session,
                    username=f"integration-human-{suffix}-{index}",
                    real_name=f"正式赛真人{index + 1}",
                    password=f"integration-human-password-{suffix}-{index}",
                    platform_terms_version="platform-terms-v1",
                )
                registered.append(result.user.id)
            room = await rooms.create_room(
                session,
                organizer_user_id=registered[0],
                organizer_role="USER",
                payload=RoomCreateRequest.model_validate(
                    {
                        "title": f"正式 4v4 房间 {suffix}",
                        "label": "正式赛",
                        "rule_id": str(rule.id),
                        "custom_topic_title": "人机协作",
                        "affirmative_text": "效率优先",
                        "negative_text": "公平优先",
                        "human_participation_terms_version": terms,
                    }
                ),
            )
            assignments = (
                (registered[0], "AFFIRMATIVE", 1),
                (registered[1], "AFFIRMATIVE", 2),
                (registered[2], "NEGATIVE", 1),
                (registered[3], "NEGATIVE", 2),
            )
            for index, (user_id, side, seat_no) in enumerate(assignments):
                if index:
                    await rooms.join_room(
                        session,
                        user_id=user_id,
                        room_id=room.id,
                        payload=RoomJoinRequest(
                            member_role="DEBATER",
                            human_participation_terms_version=terms,
                        ),
                    )
                await rooms.select_seat(
                    session,
                    user_id=user_id,
                    room_id=room.id,
                    payload=SeatSelectRequest(
                        side=side,
                        seat_no=seat_no,
                        human_participation_terms_version=terms,
                    ),
                )
                check = await rooms.save_device_check(
                    session,
                    user_id=user_id,
                    room_id=room.id,
                    payload=DeviceCheckRequest(status="PASS", details={"simulation": "pass"}),
                )
                await rooms.ready(
                    session,
                    user_id=user_id,
                    room_id=room.id,
                    check_version=check.check_version,
                )
            started = await rooms.start_room(
                session,
                actor_user_id=registered[0],
                actor_role="USER",
                room_id=room.id,
            )
            assert started.status == "START_PENDING_RUNTIME"
            _, _, seats = await rooms.snapshot(session, room_id=room.id)
            assert sum(seat.occupant_type == "HUMAN" for seat in seats) == 4
            assert sum(seat.occupant_type == "AGENT" for seat in seats) == 4
            assert all(
                sum(seat.occupant_type == kind for seat in seats if seat.side == side) == 2
                for side in ("AFFIRMATIVE", "NEGATIVE")
                for kind in ("HUMAN", "AGENT")
            )
    finally:
        await transaction.rollback()
        await connection.close()
        await engine.dispose()

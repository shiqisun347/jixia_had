"""Spec 265 persistence regressions; all service commits stay inside a rollback."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jx_core.auth.errors import AuthError
from jx_core.legal.terms import get_current_human_participation_terms
from jx_core.models import (
    AgentProfile,
    ModelProfile,
    Rule,
    RuleStage,
    StageAction,
    StagePromptTemplate,
    User,
    VoiceProfile,
)
from jx_core.rooms.schemas import (
    DeviceCheckRequest,
    RoleChangeRequest,
    RoomCreateRequest,
    RoomJoinRequest,
    SeatSelectRequest,
)
from jx_core.rooms.service import RoomService
from jx_core.rules.readiness import inspect_rule
from jx_core.rules.service import RuleService

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def composition_pool():
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("requires independent PostgreSQL")
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or url == os.environ.get("DATABASE_URL"):
        pytest.fail("independent TEST_DATABASE_URL required")
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        async with factory() as session:
            tag = uuid4().hex[:8]
            admin = await session.scalar(select(User).where(User.role == "ADMIN"))
            if admin is None:
                admin = User(
                    username=f"265-admin-{tag}",
                    username_normalized=f"265-admin-{tag}",
                    real_name="成员组合管理员",
                    password_hash="unusable-test-hash",
                    role="ADMIN",
                )
                session.add(admin)
                await session.flush()
            users = [
                User(
                    username=f"265-{tag}-{i}",
                    username_normalized=f"265-{tag}-{i}",
                    real_name=f"模拟真人{i}",
                    password_hash="unusable-test-hash",
                )
                for i in range(8)
            ]
            session.add_all(users)
            model = ModelProfile(name=f"265-{tag}", config_ref="test-only")
            session.add(model)
            await session.flush()
            rule = Rule(
                rule_key=f"265-{tag}",
                version=1,
                name="自由组成测试",
                side_size=4,
                estimated_seconds=12,
                created_by=admin.id,
            )
            session.add(rule)
            await session.flush()
            stages = [
                RuleStage(rule_id=rule.id, position=1, name="立论", stage_kind="FIXED_SPEECH"),
                RuleStage(
                    rule_id=rule.id,
                    position=2,
                    name="自由",
                    stage_kind="FREE_DEBATE",
                    duration_seconds=5,
                ),
                RuleStage(rule_id=rule.id, position=3, name="结束", stage_kind="END"),
            ]
            session.add_all(stages)
            await session.flush()
            session.add_all(
                [
                    StageAction(
                        stage_id=stages[0].id,
                        position=i + 1,
                        side=side,
                        seat_no=1,
                        duration_seconds=1,
                    )
                    for i, side in enumerate(("AFFIRMATIVE", "NEGATIVE"))
                ]
            )
            for stage, purposes in ((stages[0], ("SPEECH",)), (stages[1], ("SPEECH", "DECISION"))):
                session.add_all(
                    [
                        StagePromptTemplate(
                            stage_id=stage.id, purpose=p, template_text="测试 Prompt"
                        )
                        for p in purposes
                    ]
                )
            voices = [
                VoiceProfile(
                    name=f"265-{tag}-{i}",
                    kind="AGENT",
                    provider_voice=f"265-{tag}-{i}",
                    avatar_key=f"agent-{i + 1:02d}",
                )
                for i in range(8)
            ]
            session.add_all(voices)
            await session.flush()
            session.add_all(
                [
                    AgentProfile(
                        rule_id=rule.id,
                        name=f"测试Agent{i}",
                        model_profile_id=model.id,
                        voice_profile_id=v.id,
                    )
                    for i, v in enumerate(voices)
                ]
            )
            await session.commit()
            await RuleService().enable_rule(session, rule_id=rule.id)
            yield session, rule, users, admin, model, stages
        await transaction.rollback()
    await engine.dispose()


def payload(rule, **values):
    return RoomCreateRequest(
        rule_id=rule.id,
        title="成员组成集成测试",
        label="测试",
        custom_topic_title="辩题",
        affirmative_text="正方",
        negative_text="反方",
        human_participation_terms_version=get_current_human_participation_terms().version,
        **values,
    )


async def seat_humans(session, rooms, room, users, counts):
    index = 0
    terms = get_current_human_participation_terms().version
    for side, count in zip(("AFFIRMATIVE", "NEGATIVE"), counts, strict=True):
        for number in range(1, count + 1):
            user = users[index]
            if index:
                await rooms.join_room(
                    session,
                    user_id=user.id,
                    room_id=room.id,
                    payload=RoomJoinRequest(
                        member_role="DEBATER", human_participation_terms_version=terms
                    ),
                )
            await rooms.select_seat(
                session,
                user_id=user.id,
                room_id=room.id,
                payload=SeatSelectRequest(
                    side=side, seat_no=number, human_participation_terms_version=terms
                ),
            )
            check = await rooms.save_device_check(
                session,
                user_id=user.id,
                room_id=room.id,
                payload=DeviceCheckRequest(status="PASS", details={"synthetic": True}),
            )
            await rooms.ready(
                session, user_id=user.id, room_id=room.id, check_version=check.check_version
            )
            index += 1


@pytest.mark.asyncio
@pytest.mark.parametrize("counts", [(a, n) for a in range(5) for n in range(5)])
async def test_all_25_compositions_start(composition_pool, counts):
    session, rule, users, admin, _, _ = composition_pool
    rooms = RoomService()
    all_agent = counts == (0, 0)
    owner = admin if all_agent else users[0]
    room = await rooms.create_room(
        session,
        organizer_user_id=owner.id,
        organizer_role="ADMIN" if all_agent else "USER",
        payload=payload(rule, is_all_agent=all_agent),
    )
    await seat_humans(session, rooms, room, users, counts)
    started = await rooms.start_room(
        session,
        room_id=room.id,
        actor_user_id=owner.id,
        actor_role="ADMIN" if all_agent else "USER",
    )
    assert started.status == "START_PENDING_RUNTIME"
    _, _, seats = await rooms.snapshot(session, room_id=room.id)
    assert sum(s.occupant_type == "HUMAN" for s in seats) == sum(counts)
    await session.commit()


@pytest.mark.asyncio
async def test_no_agent_model_or_prompt_required_for_pure_humans(composition_pool):
    session, rule, users, _, model, stages = composition_pool
    model.status = "DISABLED"
    await session.execute(
        delete(StagePromptTemplate).where(StagePromptTemplate.stage_id.in_([s.id for s in stages]))
    )
    await session.commit()
    inspection = await inspect_rule(session, rule)
    assert inspection.ready and inspection.available_agent_count == 0
    await session.commit()
    rooms = RoomService()
    room = await rooms.create_room(
        session,
        organizer_user_id=users[0].id,
        organizer_role="USER",
        payload=payload(rule, auto_fill_agents=False),
    )
    await seat_humans(session, rooms, room, users, (4, 4))
    started = await rooms.start_room(
        session, room_id=room.id, actor_user_id=users[0].id, actor_role="USER"
    )
    assert started.status == "START_PENDING_RUNTIME"


@pytest.mark.asyncio
async def test_insufficient_agents_and_nonadmin_dedicated_agent_room(composition_pool):
    session, rule, users, admin, model, _ = composition_pool
    admin_id = admin.id
    user_id = users[0].id
    all_agent_payload = payload(rule, is_all_agent=True)
    mixed_payload = payload(rule)
    rooms = RoomService()
    with pytest.raises(AuthError, match="forbidden"):
        await rooms.create_room(
            session,
            organizer_user_id=user_id,
            organizer_role="USER",
            payload=all_agent_payload,
        )
    model.status = "DISABLED"
    await session.commit()
    with pytest.raises(AuthError, match="agent_capacity_insufficient"):
        await rooms.create_room(
            session,
            organizer_user_id=admin_id,
            organizer_role="ADMIN",
            payload=all_agent_payload,
        )
    room = await rooms.create_room(
        session, organizer_user_id=user_id, organizer_role="USER", payload=mixed_payload
    )
    _, _, seats = await rooms.snapshot(session, room_id=room.id)
    assert all(s.occupant_type == "EMPTY" for s in seats)
    await session.commit()
    with pytest.raises(AuthError, match="room_seats_incomplete"):
        await rooms.start_room(
            session, room_id=room.id, actor_user_id=user_id, actor_role="USER"
        )


@pytest.mark.asyncio
async def test_human_only_role_change_restores_empty_seat(composition_pool):
    session, rule, users, _, _, _ = composition_pool
    rooms = RoomService()
    room = await rooms.create_room(
        session,
        organizer_user_id=users[0].id,
        organizer_role="USER",
        payload=payload(rule, auto_fill_agents=False),
    )
    await seat_humans(session, rooms, room, users, (1, 0))
    await rooms.change_role(
        session,
        user_id=users[0].id,
        room_id=room.id,
        payload=RoleChangeRequest(member_role="SPECTATOR"),
    )
    _, _, seats = await rooms.snapshot(session, room_id=room.id)
    assert all(s.occupant_type == "EMPTY" for s in seats)

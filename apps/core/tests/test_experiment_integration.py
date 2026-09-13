from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jx_core.auth.errors import APIError, AuthError
from jx_core.auth.passwords import PasswordService
from jx_core.experiments.postmatch import create_postmatch_annotation_work
from jx_core.experiments.schemas import (
    ExperimentBatchCreateRequest,
    ExperimentBatchUpdateRequest,
    ExperimentExpertBinding,
    ExperimentMemberBinding,
    ExperimentRosterPutRequest,
    ExperimentTeamBinding,
    ParticipantAnnotationAnswerSaveRequest,
    ParticipantQuestionnaireSubmitRequest,
)
from jx_core.experiments.service import ExperimentService
from jx_core.experiments.visibility import can_view_experiment_result
from jx_core.legal.terms import get_current_human_participation_terms
from jx_core.matches.routes import _match_page_permissions
from jx_core.models import (
    AgentProfile,
    AuditLog,
    BackgroundTask,
    ExperimentBatch,
    ExperimentMatchAttempt,
    ExpertAnnotationAnswer,
    ExpertAnnotationTask,
    FormatVersion,
    FreeDebateOpportunity,
    Match,
    ModelProfile,
    ParticipantAnnotationTask,
    Room,
    Rule,
    ScheduledMatch,
    ScheduledSeat,
    Seat,
    Speech,
    Topic,
    User,
    VoiceProfile,
)
from jx_core.rooms.service import RoomService

pytestmark = pytest.mark.integration


def _database_url() -> str:
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("set RUN_DATABASE_INTEGRATION=1 to run real PostgreSQL tests")
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.fail("TEST_DATABASE_URL is required for experiment integration tests")
    if value == os.environ.get("DATABASE_URL"):
        pytest.fail("TEST_DATABASE_URL must not equal DATABASE_URL")
    return value


def _migrate(database_url: str) -> None:
    completed = subprocess.run(
        ["uv", "run", "--package", "jx-core", "alembic", "upgrade", "head"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": database_url},
    )
    if completed.returncode != 0:
        raise AssertionError(f"Alembic failed:\n{completed.stdout}\n{completed.stderr}")


async def _truncate(engine: object) -> None:
    async with engine.begin() as connection:  # type: ignore[union-attr]
        await connection.execute(
            text(
                "TRUNCATE users, voice_profiles, model_profiles, agent_profiles, topics, rules "
                "CASCADE"
            )
        )


@pytest.mark.asyncio
async def test_v21_experiment_management_closes_real_postgres_loop() -> None:
    database_url = _database_url()
    _migrate(database_url)
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ExperimentService()
    passwords = PasswordService()
    try:
        await _truncate(engine)
        async with factory() as session:
            async with session.begin():
                admin = User(
                    username="experiment-admin",
                    username_normalized="experiment-admin",
                    real_name="实验管理员",
                    password_hash=passwords.hash("Testpass123"),
                    role="ADMIN",
                )
                session.add(admin)
                await session.flush()

                model = ModelProfile(name="v21-model", config_ref="v21-model-config")
                session.add(model)
                await session.flush()
                agents: list[AgentProfile] = []
                for index in range(1, 9):
                    voice = VoiceProfile(
                        name=f"v21-voice-{index}",
                        kind="AGENT",
                        provider_voice=f"v21-provider-{index}",
                        avatar_key=f"agent-{index:02d}",
                    )
                    session.add(voice)
                    await session.flush()
                    agent = AgentProfile(
                        name=f"v21-agent-{index}",
                        model_profile_id=model.id,
                        voice_profile_id=voice.id,
                    )
                    session.add(agent)
                    agents.append(agent)
                rule = Rule(
                    rule_key="paper-experiment-v21",
                    version=1,
                    name="论文实验三阶段赛制",
                    side_size=4,
                    estimated_seconds=900,
                    status="ENABLED",
                    created_by=admin.id,
                    audio_reviewed_at=datetime.now(UTC),
                )
                session.add(rule)
                await session.flush()
                for agent in agents:
                    agent.rule_id = rule.id
                format_version = FormatVersion(
                    format_key="paper-experiment-v21",
                    version=1,
                    name="论文实验 v2.1",
                    status="PUBLISHED",
                    rule_id=rule.id,
                    created_by=admin.id,
                    published_snapshot={},
                )
                session.add(format_version)
                await session.flush()
                for agent in agents:
                    agent.format_version_id = format_version.id
                other_rule = Rule(
                    rule_key="other-experiment-rule",
                    version=1,
                    name="其他论文实验赛制",
                    side_size=4,
                    estimated_seconds=900,
                    status="ENABLED",
                    created_by=admin.id,
                    audio_reviewed_at=datetime.now(UTC),
                )
                session.add(other_rule)
                await session.flush()
                other_voice = VoiceProfile(
                    name="other-rule-voice",
                    kind="AGENT",
                    provider_voice="other-rule-voice",
                    avatar_key="agent-10",
                )
                session.add(other_voice)
                await session.flush()
                other_agent = AgentProfile(
                    name="other-rule-agent",
                    rule_id=other_rule.id,
                    model_profile_id=model.id,
                    voice_profile_id=other_voice.id,
                )
                session.add(other_agent)
                await session.flush()
                topics = [
                    Topic(
                        topic_key=f"v21-topic-{index}",
                        version=1,
                        title=f"测试辩题 {index}",
                        affirmative_text=f"正方立场 {index}",
                        negative_text=f"反方立场 {index}",
                        source_text=f"测试来源 {index}",
                        cedar_id=f"CEDAR-{index}" if index <= 6 else None,
                        created_by=admin.id,
                    )
                    for index in range(1, 8)
                ]
                session.add_all(topics)
                await session.flush()
                admin_id = admin.id
                rule_id = rule.id
                agent_ids = [agent.id for agent in agents]
                other_agent_id = other_agent.id
                topic_ids = [topic.id for topic in topics]

            first_accounts = await service.generate_anonymous_accounts(
                session, actor_user_id=admin_id
            )
            assert first_accounts.created_count == 21
            assert first_accounts.default_password == "Jixia2026"
            assert all(item.temporary_password is None for item in first_accounts.accounts)
            for item in first_accounts.accounts:
                user = await session.get(User, item.user_id)
                assert user is not None and user.must_change_password is False
                assert user.real_name == item.code
                assert passwords.verify(user.password_hash, first_accounts.default_password).valid
            await session.rollback()

            async with session.begin():
                participant = await session.scalar(
                    select(User).where(User.username_normalized == "p01")
                )
                expert = await session.scalar(select(User).where(User.username_normalized == "e01"))
                manually_bound = await session.scalar(
                    select(User).where(User.username_normalized == "p02")
                )
                assert participant is not None and expert is not None
                assert manually_bound is not None
                participant.real_name = "待绑定参与者"
                expert.real_name = "待绑定专家"
                manually_bound.real_name = "已绑定姓名"

            second_accounts = await service.generate_anonymous_accounts(
                session, actor_user_id=admin_id
            )
            assert second_accounts.created_count == 0
            assert second_accounts.existing_count == 21
            assert second_accounts.default_password == "Jixia2026"
            assert all(item.temporary_password is None for item in second_accounts.accounts)
            for item in second_accounts.accounts:
                user = await session.get(User, item.user_id)
                assert user is not None
                assert user.must_change_password is False
                assert passwords.verify(user.password_hash, second_accounts.default_password).valid
                expected_name = "已绑定姓名" if item.code == "P02" else item.code
                assert user.real_name == expected_name
            audits = list(
                (
                    await session.scalars(
                        select(AuditLog).where(
                            AuditLog.action == "admin.experiment.accounts_generated"
                        )
                    )
                ).all()
            )
            assert audits and all("password" not in str(item.details).lower() for item in audits)
            await session.rollback()

            participants = [item for item in first_accounts.accounts if item.code.startswith("P")]
            experts = [item for item in first_accounts.accounts if item.code.startswith("E")]
            batch = await service.create_batch(
                session,
                actor_user_id=admin_id,
                payload=ExperimentBatchCreateRequest(
                    code="PAPER_V21",
                    title="论文实验 v2.1",
                    rule_id=rule_id,
                ),
            )
            batch_id = batch.id
            batch = await service.update_batch(
                session,
                batch_id=batch_id,
                actor_user_id=admin_id,
                payload=ExperimentBatchUpdateRequest(
                    code="PAPER_V21",
                    title="论文实验正式批次",
                    rule_id=rule_id,
                ),
            )
            assert batch.title == "论文实验正式批次"
            assert batch.format_version_id is None
            assert batch.rule_config_snapshot is not None
            assert batch.rule_config_snapshot["schema"] == "experiment-rule-config-v1"
            assert batch.rule_config_snapshot["config"]["schema"] == "rule-config-v1"

            disposable = await service.create_batch(
                session,
                actor_user_id=admin_id,
                payload=ExperimentBatchCreateRequest(
                    code="PAPER_DELETE",
                    title="待删除草稿",
                    rule_id=rule_id,
                ),
            )
            disposable_id = disposable.id
            await service.delete_draft_batch(
                session,
                batch_id=disposable_id,
                actor_user_id=admin_id,
            )
            assert await session.get(ExperimentBatch, disposable_id) is None
            await session.rollback()
            roster = ExperimentRosterPutRequest(
                teams=[
                    ExperimentTeamBinding(
                        team_code=f"T{index + 1:02d}",
                        agent_profile_id=agent_ids[index],
                        members=[
                            ExperimentMemberBinding(
                                user_id=participants[index * 3 + offset].user_id,
                                participant_code=participants[index * 3 + offset].code,
                            )
                            for offset in range(3)
                        ],
                    )
                    for index in range(6)
                ],
                experts=[
                    ExperimentExpertBinding(user_id=item.user_id, expert_code=item.code)
                    for item in experts
                ],
            )
            invalid_roster = roster.model_copy(deep=True)
            invalid_roster.teams[0].agent_profile_id = other_agent_id
            with pytest.raises(APIError) as invalid_agent_error:
                await service.replace_roster(
                    session, batch_id=batch_id, payload=invalid_roster
                )
            assert invalid_agent_error.value.code == "experiment_roster_invalid"
            await service.replace_roster(session, batch_id=batch_id, payload=roster)
            roster_detail = await service.get_roster(session, batch_id=batch_id)
            assert [team.team_code for team in roster_detail.teams] == [
                f"T{index:02d}" for index in range(1, 7)
            ]
            assert [expert.expert_code for expert in roster_detail.experts] == [
                "E01",
                "E02",
                "E03",
            ]
            await session.rollback()

            async with session.begin():
                incomplete_topic = await session.get(Topic, topic_ids[0])
                assert incomplete_topic is not None
                incomplete_topic.source_text = None
            with pytest.raises(APIError) as source_error:
                await service.generate_schedule(
                    session,
                    batch_id=batch_id,
                    topic_ids=tuple(topic_ids[:6]),
                    training_topic_id=topic_ids[6],
                )
            assert source_error.value.code == "experiment_schedule_invalid"
            assert source_error.value.field_errors is not None
            assert "topic_sources" in source_error.value.field_errors
            async with session.begin():
                incomplete_topic = await session.get(Topic, topic_ids[0])
                assert incomplete_topic is not None
                incomplete_topic.source_text = "恢复后的测试来源"

            schedule = await service.generate_schedule(
                session,
                batch_id=batch_id,
                topic_ids=tuple(topic_ids[:6]),
                training_topic_id=topic_ids[6],
            )
            assert len(schedule.matches) == 21
            readable_csv = await service.export_readable_schedule_csv(session, batch_id=batch_id)
            assert readable_csv.startswith("\ufeff类型,轮次,场次,辩题")
            assert "正方席位,反方席位,状态" in readable_csv.splitlines()[0]
            assert "P01" in readable_csv and "v21-agent-" in readable_csv
            accounts_csv = await service.export_accounts_csv(session)
            assert accounts_csv.startswith("\ufeff编号,用户名,默认密码,用途,状态")
            assert "P01,P01,Jixia2026,参赛者,已创建" in accounts_csv
            assert "E03,E03,Jixia2026,专家,已创建" in accounts_csv
            assert sum(item.kind == "FORMAL" for item in schedule.matches) == 18
            assert sum(item.kind == "TRAINING" for item in schedule.matches) == 3
            progress = await service.batch_progress(session, batch_id=batch_id)
            assert progress.matches[0].topic_title == "测试辩题 1"
            assert progress.matches[0].affirmative_team_code.startswith("T")
            assert progress.matches[0].negative_team_code.startswith("T")
            assert (
                progress.matches[0].affirmative_team_code != progress.matches[0].negative_team_code
            )
            csv_text = await service.export_schedule_csv(session, batch_id=batch_id)
            await session.rollback()
            imported = await service.import_schedule_csv(
                session, batch_id=batch_id, csv_text=csv_text
            )
            assert imported.imported_match_count == 21
            assert imported.training_match_count == 3

            published = await service.publish_batch(session, batch_id=batch_id)
            assert published.status == "PUBLISHED"
            appointments = await service.list_appointments(session, user_id=participants[0].user_id)
            assert len(appointments) == 7
            await session.rollback()

            training_match = await session.scalar(
                select(ScheduledMatch)
                .join(ScheduledSeat, ScheduledSeat.scheduled_match_id == ScheduledMatch.id)
                .where(
                    ScheduledMatch.batch_id == batch_id,
                    ScheduledMatch.kind == "TRAINING",
                    ScheduledSeat.user_id == participants[0].user_id,
                    ScheduledSeat.occupant_kind == "HUMAN",
                )
            )
            assert training_match is not None
            training_match_id = training_match.id
            await session.rollback()
            entered = await service.enter_scheduled_match(
                session,
                scheduled_match_id=training_match_id,
                user_id=participants[0].user_id,
                user_role="PARTICIPANT",
                human_participation_terms_version=get_current_human_participation_terms().version,
            )
            training_room = await session.get(Room, entered.room_id)
            assert training_room is not None
            assert training_room.format_version_id is None
            assert training_room.format_snapshot["schema"] == "rule-config-v1"
            training_agent_ids = set(
                (
                    await session.scalars(
                        select(ScheduledSeat.agent_profile_id).where(
                            ScheduledSeat.scheduled_match_id == training_match_id,
                            ScheduledSeat.occupant_kind == "AGENT",
                        )
                    )
                ).all()
            )
            room_agent_ids = set(
                (
                    await session.scalars(
                        select(Seat.agent_profile_id).where(
                            Seat.room_id == training_room.id,
                            Seat.occupant_type == "AGENT",
                        )
                    )
                ).all()
            )
            assert len(room_agent_ids) == 8
            assert room_agent_ids.issubset(set(agent_ids))
            assert training_agent_ids != room_agent_ids
            training_room_id = training_room.id
            await session.rollback()
            await RoomService().terminate_room(
                session,
                actor_user_id=participants[0].user_id,
                actor_role="USER",
                room_id=training_room_id,
            )
            terminated_training_attempt = await session.scalar(
                select(ExperimentMatchAttempt).where(
                    ExperimentMatchAttempt.scheduled_match_id == training_match_id
                )
            )
            assert terminated_training_attempt is not None
            assert terminated_training_attempt.status == "TERMINATED"
            await session.rollback()

            scheduled_match = await session.scalar(
                select(ScheduledMatch)
                .where(ScheduledMatch.batch_id == batch_id)
                .order_by(ScheduledMatch.round_no, ScheduledMatch.match_no)
                .limit(1)
            )
            assert scheduled_match is not None
            scheduled_match_id = scheduled_match.id
            await session.rollback()
            async with session.begin():
                attempt = ExperimentMatchAttempt(
                    scheduled_match_id=scheduled_match_id,
                    attempt_no=1,
                    status="CREATED",
                )
                session.add(attempt)
                await session.flush()
                attempt_id = attempt.id
            with pytest.raises(APIError) as error:
                await service.disable_batch(session, batch_id=batch_id)
            assert error.value.code == "experiment_batch_active"

            async with session.begin():
                stored_attempt = await session.get(ExperimentMatchAttempt, attempt_id)
                assert stored_attempt is not None
                stored_attempt.status = "TERMINATED"
                await session.flush()

            completed_schedule = await session.scalar(
                select(ScheduledMatch)
                .join(ScheduledSeat, ScheduledSeat.scheduled_match_id == ScheduledMatch.id)
                .where(
                    ScheduledMatch.batch_id == batch_id,
                    ScheduledMatch.kind == "FORMAL",
                    ScheduledMatch.id != scheduled_match_id,
                    ScheduledSeat.occupant_kind == "AGENT",
                    ScheduledSeat.seat_no == 1,
                )
                .order_by(ScheduledMatch.round_no, ScheduledMatch.match_no)
                .limit(1)
            )
            assert completed_schedule is not None
            seats = list(
                (
                    await session.scalars(
                        select(ScheduledSeat)
                        .where(ScheduledSeat.scheduled_match_id == completed_schedule.id)
                        .order_by(ScheduledSeat.side, ScheduledSeat.seat_no)
                    )
                ).all()
            )
            human_seats = [seat for seat in seats if seat.occupant_kind == "HUMAN"]
            agent_seats = [seat for seat in seats if seat.occupant_kind == "AGENT"]
            assert len(human_seats) == 6 and len(agent_seats) == 2
            organizer_id = human_seats[0].user_id
            assert organizer_id is not None
            viewer = User(
                username="experiment-viewer",
                username_normalized="experiment-viewer",
                real_name="实验观众",
                password_hash=passwords.hash("Testpass123"),
            )
            session.add(viewer)
            await session.flush()
            viewer_id = viewer.id
            room = Room(
                code="V21T01",
                title="v2.1 赛后闭环",
                label="论文实验",
                topic_id=completed_schedule.topic_id,
                topic_snapshot={"title": "测试辩题"},
                rule_id=rule_id,
                rule_snapshot={"side_size": 4},
                organizer_user_id=organizer_id,
                status="FINISHED",
            )
            session.add(room)
            await session.flush()
            match = Match(
                room_id=room.id,
                status="FINISHED",
                runtime_snapshot={},
                context_version=8,
                ended_at=datetime.now(UTC),
            )
            session.add(match)
            await session.flush()
            completed_attempt = ExperimentMatchAttempt(
                scheduled_match_id=completed_schedule.id,
                attempt_no=1,
                room_id=room.id,
                match_id=match.id,
                status="COMPLETED",
                ended_at=datetime.now(UTC),
            )
            session.add(completed_attempt)
            await session.flush()
            completed_schedule.status = "COMPLETED"
            completed_schedule.effective_attempt_id = completed_attempt.id

            speech_specs = [
                ("HUMAN", seat.side, seat.seat_no, seat.user_id, None) for seat in human_seats
            ] + [
                ("AGENT", seat.side, seat.seat_no, None, seat.agent_profile_id)
                for seat in agent_seats
            ]
            for sequence_no, (kind, side, seat_no, user_id, agent_id) in enumerate(
                speech_specs, start=1
            ):
                opportunity = FreeDebateOpportunity(
                    match_id=match.id,
                    experiment_attempt_id=completed_attempt.id,
                    sequence_no=sequence_no,
                    side=side,
                    trigger_kind="HUMAN_SPEECH",
                    context_version=sequence_no,
                    status="COMPLETED",
                    selection_phase="ALLOCATED",
                    decision_fact="RAISE",
                    allocation_fact=("HUMAN_SELECTED" if kind == "HUMAN" else "AI_SELECTED"),
                    execution_fact="HUMAN_SPOKE" if kind == "HUMAN" else "AI_SPOKE",
                    frozen_context={
                        "history": [{"speech_id": f"past-{sequence_no}", "text": "过去内容"}],
                        "marker": f"frozen-{sequence_no}",
                    },
                    completed_at=datetime.now(UTC),
                )
                session.add(opportunity)
                await session.flush()
                session.add(
                    Speech(
                        match_id=match.id,
                        opportunity_id=opportunity.id,
                        action_key=f"free-{sequence_no}",
                        user_id=user_id,
                        speaker_kind=kind,
                        agent_profile_id=agent_id,
                        side=side,
                        seat_no=seat_no,
                        status="FINALIZED",
                        display_text=f"第 {sequence_no} 条发言",
                        finalized_at=datetime.now(UTC),
                        ended_at=datetime.now(UTC),
                    )
                )
            await session.flush()
            completed_attempt_id = completed_attempt.id
            completed_match_id = match.id
            await create_postmatch_annotation_work(
                session,
                attempt_id=completed_attempt_id,
                scheduled_match=completed_schedule,
                match_id=completed_match_id,
            )
            await session.commit()

            participant_user_ids = [seat.user_id for seat in human_seats]
            assert all(user_id is not None for user_id in participant_user_ids)
            controller_ids = [
                min(
                    (seat for seat in human_seats if seat.side == side),
                    key=lambda seat: seat.seat_no,
                ).user_id
                for side in ("AFFIRMATIVE", "NEGATIVE")
            ]
            assert all(user_id is not None for user_id in controller_ids)
            for controller_id in controller_ids:
                assert controller_id is not None
                assert await _match_page_permissions(
                    session,
                    actor_user_id=controller_id,
                    actor_role="USER",
                    room=room,
                    member_role="DEBATER",
                ) == (True, True)
            ordinary_participant_id = next(
                user_id for user_id in participant_user_ids if user_id not in controller_ids
            )
            assert ordinary_participant_id is not None
            assert await _match_page_permissions(
                session,
                actor_user_id=ordinary_participant_id,
                actor_role="USER",
                room=room,
                member_role="DEBATER",
            ) == (False, True)
            assert await _match_page_permissions(
                session,
                actor_user_id=viewer_id,
                actor_role="ADMIN",
                room=room,
                member_role="SPECTATOR",
            ) == (True, True)
            assert await _match_page_permissions(
                session,
                actor_user_id=viewer_id,
                actor_role="USER",
                room=room,
                member_role="SPECTATOR",
            ) == (False, False)
            completed_room_id = room.id
            await session.rollback()

            room_service = RoomService()
            with pytest.raises(AuthError) as error:
                await room_service.terminate_room(
                    session,
                    actor_user_id=ordinary_participant_id,
                    actor_role="USER",
                    room_id=completed_room_id,
                )
            assert error.value.code == "forbidden"
            controller_id = controller_ids[1]
            assert controller_id is not None
            await room_service.terminate_room(
                session,
                actor_user_id=controller_id,
                actor_role="USER",
                room_id=completed_room_id,
            )

            for index, participant_user_id in enumerate(participant_user_ids):
                assert participant_user_id is not None
                tasks = await service.list_participant_tasks(session, user_id=participant_user_id)
                assert len(tasks) == 1
                task = tasks[0]
                human_item = next(item for item in task.items if item.subject_kind == "HUMAN_SELF")
                agent_item = next(item for item in task.items if item.subject_kind == "TEAM_AI")
                assert agent_item.revealed is False and agent_item.speech_text is None
                await session.rollback()

                if index == 0:
                    with pytest.raises(APIError) as error:
                        await service.save_participant_answers(
                            session,
                            task_id=task.id,
                            item_id=human_item.id,
                            user_id=viewer_id,
                            payload=ParticipantAnnotationAnswerSaveRequest(
                                stage=1,
                                answers={
                                    "primary_reason": "自己更适合处理这个问题。",
                                    "goals": ["回应对手"],
                                },
                                client_version=1,
                            ),
                        )
                    assert error.value.code == "experiment_annotation_not_found"

                await service.save_participant_answers(
                    session,
                    task_id=task.id,
                    item_id=human_item.id,
                    user_id=participant_user_id,
                    payload=ParticipantAnnotationAnswerSaveRequest(
                        stage=1,
                        answers={
                            "primary_reason": "自己更适合处理这个问题。",
                            "goals": ["回应对手"],
                        },
                        client_version=1,
                    ),
                )
                revealed = await service.save_participant_answers(
                    session,
                    task_id=task.id,
                    item_id=agent_item.id,
                    user_id=participant_user_id,
                    payload=ParticipantAnnotationAnswerSaveRequest(
                        stage=1,
                        answers={"appropriateness": "很适合 AI 发言"},
                        client_version=1,
                        lock_stage=True,
                    ),
                )
                revealed_agent = next(item for item in revealed.items if item.id == agent_item.id)
                assert revealed_agent.revealed is True and revealed_agent.speech_text is not None
                await service.save_participant_answers(
                    session,
                    task_id=task.id,
                    item_id=agent_item.id,
                    user_id=participant_user_id,
                    payload=ParticipantAnnotationAnswerSaveRequest(
                        stage=2,
                        answers={"team_need_match": "很好，处理了当时团队真正需要处理的问题"},
                        client_version=1,
                    ),
                )
                if index == 0:
                    with pytest.raises(APIError) as error:
                        await service.submit_participant_questionnaire(
                            session,
                            task_id=task.id,
                            user_id=participant_user_id,
                            payload=ParticipantQuestionnaireSubmitRequest(
                                q1=4, q2=4, q3=4, q4=4, q5=4, q6="测试"
                            ),
                        )
                    assert error.value.code == "experiment_annotation_answer_invalid"
                await service.submit_participant_questionnaire(
                    session,
                    task_id=task.id,
                    user_id=participant_user_id,
                    payload=ParticipantQuestionnaireSubmitRequest(
                        q1=4, q2=4, q3=4, q4=4, q5=4
                    ),
                )
                assert not await can_view_experiment_result(
                    session,
                    match_id=completed_match_id,
                    user_id=participant_user_id,
                    role="USER",
                )
                await session.rollback()
                submitted = await service.submit_participant_task(
                    session, task_id=task.id, user_id=participant_user_id
                )
                assert submitted.status == "SUBMITTED"
                assert await can_view_experiment_result(
                    session,
                    match_id=completed_match_id,
                    user_id=participant_user_id,
                    role="USER",
                )
                if index < 5:
                    assert not await can_view_experiment_result(
                        session,
                        match_id=completed_match_id,
                        user_id=viewer_id,
                        role="USER",
                    )

            assert await can_view_experiment_result(
                session,
                match_id=completed_match_id,
                user_id=viewer_id,
                role="USER",
            )
            stored_completed_attempt = await session.get(
                ExperimentMatchAttempt, completed_attempt_id
            )
            assert (
                stored_completed_attempt is not None
                and stored_completed_attempt.public_at is not None
            )
            leaderboard_task = await session.scalar(
                select(BackgroundTask).where(
                    BackgroundTask.task_type == "LEADERBOARD_DAILY",
                    BackgroundTask.payload["attempt_id"].astext == str(completed_attempt_id),
                )
            )
            assert leaderboard_task is not None
            participant_task = await session.scalar(
                select(ParticipantAnnotationTask).where(
                    ParticipantAnnotationTask.experiment_attempt_id == completed_attempt_id
                )
            )
            assert participant_task is not None
            participant_task_id = participant_task.id
            participant_task_user_id = participant_task.user_id
            await session.rollback()
            with pytest.raises(APIError) as error:
                await service.submit_participant_questionnaire(
                    session,
                    task_id=participant_task_id,
                    user_id=participant_task_user_id,
                    payload=ParticipantQuestionnaireSubmitRequest(q1=3, q2=3, q3=3, q4=3, q5=3),
                )
            assert error.value.code == "experiment_annotation_locked"

            expert_tasks = list((await session.scalars(select(ExpertAnnotationTask))).all())
            assert len(expert_tasks) == 3
            for expert_task in expert_tasks:
                expert_answers = list(
                    (
                        await session.scalars(
                            select(ExpertAnnotationAnswer).where(
                                ExpertAnnotationAnswer.task_id == expert_task.id
                            )
                        )
                    ).all()
                )
                assert len(expert_answers) == 8
                assert all(
                    set(answer.frozen_payload)
                    == {
                        "opportunity_id",
                        "sequence_no",
                        "side",
                        "trigger_kind",
                        "source_speech_id",
                        "context_version",
                        "context",
                    }
                    for answer in expert_answers
                )
            assert await service.list_expert_tasks(session, user_id=viewer_id) == []
            await session.rollback()

            disabled = await service.disable_batch(session, batch_id=batch_id)
            assert disabled.status == "DISABLED"
            assert await service.list_appointments(session, user_id=participants[0].user_id) == []
            stored_batch = await session.get(ExperimentBatch, batch_id)
            assert stored_batch is not None and stored_batch.status == "DISABLED"
    finally:
        await _truncate(engine)
        await engine.dispose()

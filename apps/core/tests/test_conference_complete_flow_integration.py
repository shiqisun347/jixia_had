"""Actual Room/Manager/PostgreSQL one-match flow; media and models are synthetic.

Run only on a new disposable owned local TEST_DATABASE_URL. This fixture does
not reuse a retained QA database or read settings/credentials from dotenv.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jx_core.auth.errors import APIError, AuthError
from jx_core.auth.session import AuthContext
from jx_core.legal.terms import get_current_human_participation_terms
from jx_core.matches.domain import MatchActor, MatchCommand, MatchEvent, MatchRuntimeState
from jx_core.matches.service import MatchRuntimeManager
from jx_core.models import (
    AgentGeneration,
    BackgroundTask,
    FreeDebateOpportunity,
    HostAudioAsset,
    Match,
    MatchParticipant,
    Room,
    RuleAudioGeneration,
    Speech,
    User,
)
from jx_core.models import (
    MatchEvent as StoredEvent,
)
from jx_core.postmatch_showcase_routes import (
    AudienceVoteRequest,
    JudgeEntryRequest,
    ShowcasePhaseRequest,
    get_showcase,
    save_judge_entry,
    submit_audience_vote,
    update_showcase_phase,
)
from jx_core.rooms.schemas import (
    DeviceCheckRequest,
    RoomCreateRequest,
    RoomJoinRequest,
    SeatSelectRequest,
)
from jx_core.rooms.service import RoomService
from jx_core.rules.formal_4v4 import build_formal_4v4_draft
from jx_core.rules.schemas import ModelProfileCreate, RuleCreate, VoiceProfileCreate
from jx_core.rules.service import CatalogService, RuleService
from jx_core.runtime_identity import CallbackEnvelope
from test_conference_complete_flow import _idle_timer, run_conference_round

pytestmark = pytest.mark.integration
# This is an explicit service boundary fixture, not production integration glue.
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
async def test_room_ready_through_same_match_finish_and_postmatch() -> None:
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("requires newly owned disposable PostgreSQL")
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or url == os.environ.get("DATABASE_URL"):
        pytest.fail("independent TEST_DATABASE_URL required")
    parsed = make_url(url)
    if (
        parsed.host not in {"127.0.0.1", "localhost"}
        or parsed.database != "jx_debate_qa_agent_faults"
    ):
        pytest.fail("new owned local QA database required")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    manager = MatchRuntimeManager(factory)
    rooms, rules, catalog = RoomService(), RuleService(), CatalogService()
    media = AsyncMock()
    host_identity: dict[UUID, tuple[str, CallbackEnvelope]] = {}
    speech_identity: dict[UUID, tuple[CallbackEnvelope, UUID]] = {}

    async def register_host(**values: Any) -> None:
        envelope = CallbackEnvelope(
            match_id=values["match_id"],
            speech_id=None,
            attempt_no=1,
            generation_id=uuid4(),
            connection_epoch=None,
            context_version=values["context_version"],
            opportunity_id=None,
            opportunity_generation=None,
        )
        host_identity[values["match_id"]] = (values["action_key"], envelope)

    media.start_host_playback.side_effect = register_host

    def host_callback_is_current(*, envelope: CallbackEnvelope, action_key: str) -> bool:
        return host_identity.get(envelope.match_id) == (action_key, envelope)

    media.host_callback_is_current = Mock(side_effect=host_callback_is_current)
    # Provider/mock does no background state changes. The real Manager publishes,
    # tracks all bounded tasks and commits each trusted callback to PostgreSQL.
    manager.set_agent_runtime(cast(Any, media))
    postmatch = AsyncMock()
    manager.set_postmatch_runtime(cast(Any, postmatch))
    holder: dict[str, Any] = {}
    tag = uuid4().hex[:10]
    owned_host_task_ids: tuple[UUID, ...] = ()
    owned_rule_id: UUID | None = None

    async def prepare(
        snapshot: dict[str, Any],
        humans: dict[tuple[str, int], UUID],
        agents: dict[tuple[str, int], UUID],
        now: list[float],
        events: list[MatchEvent],
    ) -> MatchActor:
        nonlocal owned_host_task_ids, owned_rule_id
        async with factory() as session:
            human_rows = [
                User(
                    id=user_id,
                    username=f"wf-{tag}-{index}",
                    username_normalized=f"wf-{tag}-{index}",
                    real_name=f"合成真人{index + 1}",
                    password_hash="unusable-synthetic-hash",
                )
                for index, user_id in enumerate(humans.values())
            ]
            spectator = User(
                username=f"wf-{tag}-spectator",
                username_normalized=f"wf-{tag}-spectator",
                real_name="合成观众",
                password_hash="unusable-synthetic-hash",
            )
            session.add_all([*human_rows, spectator])
            await session.commit()
            owner = human_rows[0].id
            host = await catalog.create_voice(
                session,
                payload=VoiceProfileCreate(
                    name=f"wf-{tag}-host", kind="HOST", provider_voice="synthetic-host"
                ),
            )
            for index in range(8):
                await catalog.create_voice(
                    session,
                    payload=VoiceProfileCreate(
                        name=f"wf-{tag}-agent-{index}",
                        kind="AGENT",
                        provider_voice=f"synthetic-agent-{index}",
                        avatar_key=f"agent-{index + 1:02d}",
                    ),
                )
            model = await catalog.create_model(
                session,
                payload=ModelProfileCreate(name=f"wf-{tag}-model", config_ref="synthetic-only"),
            )
            rule = await rules.create_rule(
                session,
                creator_user_id=owner,
                payload=RuleCreate.model_validate(
                    {
                        "host_voice_profile_id": host.id,
                        "default_agent_model_profile_id": model.id,
                        "draft": {**build_formal_4v4_draft(), "name": f"完整流程-{tag}"},
                    }
                ),
            )
            owned_rule_id = rule.id
            assets = list(
                (
                    await session.scalars(
                        select(HostAudioAsset).where(HostAudioAsset.rule_id == rule.id)
                    )
                ).all()
            )
            owned_host_task_ids = tuple(
                asset.active_task_id for asset in assets if asset.active_task_id is not None
            )
            assert len(assets) == 10
            for asset in assets:
                asset.status = "READY"
                asset.storage_path = f"synthetic/workflow-{tag}/{asset.segment_key}.ogg"
                asset.duration_ms = 1_000
                asset.byte_count = 1
            generation = await session.get(RuleAudioGeneration, rule.current_audio_generation_id)
            assert generation is not None
            generation.status = "SUCCEEDED"
            generation.ready_count = len(assets)
            await session.commit()
            # Only synthesis completion/asset contents are fixture data. Review,
            # enable and Room snapshot freezing use their actual public services.
            await rules.review_audio(session, rule_id=rule.id)
            await rules.enable_rule(session, rule_id=rule.id)
            terms = get_current_human_participation_terms().version
            room = await rooms.create_room(
                session,
                organizer_user_id=owner,
                organizer_role="USER",
                payload=RoomCreateRequest(
                    title=f"完整流程-{tag}",
                    label="合成QA",
                    rule_id=rule.id,
                    custom_topic_title="合成完整流程辩题",
                    affirmative_text="正方",
                    negative_text="反方",
                    human_participation_terms_version=terms,
                ),
            )
            assert room.status == "WAITING"
            for index, ((side, seat_no), user_id) in enumerate(humans.items()):
                if index:
                    await rooms.join_room(
                        session,
                        user_id=user_id,
                        room_id=room.id,
                        payload=RoomJoinRequest(
                            member_role="DEBATER", human_participation_terms_version=terms
                        ),
                    )
                await rooms.select_seat(
                    session,
                    user_id=user_id,
                    room_id=room.id,
                    payload=SeatSelectRequest(
                        side=cast(Literal["AFFIRMATIVE", "NEGATIVE"], side),
                        seat_no=seat_no,
                        human_participation_terms_version=terms,
                    ),
                )
                check = await rooms.save_device_check(
                    session,
                    user_id=user_id,
                    room_id=room.id,
                    payload=DeviceCheckRequest(status="PASS", details={"synthetic": True}),
                )
                await rooms.ready(
                    session, user_id=user_id, room_id=room.id, check_version=check.check_version
                )
            await rooms.join_room(
                session,
                user_id=spectator.id,
                room_id=room.id,
                payload=RoomJoinRequest(member_role="SPECTATOR"),
            )
            await rooms.start_room(session, room_id=room.id, actor_user_id=owner, actor_role="USER")
            pending = await manager.start_room_match(
                session,
                room_id=room.id,
                actor_user_id=owner,
                actor_role="USER",
                launch_runtime=False,
            )
            assert pending.status == "START_PENDING_RUNTIME"
            assert media.mock_calls == []
            _, _, seats = await rooms.snapshot(session, room_id=room.id)
            agents.clear()
            agents.update(
                {
                    (seat.side, seat.seat_no): seat.agent_profile_id
                    for seat in seats
                    if seat.occupant_type == "AGENT" and seat.agent_profile_id is not None
                }
            )
            assert len(agents) == 4
            assert all(
                sum(side == key[0] for key in agents) == 2 for side in ("AFFIRMATIVE", "NEGATIVE")
            )
            actor = await manager.get_actor(pending.match_id)
            # Only timer delivery/clock are synthetic; _pre_commit/_commit remain
            # production manager hooks with actual SQL transactions.
            actor._clock = lambda: now[0]
            actor._wall_clock = lambda: now[0] + 1_800_000_000
            actor._sleep = _idle_timer
            original_publish = actor._publish
            assert original_publish is not None

            async def publish(state: MatchRuntimeState, batch: tuple[MatchEvent, ...]) -> None:
                await original_publish(state, batch)
                events.extend(batch)

            actor._publish = publish
            original_submit = actor.submit

            async def submit(command: MatchCommand) -> Any:
                if command.type == "asr.finalized" and command.message_id.startswith("conference:"):
                    speech_id = UUID(str(command.payload["speech_id"]))
                    envelope, task_id = speech_identity[speech_id]
                    duration = int(command.payload["audio_duration_ms"])
                    await manager.persist_asr_segment(
                        envelope=envelope,
                        speech_id=speech_id,
                        segment_no=1,
                        task_id=task_id,
                        final_text=str(command.payload["final_text"]),
                        first_interim_latency_ms=10,
                        final_latency_ms=20,
                        pcm_sample_count=duration * 16,
                    )
                    return await manager.finalize_asr_speech(
                        envelope=envelope,
                        final_text=str(command.payload["final_text"]),
                        first_interim_latency_ms=10,
                        final_latency_ms=20,
                        audio_duration_ms=duration,
                        audio_storage_path=None,
                        audio_recording_error="synthetic_media_not_recorded",
                    )
                if command.type == "host.finished":
                    action = actor.state.current_action
                    assert action is not None
                    identity: tuple[str, CallbackEnvelope] | None = None
                    for _ in range(100):
                        identity = host_identity.get(pending.match_id)
                        if identity is not None and identity[0] == action.action_key:
                            break
                        await asyncio.sleep(0.001)
                    assert identity is not None and identity[0] == action.action_key
                    envelope = identity[1]
                    command = replace(
                        command,
                        payload={
                            **command.payload,
                            "action_key": action.action_key,
                            "generation_id": str(envelope.generation_id),
                        },
                        host_callback_envelope=envelope,
                    )
                if command.type == "agent.playback_started":
                    async with factory() as write:
                        match = await write.get(Match, pending.match_id)
                        assert match is not None
                        action = actor.state.current_action
                        assert action is not None
                        write.add(
                            AgentGeneration(
                                id=UUID(str(command.payload["generation_id"])),
                                match_id=pending.match_id,
                                agent_profile_id=UUID(str(command.payload["agent_profile_id"])),
                                action_key=action.action_key,
                                context_version=match.context_version,
                                status="LLM_READY",
                                input_snapshot={"synthetic": True},
                            )
                        )
                        await write.commit()
                result = await original_submit(command)
                if command.type == "speech.start":
                    speech_id = result.state.current_speech_id
                    assert speech_id is not None
                    async with factory() as read:
                        speech = await read.get(Speech, speech_id)
                        match = await read.get(Match, pending.match_id)
                        assert speech is not None and match is not None
                        opportunity = (
                            await read.get(FreeDebateOpportunity, speech.opportunity_id)
                            if speech.opportunity_id
                            else None
                        )
                        envelope = CallbackEnvelope(
                            match_id=pending.match_id,
                            speech_id=speech_id,
                            attempt_no=speech.attempt_no,
                            generation_id=None,
                            connection_epoch=1,
                            context_version=match.context_version,
                            opportunity_id=speech.opportunity_id,
                            opportunity_generation=opportunity.opportunity_generation
                            if opportunity
                            else None,
                        )
                    task_id = uuid4()
                    speech_identity[speech_id] = (envelope, task_id)
                    await manager.start_asr_segment(
                        envelope=envelope, segment_no=1, task_id=task_id
                    )
                return result

            actor.submit = submit
            holder.update(
                owner=owner, spectator=spectator.id, room_id=room.id, match_id=pending.match_id
            )
            return actor

    async def start_runtime() -> MatchRuntimeState:
        async with factory() as session:
            with pytest.raises(AuthError, match="forbidden"):
                await manager.start_existing_match_runtime(
                    session,
                    room_id=holder["room_id"],
                    actor_user_id=holder["spectator"],
                    actor_role="USER",
                )
            await session.rollback()
            return await manager.start_existing_match_runtime(
                session,
                room_id=holder["room_id"],
                actor_user_id=holder["owner"],
                actor_role="USER",
            )

    try:
        result = await run_conference_round(prepare=prepare, start_runtime=start_runtime)
        assert result["status"] == "FINISHED"
        for _ in range(100):
            if postmatch.request_judge.await_count == 1:
                break
            await asyncio.sleep(0.01)
        assert postmatch.request_judge.await_count == 1
        async with factory() as session:
            match = await session.get(Match, holder["match_id"])
            room = await session.get(Room, holder["room_id"])
            assert match is not None and match.status == "FINISHED" and match.ended_at is not None
            assert room is not None and room.status == "FINISHED" and room.ended_at is not None
            speeches = list(
                (await session.scalars(select(Speech).where(Speech.match_id == match.id))).all()
            )
            assert len(speeches) == 20 and all(
                s.status == "FINALIZED" and s.display_text for s in speeches
            )
            stored = list(
                (
                    await session.scalars(
                        select(StoredEvent)
                        .where(StoredEvent.match_id == match.id)
                        .order_by(StoredEvent.sequence)
                    )
                ).all()
            )
            assert [event.sequence for event in stored] == list(range(1, match.sequence + 1))
            assert sum(event.event_type == "match.finished" for event in stored) == 1
            participants = list(
                (
                    await session.scalars(
                        select(MatchParticipant)
                        .where(MatchParticipant.match_id == match.id)
                        .order_by(MatchParticipant.side, MatchParticipant.seat_no)
                    )
                ).all()
            )
            assert len(participants) == 8
            owner = AuthContext(holder["owner"], "USER", uuid4(), False)
            spectator = AuthContext(holder["spectator"], "USER", uuid4(), False)
            match_id = match.id
            initial = await get_showcase(match_id, owner, session)
            assert initial.can_manage and len(initial.participants) == 8
            await save_judge_entry(
                match_id,
                JudgeEntryRequest(
                    comments="合成评委点评",
                    winner_side="AFFIRMATIVE",
                    best_participant_id=participants[0].id,
                    published=True,
                ),
                owner,
                session,
            )
            await update_showcase_phase(
                match_id, ShowcasePhaseRequest(phase="AUDIENCE_VOTE"), owner, session
            )
            vote = AudienceVoteRequest(
                winner_side="AFFIRMATIVE", ranking=[p.id for p in participants]
            )
            await submit_audience_vote(match_id, vote, spectator, session)
            with pytest.raises(APIError, match="audience_vote_duplicate"):
                await submit_audience_vote(match_id, vote, spectator, session)
            await session.rollback()
            awards = await update_showcase_phase(
                match_id, ShowcasePhaseRequest(phase="AWARDS"), owner, session
            )
            assert awards.audience["total_votes"] == 1 and len(awards.audience["ranking"]) == 8
            assert awards.judge is not None and awards.judge["winner_side"] == "AFFIRMATIVE"
            replay_count = await session.scalar(
                select(func.count())
                .select_from(BackgroundTask)
                .where(
                    BackgroundTask.task_type == "POSTMATCH_AUDIO",
                    func.jsonb_extract_path_text(BackgroundTask.payload, "match_id")
                    == str(match_id),
                )
            )
            assert replay_count == 1
    finally:
        try:
            await manager.close()
        finally:
            try:
                # Synthetic media setup does not run HOST_TTS. Remove only this
                # fixture's captured tasks so later Jobs tests claim their own.
                if owned_rule_id is not None and owned_host_task_ids:
                    async with factory() as cleanup:
                        async with cleanup.begin():
                            await cleanup.execute(
                                delete(BackgroundTask).where(
                                    BackgroundTask.id.in_(owned_host_task_ids),
                                    BackgroundTask.task_type == "HOST_TTS",
                                    func.jsonb_extract_path_text(BackgroundTask.payload, "rule_id")
                                    == str(owned_rule_id),
                                )
                            )
            finally:
                await engine.dispose()

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jx_core.auth.errors import APIError
from jx_core.matches.domain import (
    DebateParticipant,
    MatchAction,
    MatchActor,
    MatchCommand,
    MatchEvent,
    MatchRuntimeState,
)
from jx_core.matches.service import MatchRuntimeManager
from jx_core.models import (
    AgentFreeDebateDecision,
    AgentProfile,
    AuditLog,
    FreeDebateOpportunity,
    HumanHandEvent,
    Match,
    ModelProfile,
    PersonalAiSurveyResponse,
    PostmatchSurveyTask,
    Room,
    Rule,
    SpeakerAllocation,
    Speech,
    User,
    VoiceProfile,
)
from jx_core.runtime_identity import CallbackEnvelope
from jx_core.survey_service import save_personal_response, save_postmatch_task

pytestmark = pytest.mark.integration


def _database_url() -> str:
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("set RUN_DATABASE_INTEGRATION=1 to run real PostgreSQL tests")
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.fail("TEST_DATABASE_URL is required for formal 4v4 integration tests")
    if value == os.environ.get("DATABASE_URL"):
        pytest.fail("TEST_DATABASE_URL must not equal DATABASE_URL")
    return value


@pytest.mark.asyncio
async def test_formal_opportunity_persists_without_experiment_attempt() -> None:
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex[:12]

    try:
        async with factory() as session:
            transaction = await session.begin()
            human = User(
                username=f"formal-{suffix}",
                username_normalized=f"formal-{suffix}",
                real_name="正式赛测试用户",
                password_hash="not-used",
            )
            session.add(human)
            await session.flush()

            model = ModelProfile(name=f"formal-model-{suffix}", config_ref="test")
            voice = VoiceProfile(
                name=f"formal-voice-{suffix}",
                kind="AGENT",
                provider_voice=f"formal-provider-{suffix}",
                avatar_key="agent-01",
            )
            session.add_all([model, voice])
            await session.flush()
            agent = AgentProfile(
                name=f"formal-agent-{suffix}",
                model_profile_id=model.id,
                voice_profile_id=voice.id,
            )
            rule = Rule(
                rule_key=f"formal-rule-{suffix}",
                version=1,
                name="正式 4v4 测试赛制",
                side_size=4,
                estimated_seconds=900,
                status="ENABLED",
                created_by=human.id,
            )
            session.add_all([agent, rule])
            await session.flush()

            room = Room(
                code=suffix[:6].upper(),
                title="正式 4v4 持久化测试",
                label="测试",
                topic_snapshot={},
                rule_id=rule.id,
                rule_snapshot={},
                organizer_user_id=human.id,
            )
            session.add(room)
            await session.flush()
            match = Match(room_id=room.id, status="RUNNING", runtime_snapshot={})
            session.add(match)
            await session.flush()

            opportunity = FreeDebateOpportunity(
                match_id=match.id,
                experiment_attempt_id=None,
                sequence_no=1,
                side="AFFIRMATIVE",
                trigger_kind="AGENT_SPEECH",
                context_version=3,
                opportunity_generation=1,
            )
            session.add(opportunity)
            await session.flush()
            hand = HumanHandEvent(
                opportunity_id=opportunity.id,
                user_id=human.id,
                event_type="RAISE",
                server_sequence=1,
                connection_epoch=2,
            )
            decision = AgentFreeDebateDecision(
                match_id=match.id,
                action_key="free-debate",
                decision_round_id=uuid4(),
                context_version=3,
                agent_profile_id=agent.id,
                side="AFFIRMATIVE",
                seat_no=2,
                status="HAND",
                should_speak=True,
                opportunity_id=opportunity.id,
                effective_status="RAISE",
                trigger_kind="AGENT_SPEECH",
            )
            allocation = SpeakerAllocation(
                opportunity_id=opportunity.id,
                speaker_kind="HUMAN",
                user_id=human.id,
                reason="HUMAN_PRIORITY",
            )
            session.add_all([hand, decision, allocation])
            await session.flush()

            stored = await session.scalar(
                select(FreeDebateOpportunity).where(FreeDebateOpportunity.id == opportunity.id)
            )
            assert stored is not None and stored.experiment_attempt_id is None
            assert hand.opportunity_id == stored.id
            assert decision.opportunity_id == stored.id
            assert allocation.opportunity_id == stored.id
            await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_agent_decision_commit_flushes_new_opportunity_before_child() -> None:
    """The production event commit must satisfy the opportunity FK ordering."""
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex[:12]

    try:
        async with factory() as session:
            transaction = await session.begin()
            human = User(
                username=f"flush-{suffix}",
                username_normalized=f"flush-{suffix}",
                real_name="机会顺序测试用户",
                password_hash="not-used",
            )
            session.add(human)
            await session.flush()
            model = ModelProfile(name=f"flush-model-{suffix}", config_ref="test")
            voice = VoiceProfile(
                name=f"flush-voice-{suffix}",
                kind="AGENT",
                provider_voice=f"flush-provider-{suffix}",
                avatar_key="agent-01",
            )
            session.add_all([model, voice])
            await session.flush()
            agent = AgentProfile(
                name=f"flush-agent-{suffix}",
                model_profile_id=model.id,
                voice_profile_id=voice.id,
            )
            rule = Rule(
                rule_key=f"flush-rule-{suffix}",
                version=1,
                name="机会顺序测试赛制",
                side_size=4,
                estimated_seconds=900,
                status="ENABLED",
                created_by=human.id,
            )
            session.add_all([agent, rule])
            await session.flush()
            room = Room(
                code=suffix[:6].upper(),
                title="机会顺序测试房间",
                label="测试",
                topic_snapshot={},
                rule_id=rule.id,
                rule_snapshot={},
                organizer_user_id=human.id,
            )
            session.add(room)
            await session.flush()
            match = Match(room_id=room.id, status="RUNNING", runtime_snapshot={})
            session.add(match)
            await session.flush()
            source_speech_id = uuid4()
            session.add(
                Speech(
                    id=source_speech_id,
                    match_id=match.id,
                    action_key="2:1",
                    side="NEGATIVE",
                    seat_no=1,
                    speaker_kind="AGENT",
                    agent_profile_id=agent.id,
                    status="FINALIZED",
                    attempt_no=1,
                )
            )
            await session.flush()
            await transaction.commit()

        opportunity_id = uuid4()
        round_id = uuid4()
        action = MatchAction(
            stage_position=3,
            action_position=1,
            action_kind="FREE_DEBATE",
            duration_seconds=360,
            participants=(
                # A formal 4v4 action can contain multiple Agent participants;
                # this test only needs one child row to exercise the FK.
                DebateParticipant(side="NEGATIVE", seat_no=1, agent_profile_id=agent.id),
            ),
        )
        previous = MatchRuntimeState(
            match_id=match.id,
            status="RUNNING",
            action_state="AGENT_FINALIZING",
            actions=(action,),
            sequence=0,
            current_action_index=0,
            formal_4v4=True,
        )
        candidate = MatchRuntimeState(
            match_id=match.id,
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(action,),
            sequence=1,
            current_action_index=0,
            formal_4v4=True,
            opportunity_id=opportunity_id,
            opportunity_generation=1,
            selection_phase="COMPETING",
            selection_deadline_mono=3.0,
            agent_decision_round_id=round_id,
        )
        event = MatchEvent(
            type="agent.decision_started",
            match_id=match.id,
            sequence=1,
            server_time_ms=1_000,
            payload={
                "decision_round_id": str(round_id),
                "opportunity_id": str(opportunity_id),
                "opportunity_generation": 1,
                "side": "NEGATIVE",
                "trigger_kind": "AGENT_SPEECH",
                "source_speech_id": str(source_speech_id),
                "action_key": action.action_key,
                "agents": [{"agent_profile_id": str(agent.id), "seat_no": 1}],
            },
        )

        manager = MatchRuntimeManager(factory)
        await manager._commit(  # pyright: ignore[reportPrivateUsage]
            previous,
            candidate,
            (event,),
            MatchCommand(type="free.decision", message_id="flush-order"),
        )

        async with factory() as session:
            opportunity = await session.get(FreeDebateOpportunity, opportunity_id)
            decision = await session.scalar(
                select(AgentFreeDebateDecision).where(
                    AgentFreeDebateDecision.decision_round_id == round_id
                )
            )
            assert opportunity is not None
            assert decision is not None
            assert decision.opportunity_id == opportunity.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_agent_playback_flushes_speech_before_next_opportunity() -> None:
    """Playback must insert its speech before the next opportunity references it."""
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex[:12]

    try:
        async with factory() as session:
            human = User(
                username=f"playback-{suffix}",
                username_normalized=f"playback-{suffix}",
                real_name="播放顺序测试用户",
                password_hash="not-used",
            )
            session.add(human)
            await session.flush()
            model = ModelProfile(name=f"playback-model-{suffix}", config_ref="test")
            voice = VoiceProfile(
                name=f"playback-voice-{suffix}",
                kind="AGENT",
                provider_voice=f"playback-provider-{suffix}",
                avatar_key="agent-01",
            )
            session.add_all([model, voice])
            await session.flush()
            agent = AgentProfile(
                name=f"playback-agent-{suffix}",
                model_profile_id=model.id,
                voice_profile_id=voice.id,
            )
            rule = Rule(
                rule_key=f"playback-rule-{suffix}",
                version=1,
                name="播放顺序测试赛制",
                side_size=4,
                estimated_seconds=900,
                status="ENABLED",
                created_by=human.id,
            )
            session.add_all([agent, rule])
            await session.flush()
            room = Room(
                code=suffix[:6].upper(),
                title="播放顺序测试房间",
                label="测试",
                topic_snapshot={},
                rule_id=rule.id,
                rule_snapshot={},
                organizer_user_id=human.id,
            )
            session.add(room)
            await session.flush()
            match = Match(room_id=room.id, status="RUNNING", runtime_snapshot={})
            session.add(match)
            await session.flush()
            allocated_opportunity = FreeDebateOpportunity(
                match_id=match.id,
                sequence_no=1,
                side="AFFIRMATIVE",
                trigger_kind="INITIAL_HOST",
                context_version=1,
                opportunity_generation=1,
                selection_phase="ALLOCATED",
                decision_fact="RAISE",
                allocation_fact="AI_SELECTED",
            )
            session.add(allocated_opportunity)
            await session.commit()

        speech_id = uuid4()
        generation_id = uuid4()
        free_action = MatchAction(
            stage_position=3,
            action_position=1,
            action_kind="FREE_DEBATE",
            duration_seconds=360,
            participants=(
                DebateParticipant(side="AFFIRMATIVE", seat_no=2, agent_profile_id=agent.id),
                DebateParticipant(side="NEGATIVE", seat_no=4, user_id=human.id),
            ),
        )
        state = MatchRuntimeState(
            match_id=match.id,
            status="RUNNING",
            action_state="AGENT_PREPARING",
            actions=(free_action,),
            current_agent_profile_id=agent.id,
            current_speaker_side="AFFIRMATIVE",
            current_speaker_seat_no=2,
            speech_remaining_ms=30_000,
            free_holder_side="AFFIRMATIVE",
            free_affirmative_remaining_ms=360_000,
            free_negative_remaining_ms=360_000,
            formal_4v4=True,
            opportunity_id=allocated_opportunity.id,
            opportunity_generation=1,
            selection_phase="ALLOCATED",
        )
        manager = MatchRuntimeManager(factory)
        actor = MatchActor(
            state,
            commit=manager._commit,  # pyright: ignore[reportPrivateUsage]
        )
        await actor.start()
        try:
            result = await actor.submit(
                MatchCommand(
                    type="agent.playback_started",
                    message_id="playback-order",
                    payload={
                        "speech_id": str(speech_id),
                        "generation_id": str(generation_id),
                        "agent_profile_id": str(agent.id),
                    },
                )
            )
        finally:
            await actor.close()

        next_opportunity_id = result.state.opportunity_id
        assert next_opportunity_id is not None
        assert next_opportunity_id != allocated_opportunity.id
        async with factory() as session:
            speech = await session.get(Speech, speech_id)
            next_opportunity = await session.get(
                FreeDebateOpportunity, next_opportunity_id
            )
            assert speech is not None
            assert speech.opportunity_id == allocated_opportunity.id
            assert next_opportunity is not None
            assert next_opportunity.source_speech_id == speech.id

        human_state = MatchRuntimeState(
            match_id=match.id,
            status="RUNNING",
            action_state="HUMAN_READY_TO_START",
            actions=(free_action,),
            sequence=result.state.sequence,
            current_speaker_user_id=human.id,
            current_speaker_side="NEGATIVE",
            current_speaker_seat_no=4,
            speech_remaining_ms=30_000,
            free_holder_side="NEGATIVE",
            free_affirmative_remaining_ms=330_000,
            free_negative_remaining_ms=360_000,
            formal_4v4=True,
            opportunity_id=next_opportunity_id,
            opportunity_generation=2,
            selection_phase="ALLOCATED",
        )
        human_actor = MatchActor(
            human_state,
            commit=manager._commit,  # pyright: ignore[reportPrivateUsage]
        )
        await human_actor.start()
        try:
            human_result = await human_actor.submit(
                MatchCommand(
                    type="speech.start",
                    message_id="human-speech-order",
                    actor_user_id=human.id,
                )
            )
        finally:
            await human_actor.close()

        human_speech_id = human_result.state.current_speech_id
        following_opportunity_id = human_result.state.opportunity_id
        assert human_speech_id is not None
        assert following_opportunity_id is not None
        assert following_opportunity_id != next_opportunity_id
        async with factory() as session:
            human_speech = await session.get(Speech, human_speech_id)
            following_opportunity = await session.get(
                FreeDebateOpportunity, following_opportunity_id
            )
            assert human_speech is not None
            assert human_speech.opportunity_id == next_opportunity_id
            assert following_opportunity is not None
            assert following_opportunity.source_speech_id == human_speech.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_human_asr_retries_are_scoped_to_the_allocated_opportunity() -> None:
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex[:12]

    try:
        async with factory() as session:
            human = User(
                username=f"asr-retry-{suffix}",
                username_normalized=f"asr-retry-{suffix}",
                real_name="ASR 重试测试用户",
                password_hash="not-used",
            )
            session.add(human)
            await session.flush()
            rule = Rule(
                rule_key=f"asr-retry-rule-{suffix}",
                version=1,
                name="ASR 重试测试赛制",
                side_size=4,
                estimated_seconds=900,
                status="ENABLED",
                created_by=human.id,
            )
            session.add(rule)
            await session.flush()
            room = Room(
                code=suffix[:6].upper(),
                title="ASR 重试测试房间",
                label="测试",
                topic_snapshot={},
                rule_id=rule.id,
                rule_snapshot={},
                organizer_user_id=human.id,
            )
            session.add(room)
            await session.flush()
            match = Match(room_id=room.id, status="RUNNING", runtime_snapshot={})
            session.add(match)
            await session.flush()
            old_opportunity = FreeDebateOpportunity(
                match_id=match.id,
                sequence_no=1,
                side="NEGATIVE",
                trigger_kind="HUMAN_SPEECH",
                context_version=0,
                opportunity_generation=1,
                status="COMPLETED",
                selection_phase="ALLOCATED",
                allocation_fact="HUMAN_SELECTED",
                execution_fact="HUMAN_SPOKE",
            )
            allocated = FreeDebateOpportunity(
                match_id=match.id,
                sequence_no=2,
                side="AFFIRMATIVE",
                trigger_kind="HUMAN_SPEECH",
                context_version=0,
                opportunity_generation=2,
                selection_phase="ALLOCATED",
                allocation_fact="HUMAN_SELECTED",
            )
            session.add_all([old_opportunity, allocated])
            await session.flush()
            session.add(
                Speech(
                    match_id=match.id,
                    opportunity_id=old_opportunity.id,
                    action_key="3:0",
                    user_id=human.id,
                    speaker_kind="HUMAN",
                    side="NEGATIVE",
                    seat_no=1,
                    status="FAILED",
                    attempt_no=1,
                    asr_error_code="asr_task_failed",
                )
            )
            current_speech = Speech(
                match_id=match.id,
                opportunity_id=allocated.id,
                action_key="3:0",
                user_id=human.id,
                speaker_kind="HUMAN",
                side="AFFIRMATIVE",
                seat_no=1,
                status="STARTED",
                attempt_no=1,
            )
            session.add(current_speech)
            await session.flush()
            speculative = FreeDebateOpportunity(
                match_id=match.id,
                sequence_no=3,
                side="NEGATIVE",
                trigger_kind="HUMAN_SPEECH",
                source_speech_id=current_speech.id,
                context_version=0,
                opportunity_generation=3,
            )
            session.add(speculative)
            await session.commit()

        action = MatchAction(
            stage_position=3,
            action_position=0,
            action_kind="FREE_DEBATE",
            duration_seconds=360,
            participants=(
                DebateParticipant(side="AFFIRMATIVE", seat_no=1, user_id=human.id),
            ),
            free_max_speech_seconds=30,
        )
        state = MatchRuntimeState(
            match_id=match.id,
            status="RUNNING",
            action_state="HUMAN_SPEAKING",
            actions=(action,),
            current_speech_id=current_speech.id,
            current_speaker_user_id=human.id,
            current_speaker_side="AFFIRMATIVE",
            current_speaker_seat_no=1,
            speech_remaining_ms=30_000,
            free_holder_side="NEGATIVE",
            free_affirmative_remaining_ms=360_000,
            free_negative_remaining_ms=360_000,
            formal_4v4=True,
            opportunity_id=speculative.id,
            allocated_opportunity_id=allocated.id,
            opportunity_generation=3,
            selection_phase="COMPETING",
        )
        manager = MatchRuntimeManager(factory)
        actor = MatchActor(
            state,
            commit=manager._commit,  # pyright: ignore[reportPrivateUsage]
            publish=manager._publish,  # pyright: ignore[reportPrivateUsage]
        )
        manager._actors[match.id] = actor  # pyright: ignore[reportPrivateUsage]
        await actor.start()
        try:
            stale_envelopes = (
                CallbackEnvelope(
                    match_id=match.id,
                    speech_id=uuid4(),
                    attempt_no=1,
                    generation_id=None,
                    connection_epoch=1,
                    context_version=0,
                    opportunity_id=allocated.id,
                    opportunity_generation=2,
                ),
                CallbackEnvelope(
                    match_id=match.id,
                    speech_id=current_speech.id,
                    attempt_no=2,
                    generation_id=None,
                    connection_epoch=1,
                    context_version=0,
                    opportunity_id=allocated.id,
                    opportunity_generation=2,
                ),
                CallbackEnvelope(
                    match_id=match.id,
                    speech_id=current_speech.id,
                    attempt_no=1,
                    generation_id=None,
                    connection_epoch=2,
                    context_version=0,
                    opportunity_id=allocated.id,
                    opportunity_generation=2,
                ),
                CallbackEnvelope(
                    match_id=match.id,
                    speech_id=current_speech.id,
                    attempt_no=1,
                    generation_id=None,
                    connection_epoch=1,
                    context_version=1,
                    opportunity_id=allocated.id,
                    opportunity_generation=2,
                ),
                CallbackEnvelope(
                    match_id=match.id,
                    speech_id=current_speech.id,
                    attempt_no=1,
                    generation_id=None,
                    connection_epoch=1,
                    context_version=0,
                    opportunity_id=old_opportunity.id,
                    opportunity_generation=2,
                ),
                CallbackEnvelope(
                    match_id=match.id,
                    speech_id=current_speech.id,
                    attempt_no=1,
                    generation_id=None,
                    connection_epoch=1,
                    context_version=0,
                    opportunity_id=allocated.id,
                    opportunity_generation=1,
                ),
            )
            for stale_envelope in stale_envelopes:
                await manager.handle_asr_failure(
                    envelope=stale_envelope,
                    code="asr_task_failed",
                )
                assert actor.state.action_state == "HUMAN_SPEAKING"
                assert actor.state.current_speech_id == current_speech.id

            await manager.handle_asr_failure(
                envelope=CallbackEnvelope(
                    match_id=match.id,
                    speech_id=current_speech.id,
                    attempt_no=1,
                    generation_id=None,
                    connection_epoch=1,
                    context_version=0,
                    opportunity_id=allocated.id,
                    opportunity_generation=2,
                ),
                code="asr_task_failed",
            )
            assert actor.state.status == "RUNNING"
            assert actor.state.action_state == "HUMAN_READY_TO_START"
            assert actor.state.current_speech_id is None
            assert actor.state.speech_remaining_ms == 30_000
            await manager._persist_late_experiment_asr(  # pyright: ignore[reportPrivateUsage]
                match_id=match.id,
                speech_id=current_speech.id,
                final_text="迟到文本不得覆盖失败状态",
                first_interim_latency_ms=10,
                final_latency_ms=20,
                audio_duration_ms=30,
                audio_storage_path=None,
                audio_recording_error=None,
            )

            retry = await actor.submit(
                MatchCommand(
                    type="speech.start",
                    message_id="asr-retry-start",
                    actor_user_id=human.id,
                )
            )
            retry_speech_id = retry.state.current_speech_id
            assert retry_speech_id is not None and retry_speech_id != current_speech.id
            retry_speculative_id = retry.state.opportunity_id
            assert retry_speculative_id is not None

            await manager.handle_asr_failure(
                envelope=CallbackEnvelope(
                    match_id=match.id,
                    speech_id=retry_speech_id,
                    attempt_no=2,
                    generation_id=None,
                    connection_epoch=1,
                    context_version=0,
                    opportunity_id=allocated.id,
                    opportunity_generation=2,
                ),
                code="asr_task_failed",
            )
            assert actor.state.status == "ERROR"
            assert actor.state.current_speech_id is None
            assert actor.state.speech_remaining_ms == 30_000

            await actor.submit(
                MatchCommand(
                    type="match.resume",
                    message_id="asr-retry-resume",
                    payload={"privileged": True, "reasons": []},
                )
            )
            await actor.submit(MatchCommand(type="resume.elapsed", message_id="asr-resumed"))
            recovered = await actor.submit(
                MatchCommand(
                    type="speech.start",
                    message_id="asr-recovered-start",
                    actor_user_id=human.id,
                )
            )
            recovered_speech_id = recovered.state.current_speech_id
            assert recovered_speech_id not in {None, current_speech.id, retry_speech_id}
            assert recovered.events[0].payload["duration_ms"] == 30_000
        finally:
            manager._actors.pop(match.id, None)  # pyright: ignore[reportPrivateUsage]
            await actor.close()

        async with factory() as session:
            attempts = list(
                (
                    await session.scalars(
                        select(Speech)
                        .where(Speech.opportunity_id == allocated.id)
                        .order_by(Speech.attempt_no)
                    )
                ).all()
            )
            assert [item.attempt_no for item in attempts] == [1, 2, 3]
            assert [item.status for item in attempts] == ["FAILED", "FAILED", "STARTED"]
            assert attempts[0].asr_error_code == "asr_task_failed"
            assert attempts[0].display_text is None
            assert attempts[1].asr_error_code == "asr_task_failed"
            assert attempts[2].asr_error_code is None
            invalidated_first = await session.get(FreeDebateOpportunity, speculative.id)
            invalidated_retry = await session.get(
                FreeDebateOpportunity, retry_speculative_id
            )
            assert invalidated_first is not None
            assert invalidated_first.status == "INVALIDATED"
            assert invalidated_retry is not None
            assert invalidated_retry.status == "INVALIDATED"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_participant_survey_writes_are_audited_atomically() -> None:
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex[:12]

    try:
        async with factory() as session:
            user = User(
                username=f"survey-audit-{suffix}",
                username_normalized=f"survey-audit-{suffix}",
                real_name="问卷审计测试用户",
                password_hash="not-used",
            )
            session.add(user)
            await session.flush()
            rule = Rule(
                rule_key=f"survey-audit-rule-{suffix}",
                version=1,
                name="问卷审计测试赛制",
                side_size=4,
                estimated_seconds=900,
                status="ENABLED",
                created_by=user.id,
            )
            session.add(rule)
            await session.flush()
            room = Room(
                code=suffix[:6].upper(),
                title="问卷审计测试房间",
                label="测试",
                topic_snapshot={},
                rule_id=rule.id,
                rule_snapshot={},
                organizer_user_id=user.id,
            )
            session.add(room)
            await session.flush()
            match = Match(room_id=room.id, status="FINISHED", runtime_snapshot={})
            session.add(match)
            await session.flush()
            task = PostmatchSurveyTask(
                match_id=match.id,
                user_id=user.id,
                questionnaire_version="legacy-audit-test",
                answers={},
            )
            session.add(task)
            await session.commit()

        async with factory() as session:
            personal_draft = await save_personal_response(
                session, user_id=user.id, answers={"q1": 3}, submit=False
            )
            personal_id = personal_draft["id"]
            await save_personal_response(
                session,
                user_id=user.id,
                answers={"q1": 3, "q2": 3, "q3": 3, "q4": 3, "q5": 3},
                submit=True,
            )
            await save_postmatch_task(
                session, task_id=task.id, user_id=user.id, answers={}, submit=False
            )
            await save_postmatch_task(
                session,
                task_id=task.id,
                user_id=user.id,
                answers={"self": {}, "ai": {}, "overall": {}},
                submit=True,
            )

        async with factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(AuditLog)
                        .where(AuditLog.actor_user_id == user.id)
                        .order_by(AuditLog.created_at, AuditLog.action)
                    )
                ).all()
            )
            actions = {row.action for row in rows}
            assert actions == {
                "participant.survey.personal_draft_saved",
                "participant.survey.personal_submitted",
                "participant.survey.postmatch_draft_saved",
                "participant.survey.postmatch_submitted",
            }
            assert {row.target_id for row in rows} == {personal_id, str(task.id)}
            assert all("answers" not in row.details for row in rows)
            before_locked = len(rows)

        async with factory() as session:
            with pytest.raises(APIError, match="survey_locked"):
                await save_postmatch_task(
                    session, task_id=task.id, user_id=user.id, answers={}, submit=False
                )
        async with factory() as session:
            with pytest.raises(APIError, match="survey_answer_invalid"):
                await save_personal_response(
                    session,
                    user_id=user.id,
                    answers={"unknown": 3},
                    submit=False,
                )
        async with factory() as session:
            after_locked = await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.actor_user_id == user.id)
            )
            assert after_locked == before_locked
            response = await session.scalar(
                select(PersonalAiSurveyResponse).where(
                    PersonalAiSurveyResponse.user_id == user.id
                )
            )
            assert response is not None and response.status == "SUBMITTED"
    finally:
        await engine.dispose()

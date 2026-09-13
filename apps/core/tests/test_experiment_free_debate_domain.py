from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from jx_core.matches.domain import (
    DebateParticipant,
    MatchAction,
    MatchActor,
    MatchCommand,
    MatchDomainError,
    MatchRuntimeState,
)


def _actor(now: list[float]) -> tuple[MatchActor, MatchAction, object, object]:
    human_id = uuid4()
    agent_id = uuid4()
    action = MatchAction(
        stage_position=3,
        action_position=0,
        action_kind="FREE_DEBATE",
        duration_seconds=360,
        free_max_speech_seconds=30,
        participants=(
            DebateParticipant(
                side="NEGATIVE", seat_no=2, agent_profile_id=agent_id
            ),
            DebateParticipant(side="NEGATIVE", seat_no=3, user_id=human_id),
        ),
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(action,),
            free_holder_side="NEGATIVE",
            free_affirmative_remaining_ms=360_000,
            free_negative_remaining_ms=360_000,
            hand_window_open=True,
            experiment_mode=True,
            experiment_attempt_id=uuid4(),
        ),
        clock=lambda: now[0],
        wall_clock=lambda: now[0],
        sleep=lambda _: asyncio.sleep(60),
    )
    actor._start_free_agent_decisions("NEGATIVE")
    return actor, action, human_id, agent_id


def _decision_command(
    actor: MatchActor,
    action: MatchAction,
    agent_id: object,
    *,
    should_speak: bool,
    message_id: str = "decision",
) -> MatchCommand:
    return MatchCommand(
        type="free.agent_decision_result",
        message_id=message_id,
        payload={
            "action_key": action.action_key,
            "agent_profile_id": str(agent_id),
            "decision_round_id": str(actor.state.agent_decision_round_id),
            "should_speak": should_speak,
        },
    )


@pytest.mark.asyncio
async def test_experiment_skip_enters_human_only_wait_and_first_raise_wins() -> None:
    now = [100.0]
    actor, action, human_id, agent_id = _actor(now)
    await actor.start()
    try:
        decision = await actor.submit(
            _decision_command(actor, action, agent_id, should_speak=False)
        )
        assert decision.state.agent_effective_status == "SKIP"
        assert decision.state.action_state == "FREE_SELECTING"

        closed = await actor.submit(
            MatchCommand(type="hand.window_closed", message_id="deadline")
        )
        assert closed.state.selection_phase == "HUMAN_ONLY_WAIT"
        assert closed.state.agent_effective_status == "SKIP"
        assert closed.state.human_wait_deadline_mono == 160.0
        assert closed.state.free_negative_remaining_ms == 360_000

        selected = await actor.submit(
            MatchCommand(type="hand.raise", message_id="late-human", actor_user_id=human_id)
        )
        assert selected.state.selection_phase == "ALLOCATED"
        assert selected.state.action_state == "HUMAN_READY_TO_START"
        assert selected.state.current_speaker_user_id == human_id
        assert selected.state.human_wait_deadline_mono is None
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_zero_time_side_cannot_raise_hand() -> None:
    now = [100.0]
    actor, action, human_id, _ = _actor(now)
    actor.state = replace(actor.state, free_negative_remaining_ms=0)
    await actor.start()
    try:
        with pytest.raises(MatchDomainError, match="hand_not_eligible"):
            await actor.submit(
                MatchCommand(
                    type="hand.raise",
                    message_id="zero-time-human",
                    actor_user_id=human_id,
                )
            )
        assert actor.state.hand_queue == ()
        actor.state = replace(actor.state, free_negative_remaining_ms=1_000)
        raised = await actor.submit(
            MatchCommand(
                type="hand.raise",
                message_id="positive-time-human",
                actor_user_id=human_id,
            )
        )
        assert raised.state.hand_queue == (human_id,)
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_formal_4v4_all_agent_skip_uses_random_fallback() -> None:
    now = [100.0]
    first_agent, second_agent = uuid4(), uuid4()
    action = MatchAction(
        stage_position=3,
        action_position=0,
        action_kind="FREE_DEBATE",
        duration_seconds=360,
        participants=(
            DebateParticipant(side="NEGATIVE", seat_no=1, agent_profile_id=first_agent),
            DebateParticipant(side="NEGATIVE", seat_no=2, agent_profile_id=second_agent),
        ),
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(action,),
            free_holder_side="NEGATIVE",
            free_affirmative_remaining_ms=360_000,
            free_negative_remaining_ms=360_000,
            hand_window_open=True,
            formal_4v4=True,
        ),
        clock=lambda: now[0],
        wall_clock=lambda: now[0],
        sleep=lambda _: asyncio.sleep(60),
    )
    actor._start_free_agent_decisions("NEGATIVE")
    await actor.start()
    try:
        round_id = actor.state.agent_decision_round_id
        assert round_id is not None
        for index, agent_id in enumerate((first_agent, second_agent)):
            result = await actor.submit(
                MatchCommand(
                    type="free.agent_decision_result",
                    message_id=f"formal-skip-{index}",
                    payload={
                        "action_key": action.action_key,
                        "agent_profile_id": str(agent_id),
                        "decision_round_id": str(round_id),
                        "should_speak": False,
                    },
                )
            )
        assert result.state.action_state == "FREE_SELECTING"
        await actor.submit(MatchCommand(type="hand.window_closed", message_id="formal-cutoff"))
        assert actor.state.selection_phase == "ALLOCATED"
        assert actor.state.agent_selection_mode == "ALL_AGENT_SKIP_RANDOM"
        assert actor.state.current_agent_profile_id in {first_agent, second_agent}
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_formal_4v4_multiple_agent_raises_use_seeded_selection() -> None:
    now = [100.0]
    agents = (uuid4(), uuid4(), uuid4())
    action = MatchAction(
        stage_position=3,
        action_position=0,
        action_kind="FREE_DEBATE",
        duration_seconds=360,
        participants=tuple(
            DebateParticipant(side="NEGATIVE", seat_no=index, agent_profile_id=agent_id)
            for index, agent_id in enumerate(agents, start=1)
        ),
    )
    state = MatchRuntimeState(
        match_id=uuid4(),
        status="RUNNING",
        action_state="FREE_SELECTING",
        actions=(action,),
        match_seed=7,
        free_holder_side="NEGATIVE",
        free_affirmative_remaining_ms=360_000,
        free_negative_remaining_ms=360_000,
        hand_window_open=True,
        formal_4v4=True,
    )
    actor = MatchActor(
        state,
        clock=lambda: now[0],
        wall_clock=lambda: now[0],
        sleep=lambda _: asyncio.sleep(60),
    )
    actor._start_free_agent_decisions("NEGATIVE")
    await actor.start()
    try:
        round_id = actor.state.agent_decision_round_id
        for index, agent_id in enumerate(reversed(agents)):
            await actor.submit(
                MatchCommand(
                    type="free.agent_decision_result",
                    message_id=f"formal-raise-{index}",
                    payload={
                        "action_key": action.action_key,
                        "agent_profile_id": str(agent_id),
                        "decision_round_id": str(round_id),
                        "should_speak": True,
                    },
                )
            )
        eligible = sorted(
            actor.state.agent_decisions,
            key=lambda item: actor._agent_tie_break(item.agent_profile_id),
        )
        expected = eligible[state.match_seed % len(eligible)].agent_profile_id
        await actor.submit(MatchCommand(type="hand.window_closed", message_id="formal-cutoff"))
        assert actor.state.current_agent_profile_id == expected
        assert actor.state.agent_selection_mode == "VOLUNTEER"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_experiment_human_always_outranks_agent_raise() -> None:
    now = [100.0]
    actor, action, human_id, agent_id = _actor(now)
    await actor.start()
    try:
        await actor.submit(_decision_command(actor, action, agent_id, should_speak=True))
        await actor.submit(
            MatchCommand(type="hand.raise", message_id="human", actor_user_id=human_id)
        )
        selected = await actor.submit(
            MatchCommand(type="hand.window_closed", message_id="deadline")
        )

        assert selected.state.current_speaker_user_id == human_id
        assert selected.state.current_agent_profile_id is None
        assert selected.state.selection_phase == "ALLOCATED"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_experiment_deadline_marks_pending_and_rejects_late_result() -> None:
    now = [100.0]
    actor, action, _, agent_id = _actor(now)
    await actor.start()
    try:
        closed = await actor.submit(
            MatchCommand(type="hand.window_closed", message_id="deadline")
        )
        assert closed.state.selection_phase == "HUMAN_ONLY_WAIT"
        assert closed.state.agent_effective_status == "SKIP"
        assert closed.state.agent_decisions[0].failed is True

        with pytest.raises(MatchDomainError, match="stale_callback"):
            await actor.submit(
                _decision_command(
                    actor,
                    action,
                    agent_id,
                    should_speak=True,
                    message_id="late-decision",
                )
            )
        assert actor.state.selection_phase == "HUMAN_ONLY_WAIT"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_experiment_human_wait_timeout_pauses_without_consuming_side_time() -> None:
    now = [100.0]
    actor, action, _, agent_id = _actor(now)
    await actor.start()
    try:
        await actor.submit(_decision_command(actor, action, agent_id, should_speak=False))
        await actor.submit(MatchCommand(type="hand.window_closed", message_id="deadline"))
        now[0] = 160.0
        paused = await actor.submit(
            MatchCommand(type="human.wait_timeout", message_id="wait-timeout")
        )

        assert paused.state.status == "PAUSED"
        assert paused.state.action_state == "RECOVERY_REQUIRED"
        assert paused.state.error_code == "HUMAN_WAIT_TIMEOUT"
        assert paused.state.free_negative_remaining_ms == 360_000
        assert paused.state.agent_effective_status == "SKIP"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_experiment_resume_restarts_full_human_only_wait() -> None:
    now = [100.0]
    actor, action, _, agent_id = _actor(now)
    await actor.start()
    try:
        await actor.submit(_decision_command(actor, action, agent_id, should_speak=False))
        waiting = await actor.submit(
            MatchCommand(type="hand.window_closed", message_id="deadline")
        )
        opportunity_id = waiting.state.opportunity_id
        now[0] = 125.0
        await actor.submit(
            MatchCommand(
                type="match.pause",
                message_id="pause-wait",
                actor_user_id=uuid4(),
                payload={"authorized": True},
            )
        )
        await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="resume-wait",
                payload={"privileged": True, "reasons": []},
            )
        )
        now[0] = 128.0
        resumed = await actor.submit(
            MatchCommand(type="resume.elapsed", message_id="resume-wait-elapsed")
        )

        assert resumed.state.selection_phase == "HUMAN_ONLY_WAIT"
        assert resumed.state.opportunity_id == opportunity_id
        assert resumed.state.agent_effective_status == "SKIP"
        assert resumed.state.human_wait_deadline_mono == 188.0
        assert resumed.state.human_wait_remaining_ms == 60_000
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_experiment_competing_pause_preserves_decision_and_remaining_window() -> None:
    now = [100.0]
    actor, _, human_id, _ = _actor(now)
    round_id = actor.state.agent_decision_round_id
    opportunity_id = actor.state.opportunity_id
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(type="hand.raise", message_id="early-hand", actor_user_id=human_id)
        )
        now[0] = 101.0
        paused = await actor.submit(
            MatchCommand(
                type="match.pause",
                message_id="pause-window",
                actor_user_id=uuid4(),
                payload={"authorized": True},
            )
        )
        assert paused.state.selection_remaining_ms == 2_000
        await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="resume-window",
                payload={"privileged": True, "reasons": []},
            )
        )
        now[0] = 104.0
        resumed = await actor.submit(
            MatchCommand(type="resume.elapsed", message_id="resume-window-elapsed")
        )

        assert resumed.state.selection_phase == "COMPETING"
        assert resumed.state.opportunity_id == opportunity_id
        assert resumed.state.agent_decision_round_id == round_id
        assert resumed.state.hand_queue == (human_id,)
        assert resumed.state.selection_deadline_mono == 106.0
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_experiment_speech_opens_stable_next_opportunity_and_decides_during_speech() -> None:
    now = [100.0]
    actor, action, human_id, agent_id = _actor(now)
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(type="hand.raise", message_id="current-human", actor_user_id=human_id)
        )
        selected = await actor.submit(
            MatchCommand(type="hand.window_closed", message_id="current-cutoff")
        )
        allocated_opportunity_id = selected.state.opportunity_id
        actor.state = replace(actor.state, free_affirmative_remaining_ms=0)
        started = await actor.submit(
            MatchCommand(type="speech.start", message_id="speech-start", actor_user_id=human_id)
        )

        next_opportunity_id = started.state.opportunity_id
        assert next_opportunity_id is not None
        assert next_opportunity_id != allocated_opportunity_id
        assert started.state.allocated_opportunity_id == allocated_opportunity_id
        assert started.state.agent_effective_status == "WAITING"
        assert started.state.agent_decision_round_id is None
        assert any(event.type == "free.opportunity_opened" for event in started.events)

        decision_started = await actor.submit(
            MatchCommand(
                type="free.agent_decision_start",
                message_id="next-decision-start",
                payload={
                    "opportunity_id": str(next_opportunity_id),
                    "opportunity_generation": started.state.opportunity_generation,
                    "source_speech_id": str(started.state.current_speech_id),
                    "trigger_kind": "HUMAN_SPEECH",
                },
            )
        )
        assert decision_started.state.action_state == "HUMAN_SPEAKING"
        assert decision_started.state.agent_effective_status == "DECIDING"
        result = await actor.submit(
            _decision_command(
                actor,
                action,
                agent_id,
                should_speak=True,
                message_id="next-decision-result",
            )
        )
        assert result.state.action_state == "HUMAN_SPEAKING"
        assert result.state.opportunity_id == next_opportunity_id
        assert result.state.agent_effective_status == "RAISE"

        await actor.submit(
            MatchCommand(type="speech.finish", message_id="speech-finish", actor_user_id=human_id)
        )
        finalized = await actor.submit(
            MatchCommand(
                type="asr.finalized",
                message_id="asr-final",
                payload={
                    "speech_id": str(actor.state.current_speech_id),
                    "final_text": "测试发言",
                    "audio_duration_ms": 1_000,
                },
            )
        )
        assert finalized.state.action_state == "FREE_SELECTING"
        assert finalized.state.opportunity_id == next_opportunity_id
        assert finalized.state.agent_effective_status == "RAISE"
        assert finalized.state.selection_deadline_mono == 103.0
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_human_speech_cutoff_does_not_wait_for_asr_final() -> None:
    now = [100.0]
    actor, _, human_id, _ = _actor(now)
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(type="hand.raise", message_id="current-human", actor_user_id=human_id)
        )
        await actor.submit(MatchCommand(type="hand.window_closed", message_id="current-cutoff"))
        started = await actor.submit(
            MatchCommand(type="speech.start", message_id="speech-start", actor_user_id=human_id)
        )
        opportunity_id = started.state.opportunity_id
        finalizing = await actor.submit(
            MatchCommand(type="speech.finish", message_id="speech-finish", actor_user_id=human_id)
        )
        assert finalizing.state.action_state == "SPEECH_FINALIZING"
        assert finalizing.state.selection_deadline_mono == 103.0

        now[0] = 103.0
        cutoff = await actor.submit(
            MatchCommand(type="hand.window_closed", message_id="next-cutoff")
        )
        assert cutoff.state.action_state == "FREE_SELECTING"
        assert cutoff.state.opportunity_id == opportunity_id
        assert cutoff.state.selection_phase == "HUMAN_ONLY_WAIT"
        assert cutoff.state.agent_effective_status == "SKIP"
        wait_event = next(
            event for event in cutoff.events if event.type == "free.human_wait_started"
        )
        assert wait_event.payload["decision_fact"] == "TECHNICAL_MISSING"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_initial_experiment_decision_starts_with_host_and_cutoff_starts_after_host() -> None:
    now = [50.0]
    agent_id = uuid4()
    action = MatchAction(
        stage_position=3,
        action_position=0,
        action_kind="FREE_DEBATE",
        duration_seconds=360,
        free_starting_side="AFFIRMATIVE",
        host_audio_path="host.ogg",
        host_audio_duration_ms=2_000,
        participants=(
            DebateParticipant(side="AFFIRMATIVE", seat_no=2, agent_profile_id=agent_id),
        ),
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="NOT_STARTED",
            actions=(action,),
            experiment_mode=True,
            experiment_attempt_id=uuid4(),
        ),
        clock=lambda: now[0],
        wall_clock=lambda: now[0],
        sleep=lambda _: asyncio.sleep(60),
    )

    await actor.start()
    try:
        entered = actor._enter_current_action()
        opportunity_id = actor.state.opportunity_id
        round_id = actor.state.agent_decision_round_id
        assert actor.state.action_state == "HOST_ANNOUNCING"
        assert actor.state.free_holder_side == "AFFIRMATIVE"
        assert actor.state.agent_effective_status == "DECIDING"
        assert actor.state.selection_deadline_mono is None
        assert [event.type for event in entered] == [
            "host.play",
            "free_debate.started",
            "hand.window_opened",
            "agent.decision_started",
        ]

        now[0] = 52.0
        elapsed = actor._host_elapsed(
            MatchCommand(type="host.elapsed", message_id="host-elapsed")
        )
        assert actor.state.action_state == "FREE_SELECTING"
        assert actor.state.opportunity_id == opportunity_id
        assert actor.state.agent_decision_round_id == round_id
        assert actor.state.selection_deadline_mono == 55.0
        assert [event.type for event in elapsed] == ["host.finished", "hand.window_opened"]
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_experiment_reset_invalidates_next_opportunity_and_old_decision() -> None:
    now = [100.0]
    actor, action, human_id, _ = _actor(now)
    opponent_human_id = uuid4()
    opponent_agent_id = uuid4()
    action = replace(
        action,
        participants=(
            *action.participants,
            DebateParticipant(side="AFFIRMATIVE", seat_no=2, agent_profile_id=opponent_agent_id),
            DebateParticipant(side="AFFIRMATIVE", seat_no=3, user_id=opponent_human_id),
        ),
    )
    actor.state = replace(actor.state, actions=(action,))
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(type="hand.raise", message_id="current-human", actor_user_id=human_id)
        )
        await actor.submit(MatchCommand(type="hand.window_closed", message_id="current-cutoff"))
        started = await actor.submit(
            MatchCommand(type="speech.start", message_id="speech-start", actor_user_id=human_id)
        )
        old_opportunity_id = started.state.opportunity_id
        decision_started = await actor.submit(
            MatchCommand(
                type="free.agent_decision_start",
                message_id="decision-start",
                payload={
                    "opportunity_id": str(old_opportunity_id),
                    "opportunity_generation": started.state.opportunity_generation,
                    "source_speech_id": str(started.state.current_speech_id),
                    "trigger_kind": "HUMAN_SPEECH",
                },
            )
        )
        old_round_id = decision_started.state.agent_decision_round_id
        await actor.submit(
            MatchCommand(
                type="hand.raise", message_id="next-hand", actor_user_id=opponent_human_id
            )
        )

        reset = await actor.submit(
            MatchCommand(type="speech.reset", message_id="reset", actor_user_id=human_id)
        )

        assert reset.state.action_state == "HUMAN_READY_TO_START"
        assert reset.state.opportunity_id is None
        assert reset.state.agent_decision_round_id is None
        assert reset.state.hand_queue == ()
        invalidated = next(
            event for event in reset.events if event.type == "free.opportunity_invalidated"
        )
        assert invalidated.payload["opportunity_id"] == str(old_opportunity_id)
        assert invalidated.payload["reason"] == "SPEECH_RESET"

        restarted = await actor.submit(
            MatchCommand(type="speech.start", message_id="speech-restart", actor_user_id=human_id)
        )
        assert restarted.state.opportunity_id is not None
        assert restarted.state.opportunity_id != old_opportunity_id

        with pytest.raises(MatchDomainError, match="stale_callback"):
            await actor.submit(
                MatchCommand(
                    type="free.agent_decision_result",
                    message_id="old-decision-result",
                    payload={
                        "action_key": action.action_key,
                        "agent_profile_id": str(opponent_agent_id),
                        "decision_round_id": str(old_round_id),
                        "should_speak": True,
                    },
                )
            )
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_experiment_pause_during_speech_invalidates_next_opportunity() -> None:
    now = [100.0]
    actor, action, human_id, _ = _actor(now)
    opponent_human_id = uuid4()
    action = replace(
        action,
        participants=(
            *action.participants,
            DebateParticipant(side="AFFIRMATIVE", seat_no=3, user_id=opponent_human_id),
        ),
    )
    actor.state = replace(actor.state, actions=(action,))
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(type="hand.raise", message_id="current-human", actor_user_id=human_id)
        )
        await actor.submit(MatchCommand(type="hand.window_closed", message_id="current-cutoff"))
        started = await actor.submit(
            MatchCommand(type="speech.start", message_id="speech-start", actor_user_id=human_id)
        )
        opportunity_id = started.state.opportunity_id
        await actor.submit(
            MatchCommand(
                type="hand.raise", message_id="next-hand", actor_user_id=opponent_human_id
            )
        )

        paused = await actor.submit(
            MatchCommand(
                type="match.pause",
                message_id="pause-speech",
                actor_user_id=human_id,
                payload={"authorized": True},
            )
        )

        assert paused.state.status == "PAUSED"
        assert paused.state.opportunity_id is None
        assert paused.state.hand_queue == ()
        assert paused.state.agent_decisions == ()
        invalidated = next(
            event for event in paused.events if event.type == "free.opportunity_invalidated"
        )
        assert invalidated.payload["opportunity_id"] == str(opportunity_id)
        assert invalidated.payload["reason"] == "SPEECH_INTERRUPTED"
    finally:
        await actor.close()

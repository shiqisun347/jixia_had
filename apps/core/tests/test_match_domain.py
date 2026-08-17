from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from jx_core.matches.domain import (
    AgentDecisionState,
    DebateParticipant,
    MatchAction,
    MatchActor,
    MatchCommand,
    MatchDomainError,
    MatchRuntimeState,
    MatchRuntimeView,
    compile_linear_actions,
)
from jx_core.matches.routes import _event_response, _snapshot_response, match_page_permissions


def test_match_page_permissions_ignore_global_admin_role() -> None:
    organizer = uuid4()
    admin_user = uuid4()
    assert match_page_permissions(
        actor_user_id=admin_user,
        organizer_user_id=organizer,
        member_role="DEBATER",
    ) == (False, True)
    assert match_page_permissions(
        actor_user_id=admin_user,
        organizer_user_id=organizer,
        member_role="SPECTATOR",
    ) == (False, False)
    assert match_page_permissions(
        actor_user_id=organizer,
        organizer_user_id=organizer,
        member_role="DEBATER",
    ) == (True, True)


def test_free_debate_team_state_is_projected_only_to_candidate_side() -> None:
    teammate = uuid4()
    opponent = uuid4()
    agent_id = uuid4()
    round_id = uuid4()
    state = MatchRuntimeState(
        match_id=uuid4(),
        status="RUNNING",
        action_state="FREE_SELECTING",
        actions=(
            MatchAction(
                stage_position=1,
                action_position=1,
                action_kind="FREE_DEBATE",
                duration_seconds=60,
                participants=(
                    DebateParticipant(side="AFFIRMATIVE", seat_no=1, user_id=teammate),
                    DebateParticipant(side="AFFIRMATIVE", seat_no=2, agent_profile_id=agent_id),
                    DebateParticipant(side="NEGATIVE", seat_no=1, user_id=opponent),
                ),
            ),
        ),
        free_holder_side="AFFIRMATIVE",
        hand_queue=(teammate,),
        agent_hand_queue=(agent_id,),
        agent_decision_round_id=round_id,
        agent_decisions=(
            AgentDecisionState(
                agent_profile_id=agent_id,
                side="AFFIRMATIVE",
                seat_no=2,
                status="HAND",
                should_speak=True,
                willingness=0.9,
                result_order=1,
            ),
        ),
    )
    view = MatchRuntimeView(
        state=state,
        speech_remaining_ms=None,
        countdown_remaining_ms=None,
        free_affirmative_remaining_ms=60_000,
        free_negative_remaining_ms=60_000,
    )
    teammate_view = _snapshot_response(
        view,
        uuid4(),
        viewer_user_id=teammate,
        member_role="DEBATER",
    )
    assert teammate_view.hand_queue == [teammate]
    assert teammate_view.agent_decisions[0].status == "HAND"
    assert [item.rank for item in teammate_view.team_hand_queue] == [1, 2]

    opponent_view = _snapshot_response(
        view,
        uuid4(),
        viewer_user_id=opponent,
        member_role="DEBATER",
    )
    assert opponent_view.hand_queue == []
    assert opponent_view.agent_hand_queue == []
    assert opponent_view.agent_decisions == []
    assert opponent_view.team_hand_queue == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action_state", ["AGENT_PREPARING", "AGENT_SPEAKING", "AGENT_FINALIZING"])
async def test_free_agent_reset_preserves_agent_identity_and_hand_queue(action_state: str) -> None:
    agent_id = uuid4()
    queued = (uuid4(), uuid4())
    action = MatchAction(
        stage_position=1,
        action_position=0,
        action_kind="FREE_DEBATE",
        duration_seconds=300,
        participants=(DebateParticipant(side="NEGATIVE", seat_no=3, agent_profile_id=agent_id),),
        free_max_speech_seconds=60,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state=action_state,
            actions=(action,),
            current_speech_id=uuid4(),
            current_agent_profile_id=agent_id,
            current_speaker_side="NEGATIVE",
            current_speaker_seat_no=3,
            speech_remaining_ms=41_000,
            free_holder_side="NEGATIVE",
            free_affirmative_remaining_ms=193_000,
            free_negative_remaining_ms=47_000,
            hand_queue=queued,
            hand_window_open=True,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        result = await actor.submit(
            MatchCommand(
                type="speech.reset",
                message_id=f"free-agent-reset-{action_state}",
                actor_user_id=uuid4(),
                payload={"privileged": True},
            )
        )
        assert result.state.action_state == "AGENT_PREPARING"
        assert result.state.current_agent_profile_id == agent_id
        assert result.state.current_speaker_side == "NEGATIVE"
        assert result.state.current_speaker_seat_no == 3
        assert result.state.speech_remaining_ms == 47_000
        assert result.state.free_holder_side == "NEGATIVE"
        assert result.state.hand_queue == queued
        assert result.events[0].payload["agent_profile_id"] == str(agent_id)
        restarted = await actor.submit(
            MatchCommand(
                type="agent.playback_started",
                message_id=f"free-agent-replay-{action_state}",
                payload={"agent_profile_id": str(agent_id), "speech_id": str(uuid4())},
            )
        )
        assert restarted.state.action_state == "AGENT_SPEAKING"
        assert restarted.state.hand_queue == queued
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_free_human_reset_preserves_user_identity_and_hand_queue() -> None:
    user_id = uuid4()
    queued_user = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=0,
        action_kind="FREE_DEBATE",
        duration_seconds=300,
        participants=(DebateParticipant(side="AFFIRMATIVE", seat_no=2, user_id=user_id),),
        free_max_speech_seconds=60,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HUMAN_SPEAKING",
            actions=(action,),
            current_speech_id=uuid4(),
            current_speaker_user_id=user_id,
            current_speaker_side="AFFIRMATIVE",
            current_speaker_seat_no=2,
            speech_remaining_ms=30_000,
            free_holder_side="AFFIRMATIVE",
            free_affirmative_remaining_ms=52_000,
            free_negative_remaining_ms=121_000,
            hand_queue=(queued_user,),
            hand_window_open=True,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        result = await actor.submit(
            MatchCommand(
                type="speech.reset",
                message_id="free-human-reset",
                actor_user_id=user_id,
            )
        )
        assert result.state.action_state == "HUMAN_READY_TO_START"
        assert result.state.current_speaker_user_id == user_id
        assert result.state.current_speaker_side == "AFFIRMATIVE"
        assert result.state.current_speaker_seat_no == 2
        assert result.state.speech_remaining_ms == 52_000
        assert result.state.hand_queue == (queued_user,)
        restarted = await actor.submit(
            MatchCommand(
                type="speech.start",
                message_id="free-human-replay",
                actor_user_id=user_id,
            )
        )
        assert restarted.state.action_state == "HUMAN_SPEAKING"
        assert restarted.state.hand_queue == (queued_user,)
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_reset_pre_commit_runs_after_validation_once_and_before_commit() -> None:
    user_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        side="AFFIRMATIVE",
        seat_no=1,
        speaker_user_id=user_id,
    )
    calls: list[str] = []

    async def pre_commit(*_: object) -> None:
        calls.append("pre_commit")

    async def commit(*_: object) -> None:
        calls.append("commit")

    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HUMAN_SPEAKING",
            actions=(action,),
            current_speaker_user_id=user_id,
            current_speech_id=uuid4(),
        ),
        pre_commit=pre_commit,
        commit=commit,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        command = MatchCommand(type="speech.reset", message_id="reset-once", actor_user_id=user_id)
        first = await actor.submit(command)
        duplicate = await actor.submit(command)
        assert first.duplicate is False
        assert duplicate.duplicate is True
        assert calls == ["pre_commit", "commit"]
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_invalid_reset_does_not_run_pre_commit() -> None:
    speaker_id = uuid4()
    calls = 0

    async def pre_commit(*_: object) -> None:
        nonlocal calls
        calls += 1

    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        side="AFFIRMATIVE",
        seat_no=1,
        speaker_user_id=speaker_id,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HUMAN_SPEAKING",
            actions=(action,),
            current_speaker_user_id=speaker_id,
            current_speech_id=uuid4(),
        ),
        pre_commit=pre_commit,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        with pytest.raises(MatchDomainError, match="forbidden"):
            await actor.submit(
                MatchCommand(
                    type="speech.reset",
                    message_id="unauthorized-reset",
                    actor_user_id=uuid4(),
                )
            )
        assert calls == 0
        assert actor.state.action_state == "HUMAN_SPEAKING"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_failed_reset_pre_commit_does_not_commit_or_change_state() -> None:
    user_id = uuid4()
    committed = False

    async def pre_commit(*_: object) -> None:
        raise RuntimeError("runtime cleanup failed")

    async def commit(*_: object) -> None:
        nonlocal committed
        committed = True

    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        side="AFFIRMATIVE",
        seat_no=1,
        speaker_user_id=user_id,
    )
    speech_id = uuid4()
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HUMAN_SPEAKING",
            actions=(action,),
            current_speaker_user_id=user_id,
            current_speech_id=speech_id,
        ),
        pre_commit=pre_commit,
        commit=commit,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        with pytest.raises(RuntimeError, match="runtime cleanup failed"):
            await actor.submit(
                MatchCommand(
                    type="speech.reset",
                    message_id="failed-cleanup",
                    actor_user_id=user_id,
                )
            )
        assert committed is False
        assert actor.state.action_state == "HUMAN_SPEAKING"
        assert actor.state.current_speech_id == speech_id
    finally:
        await actor.close()


def snapshot() -> dict[str, object]:
    return {
        "host_audio": [{"segment_key": "stage-1-start", "storage_path": "rules/host.opus"}],
        "stages": [
            {
                "position": 1,
                "stage_kind": "FIXED_SPEECH",
                "actions": [
                    {
                        "position": 1,
                        "action_kind": "SPEECH",
                        "side": "AFFIRMATIVE",
                        "seat_no": 1,
                        "duration_seconds": 1,
                    },
                    {
                        "position": 2,
                        "action_kind": "SPEECH",
                        "side": "NEGATIVE",
                        "seat_no": 1,
                        "duration_seconds": 1,
                    },
                ],
            },
            {"position": 2, "stage_kind": "END", "actions": []},
        ],
    }


@pytest.mark.asyncio
async def test_actor_serializes_start_speech_finish_and_idempotency() -> None:
    affirmative = uuid4()
    negative = uuid4()
    actions = compile_linear_actions(
        snapshot(),
        {("AFFIRMATIVE", 1): affirmative, ("NEGATIVE", 1): negative},
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="START_PENDING_RUNTIME",
            action_state="NOT_STARTED",
            actions=actions,
            current_speaker_user_id=negative,
            current_agent_profile_id=uuid4(),
            current_speaker_side="NEGATIVE",
            current_speaker_seat_no=1,
            current_speech_id=uuid4(),
            speech_deadline_mono=123.0,
            speech_remaining_ms=900,
            hand_queue=(negative,),
            agent_hand_queue=(uuid4(),),
            agent_selection_mode="VOLUNTEER",
            hand_window_open=True,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        started = await actor.submit(MatchCommand(type="runtime.start", message_id="m1"))
        assert started.state.status == "START_COUNTDOWN"
        duplicate = await actor.submit(MatchCommand(type="runtime.start", message_id="m1"))
        assert duplicate.duplicate is True
        ready = await actor.submit(MatchCommand(type="countdown.elapsed", message_id="m2"))
        assert ready.state.action_state == "HOST_ANNOUNCING"
        assert ready.state.current_speaker_user_id is None
        assert ready.state.current_agent_profile_id is None
        assert ready.state.current_speaker_side is None
        assert ready.state.current_speaker_seat_no is None
        assert ready.state.current_speech_id is None
        assert ready.state.speech_deadline_mono is None
        assert ready.state.speech_remaining_ms is None
        assert ready.state.hand_queue == ()
        assert ready.state.agent_hand_queue == ()
        assert ready.state.agent_selection_mode is None
        assert ready.state.hand_window_open is False
        announced = await actor.submit(
            MatchCommand(
                type="host.finished",
                message_id="m3",
                payload={"authorized": True},
            )
        )
        assert announced.state.action_state == "HUMAN_READY_TO_START"
        assert announced.state.current_speaker_side == "AFFIRMATIVE"
        assert announced.state.current_speaker_seat_no == 1
        with pytest.raises(MatchDomainError, match="not_current_speaker"):
            await actor.submit(
                MatchCommand(type="speech.start", message_id="m4", actor_user_id=negative)
            )
        speaking = await actor.submit(
            MatchCommand(type="speech.start", message_id="m5", actor_user_id=affirmative)
        )
        assert speaking.state.action_state == "HUMAN_SPEAKING"
        assert speaking.state.current_speech_id is not None
        assert speaking.state.current_speaker_side == "AFFIRMATIVE"
        assert speaking.state.current_speaker_seat_no == 1
        finished = await actor.submit(
            MatchCommand(type="speech.finish", message_id="m6", actor_user_id=affirmative)
        )
        assert finished.state.action_state == "SPEECH_FINALIZING"
        finalized = await actor.submit(
            MatchCommand(
                type="asr.finalized",
                message_id="m7",
                payload={"speech_id": str(finished.state.current_speech_id), "reason": "EARLY"},
            )
        )
        assert finalized.state.action_state == "HUMAN_READY_TO_START"
        assert finalized.state.current_speaker_user_id == negative
        assert finalized.events[-1].type == "speech.ready"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_agent_action_publishes_its_side_and_seat_as_current_speaker() -> None:
    agent_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="AGENT_SPEECH",
        duration_seconds=10,
        side="NEGATIVE",
        seat_no=2,
        speaker_kind="AGENT",
        agent_profile_id=agent_id,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HOST_ANNOUNCING",
            actions=(action,),
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        result = await actor.submit(
            MatchCommand(
                type="host.finished",
                message_id="agent-ready",
                payload={"authorized": True},
            )
        )
        assert result.state.action_state == "AGENT_PREPARING"
        assert result.state.current_agent_profile_id == agent_id
        assert result.state.current_speaker_user_id is None
        assert result.state.current_speaker_side == "NEGATIVE"
        assert result.state.current_speaker_seat_no == 2

        speech_id = uuid4()
        started = await actor.submit(
            MatchCommand(
                type="agent.playback_started",
                message_id="agent-start",
                payload={"agent_profile_id": str(agent_id), "speech_id": str(speech_id)},
            )
        )
        assert started.state.current_speaker_side == "NEGATIVE"
        assert started.state.current_speaker_seat_no == 2
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_host_finished_rejects_unauthorized_member() -> None:
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=10,
        side="AFFIRMATIVE",
        seat_no=1,
        speaker_kind="HUMAN",
        speaker_user_id=uuid4(),
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HOST_ANNOUNCING",
            actions=(action,),
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        with pytest.raises(MatchDomainError, match="forbidden"):
            await actor.submit(
                MatchCommand(
                    type="host.finished",
                    message_id="unauthorized-host-finished",
                    payload={"authorized": False},
                )
            )
        assert actor.state.action_state == "HOST_ANNOUNCING"
    finally:
        await actor.close()


def test_linear_compiler_accepts_free_debate_and_rejects_missing_side() -> None:
    affirmative = uuid4()
    negative = uuid4()
    free_debate = {
        "stages": [
            {
                "position": 1,
                "stage_kind": "FREE_DEBATE",
                "duration_seconds": 120,
                "parameters": {
                    "max_speech_seconds": 30,
                    "starting_side": "NEGATIVE",
                },
            }
        ]
    }
    actions = compile_linear_actions(
        free_debate,
        {("AFFIRMATIVE", 1): affirmative, ("NEGATIVE", 1): negative},
    )
    assert actions[0].action_kind == "FREE_DEBATE"
    assert actions[0].free_max_speech_seconds == 30
    assert actions[0].free_starting_side == "NEGATIVE"
    with pytest.raises(MatchDomainError, match="free_debate_participants_required"):
        compile_linear_actions(free_debate, {("AFFIRMATIVE", 1): affirmative})


@pytest.mark.asyncio
async def test_runtime_view_reports_effective_countdown_without_mutating_state() -> None:
    now = [100.0]
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="START_PENDING_RUNTIME",
            action_state="NOT_STARTED",
            actions=(),
        ),
        clock=lambda: now[0],
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        await actor.submit(MatchCommand(type="runtime.start", message_id="view-countdown"))
        sequence = actor.state.sequence
        now[0] = 101.25
        view = actor.view()
        assert 1749 <= (view.countdown_remaining_ms or 0) <= 1750
        assert view.state.sequence == sequence
        assert actor.state.sequence == sequence
    finally:
        await actor.close()


def test_runtime_view_reports_effective_speech_and_free_side_remaining() -> None:
    now = [52.0]
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="FREE_DEBATE",
        duration_seconds=120,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="AGENT_SPEAKING",
            actions=(action,),
            current_agent_profile_id=uuid4(),
            current_speaker_side="NEGATIVE",
            current_speaker_seat_no=2,
            speech_deadline_mono=60.0,
            speech_remaining_ms=10_000,
            free_affirmative_remaining_ms=40_000,
            free_negative_remaining_ms=30_000,
            sequence=9,
        ),
        clock=lambda: now[0],
    )

    view = actor.view()

    assert view.speech_remaining_ms == 8_000
    assert view.free_affirmative_remaining_ms == 40_000
    assert view.free_negative_remaining_ms == 28_000
    assert view.state.sequence == 9


def test_linear_compiler_preserves_stage_end_host_audio() -> None:
    actions = compile_linear_actions(
        {
            "host_audio": [
                {"segment_key": "stage-1-end", "storage_path": "rules/end.opus"},
            ],
            "stages": [
                {
                    "position": 1,
                    "stage_kind": "FIXED_SPEECH",
                    "actions": [
                        {
                            "position": 1,
                            "side": "AFFIRMATIVE",
                            "seat_no": 1,
                            "duration_seconds": 1,
                        }
                    ],
                },
                {"position": 2, "stage_kind": "END", "actions": []},
            ],
        },
        {("AFFIRMATIVE", 1): uuid4()},
    )

    assert actions[-1].action_kind == "HOST_AUDIO"
    assert actions[-1].host_audio_path == "rules/end.opus"
    with pytest.raises(MatchDomainError, match="human_speaker_required"):
        compile_linear_actions(snapshot(), {("AFFIRMATIVE", 1): None})


@pytest.mark.asyncio
async def test_free_debate_hand_order_cancel_and_alternation() -> None:
    affirmative_one = uuid4()
    affirmative_two = uuid4()
    negative = uuid4()
    actions = compile_linear_actions(
        {
            "stages": [
                {
                    "position": 1,
                    "stage_kind": "FREE_DEBATE",
                    "duration_seconds": 60,
                    "parameters": {"max_speech_seconds": 20},
                }
            ]
        },
        {
            ("AFFIRMATIVE", 1): affirmative_one,
            ("AFFIRMATIVE", 2): affirmative_two,
            ("NEGATIVE", 1): negative,
        },
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="START_PENDING_RUNTIME",
            action_state="NOT_STARTED",
            actions=actions,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        await actor.submit(MatchCommand(type="runtime.start", message_id="f1"))
        entered = await actor.submit(MatchCommand(type="countdown.elapsed", message_id="f2"))
        assert entered.state.action_state == "FREE_SELECTING"
        first = await actor.submit(
            MatchCommand(type="hand.raise", message_id="f3", actor_user_id=affirmative_one)
        )
        assert first.events[0].payload["order"] == 1
        await actor.submit(
            MatchCommand(type="hand.raise", message_id="f4", actor_user_id=affirmative_two)
        )
        await actor.submit(
            MatchCommand(type="hand.cancel", message_id="f5", actor_user_id=affirmative_one)
        )
        selected = await actor.submit(MatchCommand(type="hand.window_closed", message_id="f6"))
        assert selected.state.current_speaker_user_id == affirmative_two
        assert selected.state.speech_remaining_ms == 20_000
        speaking = await actor.submit(
            MatchCommand(type="speech.start", message_id="f7", actor_user_id=affirmative_two)
        )
        assert speaking.state.hand_window_open is True
        await actor.submit(MatchCommand(type="hand.raise", message_id="f8", actor_user_id=negative))
        finalizing = await actor.submit(
            MatchCommand(type="speech.finish", message_id="f9", actor_user_id=affirmative_two)
        )
        speech_id = finalizing.state.current_speech_id
        assert speech_id is not None
        next_turn = await actor.submit(
            MatchCommand(
                type="asr.finalized",
                message_id="f10",
                payload={"speech_id": str(speech_id), "audio_duration_ms": 8_000},
            )
        )
        assert next_turn.state.free_holder_side == "NEGATIVE"
        assert next_turn.state.free_affirmative_remaining_ms == 52_000
        selected_negative = await actor.submit(
            MatchCommand(type="hand.window_closed", message_id="f11")
        )
        assert selected_negative.state.current_speaker_user_id == negative
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_free_debate_initial_hand_window_closes_automatically() -> None:
    affirmative = uuid4()
    negative = uuid4()
    actions = compile_linear_actions(
        {
            "stages": [
                {
                    "position": 1,
                    "stage_kind": "FREE_DEBATE",
                    "duration_seconds": 30,
                    "parameters": {"max_speech_seconds": 8},
                }
            ]
        },
        {("AFFIRMATIVE", 1): affirmative, ("NEGATIVE", 1): negative},
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="START_PENDING_RUNTIME",
            action_state="NOT_STARTED",
            actions=actions,
        )
    )
    await actor.start()
    try:
        await actor.submit(MatchCommand(type="runtime.start", message_id="auto-1"))
        await asyncio.sleep(6.5)
        assert actor.state.action_state == "RECOVERY_REQUIRED"
        assert actor.state.error_code == "agent_unavailable"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_free_agent_selection_persists_volunteer_queue_until_playback() -> None:
    first = uuid4()
    second = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="FREE_DEBATE",
        duration_seconds=60,
        participants=(
            DebateParticipant(side="AFFIRMATIVE", seat_no=1, agent_profile_id=first),
            DebateParticipant(side="AFFIRMATIVE", seat_no=2, agent_profile_id=second),
        ),
    )
    decision_round_id = uuid4()
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(action,),
            free_holder_side="AFFIRMATIVE",
            free_affirmative_remaining_ms=60_000,
            free_negative_remaining_ms=60_000,
            hand_window_open=False,
            agent_decision_round_id=decision_round_id,
            agent_decisions=(
                AgentDecisionState(
                    agent_profile_id=first,
                    side="AFFIRMATIVE",
                    seat_no=1,
                ),
                AgentDecisionState(
                    agent_profile_id=second,
                    side="AFFIRMATIVE",
                    seat_no=2,
                ),
            ),
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        partial = await actor.submit(
            MatchCommand(
                type="free.agent_decision_result",
                message_id="second-result",
                payload={
                    "action_key": action.action_key,
                    "agent_profile_id": str(second),
                    "decision_round_id": str(decision_round_id),
                    "should_speak": True,
                    "willingness": 0.8,
                },
            )
        )
        assert partial.state.action_state == "FREE_SELECTING"
        assert partial.state.agent_hand_queue == (second,)
        partial_queue_event = next(
            event for event in partial.events if event.type == "free.queue_reordered"
        )
        assert partial_queue_event.payload["reason"] == "AGENT_DECISION_COMPLETED"
        assert partial_queue_event.payload["agent_queue"] == [str(second)]
        assert _event_response(partial_queue_event, can_view_team=False).type == "match.updated"
        selected = await actor.submit(
            MatchCommand(
                type="free.agent_decision_result",
                message_id="first-result",
                payload={
                    "action_key": action.action_key,
                    "agent_profile_id": str(first),
                    "decision_round_id": str(decision_round_id),
                    "should_speak": True,
                    "willingness": 0.9,
                },
            )
        )
        assert selected.state.agent_hand_queue == (first, second)
        assert selected.state.agent_selection_mode == "VOLUNTEER"
        selected_queue_event = next(
            event for event in selected.events if event.type == "free.queue_reordered"
        )
        assert [
            item["participant_id"] for item in selected_queue_event.payload["combined_queue"]
        ] == [
            str(first),
            str(second),
        ]
        started = await actor.submit(
            MatchCommand(
                type="agent.playback_started",
                message_id="playback",
                payload={"agent_profile_id": str(first), "speech_id": str(uuid4())},
            )
        )
        assert started.state.agent_hand_queue == ()
        assert started.state.agent_selection_mode is None
        assert started.state.agent_decisions == ()
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_free_agent_all_false_uses_highest_willingness() -> None:
    first = uuid4()
    second = uuid4()
    decision_round_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="FREE_DEBATE",
        duration_seconds=60,
        participants=(
            DebateParticipant(
                side="AFFIRMATIVE",
                seat_no=1,
                agent_profile_id=first,
            ),
            DebateParticipant(side="AFFIRMATIVE", seat_no=2, agent_profile_id=second),
        ),
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(action,),
            free_holder_side="AFFIRMATIVE",
            free_affirmative_remaining_ms=60_000,
            free_negative_remaining_ms=60_000,
            agent_decision_round_id=decision_round_id,
            agent_decisions=(
                AgentDecisionState(first, "AFFIRMATIVE", 1),
                AgentDecisionState(second, "AFFIRMATIVE", 2),
            ),
        )
    )
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(
                type="free.agent_decision_result",
                message_id="false-first",
                payload={
                    "action_key": action.action_key,
                    "agent_profile_id": str(first),
                    "decision_round_id": str(decision_round_id),
                    "should_speak": False,
                    "willingness": 0.4,
                },
            )
        )
        selected = await actor.submit(
            MatchCommand(
                type="free.agent_decision_result",
                message_id="false-second",
                payload={
                    "action_key": action.action_key,
                    "agent_profile_id": str(second),
                    "decision_round_id": str(decision_round_id),
                    "should_speak": False,
                    "willingness": 0.8,
                },
            )
        )
        assert selected.state.current_agent_profile_id == second
        assert selected.state.agent_selection_mode == "FALLBACK"
        assert selected.state.agent_hand_queue == ()
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_free_human_stays_ahead_of_agent_after_agent_decision_finishes() -> None:
    human_id = uuid4()
    agent_id = uuid4()
    decision_round_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="FREE_DEBATE",
        duration_seconds=60,
        participants=(
            DebateParticipant(side="AFFIRMATIVE", seat_no=1, user_id=human_id),
            DebateParticipant(side="AFFIRMATIVE", seat_no=2, agent_profile_id=agent_id),
        ),
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(action,),
            free_holder_side="AFFIRMATIVE",
            free_affirmative_remaining_ms=60_000,
            free_negative_remaining_ms=60_000,
            hand_queue=(human_id,),
            hand_window_open=False,
            agent_decision_round_id=decision_round_id,
            agent_decisions=(AgentDecisionState(agent_id, "AFFIRMATIVE", 2),),
        )
    )
    await actor.start()
    try:
        selected = await actor.submit(
            MatchCommand(
                type="free.agent_decision_result",
                message_id="agent-wants-to-speak",
                payload={
                    "action_key": action.action_key,
                    "agent_profile_id": str(agent_id),
                    "decision_round_id": str(decision_round_id),
                    "should_speak": True,
                    "willingness": 1.0,
                },
            )
        )
        assert selected.state.action_state == "HUMAN_READY_TO_START"
        assert selected.state.current_speaker_user_id == human_id
        assert selected.state.agent_hand_queue == (agent_id,)
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_free_all_failed_decisions_use_round_tie_break_fallback() -> None:
    first = uuid4()
    second = uuid4()
    decision_round_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="FREE_DEBATE",
        duration_seconds=60,
        participants=(
            DebateParticipant(side="AFFIRMATIVE", seat_no=1, agent_profile_id=first),
            DebateParticipant(side="AFFIRMATIVE", seat_no=2, agent_profile_id=second),
        ),
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(action,),
            match_seed=1,
            free_holder_side="AFFIRMATIVE",
            free_affirmative_remaining_ms=60_000,
            free_negative_remaining_ms=60_000,
            hand_window_open=False,
            agent_decision_round_id=decision_round_id,
            agent_decisions=(
                AgentDecisionState(first, "AFFIRMATIVE", 1),
                AgentDecisionState(second, "AFFIRMATIVE", 2),
            ),
        )
    )
    await actor.start()
    try:
        for agent_id in (first, second):
            result = await actor.submit(
                MatchCommand(
                    type="free.agent_decision_result",
                    message_id=f"failed-{agent_id}",
                    payload={
                        "action_key": action.action_key,
                        "agent_profile_id": str(agent_id),
                        "decision_round_id": str(decision_round_id),
                        "failed": True,
                        "attempt_no": 2,
                        "error_code": "llm_first_token_timeout",
                    },
                )
            )
        assert result.state.status == "RUNNING"
        assert result.state.current_agent_profile_id in {first, second}
        assert result.state.agent_selection_mode == "FALLBACK"
        assert all(item.status == "SKIP" and item.failed for item in result.state.agent_decisions)
    finally:
        await actor.close()


def test_free_equal_willingness_tie_break_changes_with_decision_round() -> None:
    first = UUID(int=1)
    second = UUID(int=2)
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="FREE_DEBATE",
        duration_seconds=60,
        participants=(
            DebateParticipant(side="AFFIRMATIVE", seat_no=1, agent_profile_id=first),
            DebateParticipant(side="AFFIRMATIVE", seat_no=2, agent_profile_id=second),
        ),
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(action,),
            match_seed=1,
            agent_decision_round_id=UUID(int=1),
        )
    )
    first_round = sorted((first, second), key=actor._agent_tie_break)
    actor.state = replace(actor.state, agent_decision_round_id=UUID(int=2))
    second_round = sorted((first, second), key=actor._agent_tie_break)
    assert first_round != second_round


@pytest.mark.asyncio
async def test_free_decision_resume_starts_new_round_and_rejects_old_result() -> None:
    agent_id = uuid4()
    old_round_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="FREE_DEBATE",
        duration_seconds=60,
        participants=(DebateParticipant(side="AFFIRMATIVE", seat_no=1, agent_profile_id=agent_id),),
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(action,),
            free_holder_side="AFFIRMATIVE",
            free_affirmative_remaining_ms=60_000,
            free_negative_remaining_ms=60_000,
            hand_window_open=True,
            agent_decision_round_id=old_round_id,
            agent_decisions=(AgentDecisionState(agent_id, "AFFIRMATIVE", 1),),
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(
                type="match.pause",
                message_id="pause-free-selection",
                payload={"authorized": True},
            )
        )
        await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="resume-free-selection",
                payload={"privileged": True, "reasons": []},
            )
        )
        resumed = await actor.submit(
            MatchCommand(type="resume.elapsed", message_id="resume-free-selection-elapsed")
        )
        new_round_id = resumed.state.agent_decision_round_id
        assert resumed.state.action_state == "FREE_SELECTING"
        assert new_round_id is not None and new_round_id != old_round_id
        assert resumed.state.agent_decisions == (AgentDecisionState(agent_id, "AFFIRMATIVE", 1),)

        with pytest.raises(MatchDomainError, match="stale_callback"):
            await actor.submit(
                MatchCommand(
                    type="free.agent_decision_result",
                    message_id="late-old-decision",
                    payload={
                        "action_key": action.action_key,
                        "agent_profile_id": str(agent_id),
                        "decision_round_id": str(old_round_id),
                        "should_speak": True,
                        "willingness": 1.0,
                    },
                )
            )
        assert actor.state.agent_decision_round_id == new_round_id
        assert actor.state.agent_decisions[0].status == "DECIDING"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_free_debate_does_not_start_a_sub_three_second_final_turn() -> None:
    negative_agent = uuid4()
    speech_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=0,
        action_kind="FREE_DEBATE",
        duration_seconds=60,
        participants=(),
        free_max_speech_seconds=20,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="AGENT_FINALIZING",
            actions=(action,),
            current_speech_id=speech_id,
            current_agent_profile_id=negative_agent,
            current_speaker_side="NEGATIVE",
            current_speaker_seat_no=1,
            speech_remaining_ms=5_000,
            free_holder_side="NEGATIVE",
            free_affirmative_remaining_ms=1_800,
            free_negative_remaining_ms=5_000,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        result = await actor.submit(
            MatchCommand(
                type="agent.finalized",
                message_id="free-short-final-turn",
                payload={
                    "speech_id": str(speech_id),
                    "agent_profile_id": str(negative_agent),
                    "audio_duration_ms": 5_000,
                },
            )
        )

        assert result.state.status == "FINISHED"
        assert result.state.action_state == "MATCH_FINISHED"
        assert result.state.free_affirmative_remaining_ms == 0
        assert result.state.free_negative_remaining_ms == 0
        assert [event.type for event in result.events] == ["agent.finalized", "match.finished"]
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_actor_deadline_finishes_speech_and_termination_is_authorized() -> None:
    user_id = uuid4()
    actions = compile_linear_actions(
        {
            "stages": [
                {
                    "position": 1,
                    "stage_kind": "FIXED_SPEECH",
                    "actions": [
                        {"position": 1, "side": "AFFIRMATIVE", "seat_no": 1, "duration_seconds": 1}
                    ],
                }
            ]
        },
        {("AFFIRMATIVE", 1): user_id},
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="START_PENDING_RUNTIME",
            action_state="NOT_STARTED",
            actions=actions,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        await actor.submit(MatchCommand(type="runtime.start", message_id="d1"))
        await actor.submit(MatchCommand(type="countdown.elapsed", message_id="d2"))
        await actor.submit(
            MatchCommand(type="speech.start", message_id="d3", actor_user_id=user_id)
        )
        deadline = await actor.submit(MatchCommand(type="speech.deadline", message_id="d4"))
        assert deadline.events[0].payload["reason"] == "TIME_LIMIT"
        assert deadline.state.action_state == "SPEECH_FINALIZING"
        speech_id = deadline.state.current_speech_id
        assert speech_id is not None
        finalized = await actor.submit(
            MatchCommand(
                type="asr.finalized",
                message_id="d5",
                payload={"speech_id": str(speech_id), "reason": "TIME_LIMIT"},
            )
        )
        assert finalized.state.status == "FINISHED"
        with pytest.raises(MatchDomainError, match="match_not_running"):
            await actor.submit(
                MatchCommand(type="match.terminate", message_id="d6", payload={"privileged": True})
            )
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_failed_commit_does_not_advance_authoritative_state() -> None:
    user_id = uuid4()
    actions = compile_linear_actions(
        {
            "stages": [
                {
                    "position": 1,
                    "stage_kind": "FIXED_SPEECH",
                    "actions": [
                        {"position": 1, "side": "AFFIRMATIVE", "seat_no": 1, "duration_seconds": 1}
                    ],
                }
            ]
        },
        {("AFFIRMATIVE", 1): user_id},
    )

    async def fail_commit(*_: object) -> None:
        raise RuntimeError("database unavailable")

    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="START_PENDING_RUNTIME",
            action_state="NOT_STARTED",
            actions=actions,
        ),
        commit=fail_commit,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        with pytest.raises(RuntimeError, match="database unavailable"):
            await actor.submit(MatchCommand(type="runtime.start", message_id="commit-fail"))
        assert actor.state.status == "START_PENDING_RUNTIME"
        assert actor.state.sequence == 0
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_system_recovery_stops_progress_but_still_allows_privileged_termination() -> None:
    user_id = uuid4()
    actions = compile_linear_actions(
        {
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
            ]
        },
        {("AFFIRMATIVE", 1): user_id},
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HUMAN_SPEAKING",
            actions=actions,
            current_speaker_user_id=user_id,
            current_speech_id=uuid4(),
        )
    )
    await actor.start()
    try:
        recovered = await actor.submit(MatchCommand(type="system.recover", message_id="recovery-1"))
        assert recovered.state.status == "SYSTEM_RECOVERY"
        assert recovered.state.action_state == "RECOVERY_REQUIRED"
        assert recovered.state.current_speech_id is None
        assert recovered.state.speech_remaining_ms == 30_000
        with pytest.raises(MatchDomainError, match="match_not_running"):
            await actor.submit(
                MatchCommand(type="speech.start", message_id="recovery-2", actor_user_id=user_id)
            )
        terminated = await actor.submit(
            MatchCommand(
                type="match.terminate",
                message_id="recovery-3",
                payload={"privileged": True},
            )
        )
        assert terminated.state.status == "TERMINATED"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_system_recovery_restarts_an_agent_action_instead_of_waiting_for_a_human() -> None:
    agent_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="AGENT_SPEECH",
        duration_seconds=30,
        side="NEGATIVE",
        seat_no=1,
        speaker_kind="AGENT",
        agent_profile_id=agent_id,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HUMAN_READY_TO_START",
            actions=(action,),
            current_agent_profile_id=agent_id,
            current_speaker_side="NEGATIVE",
            current_speaker_seat_no=1,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        recovered = await actor.submit(
            MatchCommand(type="system.recover", message_id="agent-recovery-1")
        )
        assert recovered.state.paused_from_action_state == "AGENT_PREPARING"
        countdown = await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="agent-recovery-2",
                payload={"privileged": True, "reasons": []},
            )
        )
        assert countdown.state.action_state == "RESUME_COUNTDOWN"
        resumed = await actor.submit(
            MatchCommand(type="resume.elapsed", message_id="agent-recovery-3")
        )
        assert resumed.state.status == "RUNNING"
        assert resumed.state.action_state == "AGENT_PREPARING"
        assert [event.type for event in resumed.events] == ["match.resumed", "agent.preparing"]
        assert resumed.events[1].payload["agent_profile_id"] == str(agent_id)
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_pause_freezes_speech_and_resume_checks_then_restarts_human_turn() -> None:
    user_id = uuid4()
    speech_id = uuid4()
    actions = compile_linear_actions(
        {
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
            ]
        },
        {("AFFIRMATIVE", 1): user_id},
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HUMAN_SPEAKING",
            actions=actions,
            current_speaker_user_id=user_id,
            current_speaker_side="AFFIRMATIVE",
            current_speaker_seat_no=1,
            current_speech_id=speech_id,
            speech_deadline_mono=110.0,
            speech_remaining_ms=30_000,
        ),
        clock=lambda: 100.0,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        paused = await actor.submit(
            MatchCommand(
                type="match.pause",
                message_id="p1",
                actor_user_id=user_id,
                payload={"authorized": True},
            )
        )
        assert paused.state.status == "PAUSED"
        assert paused.state.speech_remaining_ms == 10_000
        rejected = await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="p2",
                actor_user_id=user_id,
                payload={"authorized": True, "reasons": ["辩手设备不可用"]},
            )
        )
        assert rejected.events[0].type == "match.resume_check_failed"
        countdown = await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="p3",
                actor_user_id=user_id,
                payload={"authorized": True, "reasons": []},
            )
        )
        assert countdown.state.action_state == "RESUME_COUNTDOWN"
        resumed = await actor.submit(MatchCommand(type="resume.elapsed", message_id="p4"))
        assert resumed.state.status == "RUNNING"
        assert resumed.state.action_state == "HUMAN_READY_TO_START"
        assert resumed.state.current_speech_id == speech_id
        assert resumed.state.speech_remaining_ms == 10_000
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_error_state_still_allows_privileged_termination() -> None:
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="ERROR",
            action_state="RECOVERY_REQUIRED",
            actions=(),
        )
    )
    await actor.start()
    try:
        result = await actor.submit(
            MatchCommand(
                type="match.terminate",
                message_id="error-terminate",
                payload={"privileged": True},
            )
        )
        assert result.state.status == "TERMINATED"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_offline_grace_can_recover_or_pause_after_expiry() -> None:
    user_id = uuid4()
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(),
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        offline = await actor.submit(
            MatchCommand(type="member.offline", message_id="o1", actor_user_id=user_id)
        )
        assert offline.state.offline_user_id == user_id
        online = await actor.submit(
            MatchCommand(type="member.online", message_id="o2", actor_user_id=user_id)
        )
        assert online.state.offline_user_id is None
        ignored = await actor.submit(MatchCommand(type="offline.expired", message_id="o3"))
        assert ignored.state.status == "RUNNING"
        await actor.submit(
            MatchCommand(type="member.offline", message_id="o4", actor_user_id=user_id)
        )
        paused = await actor.submit(MatchCommand(type="offline.expired", message_id="o5"))
        assert paused.state.status == "PAUSED"
        assert paused.events[0].payload["reason"] == "PLAYER_OFFLINE_TIMEOUT"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_offline_grace_is_independent_from_speech_timer_and_other_users() -> None:
    speaker_id = uuid4()
    other_id = uuid4()
    now = 100.0
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HUMAN_SPEAKING",
            actions=(),
            current_speaker_user_id=speaker_id,
            current_speech_id=uuid4(),
            speech_deadline_mono=130.0,
            speech_remaining_ms=30_000,
        ),
        clock=lambda: now,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(type="member.offline", message_id="other-offline", actor_user_id=other_id)
        )
        assert actor.state.speech_deadline_mono == 130.0
        assert other_id in actor._offline_timers

        await actor.submit(
            MatchCommand(
                type="member.offline",
                message_id="speaker-offline",
                actor_user_id=speaker_id,
            )
        )
        assert actor.state.speech_deadline_mono is None
        assert actor.state.speech_remaining_ms == 30_000
        assert set(actor._offline_timers) == {speaker_id, other_id}

        await actor.submit(
            MatchCommand(
                type="member.online",
                message_id="speaker-online",
                actor_user_id=speaker_id,
            )
        )
        assert actor.state.speech_deadline_mono == 130.0
        assert speaker_id not in actor._offline_timers
        assert other_id in actor._offline_timers

        ignored = await actor.submit(
            MatchCommand(
                type="offline.expired",
                message_id="stale-speaker-expiry",
                payload={"user_id": str(speaker_id)},
            )
        )
        assert ignored.state.status == "RUNNING"
        assert ignored.events == ()
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_stale_disconnect_after_reconnect_cannot_mark_user_offline() -> None:
    user_id = uuid4()
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(),
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(
                type="member.online",
                message_id="reconnect-online",
                actor_user_id=user_id,
                payload={"connection_epoch": 2},
            )
        )
        stale = await actor.submit(
            MatchCommand(
                type="member.offline",
                message_id="old-connection-close",
                actor_user_id=user_id,
                payload={"connection_epoch": 1},
            )
        )
        assert stale.events == ()
        assert actor.state.offline_user_id is None
        assert user_id not in actor._offline_users
        assert user_id not in actor._offline_timers
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_duplicate_disconnect_does_not_restart_offline_grace() -> None:
    user_id = uuid4()
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(), status="RUNNING", action_state="FREE_SELECTING", actions=()
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        first = await actor.submit(
            MatchCommand(
                type="member.offline",
                message_id="first-offline",
                actor_user_id=user_id,
                payload={"connection_epoch": 4, "offline_since_ms": 1},
            )
        )
        duplicate = await actor.submit(
            MatchCommand(
                type="member.offline",
                message_id="duplicate-offline",
                actor_user_id=user_id,
                payload={"connection_epoch": 4, "offline_since_ms": 2},
            )
        )
        assert first.events
        assert duplicate.events == ()
        assert dict(actor.state.offline_since_ms)[user_id] == 1
        assert len(actor._offline_timers) == 1
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_resume_clears_stale_system_error_and_offline_marker() -> None:
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="ERROR",
            action_state="RECOVERY_REQUIRED",
            actions=(),
            error_code="tts_stream_interrupted",
            offline_user_id=uuid4(),
            paused_from_status="RUNNING",
            paused_from_action_state="HUMAN_READY_TO_START",
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        countdown = await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="clear-error-resume",
                payload={"privileged": True, "reasons": []},
            )
        )
        assert countdown.state.status == "PAUSED"
        assert countdown.state.action_state == "RESUME_COUNTDOWN"
        assert countdown.state.error_code is None
        assert countdown.state.offline_user_id is None
    finally:
        await actor.close()

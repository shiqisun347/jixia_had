from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from jx_core.matches.domain import (
    AgentDecisionState,
    DebateParticipant,
    MatchAction,
    MatchActor,
    MatchCommand,
    MatchDomainError,
    MatchEvent,
    MatchRuntimeState,
    MatchRuntimeView,
    compile_linear_actions,
)
from jx_core.matches.routes import _event_response, _snapshot_response, match_page_permissions


def test_match_page_permissions_grant_global_admin_control() -> None:
    organizer = uuid4()
    admin_user = uuid4()
    assert match_page_permissions(
        actor_user_id=admin_user,
        organizer_user_id=organizer,
        member_role="DEBATER",
        actor_role="ADMIN",
    ) == (True, True)
    assert match_page_permissions(
        actor_user_id=admin_user,
        organizer_user_id=organizer,
        member_role="SPECTATOR",
        actor_role="ADMIN",
    ) == (True, True)
    assert match_page_permissions(
        actor_user_id=organizer,
        organizer_user_id=organizer,
        member_role="DEBATER",
    ) == (True, True)
    assert match_page_permissions(
        actor_user_id=admin_user,
        organizer_user_id=organizer,
        member_role="SPECTATOR",
        experiment_controller=True,
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
        experiment_mode=True,
        opportunity_id=uuid4(),
        opportunity_generation=3,
        selection_phase="COMPETING",
        agent_effective_status="RAISE",
        selection_remaining_ms=2_000,
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
    assert teammate_view.experiment_team_state is not None
    assert teammate_view.experiment_team_state.agent_status == "RAISE"

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
    assert opponent_view.experiment_team_state is None
    assert "experiment_team_state" not in opponent_view.model_dump(exclude_none=True)

    admin_view = _snapshot_response(
        view,
        uuid4(),
        viewer_user_id=uuid4(),
        member_role="SPECTATOR",
        admin_control=True,
    )
    assert admin_view.experiment_team_state is not None
    assert admin_view.hand_queue == [teammate]


@pytest.mark.parametrize(
    "event_type",
    [
        "hand.raised",
        "hand.cancelled",
        "agent.decision_started",
        "agent.decision_progress",
        "agent.text_delta",
        "free.queue_reordered",
        "free.opportunity_opened",
        "free.opportunity_invalidated",
        "free.human_wait_started",
    ],
)
def test_private_free_debate_events_are_masked_for_other_side(event_type: str) -> None:
    event = MatchEvent(
        type=event_type,
        match_id=uuid4(),
        sequence=2,
        server_time_ms=123,
        payload={"side": "AFFIRMATIVE", "secret": "private"},
    )

    masked = _event_response(event, can_view_team=False)

    assert masked.type == "match.updated"
    assert masked.payload == {}


@pytest.mark.parametrize(
    ("event_type", "payload", "expected"),
    [
        (
            "free.selection_locked",
            {
                "opportunity_id": "private-opportunity",
                "decision_round_id": "private-round",
                "speaker_kind": "AGENT",
                "agent_profile_id": "public-agent",
                "seat_no": 2,
            },
            {"speaker_kind": "AGENT", "agent_profile_id": "public-agent", "seat_no": 2},
        ),
        (
            "speech.started",
            {"speech_id": "public-speech", "opportunity_id": "private-opportunity"},
            {"speech_id": "public-speech"},
        ),
        (
            "agent.playback_started",
            {
                "speech_id": "public-speech",
                "generation_id": "private-generation",
                "opportunity_id": "private-opportunity",
                "agent_profile_id": "public-agent",
            },
            {"speech_id": "public-speech", "agent_profile_id": "public-agent"},
        ),
    ],
)
def test_public_free_debate_events_strip_private_identity_fields(
    event_type: str, payload: dict[str, object], expected: dict[str, object]
) -> None:
    event = MatchEvent(
        type=event_type,
        match_id=uuid4(),
        sequence=2,
        server_time_ms=123,
        payload=payload,
    )

    projected = _event_response(event, can_view_team=False)

    assert projected.type == event_type
    assert projected.payload == expected


@pytest.mark.parametrize(
    "event_type",
    ["agent.retrying", "match.paused"],
)
def test_public_events_defensively_strip_all_private_runtime_ids(event_type: str) -> None:
    event = MatchEvent(
        type=event_type,
        match_id=uuid4(),
        sequence=3,
        server_time_ms=456,
        payload={
            "speech_id": "public-speech",
            "reason": "public-reason",
            "opportunity_id": "private-opportunity",
            "opportunity_generation": 4,
            "decision_round_id": "private-round",
            "generation_id": "private-generation",
        },
    )

    projected = _event_response(event, can_view_team=False)

    assert projected.type == event_type
    assert projected.payload == {
        "speech_id": "public-speech",
        "reason": "public-reason",
    }


@pytest.mark.parametrize("event_type", ["speech.finished", "agent.finalized"])
def test_public_completion_events_hide_drafts_and_storage_paths(event_type: str) -> None:
    event = MatchEvent(
        type=event_type,
        match_id=uuid4(),
        sequence=4,
        server_time_ms=789,
        payload={
            "speech_id": "public-speech",
            "final_text": "已经实际播放的正文",
            "llm_draft_text": "尚未完整播放的草稿",
            "audio_storage_path": "/internal/match/audio.ogg",
            "audio_duration_ms": 12_000,
            "audio_truncated": True,
            "generation_id": "private-generation",
            "opportunity_id": "private-opportunity",
        },
    )

    projected = _event_response(event, can_view_team=False)

    assert projected.payload == {
        "speech_id": "public-speech",
        "final_text": "已经实际播放的正文",
        "audio_duration_ms": 12_000,
        "audio_truncated": True,
    }


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
        "host_audio": [
            {
                "segment_key": "stage-1-start",
                "storage_path": "rules/host.opus",
                "duration_ms": 2_000,
            }
        ],
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
        assert ready.state.host_audio_remaining_ms == 2_000
        assert actor._timer_command_type == "host.elapsed"
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
        assert announced.state.host_audio_remaining_ms is None
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
        assert result.events[-1].type == "agent.preparing"
        assert result.events[-1].payload["side"] == "NEGATIVE"
        assert result.events[-1].payload["seat_no"] == 2

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


@pytest.mark.asyncio
async def test_host_elapsed_advances_without_browser_command() -> None:
    user_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=10,
        speaker_user_id=user_id,
        host_audio_path="rules/host.ogg",
        host_audio_duration_ms=4_000,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="NOT_STARTED",
            actions=(action,),
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        entered = actor._enter_current_action()
        assert entered[0].type == "host.play"
        elapsed = await actor.submit(MatchCommand(type="host.elapsed", message_id="host-elapsed"))
        assert elapsed.state.action_state == "HUMAN_READY_TO_START"
        assert elapsed.state.host_audio_remaining_ms is None
        assert elapsed.events[-1].type == "speech.ready"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_human_start_window_pauses_at_60_seconds_but_not_at_59_9() -> None:
    user_id = uuid4()
    now = [100.0]
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        speaker_user_id=user_id,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(), status="RUNNING", action_state="NOT_STARTED", actions=(action,)
        ),
        clock=lambda: now[0],
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        actor._enter_current_action()
        assert actor.state.action_state == "HUMAN_READY_TO_START"
        assert actor.state.human_start_deadline_mono == 160.0
        assert actor._timer_command_type == "human.start_timeout"

        now[0] = 159.9
        early = await actor.submit(
            MatchCommand(type="human.start_timeout", message_id="human-start-early")
        )
        assert early.events == ()
        assert early.state.status == "RUNNING"
        assert early.state.speech_remaining_ms == 30_000
        assert actor._timer_command_type == "human.start_timeout"

        now[0] = 160.0
        expired = await actor.submit(
            MatchCommand(type="human.start_timeout", message_id="human-start-expired")
        )
        assert expired.state.status == "PAUSED"
        assert expired.state.action_state == "RECOVERY_REQUIRED"
        assert expired.state.paused_from_action_state == "HUMAN_READY_TO_START"
        assert expired.state.speech_remaining_ms == 30_000
        assert expired.state.human_start_deadline_mono is None
        assert expired.state.error_code == "HUMAN_START_TIMEOUT"
        assert expired.events[0].type == "match.paused"
        assert expired.events[0].payload["reason"] == "HUMAN_START_TIMEOUT"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_speech_start_replaces_ready_timeout_and_queued_expiry_is_ignored() -> None:
    user_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        speaker_user_id=user_id,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(), status="RUNNING", action_state="NOT_STARTED", actions=(action,)
        ),
        clock=lambda: 100.0,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        actor._enter_current_action()
        started = await actor.submit(
            MatchCommand(type="speech.start", message_id="human-start", actor_user_id=user_id)
        )
        assert started.state.action_state == "HUMAN_SPEAKING"
        assert started.state.human_start_deadline_mono is None
        assert actor._timer_command_type == "speech.deadline"

        stale = await actor.submit(
            MatchCommand(type="human.start_timeout", message_id="queued-ready-timeout")
        )
        assert stale.events == ()
        assert stale.state.action_state == "HUMAN_SPEAKING"
        assert actor._timer_command_type == "speech.deadline"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_free_human_selection_and_reset_schedule_start_timeout() -> None:
    user_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="FREE_DEBATE",
        duration_seconds=60,
        participants=(DebateParticipant(side="AFFIRMATIVE", seat_no=1, user_id=user_id),),
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
            hand_queue=(user_id,),
            hand_window_open=True,
        ),
        clock=lambda: 100.0,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        selected = await actor.submit(
            MatchCommand(type="hand.window_closed", message_id="select-free-human")
        )
        assert selected.state.action_state == "HUMAN_READY_TO_START"
        assert selected.state.human_start_deadline_mono == 160.0
        assert actor._timer_command_type == "human.start_timeout"

        await actor.submit(
            MatchCommand(type="speech.start", message_id="start-free-human", actor_user_id=user_id)
        )
        reset = await actor.submit(
            MatchCommand(type="speech.reset", message_id="reset-free-human", actor_user_id=user_id)
        )
        assert reset.state.action_state == "HUMAN_READY_TO_START"
        assert reset.state.human_start_deadline_mono == 160.0
        assert actor._timer_command_type == "human.start_timeout"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_resume_restarts_full_human_start_window() -> None:
    user_id = uuid4()
    now = [100.0]
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        speaker_user_id=user_id,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="PAUSED",
            action_state="RECOVERY_REQUIRED",
            actions=(action,),
            current_speaker_user_id=user_id,
            speech_remaining_ms=12_000,
            paused_from_status="RUNNING",
            paused_from_action_state="HUMAN_READY_TO_START",
        ),
        clock=lambda: now[0],
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="resume-human-ready",
                payload={"privileged": True, "reasons": []},
            )
        )
        now[0] = 103.0
        resumed = await actor.submit(
            MatchCommand(type="resume.elapsed", message_id="resume-human-ready-elapsed")
        )
        assert resumed.state.status == "RUNNING"
        assert resumed.state.action_state == "HUMAN_READY_TO_START"
        assert resumed.state.speech_remaining_ms == 12_000
        assert resumed.state.human_start_deadline_mono == 163.0
        assert actor._timer_command_type == "human.start_timeout"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_reconnect_does_not_extend_human_start_window() -> None:
    user_id = uuid4()
    now = [100.0]
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        speaker_user_id=user_id,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(), status="RUNNING", action_state="NOT_STARTED", actions=(action,)
        ),
        clock=lambda: now[0],
        wall_clock=lambda: now[0],
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        actor._enter_current_action()
        original_deadline = actor.state.human_start_deadline_mono
        now[0] = 120.0
        await actor.submit(
            MatchCommand(
                type="member.offline",
                message_id="ready-offline",
                actor_user_id=user_id,
                payload={"connection_epoch": 1, "offline_since_ms": 120_000},
            )
        )
        await actor.submit(
            MatchCommand(
                type="member.online",
                message_id="ready-online",
                actor_user_id=user_id,
                payload={"connection_epoch": 2},
            )
        )
        assert actor.state.status == "RUNNING"
        assert actor.state.human_start_deadline_mono == original_deadline
        assert actor._timer_command_type == "human.start_timeout"
    finally:
        await actor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command_type", "payload"),
    [
        ("match.pause", {"authorized": True}),
        ("match.terminate", {"privileged": True}),
        ("system.recover", {}),
        ("system.error", {"error_code": "test_failure"}),
    ],
)
async def test_interruptions_cancel_human_start_timeout(
    command_type: str, payload: dict[str, object]
) -> None:
    user_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        speaker_user_id=user_id,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(), status="RUNNING", action_state="NOT_STARTED", actions=(action,)
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        actor._enter_current_action()
        result = await actor.submit(
            MatchCommand(
                type=command_type,
                message_id=f"cancel-ready-{command_type}",
                payload=payload,
            )
        )
        assert result.state.human_start_deadline_mono is None
        assert actor._timer_command_type is None
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_host_pause_and_resume_freezes_authoritative_remaining_time() -> None:
    clock = [100.0]
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HOST_AUDIO",
        duration_seconds=0,
        host_audio_path="rules/host.ogg",
        host_audio_duration_ms=10_000,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(), status="RUNNING", action_state="NOT_STARTED", actions=(action,)
        ),
        clock=lambda: clock[0],
        wall_clock=lambda: clock[0],
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        actor._enter_current_action()
        clock[0] = 103.0
        paused = await actor.submit(
            MatchCommand(type="match.pause", message_id="host-pause", payload={"authorized": True})
        )
        assert paused.state.host_audio_remaining_ms == 7_000
        await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="host-resume",
                payload={"privileged": True},
            )
        )
        resumed = await actor.submit(
            MatchCommand(type="resume.elapsed", message_id="host-resume-elapsed")
        )
        assert resumed.state.action_state == "HOST_ANNOUNCING"
        assert resumed.state.host_audio_deadline_mono == 110.0
    finally:
        await actor.close()


def test_linear_compiler_accepts_free_debate_and_rejects_missing_side() -> None:
    """Frozen legacy rules retain an explicit negative starting side."""
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
async def test_two_agent_theory_speeches_advance_into_free_debate() -> None:
    affirmative_agent = uuid4()
    negative_agent = uuid4()
    first_speech = uuid4()
    second_speech = uuid4()
    first_generation = uuid4()
    second_generation = uuid4()
    free_action = MatchAction(
        stage_position=3,
        action_position=1,
        action_kind="FREE_DEBATE",
        duration_seconds=360,
        participants=(),
        free_starting_side="AFFIRMATIVE",
        free_max_speech_seconds=60,
    )
    actions = (
        MatchAction(
            stage_position=1,
            action_position=1,
            action_kind="AGENT_SPEECH",
            duration_seconds=90,
            side="AFFIRMATIVE",
            seat_no=1,
            speaker_kind="AGENT",
            agent_profile_id=affirmative_agent,
        ),
        MatchAction(
            stage_position=2,
            action_position=1,
            action_kind="AGENT_SPEECH",
            duration_seconds=90,
            side="NEGATIVE",
            seat_no=1,
            speaker_kind="AGENT",
            agent_profile_id=negative_agent,
        ),
        free_action,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="AGENT_PREPARING",
            actions=actions,
            current_action_index=0,
            current_agent_profile_id=affirmative_agent,
            current_speaker_side="AFFIRMATIVE",
            current_speaker_seat_no=1,
            speech_remaining_ms=90_000,
            formal_4v4=True,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        first_started = await actor.submit(
            MatchCommand(
                type="agent.playback_started",
                message_id="theory-affirmative-start",
                payload={
                    "speech_id": str(first_speech),
                    "generation_id": str(first_generation),
                    "agent_profile_id": str(affirmative_agent),
                },
            )
        )
        assert first_started.state.action_state == "AGENT_SPEAKING"
        await actor.submit(
            MatchCommand(
                type="agent.playback_finished",
                message_id="theory-affirmative-finish",
                payload={"speech_id": str(first_speech)},
            )
        )
        first_finalized = await actor.submit(
            MatchCommand(
                type="agent.finalized",
                message_id="theory-affirmative-finalized",
                payload={
                    "speech_id": str(first_speech),
                    "generation_id": str(first_generation),
                    "audio_duration_ms": 90_000,
                },
            )
        )
        assert first_finalized.state.action_state == "AGENT_PREPARING"
        assert first_finalized.state.current_action_index == 1
        assert first_finalized.state.current_agent_profile_id == negative_agent

        second_started = await actor.submit(
            MatchCommand(
                type="agent.playback_started",
                message_id="theory-negative-start",
                payload={
                    "speech_id": str(second_speech),
                    "generation_id": str(second_generation),
                    "agent_profile_id": str(negative_agent),
                },
            )
        )
        assert second_started.state.action_state == "AGENT_SPEAKING"
        await actor.submit(
            MatchCommand(
                type="agent.playback_finished",
                message_id="theory-negative-finish",
                payload={"speech_id": str(second_speech)},
            )
        )
        second_finalized = await actor.submit(
            MatchCommand(
                type="agent.finalized",
                message_id="theory-negative-finalized",
                payload={
                    "speech_id": str(second_speech),
                    "generation_id": str(second_generation),
                    "audio_duration_ms": 90_000,
                },
            )
        )
        assert second_finalized.state.current_action_index == 2
        assert second_finalized.state.current_action == free_action
        assert second_finalized.state.action_state == "FREE_SELECTING"
        assert any(event.type == "free_debate.started" for event in second_finalized.events)
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_two_agent_theories_and_free_debate_complete_the_match() -> None:
    """Exercise the full formal flow past the production transition boundary."""
    affirmative_agent = uuid4()
    negative_agent = uuid4()
    free_action = MatchAction(
        stage_position=3,
        action_position=1,
        action_kind="FREE_DEBATE",
        duration_seconds=5,
        participants=(
            DebateParticipant(side="AFFIRMATIVE", seat_no=1, agent_profile_id=affirmative_agent),
            DebateParticipant(side="NEGATIVE", seat_no=1, agent_profile_id=negative_agent),
        ),
        free_starting_side="AFFIRMATIVE",
        free_max_speech_seconds=5,
    )
    actions = (
        MatchAction(
            stage_position=1,
            action_position=1,
            action_kind="AGENT_SPEECH",
            duration_seconds=5,
            side="AFFIRMATIVE",
            seat_no=1,
            speaker_kind="AGENT",
            agent_profile_id=affirmative_agent,
        ),
        MatchAction(
            stage_position=2,
            action_position=1,
            action_kind="AGENT_SPEECH",
            duration_seconds=5,
            side="NEGATIVE",
            seat_no=1,
            speaker_kind="AGENT",
            agent_profile_id=negative_agent,
        ),
        free_action,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="AGENT_PREPARING",
            actions=actions,
            current_action_index=0,
            current_agent_profile_id=affirmative_agent,
            current_speaker_side="AFFIRMATIVE",
            current_speaker_seat_no=1,
            speech_remaining_ms=5_000,
            formal_4v4=True,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )

    async def finish_agent_turn(
        *, agent_id: UUID, message_prefix: str, audio_duration_ms: int = 5_000
    ) -> tuple[str, ...]:
        speech_id = uuid4()
        generation_id = uuid4()
        await actor.submit(
            MatchCommand(
                type="agent.playback_started",
                message_id=f"{message_prefix}-start",
                payload={
                    "speech_id": str(speech_id),
                    "generation_id": str(generation_id),
                    "agent_profile_id": str(agent_id),
                },
            )
        )
        await actor.submit(
            MatchCommand(
                type="agent.playback_finished",
                message_id=f"{message_prefix}-finish",
                payload={"speech_id": str(speech_id)},
            )
        )
        finalized = await actor.submit(
            MatchCommand(
                type="agent.finalized",
                message_id=f"{message_prefix}-finalized",
                payload={
                    "speech_id": str(speech_id),
                    "generation_id": str(generation_id),
                    "audio_duration_ms": audio_duration_ms,
                },
            )
        )
        return tuple(event.type for event in finalized.events)

    async def choose_current_agent(*, agent_id: UUID, message_prefix: str) -> None:
        action = actor.state.current_action
        assert action == free_action
        round_id = actor.state.agent_decision_round_id
        if round_id is None:
            opportunity_id = actor.state.opportunity_id
            assert opportunity_id is not None
            decision_started = await actor.submit(
                MatchCommand(
                    type="free.agent_decision_start",
                    message_id=f"{message_prefix}-decision-start",
                    payload={
                        "opportunity_id": str(opportunity_id),
                        "opportunity_generation": actor.state.opportunity_generation,
                        "trigger_kind": "AGENT_SPEECH",
                    },
                )
            )
            round_id = decision_started.state.agent_decision_round_id
        assert round_id is not None
        decided = await actor.submit(
            MatchCommand(
                type="free.agent_decision_result",
                message_id=f"{message_prefix}-decision",
                payload={
                    "action_key": action.action_key,
                    "agent_profile_id": str(agent_id),
                    "decision_round_id": str(round_id),
                    "should_speak": True,
                },
            )
        )
        assert decided.state.agent_effective_status == "RAISE"
        selected = await actor.submit(
            MatchCommand(type="hand.window_closed", message_id=f"{message_prefix}-select")
        )
        assert selected.state.action_state == "AGENT_PREPARING"
        assert selected.state.current_agent_profile_id == agent_id

    await actor.start()
    try:
        await finish_agent_turn(agent_id=affirmative_agent, message_prefix="theory-affirmative")
        assert actor.state.current_action_index == 1
        assert actor.state.current_agent_profile_id == negative_agent

        transition_events = await finish_agent_turn(
            agent_id=negative_agent, message_prefix="theory-negative"
        )
        assert actor.state.current_action_index == 2
        assert actor.state.action_state == "FREE_SELECTING"
        assert "free_debate.started" in transition_events
        assert "agent.decision_started" in transition_events

        assert actor.state.free_holder_side == "AFFIRMATIVE"
        await choose_current_agent(agent_id=affirmative_agent, message_prefix="free-affirmative")
        first_free_events = await finish_agent_turn(
            agent_id=affirmative_agent, message_prefix="free-affirmative"
        )
        assert actor.state.status == "RUNNING"
        assert actor.state.action_state == "FREE_SELECTING"
        assert actor.state.free_affirmative_remaining_ms == 0
        assert actor.state.free_holder_side == "NEGATIVE"
        assert first_free_events == ("agent.finalized", "hand.window_opened")

        await choose_current_agent(agent_id=negative_agent, message_prefix="free-negative")
        final_events = await finish_agent_turn(
            agent_id=negative_agent, message_prefix="free-negative"
        )
        assert actor.state.status == "FINISHED"
        assert actor.state.action_state == "MATCH_FINISHED"
        assert actor.state.free_affirmative_remaining_ms == 0
        assert actor.state.free_negative_remaining_ms == 0
        assert final_events == ("agent.finalized", "match.finished")
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_formal_4v4_mixed_human_agent_flow_pauses_resumes_and_finishes() -> None:
    affirmative_humans = tuple(uuid4() for _ in range(3))
    negative_humans = tuple(uuid4() for _ in range(3))
    affirmative_agent = uuid4()
    negative_agent = uuid4()
    free_action = MatchAction(
        stage_position=3,
        action_position=1,
        action_kind="FREE_DEBATE",
        duration_seconds=5,
        participants=tuple(
            [
                *(
                    DebateParticipant(side="AFFIRMATIVE", seat_no=index, user_id=user_id)
                    for index, user_id in enumerate(affirmative_humans, start=1)
                ),
                DebateParticipant(
                    side="AFFIRMATIVE", seat_no=4, agent_profile_id=affirmative_agent
                ),
                *(
                    DebateParticipant(side="NEGATIVE", seat_no=index, user_id=user_id)
                    for index, user_id in enumerate(negative_humans, start=1)
                ),
                DebateParticipant(side="NEGATIVE", seat_no=4, agent_profile_id=negative_agent),
            ]
        ),
        free_starting_side="NEGATIVE",
        free_max_speech_seconds=5,
    )
    actions = (
        MatchAction(
            stage_position=1,
            action_position=1,
            action_kind="HUMAN_SPEECH",
            duration_seconds=5,
            side="AFFIRMATIVE",
            seat_no=1,
            speaker_user_id=affirmative_humans[0],
        ),
        MatchAction(
            stage_position=2,
            action_position=1,
            action_kind="AGENT_SPEECH",
            duration_seconds=5,
            side="NEGATIVE",
            seat_no=4,
            speaker_kind="AGENT",
            agent_profile_id=negative_agent,
        ),
        free_action,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HUMAN_READY_TO_START",
            actions=actions,
            current_action_index=0,
            current_speaker_user_id=affirmative_humans[0],
            current_speaker_side="AFFIRMATIVE",
            current_speaker_seat_no=1,
            speech_remaining_ms=5_000,
            formal_4v4=True,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )

    async def finish_agent_turn(agent_id: UUID, prefix: str) -> None:
        speech_id = uuid4()
        generation_id = uuid4()
        await actor.submit(
            MatchCommand(
                type="agent.playback_started",
                message_id=f"{prefix}-start",
                payload={
                    "speech_id": str(speech_id),
                    "generation_id": str(generation_id),
                    "agent_profile_id": str(agent_id),
                },
            )
        )
        await actor.submit(
            MatchCommand(
                type="agent.playback_finished",
                message_id=f"{prefix}-finish",
                payload={"speech_id": str(speech_id)},
            )
        )
        await actor.submit(
            MatchCommand(
                type="agent.finalized",
                message_id=f"{prefix}-finalized",
                payload={
                    "speech_id": str(speech_id),
                    "generation_id": str(generation_id),
                    "audio_duration_ms": 5_000,
                },
            )
        )

    await actor.start()
    try:
        first_start = await actor.submit(
            MatchCommand(
                type="speech.start",
                message_id="mixed-theory-human-start",
                actor_user_id=affirmative_humans[0],
            )
        )
        theory_speech_id = first_start.state.current_speech_id
        assert theory_speech_id is not None

        await actor.submit(
            MatchCommand(
                type="match.pause",
                message_id="mixed-theory-pause",
                actor_user_id=affirmative_humans[0],
                payload={"authorized": True},
            )
        )
        await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="mixed-theory-resume",
                actor_user_id=affirmative_humans[0],
                payload={"authorized": True, "reasons": []},
            )
        )
        resumed = await actor.submit(
            MatchCommand(type="resume.elapsed", message_id="mixed-theory-resume-elapsed")
        )
        assert resumed.state.action_state == "HUMAN_READY_TO_START"
        assert resumed.state.current_speech_id == theory_speech_id

        restarted = await actor.submit(
            MatchCommand(
                type="speech.start",
                message_id="mixed-theory-human-restart",
                actor_user_id=affirmative_humans[0],
            )
        )
        assert restarted.state.current_speech_id == theory_speech_id
        await actor.submit(
            MatchCommand(
                type="speech.finish",
                message_id="mixed-theory-human-finish",
                actor_user_id=affirmative_humans[0],
            )
        )
        await actor.submit(
            MatchCommand(
                type="asr.finalized",
                message_id="mixed-theory-human-finalized",
                payload={"speech_id": str(theory_speech_id), "audio_duration_ms": 5_000},
            )
        )
        assert actor.state.action_state == "AGENT_PREPARING"

        await finish_agent_turn(negative_agent, "mixed-theory-agent")
        assert actor.state.action_state == "FREE_SELECTING"
        assert actor.state.free_holder_side == "NEGATIVE"

        await actor.submit(
            MatchCommand(
                type="hand.raise",
                message_id="mixed-free-human-hand",
                actor_user_id=negative_humans[0],
            )
        )
        selected = await actor.submit(
            MatchCommand(type="hand.window_closed", message_id="mixed-free-human-select")
        )
        assert selected.state.action_state == "HUMAN_READY_TO_START"
        assert selected.state.current_speaker_user_id == negative_humans[0]
        human_free_start = await actor.submit(
            MatchCommand(
                type="speech.start",
                message_id="mixed-free-human-start",
                actor_user_id=negative_humans[0],
            )
        )
        free_speech_id = human_free_start.state.current_speech_id
        assert free_speech_id is not None
        await actor.submit(
            MatchCommand(
                type="speech.finish",
                message_id="mixed-free-human-finish",
                actor_user_id=negative_humans[0],
            )
        )
        await actor.submit(
            MatchCommand(
                type="asr.finalized",
                message_id="mixed-free-human-finalized",
                payload={"speech_id": str(free_speech_id), "audio_duration_ms": 5_000},
            )
        )
        assert actor.state.free_negative_remaining_ms == 0
        assert actor.state.free_holder_side == "AFFIRMATIVE"

        round_id = actor.state.agent_decision_round_id
        if round_id is None:
            opportunity_id = actor.state.opportunity_id
            assert opportunity_id is not None
            decision_started = await actor.submit(
                MatchCommand(
                    type="free.agent_decision_start",
                    message_id="mixed-free-agent-decision-start",
                    payload={
                        "opportunity_id": str(opportunity_id),
                        "opportunity_generation": actor.state.opportunity_generation,
                        "trigger_kind": "HUMAN_SPEECH",
                    },
                )
            )
            round_id = decision_started.state.agent_decision_round_id
        assert round_id is not None
        await actor.submit(
            MatchCommand(
                type="free.agent_decision_result",
                message_id="mixed-free-agent-decision",
                payload={
                    "action_key": free_action.action_key,
                    "agent_profile_id": str(affirmative_agent),
                    "decision_round_id": str(round_id),
                    "should_speak": True,
                },
            )
        )
        agent_selected = await actor.submit(
            MatchCommand(type="hand.window_closed", message_id="mixed-free-agent-select")
        )
        assert agent_selected.state.action_state == "AGENT_PREPARING"
        assert agent_selected.state.current_agent_profile_id == affirmative_agent
        await finish_agent_turn(affirmative_agent, "mixed-free-agent")

        assert actor.state.status == "FINISHED"
        assert actor.state.action_state == "MATCH_FINISHED"
        assert actor.state.free_affirmative_remaining_ms == 0
        assert actor.state.free_negative_remaining_ms == 0
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
async def test_interim_text_waits_for_inflight_actor_commit_and_rejects_terminal_state() -> None:
    commit_started = asyncio.Event()
    release_commit = asyncio.Event()
    speech_id = uuid4()

    async def delayed_commit(*_args: object) -> None:
        commit_started.set()
        await release_commit.wait()

    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="AGENT_SPEAKING",
            actions=(),
            current_speech_id=speech_id,
            current_agent_profile_id=uuid4(),
            interim_text="正在播放的临时字幕",
        ),
        commit=delayed_commit,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        command = asyncio.create_task(
            actor.submit(
                MatchCommand(
                    type="match.terminate",
                    message_id="delayed-terminate",
                    payload={"privileged": True},
                )
            )
        )
        await commit_started.wait()
        subtitle = asyncio.create_task(
            actor.set_interim_text("提交期间到达的字幕", speech_id=speech_id)
        )
        await asyncio.sleep(0)
        assert not subtitle.done()

        release_commit.set()
        await command
        assert not await subtitle
        assert actor.state.status == "TERMINATED"
        assert actor.state.interim_text == ""
    finally:
        await actor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action_state",
    ["HUMAN_SPEAKING", "SPEECH_FINALIZING", "AGENT_SPEAKING", "AGENT_FINALIZING"],
)
async def test_interim_text_accepts_only_current_active_speech(action_state: str) -> None:
    speech_id = uuid4()
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state=action_state,
            actions=(),
            current_speech_id=speech_id,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        assert await actor.set_interim_text("当前字幕", speech_id=speech_id)
        assert actor.state.interim_text == "当前字幕"
        assert not await actor.set_interim_text("旧字幕", speech_id=uuid4())
        assert actor.state.interim_text == "当前字幕"
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
async def test_system_recovery_keeps_free_selection_without_resurrecting_stale_speaker() -> None:
    stale_speaker = uuid4()
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
        {("AFFIRMATIVE", 1): stale_speaker, ("NEGATIVE", 1): uuid4()},
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=actions,
            experiment_mode=True,
            free_holder_side="NEGATIVE",
            selection_phase="HUMAN_ONLY_WAIT",
            opportunity_id=uuid4(),
            current_speaker_user_id=stale_speaker,
            current_speaker_side="AFFIRMATIVE",
            current_speaker_seat_no=1,
            speech_remaining_ms=20_000,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        recovered = await actor.submit(
            MatchCommand(type="system.recover", message_id="free-recovery-1")
        )
        assert recovered.state.paused_from_action_state == "FREE_SELECTING"
        assert recovered.state.current_speaker_user_id is None
        assert recovered.state.current_speaker_side is None
        assert recovered.state.current_speaker_seat_no is None
        assert recovered.state.speech_remaining_ms is None

        await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="free-recovery-2",
                payload={"privileged": True, "reasons": []},
            )
        )
        resumed = await actor.submit(
            MatchCommand(type="resume.elapsed", message_id="free-recovery-3")
        )
        assert resumed.state.status == "RUNNING"
        assert resumed.state.action_state == "FREE_SELECTING"
        assert resumed.state.current_speaker_user_id is None
        assert resumed.state.free_holder_side == "NEGATIVE"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_system_recovery_during_start_countdown_enters_current_action_on_resume() -> None:
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
            status="START_COUNTDOWN",
            action_state="NOT_STARTED",
            actions=actions,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        recovered = await actor.submit(
            MatchCommand(type="system.recover", message_id="countdown-recover")
        )
        assert recovered.state.status == "SYSTEM_RECOVERY"
        assert recovered.state.action_state == "RECOVERY_REQUIRED"

        resumed = await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="countdown-resume",
                payload={"privileged": True},
            )
        )
        assert resumed.state.action_state == "RESUME_COUNTDOWN"
        entered = await actor.submit(
            MatchCommand(type="resume.elapsed", message_id="countdown-resume-elapsed")
        )
        assert entered.state.status == "RUNNING"
        assert entered.state.action_state == "HUMAN_READY_TO_START"
        assert entered.state.current_speaker_user_id == user_id
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
async def test_system_recovery_preserves_host_audio_before_a_human_action() -> None:
    user_id = uuid4()
    now = 100.0
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        speaker_user_id=user_id,
        host_audio_path="rules/host.ogg",
        host_audio_duration_ms=10_000,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HOST_ANNOUNCING",
            actions=(action,),
            current_speaker_user_id=None,
            host_audio_remaining_ms=10_000,
            host_audio_deadline_mono=107.0,
        ),
        clock=lambda: now,
        wall_clock=lambda: 1_000.0,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        recovered = await actor.submit(
            MatchCommand(type="system.recover", message_id="host-system-recovery")
        )
        assert recovered.state.status == "SYSTEM_RECOVERY"
        assert recovered.state.paused_from_action_state == "HOST_ANNOUNCING"
        assert recovered.state.host_audio_remaining_ms == 7_000
        assert recovered.state.host_audio_deadline_mono is None

        await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="host-system-resume",
                payload={"privileged": True, "reasons": []},
            )
        )
        resumed = await actor.submit(
            MatchCommand(type="resume.elapsed", message_id="host-system-resume-elapsed")
        )
        assert resumed.state.status == "RUNNING"
        assert resumed.state.action_state == "HOST_ANNOUNCING"
        assert resumed.state.host_audio_deadline_mono == 107.0
        assert resumed.state.host_audio_deadline_ms == 1_007_000
        assert [event.type for event in resumed.events] == ["match.resumed"]
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
        restarted = await actor.submit(
            MatchCommand(type="speech.start", message_id="p5", actor_user_id=user_id)
        )
        assert restarted.state.action_state == "HUMAN_SPEAKING"
        assert restarted.state.current_speech_id == speech_id
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_pause_resume_restarts_preparation_timer() -> None:
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="PREPARATION",
        duration_seconds=30,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="PREPARING",
            actions=(action,),
            speech_remaining_ms=30_000,
            speech_deadline_mono=110.0,
        ),
        clock=lambda: 100.0,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        paused = await actor.submit(
            MatchCommand(
                type="match.pause",
                message_id="preparation-pause",
                payload={"authorized": True},
            )
        )
        assert paused.state.status == "PAUSED"
        assert paused.state.speech_remaining_ms == 10_000

        await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="preparation-resume",
                payload={"privileged": True, "reasons": []},
            )
        )
        resumed = await actor.submit(
            MatchCommand(type="resume.elapsed", message_id="preparation-resume-elapsed")
        )
        assert resumed.state.status == "RUNNING"
        assert resumed.state.action_state == "PREPARING"
        assert resumed.state.speech_deadline_mono == 110.0
        assert actor._timer_command_type == "preparation.elapsed"

        finished = await actor.submit(
            MatchCommand(type="preparation.elapsed", message_id="preparation-finished")
        )
        assert finished.state.status == "FINISHED"
    finally:
        await actor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action_state", ["AGENT_PREPARING", "AGENT_SPEAKING", "AGENT_FINALIZING"])
async def test_pause_resume_restarts_agent_work_with_fresh_speech_identity(
    action_state: str,
) -> None:
    agent_id = uuid4()
    old_speech_id = None if action_state == "AGENT_PREPARING" else uuid4()
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
            action_state=cast(Any, action_state),
            actions=(action,),
            current_speech_id=old_speech_id,
            current_agent_profile_id=agent_id,
            current_speaker_side="NEGATIVE",
            current_speaker_seat_no=1,
            speech_remaining_ms=12_000,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(
                type="match.pause",
                message_id=f"pause-{action_state}",
                payload={"authorized": True},
            )
        )
        await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id=f"resume-{action_state}",
                payload={"privileged": True, "reasons": []},
            )
        )
        resumed = await actor.submit(
            MatchCommand(type="resume.elapsed", message_id=f"resume-elapsed-{action_state}")
        )

        assert resumed.state.status == "RUNNING"
        assert resumed.state.action_state == "AGENT_PREPARING"
        assert resumed.state.current_agent_profile_id == agent_id
        assert resumed.state.current_speech_id is None
        assert [event.type for event in resumed.events] == ["match.resumed", "agent.preparing"]
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_pause_resume_restarts_full_human_only_wait_and_keeps_agent_skipped() -> None:
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="FREE_DEBATE",
        duration_seconds=60,
        participants=(),
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(action,),
            formal_4v4=True,
            free_holder_side="NEGATIVE",
            free_affirmative_remaining_ms=60_000,
            free_negative_remaining_ms=60_000,
            opportunity_id=uuid4(),
            selection_phase="HUMAN_ONLY_WAIT",
            agent_effective_status="SKIP",
            human_wait_deadline_mono=130.0,
            human_wait_remaining_ms=60_000,
            hand_window_open=True,
        ),
        clock=lambda: 100.0,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        paused = await actor.submit(
            MatchCommand(
                type="match.pause",
                message_id="pause-human-only-wait",
                payload={"authorized": True},
            )
        )
        assert paused.state.human_wait_remaining_ms == 30_000

        await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="resume-human-only-wait",
                payload={"privileged": True, "reasons": []},
            )
        )
        resumed = await actor.submit(
            MatchCommand(type="resume.elapsed", message_id="resume-human-only-wait-elapsed")
        )

        assert resumed.state.action_state == "FREE_SELECTING"
        assert resumed.state.selection_phase == "HUMAN_ONLY_WAIT"
        assert resumed.state.agent_effective_status == "SKIP"
        assert resumed.state.human_wait_remaining_ms == 60_000
        assert resumed.state.human_wait_deadline_mono == 160.0
        assert [event.type for event in resumed.events] == [
            "match.resumed",
            "free.human_wait_started",
        ]
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_human_can_raise_after_human_only_wait_recovery() -> None:
    user_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="FREE_DEBATE",
        duration_seconds=60,
        participants=(DebateParticipant(side="NEGATIVE", seat_no=1, user_id=user_id),),
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(action,),
            formal_4v4=True,
            free_holder_side="NEGATIVE",
            free_affirmative_remaining_ms=60_000,
            free_negative_remaining_ms=60_000,
            opportunity_id=uuid4(),
            selection_phase="HUMAN_ONLY_WAIT",
            agent_effective_status="SKIP",
            human_wait_deadline_mono=130.0,
            human_wait_remaining_ms=60_000,
            hand_window_open=True,
        ),
        clock=lambda: 100.0,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        result = await actor.submit(
            MatchCommand(
                type="hand.raise",
                message_id="raise-after-recovery",
                actor_user_id=user_id,
                payload={"connection_epoch": 2},
            )
        )

        assert result.state.action_state == "HUMAN_READY_TO_START"
        assert result.state.current_speaker_user_id == user_id
        assert [event.type for event in result.events] == [
            "hand.raised",
            "free.queue_reordered",
            "free.selection_locked",
            "speech.ready",
        ]
        assert result.events[0].payload["connection_epoch"] == 2
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_resume_at_zero_remaining_rebuilds_immediate_host_timer() -> None:
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HOST_AUDIO",
        duration_seconds=0,
        host_audio_path="rules/host.ogg",
        host_audio_duration_ms=10_000,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="PAUSED",
            action_state="RECOVERY_REQUIRED",
            actions=(action,),
            host_audio_remaining_ms=0,
            paused_from_status="RUNNING",
            paused_from_action_state="HOST_ANNOUNCING",
        ),
        clock=lambda: 100.0,
        wall_clock=lambda: 1_000.0,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="zero-host-resume",
                payload={"privileged": True, "reasons": []},
            )
        )
        resumed = await actor.submit(
            MatchCommand(type="resume.elapsed", message_id="zero-host-resume-elapsed")
        )

        assert resumed.state.status == "RUNNING"
        assert resumed.state.action_state == "HOST_ANNOUNCING"
        assert resumed.state.host_audio_deadline_mono == 100.0
        assert resumed.state.host_audio_deadline_ms == 1_000_000
        assert actor._timer_command_type == "host.elapsed"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_restarted_human_speech_with_zero_time_finalizes_immediately() -> None:
    user_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        speaker_user_id=user_id,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HUMAN_READY_TO_START",
            actions=(action,),
            current_speaker_user_id=user_id,
            speech_remaining_ms=0,
        ),
        clock=lambda: 100.0,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        started = await actor.submit(
            MatchCommand(
                type="speech.start",
                message_id="zero-human-restart",
                actor_user_id=user_id,
            )
        )

        assert started.state.speech_remaining_ms == 0
        assert started.state.action_state == "SPEECH_FINALIZING"
        assert started.state.speech_deadline_mono is None
        assert started.events[0].payload["duration_ms"] == 0
        assert [event.type for event in started.events] == [
            "speech.started",
            "speech.finalizing",
        ]
        assert started.events[1].payload["reason"] == "TIME_LIMIT"
        assert actor._timer_command_type is None
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_restarted_human_speech_uses_a_new_deadline_idempotency_key() -> None:
    user_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        speaker_user_id=user_id,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HUMAN_READY_TO_START",
            actions=(action,),
            current_speaker_user_id=user_id,
            speech_remaining_ms=30_000,
        ),
        clock=lambda: 100.0,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        first = await actor.submit(
            MatchCommand(type="speech.start", message_id="first-start", actor_user_id=user_id)
        )
        speech_id = first.state.current_speech_id
        assert speech_id is not None
        first_deadline_id = f"internal:speech-deadline:{speech_id}:{first.events[0].sequence}"
        first_deadline = await actor.submit(
            MatchCommand(type="speech.deadline", message_id=first_deadline_id)
        )
        assert first_deadline.state.action_state == "SPEECH_FINALIZING"

        await actor.submit(
            MatchCommand(
                type="system.error",
                message_id="asr-second-failure",
                payload={"error_code": "asr_task_failed", "asr_failure": True},
            )
        )
        await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="resume-second-attempt",
                payload={"privileged": True, "reasons": []},
            )
        )
        await actor.submit(MatchCommand(type="resume.elapsed", message_id="resume-finished"))
        second = await actor.submit(
            MatchCommand(type="speech.start", message_id="second-start", actor_user_id=user_id)
        )
        second_speech_id = second.state.current_speech_id
        assert second_speech_id is not None
        assert second_speech_id != speech_id
        second_deadline_id = (
            f"internal:speech-deadline:{second_speech_id}:{second.events[0].sequence}"
        )
        assert second_deadline_id != first_deadline_id

        second_deadline = await actor.submit(
            MatchCommand(type="speech.deadline", message_id=second_deadline_id)
        )
        assert not second_deadline.duplicate
        assert second_deadline.state.action_state == "SPEECH_FINALIZING"
        assert second_deadline.events[0].payload["reason"] == "TIME_LIMIT"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_stale_speech_deadline_cannot_finalize_a_restarted_speech() -> None:
    user_id = uuid4()
    old_speech_id = uuid4()
    new_speech_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        speaker_user_id=user_id,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HUMAN_SPEAKING",
            actions=(action,),
            current_speech_id=new_speech_id,
            current_speaker_user_id=user_id,
            speech_start_sequence=12,
            speech_remaining_ms=30_000,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        stale = await actor.submit(
            MatchCommand(
                type="speech.deadline",
                message_id="stale-speech-deadline",
                payload={"speech_id": str(old_speech_id), "start_sequence": 8},
            )
        )
        assert stale.events == ()
        assert stale.state.action_state == "HUMAN_SPEAKING"
        assert stale.state.current_speech_id == new_speech_id

        current = await actor.submit(
            MatchCommand(
                type="speech.deadline",
                message_id="current-speech-deadline",
                payload={"speech_id": str(new_speech_id), "start_sequence": 12},
            )
        )
        assert current.state.action_state == "SPEECH_FINALIZING"
        assert current.events[0].payload["reason"] == "TIME_LIMIT"
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
        assert dict(offline.state.offline_since_ms)[user_id] > 1_000_000_000_000
        online = await actor.submit(
            MatchCommand(type="member.online", message_id="o2", actor_user_id=user_id)
        )
        assert online.state.offline_user_id is None
        ignored = await actor.submit(MatchCommand(type="offline.expired", message_id="o3"))
        assert ignored.state.status == "RUNNING"
        await actor.submit(
            MatchCommand(
                type="member.offline",
                message_id="o4",
                actor_user_id=user_id,
                payload={"offline_since_ms": 1},
            )
        )
        paused = await actor.submit(MatchCommand(type="offline.expired", message_id="o5"))
        assert paused.state.status == "PAUSED"
        assert paused.events[0].payload["reason"] == "PLAYER_OFFLINE_TIMEOUT"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_offline_grace_does_not_pause_at_59_9_seconds_but_pauses_at_60() -> None:
    user_id = uuid4()
    wall_now = 1_000.0
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(), status="RUNNING", action_state="FREE_SELECTING", actions=()
        ),
        wall_clock=lambda: wall_now,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(
                type="member.offline",
                message_id="boundary-offline",
                actor_user_id=user_id,
                payload={"connection_epoch": 1, "offline_since_ms": 940_100},
            )
        )
        before_expiry = await actor.submit(
            MatchCommand(
                type="offline.expired",
                message_id="boundary-59-9",
                payload={"user_id": str(user_id)},
            )
        )
        assert before_expiry.state.status == "RUNNING"
        assert before_expiry.events == ()
        assert user_id in actor._offline_timers

        wall_now = 1_000.1
        expired = await actor.submit(
            MatchCommand(
                type="offline.expired",
                message_id="boundary-60",
                payload={"user_id": str(user_id)},
            )
        )
        assert expired.state.status == "PAUSED"
        assert expired.events[0].payload["reason"] == "PLAYER_OFFLINE_TIMEOUT"
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
async def test_online_epoch_is_snapshotted_even_without_an_offline_transition() -> None:
    user_id = uuid4()
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(), status="RUNNING", action_state="FREE_SELECTING", actions=()
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        result = await actor.submit(
            MatchCommand(
                type="member.online",
                message_id="already-online-higher-epoch",
                actor_user_id=user_id,
                payload={"connection_epoch": 7},
            )
        )
        assert result.events == ()
        assert dict(result.state.connection_epochs) == {user_id: 7}
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_failed_online_epoch_commit_rolls_back_actor_high_water() -> None:
    user_id = uuid4()

    async def fail_commit(*_args: object) -> None:
        raise RuntimeError("database unavailable")

    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(), status="RUNNING", action_state="FREE_SELECTING", actions=()
        ),
        commit=fail_commit,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        with pytest.raises(RuntimeError, match="database unavailable"):
            await actor.submit(
                MatchCommand(
                    type="member.online",
                    message_id="failed-online-epoch",
                    actor_user_id=user_id,
                    payload={"connection_epoch": 7},
                )
            )
        assert actor.state.connection_epochs == ()
        assert actor._connection_epochs == {}
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_failed_higher_offline_epoch_commit_rolls_back_high_water() -> None:
    user_id = uuid4()

    async def fail_higher_epoch(
        _previous: object,
        _candidate: object,
        _events: object,
        command: MatchCommand,
    ) -> None:
        if command.message_id == "failed-higher-offline-epoch":
            raise RuntimeError("database unavailable")

    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(), status="RUNNING", action_state="FREE_SELECTING", actions=()
        ),
        commit=fail_higher_epoch,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(
                type="member.offline",
                message_id="first-offline-epoch",
                actor_user_id=user_id,
                payload={"connection_epoch": 4, "offline_since_ms": 1},
            )
        )
        original_timer = actor._offline_timers[user_id]

        with pytest.raises(RuntimeError, match="database unavailable"):
            await actor.submit(
                MatchCommand(
                    type="member.offline",
                    message_id="failed-higher-offline-epoch",
                    actor_user_id=user_id,
                    payload={"connection_epoch": 5, "offline_since_ms": 2},
                )
            )

        assert dict(actor.state.connection_epochs) == {user_id: 4}
        assert actor._connection_epochs == {user_id: 4}
        assert dict(actor.state.offline_since_ms) == {user_id: 1}
        assert actor._offline_timers[user_id] is original_timer
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_queued_expiry_is_ignored_after_higher_epoch_reconnect() -> None:
    user_id = uuid4()
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(), status="RUNNING", action_state="FREE_SELECTING", actions=()
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(
                type="member.offline",
                message_id="epoch-1-offline",
                actor_user_id=user_id,
                payload={"connection_epoch": 1, "offline_since_ms": 1},
            )
        )
        await actor.submit(
            MatchCommand(
                type="member.online",
                message_id="epoch-2-online",
                actor_user_id=user_id,
                payload={"connection_epoch": 2},
            )
        )
        expired = await actor.submit(
            MatchCommand(
                type="offline.expired",
                message_id="queued-epoch-1-expiry",
                payload={"user_id": str(user_id)},
            )
        )
        assert expired.events == ()
        assert expired.state.status == "RUNNING"
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
async def test_higher_epoch_disconnect_preserves_original_offline_grace() -> None:
    user_id = uuid4()
    wall_now = 1_000.0
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(), status="RUNNING", action_state="FREE_SELECTING", actions=()
        ),
        wall_clock=lambda: wall_now,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        first = await actor.submit(
            MatchCommand(
                type="member.offline",
                message_id="epoch-4-offline",
                actor_user_id=user_id,
                payload={"connection_epoch": 4, "offline_since_ms": 940_000},
            )
        )
        original_timer = actor._offline_timers[user_id]

        wall_now = 999.0
        duplicate = await actor.submit(
            MatchCommand(
                type="member.offline",
                message_id="epoch-5-offline-at-59-seconds",
                actor_user_id=user_id,
                payload={"connection_epoch": 5, "offline_since_ms": 999_000},
            )
        )

        assert first.events
        assert duplicate.events == ()
        assert dict(actor.state.connection_epochs)[user_id] == 5
        assert dict(actor.state.offline_since_ms)[user_id] == 940_000
        assert actor._offline_timers[user_id] is original_timer

        wall_now = 1_000.0
        expired = await actor.submit(
            MatchCommand(
                type="offline.expired",
                message_id="original-epoch-4-expiry",
                payload={"user_id": str(user_id)},
            )
        )
        assert expired.state.status == "PAUSED"
        assert expired.events[0].payload["reason"] == "PLAYER_OFFLINE_TIMEOUT"
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


@pytest.mark.asyncio
async def test_system_error_clears_transient_interim_text() -> None:
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="AGENT_SPEAKING",
            actions=(),
            interim_text="过期字幕",
            current_speech_id=uuid4(),
            current_agent_profile_id=uuid4(),
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        result = await actor.submit(
            MatchCommand(
                type="system.error",
                message_id="clear-interim-on-error",
                payload={"error_code": "tts_failed"},
            )
        )
        assert result.state.status == "ERROR"
        assert result.state.interim_text == ""
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_system_error_freezes_host_audio_and_restores_host_action() -> None:
    now = 100.0
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        speaker_user_id=uuid4(),
        host_audio_path="rules/host.ogg",
        host_audio_duration_ms=10_000,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HOST_ANNOUNCING",
            actions=(action,),
            host_audio_remaining_ms=10_000,
            host_audio_deadline_mono=107.0,
        ),
        clock=lambda: now,
        wall_clock=lambda: 1_000.0,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        result = await actor.submit(
            MatchCommand(
                type="system.error",
                message_id="host-error-recovery",
                payload={"error_code": "provider_failed"},
            )
        )
        assert result.state.paused_from_action_state == "HOST_ANNOUNCING"
        assert result.state.host_audio_remaining_ms == 7_000
        assert result.state.host_audio_deadline_mono is None
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_invalid_callback_uuid_is_a_domain_error_and_actor_survives() -> None:
    agent_id = uuid4()
    round_id = uuid4()
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(
                MatchAction(
                    stage_position=1,
                    action_position=1,
                    action_kind="FREE_DEBATE",
                    duration_seconds=30,
                ),
            ),
            free_holder_side="AFFIRMATIVE",
            agent_decision_round_id=round_id,
            agent_decisions=(
                AgentDecisionState(
                    agent_profile_id=agent_id,
                    side="AFFIRMATIVE",
                    seat_no=1,
                ),
            ),
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        with pytest.raises(MatchDomainError, match="invalid_event_payload"):
            await actor.submit(
                MatchCommand(
                    type="free.agent_decision_result",
                    message_id="invalid-agent-uuid",
                    payload={
                        "action_key": "1:1",
                        "decision_round_id": str(round_id),
                        "agent_profile_id": "None",
                        "failed": True,
                    },
                )
            )
        recovered = await actor.submit(
            MatchCommand(
                type="system.error",
                message_id="actor-still-responsive",
                payload={"error_code": "callback_invalid"},
            )
        )
        assert recovered.state.action_state == "RECOVERY_REQUIRED"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_internal_timer_unknown_exception_converges_to_recovery() -> None:
    fail_once = True

    async def commit(
        _previous: MatchRuntimeState,
        _candidate: MatchRuntimeState,
        _events: tuple[MatchEvent, ...],
        command: MatchCommand,
    ) -> None:
        nonlocal fail_once
        if command.message_id == "timer-command" and fail_once:
            fail_once = False
            raise ValueError("badly formed hexadecimal UUID string")

    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(),
        ),
        sleep=lambda _: asyncio.sleep(0),
        commit=commit,
    )
    await actor.start()
    try:
        actor._schedule_internal_now(0, "system.error", "timer-command")
        for _ in range(20):
            if actor.state.action_state == "RECOVERY_REQUIRED":
                break
            await asyncio.sleep(0)
        assert actor.state.status == "ERROR"
        assert actor.state.action_state == "RECOVERY_REQUIRED"
        assert actor.state.error_code == "internal_timer_failed"
        assert actor._timer is None
    finally:
        await actor.close()

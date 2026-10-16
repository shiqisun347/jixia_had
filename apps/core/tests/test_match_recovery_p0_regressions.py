"""Unapproved Spec 304: reproduce operator reset and delayed-ASR handoff defects."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest

from jx_core.matches.domain import (
    DebateParticipant,
    MatchAction,
    MatchActor,
    MatchCommand,
    MatchDomainError,
    MatchRuntimeState,
)


def mixed_free_actor(now: list[float]) -> tuple[MatchActor, UUID, UUID]:
    humans = {(side, seat): uuid4() for side in ("AFFIRMATIVE", "NEGATIVE") for seat in (1, 2)}
    participants = tuple(
        DebateParticipant(side=side, seat_no=seat, user_id=humans[(side, seat)])
        if seat <= 2
        else DebateParticipant(side=side, seat_no=seat, agent_profile_id=uuid4())
        for side in ("AFFIRMATIVE", "NEGATIVE")
        for seat in range(1, 5)
    )
    action = MatchAction(
        stage_position=7,
        action_position=0,
        action_kind="FREE_DEBATE",
        duration_seconds=60,
        free_max_speech_seconds=10,
        participants=participants,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(action,),
            formal_4v4=True,
            free_holder_side="AFFIRMATIVE",
            free_affirmative_remaining_ms=60_000,
            free_negative_remaining_ms=60_000,
            hand_window_open=True,
        ),
        clock=lambda: now[0],
        wall_clock=lambda: now[0],
        sleep=lambda _: asyncio.sleep(60),
    )
    actor._start_free_agent_decisions("AFFIRMATIVE")
    return actor, humans[("AFFIRMATIVE", 1)], humans[("NEGATIVE", 1)]


async def start_first_human(actor: MatchActor, human_id: UUID) -> UUID:
    await actor.submit(
        MatchCommand(type="hand.raise", message_id="first-hand", actor_user_id=human_id)
    )
    await actor.submit(MatchCommand(type="hand.window_closed", message_id="first-cutoff"))
    result = await actor.submit(
        MatchCommand(type="speech.start", message_id="first-start", actor_user_id=human_id)
    )
    speech_id = result.state.current_speech_id
    assert speech_id is not None
    assert result.state.current_speaker_side == "AFFIRMATIVE"
    assert await actor.set_interim_text("合成旧发言的临时文字", speech_id=speech_id)
    return speech_id


@pytest.mark.asyncio
@pytest.mark.parametrize("select_after_wait", [False, True])
async def test_next_human_gets_a_new_speech_while_previous_asr_is_pending(
    select_after_wait: bool,
) -> None:
    now = [100.0]
    actor, first_human, next_human = mixed_free_actor(now)
    await actor.start()
    try:
        old_speech_id = await start_first_human(actor, first_human)
        if select_after_wait:
            now[0] = 102.0
            finished = await actor.submit(
                MatchCommand(
                    type="speech.finish", message_id="early-finish", actor_user_id=first_human
                )
            )
        else:
            now[0] = 110.0
            finished = await actor.submit(
                MatchCommand(type="speech.deadline", message_id="first-time-limit")
            )
        assert finished.state.action_state == "SPEECH_FINALIZING"
        assert finished.state.current_speech_id == old_speech_id
        assert finished.state.hand_window_open
        if not select_after_wait:
            # The opposite side's ordinary three-second application is legal.
            await actor.submit(
                MatchCommand(type="hand.raise", message_id="next-hand", actor_user_id=next_human)
            )
        now[0] += 3.0
        await actor.submit(MatchCommand(type="hand.window_closed", message_id="next-cutoff"))
        if select_after_wait:
            assert actor.state.selection_phase == "MODEL_WAIT"
            # Match the production sequence: pause/resume during delayed ASR,
            # then the other side applies through the new recovery priority window.
            await actor.submit(
                MatchCommand(
                    type="match.pause",
                    message_id="wait-pause",
                    actor_user_id=first_human,
                    payload={"privileged": True, "authorized": True},
                )
            )
            await actor.submit(
                MatchCommand(
                    type="match.resume",
                    message_id="wait-resume",
                    payload={"privileged": True, "reasons": []},
                )
            )
            now[0] += 3.0
            await actor.submit(MatchCommand(type="resume.elapsed", message_id="resumed"))
            await actor.submit(
                MatchCommand(
                    type="hand.raise", message_id="late-next-hand", actor_user_id=next_human
                )
            )
        if select_after_wait:
            now[0] += 3.0
            await actor.submit(MatchCommand("hand.window_closed", "new-recovery-human-cutoff"))
        assert actor.state.current_speaker_user_id == next_human
        assert actor.state.action_state == "HUMAN_READY_TO_START"
        ready_speech_id = actor.state.current_speech_id
        started = await actor.submit(
            MatchCommand(type="speech.start", message_id="next-start", actor_user_id=next_human)
        )
        assert started.state.current_speaker_side == "NEGATIVE"
        assert started.state.current_speech_id != old_speech_id
        assert ready_speech_id is None
    finally:
        await actor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action_state",
    [
        "HUMAN_SPEAKING",
        "SPEECH_FINALIZING",
        "AGENT_PREPARING",
        "AGENT_SPEAKING",
        "AGENT_FINALIZING",
    ],
)
async def test_admin_reset_removes_old_interim_immediately(action_state: str) -> None:
    human_id = uuid4()
    is_agent = action_state.startswith("AGENT_")
    agent_id = uuid4() if is_agent else None
    action = MatchAction(
        stage_position=1,
        action_position=0,
        action_kind="AGENT_SPEECH" if is_agent else "HUMAN_SPEECH",
        duration_seconds=30,
        side="AFFIRMATIVE",
        seat_no=1,
        agent_profile_id=agent_id,
        speaker_user_id=None if is_agent else human_id,
    )
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state=action_state,
            actions=(action,),
            current_speech_id=uuid4(),
            current_speaker_user_id=None if is_agent else human_id,
            current_agent_profile_id=agent_id,
            current_speaker_side="AFFIRMATIVE",
            current_speaker_seat_no=1,
            interim_text="合成旧发言的临时文字",
            speech_remaining_ms=12_000,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        result = await actor.submit(
            MatchCommand(
                type="speech.reset",
                message_id="admin-reset-current",
                actor_user_id=uuid4(),
                payload={"privileged": True},
            )
        )
        assert result.state.current_speech_id is None
        assert result.state.speech_remaining_ms == 30_000
        assert result.state.interim_text == ""
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_raise_after_selection_deadline_is_rejected_even_if_timer_is_queued() -> None:
    now = [100.0]
    actor, first_human, next_human = mixed_free_actor(now)
    await actor.start()
    try:
        await start_first_human(actor, first_human)
        now[0] = 110.0
        await actor.submit(MatchCommand(type="speech.deadline", message_id="first-time-limit"))
        assert actor.state.selection_deadline_mono == 113.0
        now[0] = 113.001
        # The Actor's time is authoritative even if its timer command has not
        # yet reached the queue; no client time or forced advance is involved.
        with pytest.raises(MatchDomainError, match="hand_window_closed"):
            await actor.submit(
                MatchCommand(type="hand.raise", message_id="expired-hand", actor_user_id=next_human)
            )
        assert actor.state.hand_queue == ()
    finally:
        await actor.close()

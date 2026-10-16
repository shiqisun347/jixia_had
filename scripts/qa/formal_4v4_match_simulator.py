"""Deterministic formal 4v4 match simulator.

The simulator drives the production MatchActor with two human and two Agent
seats on each side. It includes a pause/resume cycle, stale callback checks,
and runs the match through a terminal state without provider or database calls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from time import perf_counter
from uuid import UUID, uuid4, uuid5

from jx_core.matches.domain import (
    DebateParticipant,
    MatchAction,
    MatchActor,
    MatchCommand,
    MatchDomainError,
    MatchRuntimeState,
)

NAMESPACE = UUID("72adcb0b-1fcb-4c59-bf82-2d799ab2f2a8")


@dataclass(frozen=True, slots=True)
class Seat:
    side: str
    seat_no: int
    kind: str
    participant_id: UUID


@dataclass(frozen=True, slots=True)
class SimulationSummary:
    rounds: int
    completed_rounds: int
    human_participants: int
    agent_participants: int
    completed_speeches: int
    stale_callbacks_rejected: int
    pause_resume_verified: bool
    elapsed_ms: int


def stable(label: str) -> UUID:
    return uuid5(NAMESPACE, label)


def formal_seats(round_no: int) -> tuple[Seat, ...]:
    return tuple(
        Seat(side, seat_no, kind, stable(f"round-{round_no}-{side}-{kind}-{seat_no}"))
        for side in ("AFFIRMATIVE", "NEGATIVE")
        for seat_no, kind in ((1, "HUMAN"), (2, "AGENT"), (3, "HUMAN"), (4, "AGENT"))
    )


def actions_for(seats: tuple[Seat, ...]) -> tuple[MatchAction, ...]:
    fixed = tuple(
        MatchAction(
            stage_position=index,
            action_position=1,
            action_kind="HUMAN_SPEECH" if seat.kind == "HUMAN" else "AGENT_SPEECH",
            duration_seconds=30,
            side=seat.side,
            seat_no=seat.seat_no,
            speaker_kind=seat.kind,
            speaker_user_id=seat.participant_id if seat.kind == "HUMAN" else None,
            agent_profile_id=seat.participant_id if seat.kind == "AGENT" else None,
        )
        for index, seat in enumerate(seats, start=1)
    )
    participants = tuple(
        DebateParticipant(
            side=seat.side,
            seat_no=seat.seat_no,
            user_id=seat.participant_id if seat.kind == "HUMAN" else None,
            agent_profile_id=seat.participant_id if seat.kind == "AGENT" else None,
        )
        for seat in seats
    )
    return fixed + (
        MatchAction(
            stage_position=len(fixed) + 1,
            action_position=1,
            action_kind="FREE_DEBATE",
            duration_seconds=5,
            participants=participants,
            free_starting_side="NEGATIVE",
            free_max_speech_seconds=5,
        ),
    )


async def run_round(round_no: int) -> tuple[int, int, bool]:
    seats = formal_seats(round_no)
    if (
        sum(seat.kind == "HUMAN" for seat in seats) != 4
        or sum(seat.kind == "AGENT" for seat in seats) != 4
    ):
        raise RuntimeError("formal_4v4_seat_composition_invalid")
    actions = actions_for(seats)
    first = actions[0]
    now = [100.0]
    actor = MatchActor(
        MatchRuntimeState(
            match_id=stable(f"round-{round_no}-match"),
            status="RUNNING",
            action_state="HUMAN_READY_TO_START",
            actions=actions,
            current_speaker_user_id=first.speaker_user_id,
            current_speaker_side=first.side,
            current_speaker_seat_no=first.seat_no,
            speech_remaining_ms=30_000,
            formal_4v4=True,
        ),
        clock=lambda: now[0],
        wall_clock=lambda: now[0],
        sleep=lambda _: asyncio.sleep(60),
    )
    stale_rejected = 0
    pause_resume_verified = False
    completed = 0
    previous_speech_id: UUID | None = None
    await actor.start()
    try:
        for index, action in enumerate(actions[:-1], start=1):
            if action.action_kind == "HUMAN_SPEECH":
                user_id = action.speaker_user_id
                assert user_id is not None
                started = await actor.submit(
                    MatchCommand(
                        type="speech.start",
                        message_id=f"sim:{round_no}:{index}:start",
                        actor_user_id=user_id,
                    )
                )
                speech_id = started.state.current_speech_id
                if speech_id is None:
                    raise RuntimeError("human_speech_id_missing")
                if index == 1:
                    await actor.submit(
                        MatchCommand(
                            type="match.pause",
                            message_id=f"sim:{round_no}:pause",
                            actor_user_id=user_id,
                            payload={"authorized": True},
                        )
                    )
                    await actor.submit(
                        MatchCommand(
                            type="match.resume",
                            message_id=f"sim:{round_no}:resume",
                            actor_user_id=user_id,
                            payload={"authorized": True, "reasons": []},
                        )
                    )
                    resumed = await actor.submit(
                        MatchCommand(
                            type="resume.elapsed", message_id=f"sim:{round_no}:resume-elapsed"
                        )
                    )
                    pause_resume_verified = (
                        resumed.state.current_speech_id == speech_id
                        and resumed.state.action_state == "HUMAN_READY_TO_START"
                    )
                    await actor.submit(
                        MatchCommand(
                            type="speech.start",
                            message_id=f"sim:{round_no}:restart",
                            actor_user_id=user_id,
                        )
                    )
                if previous_speech_id is not None:
                    before = actor.state
                    try:
                        await actor.submit(
                            MatchCommand(
                                type="asr.finalized",
                                message_id=f"sim:{round_no}:{index}:stale",
                                payload={"speech_id": str(previous_speech_id)},
                            )
                        )
                    except MatchDomainError:
                        stale_rejected += 1
                    if actor.state != before:
                        raise RuntimeError("stale_callback_mutated_state")
                now[0] += 5.0
                await actor.submit(
                    MatchCommand(
                        type="speech.finish",
                        message_id=f"sim:{round_no}:{index}:finish",
                        actor_user_id=user_id,
                    )
                )
                await actor.submit(
                    MatchCommand(
                        type="asr.finalized",
                        message_id=f"sim:{round_no}:{index}:final",
                        payload={
                            "speech_id": str(speech_id),
                            "audio_duration_ms": 5_000,
                            "final_text": f"synthetic-human-{index}",
                        },
                    )
                )
            else:
                agent_id = action.agent_profile_id
                assert agent_id is not None
                speech_id = uuid4()
                generation_id = uuid4()
                await actor.submit(
                    MatchCommand(
                        type="agent.playback_started",
                        message_id=f"sim:{round_no}:{index}:agent-start",
                        payload={
                            "speech_id": str(speech_id),
                            "generation_id": str(generation_id),
                            "agent_profile_id": str(agent_id),
                        },
                    )
                )
                now[0] += 5.0
                await actor.submit(
                    MatchCommand(
                        type="agent.playback_finished",
                        message_id=f"sim:{round_no}:{index}:agent-finish",
                        payload={"speech_id": str(speech_id)},
                    )
                )
                await actor.submit(
                    MatchCommand(
                        type="agent.finalized",
                        message_id=f"sim:{round_no}:{index}:agent-finalized",
                        payload={
                            "speech_id": str(speech_id),
                            "generation_id": str(generation_id),
                            "audio_duration_ms": 5_000,
                        },
                    )
                )
            previous_speech_id = speech_id
            completed += 1
        if actor.state.action_state != "FREE_SELECTING":
            raise RuntimeError("free_debate_did_not_start")
        free_side = actor.state.free_holder_side
        if free_side is None:
            raise RuntimeError("free_debate_side_missing")
        free_human = next(seat for seat in seats if seat.side == free_side and seat.kind == "HUMAN")
        await actor.submit(
            MatchCommand(
                type="hand.raise",
                message_id=f"sim:{round_no}:free-human-hand",
                actor_user_id=free_human.participant_id,
            )
        )
        selected = await actor.submit(
            MatchCommand(type="hand.window_closed", message_id=f"sim:{round_no}:free-human-select")
        )
        if selected.state.current_speaker_user_id != free_human.participant_id:
            raise RuntimeError("free_human_not_selected")
        free_started = await actor.submit(
            MatchCommand(
                type="speech.start",
                message_id=f"sim:{round_no}:free-human-start",
                actor_user_id=free_human.participant_id,
            )
        )
        free_speech_id = free_started.state.current_speech_id
        if free_speech_id is None:
            raise RuntimeError("free_human_speech_id_missing")
        now[0] += 5.0
        await actor.submit(
            MatchCommand(
                type="speech.finish",
                message_id=f"sim:{round_no}:free-human-finish",
                actor_user_id=free_human.participant_id,
            )
        )
        await actor.submit(
            MatchCommand(
                type="asr.finalized",
                message_id=f"sim:{round_no}:free-human-finalized",
                payload={"speech_id": str(free_speech_id), "audio_duration_ms": 5_000},
            )
        )
        completed += 1
        decision_round_id = actor.state.agent_decision_round_id
        if decision_round_id is None:
            opportunity_id = actor.state.opportunity_id
            if opportunity_id is None:
                raise RuntimeError("free_agent_opportunity_missing")
            decision_started = await actor.submit(
                MatchCommand(
                    type="free.agent_decision_start",
                    message_id=f"sim:{round_no}:free-agent-decision-start",
                    payload={
                        "opportunity_id": str(opportunity_id),
                        "opportunity_generation": actor.state.opportunity_generation,
                        "trigger_kind": "HUMAN_SPEECH",
                    },
                )
            )
            decision_round_id = decision_started.state.agent_decision_round_id
        if decision_round_id is None:
            raise RuntimeError("free_agent_decision_round_missing")
        free_agent = next(seat for seat in seats if seat.side != free_side and seat.kind == "AGENT")
        await actor.submit(
            MatchCommand(
                type="free.agent_decision_result",
                message_id=f"sim:{round_no}:free-agent-decision",
                payload={
                    "action_key": actions[-1].action_key,
                    "agent_profile_id": str(free_agent.participant_id),
                    "decision_round_id": str(decision_round_id),
                    "should_speak": True,
                },
            )
        )
        # Every eligible candidate gets an outcome. A partial result now leaves
        # the opportunity in MODEL_WAIT until its bounded decision deadline.
        for decision in actor.state.agent_decisions:
            if decision.status != "DECIDING":
                continue
            await actor.submit(
                MatchCommand(
                    type="free.agent_decision_result",
                    message_id=f"sim:{round_no}:free-agent-skip:{decision.agent_profile_id}",
                    payload={
                        "action_key": actions[-1].action_key,
                        "agent_profile_id": str(decision.agent_profile_id),
                        "decision_round_id": str(decision_round_id),
                        "should_speak": False,
                    },
                )
            )
        selected_agent = await actor.submit(
            MatchCommand(type="hand.window_closed", message_id=f"sim:{round_no}:free-agent-select")
        )
        if selected_agent.state.current_agent_profile_id != free_agent.participant_id:
            raise RuntimeError("free_agent_not_selected")
        free_agent_speech_id = uuid4()
        free_generation_id = uuid4()
        await actor.submit(
            MatchCommand(
                type="agent.playback_started",
                message_id=f"sim:{round_no}:free-agent-start",
                payload={
                    "speech_id": str(free_agent_speech_id),
                    "generation_id": str(free_generation_id),
                    "agent_profile_id": str(free_agent.participant_id),
                },
            )
        )
        now[0] += 5.0
        await actor.submit(
            MatchCommand(
                type="agent.playback_finished",
                message_id=f"sim:{round_no}:free-agent-finish",
                payload={"speech_id": str(free_agent_speech_id)},
            )
        )
        await actor.submit(
            MatchCommand(
                type="agent.finalized",
                message_id=f"sim:{round_no}:free-agent-finalized",
                payload={
                    "speech_id": str(free_agent_speech_id),
                    "generation_id": str(free_generation_id),
                    "audio_duration_ms": 5_000,
                },
            )
        )
        completed += 1
        if actor.state.status != "FINISHED" or actor.state.action_state != "MATCH_FINISHED":
            raise RuntimeError("formal_4v4_match_did_not_finish")
        return completed, stale_rejected, pause_resume_verified
    finally:
        await actor.close()


async def run_simulation(rounds: int = 1) -> SimulationSummary:
    if rounds < 1 or rounds > 100:
        raise ValueError("rounds_must_be_between_1_and_100")
    started = perf_counter()
    completed = 0
    stale = 0
    paused = True
    for round_no in range(1, rounds + 1):
        speeches, rejected, pause_verified = await run_round(round_no)
        completed += speeches
        stale += rejected
        paused = paused and pause_verified
    return SimulationSummary(
        rounds, rounds, 4, 4, completed, stale, paused, int((perf_counter() - started) * 1000)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=1)
    args = parser.parse_args()
    print(
        json.dumps(
            asdict(asyncio.run(run_simulation(args.rounds))), ensure_ascii=False, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()

"""Deterministic five-human MatchActor reliability simulator.

This QA tool is intentionally in-memory.  It exercises the production actor
commands and timers without creating users, rooms, matches, or provider calls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from time import perf_counter
from uuid import UUID, uuid5

from jx_core.matches.domain import (
    MatchAction,
    MatchActor,
    MatchCommand,
    MatchDomainError,
    MatchEvent,
    MatchRuntimeState,
)
from jx_core.matches.service import classify_asr_error

SIMULATION_NAMESPACE = UUID("31b4162f-d851-41b4-8a5f-64457999f17f")


@dataclass(frozen=True, slots=True)
class SimulationSummary:
    rounds: int
    completed_rounds: int
    human_participants: int
    completed_speeches: int
    stale_callbacks_rejected: int
    invalid_uuid_rejected: bool
    timer_failure_converged: bool
    asr_classes: dict[str, str]
    elapsed_ms: int


def _stable_uuid(label: str) -> UUID:
    return uuid5(SIMULATION_NAMESPACE, label)


def _five_human_actions(round_no: int) -> tuple[tuple[UUID, ...], tuple[MatchAction, ...]]:
    users = tuple(_stable_uuid(f"round-{round_no}-human-{index}") for index in range(1, 6))
    actions = tuple(
        MatchAction(
            stage_position=index,
            action_position=1,
            action_kind="HUMAN_SPEECH",
            duration_seconds=30,
            side="AFFIRMATIVE" if index % 2 else "NEGATIVE",
            seat_no=((index - 1) % 4) + 1,
            speaker_user_id=user_id,
        )
        for index, user_id in enumerate(users, start=1)
    )
    return users, actions


async def _run_five_human_round(round_no: int) -> tuple[int, int]:
    users, actions = _five_human_actions(round_no)
    actor = MatchActor(
        MatchRuntimeState(
            match_id=_stable_uuid(f"round-{round_no}-match"),
            status="RUNNING",
            action_state="HUMAN_READY_TO_START",
            actions=actions,
            current_speaker_user_id=users[0],
            current_speaker_side=actions[0].side,
            current_speaker_seat_no=actions[0].seat_no,
            speech_remaining_ms=30_000,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    speech_ids: set[UUID] = set()
    stale_rejections = 0
    await actor.start()
    try:
        previous_speech_id: UUID | None = None
        for index, user_id in enumerate(users, start=1):
            if actor.state.current_speaker_user_id != user_id:
                raise RuntimeError("speaker_order_mismatch")
            started = await actor.submit(
                MatchCommand(
                    type="speech.start",
                    message_id=f"sim:{round_no}:{index}:start",
                    actor_user_id=user_id,
                )
            )
            speech_id = started.state.current_speech_id
            if speech_id is None or speech_id in speech_ids:
                raise RuntimeError("speech_identity_not_unique")
            speech_ids.add(speech_id)

            if previous_speech_id is not None:
                before = actor.state
                try:
                    await actor.submit(
                        MatchCommand(
                            type="asr.finalized",
                            message_id=f"sim:{round_no}:{index}:late-final",
                            payload={"speech_id": str(previous_speech_id)},
                        )
                    )
                except MatchDomainError:
                    stale_rejections += 1
                if actor.state != before:
                    raise RuntimeError("stale_callback_mutated_state")

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
                        "final_text": f"synthetic-human-{index}",
                        "audio_duration_ms": 5_000,
                    },
                )
            )
            previous_speech_id = speech_id

        if actor.state.status != "FINISHED" or actor.state.action_state != "MATCH_FINISHED":
            raise RuntimeError("match_did_not_finish")
        return len(speech_ids), stale_rejections
    finally:
        await actor.close()


async def _invalid_uuid_actor_survives() -> bool:
    user_id = _stable_uuid("invalid-uuid-human")
    actor = MatchActor(
        MatchRuntimeState(
            match_id=_stable_uuid("invalid-uuid-match"),
            status="RUNNING",
            action_state="HUMAN_READY_TO_START",
            actions=(
                MatchAction(
                    stage_position=1,
                    action_position=1,
                    action_kind="HUMAN_SPEECH",
                    duration_seconds=30,
                    speaker_user_id=user_id,
                ),
            ),
            current_speaker_user_id=user_id,
            speech_remaining_ms=30_000,
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        try:
            await actor.submit(
                MatchCommand(
                    type="speech.start",
                    message_id="sim:invalid-uuid",
                    actor_user_id=user_id,
                    payload={"speech_id": "not-a-uuid"},
                )
            )
        except MatchDomainError as error:
            if str(error) != "invalid_event_payload":
                raise
        else:
            return False
        result = await actor.submit(
            MatchCommand(
                type="system.error",
                message_id="sim:invalid-uuid:survival",
                payload={"error_code": "synthetic_callback_invalid"},
            )
        )
        return result.state.action_state == "RECOVERY_REQUIRED"
    finally:
        await actor.close()


async def _timer_failure_converges() -> bool:
    fail_once = True

    async def commit(
        _previous: MatchRuntimeState,
        _candidate: MatchRuntimeState,
        _events: tuple[MatchEvent, ...],
        command: MatchCommand,
    ) -> None:
        nonlocal fail_once
        if command.message_id == "sim:timer:explode" and fail_once:
            fail_once = False
            raise ValueError("synthetic malformed UUID")

    actor = MatchActor(
        MatchRuntimeState(
            match_id=_stable_uuid("timer-failure-match"),
            status="RUNNING",
            action_state="FREE_SELECTING",
            actions=(),
        ),
        sleep=lambda _: asyncio.sleep(0),
        commit=commit,
    )
    await actor.start()
    try:
        actor._schedule_internal_now(  # pyright: ignore[reportPrivateUsage]
            0, "system.error", "sim:timer:explode"
        )
        for _ in range(50):
            if actor.state.action_state == "RECOVERY_REQUIRED":
                break
            await asyncio.sleep(0)
        return (
            actor.state.status == "ERROR"
            and actor.state.action_state == "RECOVERY_REQUIRED"
            and actor.state.error_code == "internal_timer_failed"
        )
    finally:
        await actor.close()


async def run_simulation(rounds: int) -> SimulationSummary:
    if rounds < 1 or rounds > 1_000:
        raise ValueError("rounds_must_be_between_1_and_1000")
    started = perf_counter()
    completed_speeches = 0
    stale_callbacks = 0
    for round_no in range(1, rounds + 1):
        speeches, stale = await _run_five_human_round(round_no)
        completed_speeches += speeches
        stale_callbacks += stale
    invalid_uuid_rejected = await _invalid_uuid_actor_survives()
    timer_failure_converged = await _timer_failure_converges()
    if not invalid_uuid_rejected or not timer_failure_converged:
        raise RuntimeError("fault_convergence_failed")
    return SimulationSummary(
        rounds=rounds,
        completed_rounds=rounds,
        human_participants=5,
        completed_speeches=completed_speeches,
        stale_callbacks_rejected=stale_callbacks,
        invalid_uuid_rejected=invalid_uuid_rejected,
        timer_failure_converged=timer_failure_converged,
        asr_classes={
            code: classify_asr_error(code)
            for code in (
                "asr_empty_audio",
                "asr_pcm_queue_full",
                "asr_task_failed",
                "asr_not_configured",
            )
        },
        elapsed_ms=int((perf_counter() - started) * 1_000),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=10)
    args = parser.parse_args()
    summary = asyncio.run(run_simulation(args.rounds))
    print(json.dumps(asdict(summary), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

"""One-match conference flow using the real compiler and authoritative Actor.

The four human seats and four Agent seats are synthetic. Provider/media callbacks
and elapsed-time delivery are controlled boundaries; this is not microphone,
LiveKit, database, model, or browser acceptance.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

import pytest

from jx_core.matches.domain import (
    MatchActor,
    MatchCommand,
    MatchDomainError,
    MatchEvent,
    MatchRuntimeState,
    compile_linear_actions,
)
from jx_core.rules.formal_4v4 import build_formal_4v4_draft
from jx_core.rules.validation import validate_rule_draft


async def _idle_timer(_: float) -> None:
    await asyncio.Event().wait()


async def run_conference_round(
    interruption: str = "none",
    *,
    prepare: Callable[
        [
            dict[str, Any],
            dict[tuple[str, int], UUID],
            dict[tuple[str, int], UUID],
            list[float],
            list[MatchEvent],
        ],
        Awaitable[MatchActor],
    ]
    | None = None,
    start_runtime: Callable[[], Awaitable[MatchRuntimeState]] | None = None,
) -> dict[str, Any]:
    humans = {(side, seat): uuid4() for side in ("AFFIRMATIVE", "NEGATIVE") for seat in (1, 2)}
    agents = {(side, seat): uuid4() for side in ("AFFIRMATIVE", "NEGATIVE") for seat in (3, 4)}
    snapshot = validate_rule_draft(build_formal_4v4_draft())
    # Rule publication assigns persisted Stage/Action positions; draft validation
    # does not. Match compilation receives their room-snapshot projection.
    for position, stage in enumerate(snapshot["stages"], start=1):
        stage["position"] = position
        for action_position, action in enumerate(stage["actions"], start=1):
            action["position"] = action_position
    snapshot["host_audio"] = [
        {
            "segment_key": f"stage-{stage['position']}-{boundary}",
            "storage_path": f"synthetic/host-{stage['position']}-{boundary}.ogg",
            "duration_ms": 1_000,
        }
        for stage in snapshot["stages"]
        for boundary in ("start", "end")
        if stage.get(f"{boundary}_host_text")
    ]
    actions = compile_linear_actions(snapshot, humans, agents)
    now = [100.0]
    events: list[MatchEvent] = []

    async def capture_events(_: MatchRuntimeState, committed: tuple[MatchEvent, ...]) -> None:
        events.extend(committed)

    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="START_PENDING_RUNTIME",
            action_state="NOT_STARTED",
            actions=actions,
            formal_4v4=True,
        ),
        clock=lambda: now[0],
        wall_clock=lambda: now[0] + 1_800_000_000,
        sleep=_idle_timer,
        publish=capture_events,
    )
    if prepare is not None:
        actor = await prepare(snapshot, humans, agents, now, events)
    trace: list[dict[str, Any]] = []
    fixed_speakers: list[tuple[str, int, str]] = []
    free_speakers: list[tuple[str, str]] = []
    interrupted = interruption == "none"
    command_no = 0
    rejected_late_callbacks = 0

    async def submit(command_type: str, user_id: UUID | None = None, **payload: Any) -> None:
        nonlocal command_no
        command_no += 1
        result = await actor.submit(
            MatchCommand(
                type=command_type,
                message_id=f"conference:{command_no}",
                actor_user_id=user_id,
                payload=payload,
            )
        )
        trace.append(
            {
                "command": command_type,
                "status": result.state.status,
                "action_state": result.state.action_state,
                "action_index": result.state.current_action_index,
                "sequence": result.state.sequence,
                "event_types": [event.type for event in result.events],
            }
        )

    async def interrupt_and_resume() -> None:
        nonlocal interrupted
        before = actor.state
        if interruption == "system_recovery":
            await submit("system.recover")
            assert actor.state.status == "SYSTEM_RECOVERY"
        else:
            await submit("match.pause", next(iter(humans.values())), authorized=True)
            assert actor.state.status == "PAUSED"
        await submit("match.resume", next(iter(humans.values())), privileged=True, reasons=[])
        assert actor.state.action_state == "RESUME_COUNTDOWN"
        now[0] += 3
        await submit("resume.elapsed")
        assert actor.state.status == "RUNNING"
        if before.action_state == "HUMAN_SPEAKING":
            assert actor.state.action_state == "HUMAN_READY_TO_START"
            assert actor.state.current_speech_id == before.current_speech_id
            assert actor.state.speech_deadline_mono is None
        elif before.action_state == "AGENT_SPEAKING":
            assert actor.state.action_state == "AGENT_PREPARING"
            assert actor.state.current_speech_id is None
        elif before.action_state == "HOST_ANNOUNCING":
            assert actor.state.action_state == "HOST_ANNOUNCING"
            assert actor.state.host_audio_remaining_ms == 1_000
        interrupted = True

    await actor.start()
    try:
        await asyncio.sleep(0)
        assert actor.state.status == "START_PENDING_RUNTIME"
        assert actor.state.action_state == "NOT_STARTED"
        assert actor.state.sequence == 0 and events == []
        assert actor.state.speech_deadline_mono is None
        assert actor.state.host_audio_deadline_mono is None

        if start_runtime is None:
            start = MatchCommand(type="runtime.start", message_id="conference:formal-start")
            first = await actor.submit(start)
            first_state = first.state
            duplicate = await actor.submit(start)
            assert duplicate.duplicate is True
        else:
            first_state = await start_runtime()
            duplicate_state = await start_runtime()
            assert duplicate_state == first_state
        assert first_state.status == "START_COUNTDOWN"
        assert actor.state.sequence == first_state.sequence
        assert actor.state.speech_deadline_mono is None
        trace.append({"command": "runtime.start", "status": first_state.status})
        now[0] += 3
        await submit("countdown.elapsed")

        for _ in range(150):
            current = actor.view().state
            if current.status == "FINISHED":
                break
            action = current.current_action
            assert action is not None
            state = current.action_state
            if state == "HOST_ANNOUNCING":
                if not interrupted and interruption == "host_pause":
                    await interrupt_and_resume()
                now[0] += 1
                await submit("host.finished", authorized=True)
            elif state == "FREE_SELECTING":
                if not interrupted and interruption == "free_pause":
                    await interrupt_and_resume()
                side = actor.state.free_holder_side
                assert side is not None
                if actor.state.agent_decision_round_id is None:
                    await submit(
                        "free.agent_decision_start",
                        opportunity_id=str(actor.state.opportunity_id),
                        opportunity_generation=actor.state.opportunity_generation,
                        trigger_kind="HUMAN_SPEECH",
                    )
                choose_human = len(free_speakers) % 4 < 2
                if choose_human:
                    await submit("hand.raise", humans[(side, 1)])
                for decision in actor.state.agent_decisions:
                    if decision.status == "DECIDING":
                        await submit(
                            "free.agent_decision_result",
                            action_key=action.action_key,
                            decision_round_id=str(actor.state.agent_decision_round_id),
                            agent_profile_id=str(decision.agent_profile_id),
                            should_speak=True,
                            decision_reason="合成发言测试",
                        )
                now[0] += 3
                await submit("hand.window_closed")
                assert actor.state.action_state in {"HUMAN_READY_TO_START", "AGENT_PREPARING"}
                if choose_human:
                    assert actor.state.current_speaker_user_id == humans[(side, 1)]
            elif state == "HUMAN_READY_TO_START":
                user_id = actor.state.current_speaker_user_id
                assert user_id is not None
                await submit("speech.start", user_id)
                if not interrupted and interruption == "human_pause":
                    await interrupt_and_resume()
                    await submit("speech.start", user_id)
                speech_id = actor.state.current_speech_id
                assert speech_id is not None
                duration = actor.state.speech_remaining_ms
                assert duration is not None and duration > 0
                speaking_side = actor.state.current_speaker_side
                now[0] += duration / 1000
                await submit("speech.finish", user_id)
                await submit(
                    "asr.finalized",
                    speech_id=str(speech_id),
                    audio_duration_ms=duration,
                    final_text="合成真人最终文字",
                )
                if action.action_kind == "FREE_DEBATE":
                    free_speakers.append((str(speaking_side), "HUMAN"))
                else:
                    fixed_speakers.append((str(action.side), int(action.seat_no or 0), "HUMAN"))
            elif state == "AGENT_PREPARING":
                agent_id = actor.state.current_agent_profile_id
                assert agent_id is not None
                speech_id, generation_id = uuid4(), uuid4()
                await submit(
                    "agent.playback_started",
                    agent_profile_id=str(agent_id),
                    speech_id=str(speech_id),
                    generation_id=str(generation_id),
                )
                if not interrupted and interruption in {"agent_pause", "system_recovery"}:
                    await interrupt_and_resume()
                    before = actor.state
                    with pytest.raises(MatchDomainError):
                        await submit("agent.finalized", speech_id=str(speech_id))
                    assert actor.state == before
                    rejected_late_callbacks += 1
                    speech_id, generation_id = uuid4(), uuid4()
                    await submit(
                        "agent.playback_started",
                        agent_profile_id=str(agent_id),
                        speech_id=str(speech_id),
                        generation_id=str(generation_id),
                    )
                duration = actor.state.speech_remaining_ms
                assert duration is not None and duration > 0
                speaking_side = actor.state.current_speaker_side
                now[0] += duration / 1000
                await submit("agent.playback_finished", speech_id=str(speech_id))
                await submit(
                    "agent.finalized",
                    speech_id=str(speech_id),
                    generation_id=str(generation_id),
                    audio_duration_ms=duration,
                    final_text="合成Agent实际播放完成文字",
                    llm_draft_text="合成Agent完整草稿文字",
                )
                if action.action_kind == "FREE_DEBATE":
                    free_speakers.append((str(speaking_side), "AGENT"))
                else:
                    fixed_speakers.append((str(action.side), int(action.seat_no or 0), "AGENT"))
            else:
                raise AssertionError(f"unexpected conference state: {state}")
        assert interrupted
        assert actor.state.status == "FINISHED"
        assert actor.state.action_state == "MATCH_FINISHED"
        assert fixed_speakers == [
            ("AFFIRMATIVE", 1, "HUMAN"),
            ("NEGATIVE", 1, "HUMAN"),
            ("AFFIRMATIVE", 2, "HUMAN"),
            ("NEGATIVE", 2, "HUMAN"),
            ("AFFIRMATIVE", 3, "AGENT"),
            ("NEGATIVE", 3, "AGENT"),
            ("NEGATIVE", 4, "AGENT"),
            ("AFFIRMATIVE", 4, "AGENT"),
        ]
        assert len(free_speakers) == 12
        assert [side for side, _ in free_speakers] == ["AFFIRMATIVE", "NEGATIVE"] * 6
        assert {kind for _, kind in free_speakers} == {"HUMAN", "AGENT"}
        assert len([event for event in events if event.type == "match.finished"]) == 1
        assert [event.sequence for event in events] == list(range(1, actor.state.sequence + 1))
        assert len([event for event in events if event.type == "host.play"]) == 10
        return {
            "interruption": interruption,
            "status": actor.state.status,
            "fixed_speakers": fixed_speakers,
            "free_speakers": free_speakers,
            "finished_events": 1,
            "sequence": actor.state.sequence,
            "rejected_late_callbacks": rejected_late_callbacks,
            "trace": trace,
        }
    finally:
        await actor.close()
        await asyncio.sleep(0)
        assert actor._runner is None  # pyright: ignore[reportPrivateUsage]
        assert actor._timer is None  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interruption",
    ["none", "host_pause", "human_pause", "agent_pause", "free_pause", "system_recovery"],
)
async def test_conference_one_match_reaches_both_summaries_and_finish(interruption: str) -> None:
    await run_conference_round(interruption)

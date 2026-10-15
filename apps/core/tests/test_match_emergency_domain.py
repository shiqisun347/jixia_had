from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from jx_core.matches.domain import MatchCommand, MatchDomainError
from jx_core.matches.service import _state_from_snapshot, state_snapshot
from test_match_recovery_p0_regressions import mixed_free_actor, start_first_human


def operator_payload(actor, *, operation_id=None, context_version=0):
    return {
        "privileged": True,
        "authorized": True,
        "operation_id": str(operation_id or uuid4()),
        "expected_sequence": actor.state.sequence,
        "expected_action_key": actor.state.current_action.action_key,
        "expected_stage_generation": actor.state.stage_generation,
        "expected_speech_id": str(actor.state.current_speech_id)
        if actor.state.current_speech_id
        else None,
        "expected_context_version": context_version,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", [False, True])
async def test_admin_reset_from_running_waits_paused_for_explicit_resume(agent):
    actor, human, _ = mixed_free_actor([100.0])
    await actor.start()
    try:
        await start_first_human(actor, human)
        if agent:
            participant = next(
                item for item in actor.state.current_action.participants if item.agent_profile_id
            )
            actor.state = replace(
                actor.state,
                action_state="AGENT_PREPARING",
                current_speech_id=None,
                current_speaker_user_id=None,
                current_agent_profile_id=participant.agent_profile_id,
                current_speaker_side=participant.side,
                current_speaker_seat_no=participant.seat_no,
            )
        result = await actor.submit(
            MatchCommand(
                type="speech.reset", message_id="admin-reset", payload=operator_payload(actor)
            )
        )
        assert actor.state.status == "PAUSED"
        assert actor.state.action_state == "RECOVERY_REQUIRED"
        assert actor.state.paused_from_action_state == (
            "AGENT_PREPARING" if agent else "HUMAN_READY_TO_START"
        )
        assert result.events[-1].type == "speech.reset"
        assert actor.state.emergency_operation["status"] == "SUCCEEDED"
    finally:
        await actor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["stage.reset", "speech.reset", "speech.skip.begin"])
async def test_cached_operator_receipt_still_validates_target_binding(kind):
    now = [100.0]
    actor, human, _ = mixed_free_actor(now)
    actor.state = replace(actor.state, status="START_PENDING_RUNTIME", action_state="NOT_STARTED")
    await actor.start()
    try:
        await actor.submit(MatchCommand(type="runtime.start", message_id="runtime"))
        await actor.submit(MatchCommand(type="countdown.elapsed", message_id="countdown"))
        await start_first_human(actor, human)
        payload = operator_payload(actor)
        command = MatchCommand(type=kind, message_id="stable-http-command", payload=payload)
        await actor.submit(command)
        before = actor.state
        with pytest.raises(MatchDomainError, match="match_state_conflict"):
            await actor.submit(
                replace(command, payload={**payload, "expected_speech_id": str(uuid4())})
            )
        assert actor.state == before
        duplicate = await actor.submit(command)
        assert duplicate.duplicate and duplicate.state == actor.state
    finally:
        await actor.close()


def test_display_windows_use_actor_clock_and_preserve_paused_remaining():
    now = [100.0]
    actor, _, _ = mixed_free_actor(now)
    actor.state = replace(
        actor.state,
        selection_deadline_mono=103,
        selection_remaining_ms=3000,
        human_wait_deadline_mono=160,
        human_wait_remaining_ms=60_000,
    )
    now[0] = 102.9
    assert actor.view().selection_remaining_ms == 100
    assert actor.view().human_wait_remaining_ms == 57_100
    now[0] = 200
    assert actor.view().selection_remaining_ms == actor.view().human_wait_remaining_ms == 0
    actor.state = replace(
        actor.state,
        status="PAUSED",
        selection_deadline_mono=None,
        human_wait_deadline_mono=None,
        selection_remaining_ms=100,
        human_wait_remaining_ms=57_100,
    )
    assert actor.view().selection_remaining_ms == 100
    assert actor.view().human_wait_remaining_ms == 57_100


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", [False, True])
async def test_agent_source_wait_timeout_is_scoped_to_selected_owner(stale):
    now = [100.0]
    actor, _, _ = mixed_free_actor(now)
    participant = next(
        item for item in actor.state.current_action.participants if item.agent_profile_id
    )
    actor.state = replace(
        actor.state,
        action_state="AGENT_PREPARING",
        current_agent_profile_id=participant.agent_profile_id,
        current_speaker_side=participant.side,
        current_speaker_seat_no=participant.seat_no,
        free_source_speech_id=uuid4(),
    )
    owner = {
        "action_key": actor.state.current_action.action_key,
        "agent_profile_id": str(participant.agent_profile_id),
        "stage_generation": actor.state.stage_generation,
        "opportunity_generation": actor.state.opportunity_generation,
        "opportunity_id": str(actor.state.allocated_opportunity_id or actor.state.opportunity_id)
        if actor.state.allocated_opportunity_id or actor.state.opportunity_id
        else None,
        "source_speech_id": str(actor.state.free_source_speech_id),
    }
    if stale:
        actor.state = replace(
            actor.state, opportunity_generation=actor.state.opportunity_generation + 1
        )
    before = actor.state
    await actor.start()
    try:
        result = await actor.submit(
            MatchCommand(
                type="system.error",
                message_id="source-wait-timeout",
                payload={"error_code": "asr_finalization_pending", "source_wait_owner": owner},
            )
        )
        if stale:
            assert result.events == () and actor.state == before
        else:
            assert actor.state.status == "ERROR"
            assert actor.state.error_code == "asr_finalization_pending"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_exhausted_free_stage_waits_for_its_valid_final_before_advancing():
    now = [100.0]
    actor, human, _ = mixed_free_actor(now)
    actor.state = replace(
        actor.state, free_affirmative_remaining_ms=10_000, free_negative_remaining_ms=0
    )
    await actor.start()
    try:
        speech_id = await start_first_human(actor, human)
        now[0] = 110
        await actor.submit(MatchCommand(type="speech.deadline", message_id="deadline"))
        now[0] = 113
        await actor.submit(MatchCommand(type="hand.window_closed", message_id="cutoff"))
        assert actor.state.status == "RUNNING" and actor.state.action_state == "SPEECH_FINALIZING"
        assert actor.state.current_speech_id == speech_id
        assert (
            actor.view().free_affirmative_remaining_ms
            == actor.view().free_negative_remaining_ms
            == 0
        )
        result = await actor.submit(
            MatchCommand(
                type="asr.finalized",
                message_id="valid-final",
                payload={"speech_id": str(speech_id), "audio_duration_ms": 10_000},
            )
        )
        assert result.state.status == "FINISHED"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_termination_retires_pending_emergency_in_the_same_state_change():
    actor, human, _ = mixed_free_actor([100.0])
    await actor.start()
    try:
        await start_first_human(actor, human)
        await actor.submit(
            MatchCommand(
                type="speech.skip.begin",
                message_id="begin",
                payload=operator_payload(actor),
            )
        )
        old = actor.state.emergency_operation
        await actor.submit(
            MatchCommand(
                type="match.terminate",
                message_id="terminate",
                payload={"privileged": True},
            )
        )
        assert actor.state.status == "TERMINATED"
        assert actor.state.emergency_operation["id"] == old["id"]
        assert actor.state.emergency_operation["status"] == "CANCELLED"
        assert actor.state.emergency_operation["error_code"] == "match_terminated"
        assert old["status"] == "PENDING"
    finally:
        await actor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("offline", [False, True])
async def test_multisegment_human_keeps_identity_and_settles_only_active_time(offline):
    now = [100.0]
    actor, human, _ = mixed_free_actor(now)
    await actor.start()
    try:
        speech_id = await start_first_human(actor, human)
        now[0] += 4
        if offline:
            await actor.submit(
                MatchCommand(
                    type="member.offline",
                    message_id="offline",
                    actor_user_id=human,
                    payload={"connection_epoch": 1},
                )
            )
            now[0] += 10
            await actor.submit(
                MatchCommand(
                    type="member.online",
                    message_id="online",
                    actor_user_id=human,
                    payload={"connection_epoch": 1},
                )
            )
        else:
            await actor.submit(
                MatchCommand(
                    type="match.pause",
                    message_id="pause",
                    actor_user_id=human,
                    payload={"authorized": True},
                )
            )
            now[0] += 10
            await actor.submit(
                MatchCommand(
                    type="match.resume",
                    message_id="resume",
                    actor_user_id=human,
                    payload={"privileged": True, "reasons": []},
                )
            )
            now[0] += 3
            await actor.submit(MatchCommand(type="resume.elapsed", message_id="resumed"))
            await actor.submit(
                MatchCommand(type="speech.start", message_id="second-segment", actor_user_id=human)
            )
        assert actor.state.current_speech_id == speech_id
        now[0] += 4
        await actor.submit(
            MatchCommand(type="speech.finish", message_id="finish", actor_user_id=human)
        )
        assert actor.view().free_affirmative_remaining_ms == 52_000
        now[0] += 10
        await actor.submit(
            MatchCommand(
                type="asr.finalized",
                message_id="final",
                payload={"speech_id": str(speech_id), "audio_duration_ms": 99_000},
            )
        )
        assert actor.view().free_affirmative_remaining_ms == 52_000
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_reset_refunds_entire_current_attempt_and_clears_interim():
    now = [100.0]
    actor, human, _ = mixed_free_actor(now)
    await actor.start()
    try:
        await start_first_human(actor, human)
        now[0] += 7
        await actor.submit(
            MatchCommand(type="speech.finish", message_id="finish", actor_user_id=human)
        )
        assert actor.view().free_affirmative_remaining_ms == 53_000
        command = MatchCommand(type="speech.reset", message_id="reset", actor_user_id=human)
        await actor.submit(command)
        assert actor.view().free_affirmative_remaining_ms == 60_000
        assert actor.state.interim_text == ""
        assert (await actor.submit(command)).duplicate
        assert actor.view().free_affirmative_remaining_ms == 60_000
    finally:
        await actor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed", [0, 4, 10])
async def test_skip_charges_only_elapsed_time_and_completes_once(elapsed):
    now = [100.0]
    actor, human, _ = mixed_free_actor(now)
    await actor.start()
    try:
        old_id = await start_first_human(actor, human)
        now[0] += elapsed
        if elapsed == 10:
            await actor.submit(MatchCommand(type="speech.deadline", message_id="deadline"))
        payload = operator_payload(actor)
        begun = await actor.submit(
            MatchCommand(
                type="speech.skip.begin", message_id="begin", actor_user_id=uuid4(), payload=payload
            )
        )
        operation = begun.state.emergency_operation
        assert operation["speech_id"] == str(old_id)
        assert begun.state.status == "PAUSED"
        assert actor.view().free_affirmative_remaining_ms == 60_000 - elapsed * 1000
        assert not any(event.type == "agent.preparing" for event in begun.events)
        now[0] += 30
        complete_payload = {
            "privileged": True,
            "operation_id": operation["id"],
            "operation_generation": operation["generation"],
            "context_version": operation["context_version"],
            "text": "管理员 AI 辅助占位，未实际播报。",
        }
        complete = await actor.submit(
            MatchCommand(
                type="speech.skip.complete", message_id="complete", payload=complete_payload
            )
        )
        assert [event.type for event in complete.events] == ["speech.skipped"]
        assert complete.events[0].payload["origin"] == "ADMIN_PLACEHOLDER"
        assert actor.state.status == "PAUSED"
        assert actor.view().free_affirmative_remaining_ms == 60_000 - elapsed * 1000
        sequence = actor.state.sequence
        repeated = await actor.submit(
            MatchCommand(
                type="speech.skip.complete",
                message_id="complete-replayed-http",
                payload=complete_payload,
            )
        )
        assert repeated.events == ()
        assert actor.state.sequence == sequence
        assert actor.state.emergency_operation["status"] == "SUCCEEDED"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_stage_reset_requires_real_checkpoint_and_replays_via_normal_resume():
    now = [100.0]
    actor, human, _ = mixed_free_actor(now)
    actor.state = replace(actor.state, status="START_PENDING_RUNTIME", action_state="NOT_STARTED")
    await actor.start()
    try:
        await actor.submit(MatchCommand(type="runtime.start", message_id="runtime"))
        await actor.submit(MatchCommand(type="countdown.elapsed", message_id="countdown"))
        checkpoint = actor.state.stage_checkpoint
        assert checkpoint is not None
        assert checkpoint["match_id"] == str(actor.state.match_id)
        assert checkpoint["affirmative_budget_ms"] == 60_000
        await start_first_human(actor, human)
        now[0] += 4
        payload = operator_payload(actor)
        reset = await actor.submit(
            MatchCommand(type="stage.reset", message_id="stage-reset", payload=payload)
        )
        assert reset.state.status == "PAUSED"
        assert reset.state.paused_from_action_state == "NOT_STARTED"
        assert reset.state.current_speech_id is None
        assert reset.state.stage_generation == checkpoint["generation"] + 1
        assert reset.state.free_affirmative_remaining_ms == 60_000
        assert [event.type for event in reset.events] == ["stage.reset"]
        restored = _state_from_snapshot(actor.state.match_id, state_snapshot(actor.state))
        assert restored.stage_checkpoint == reset.state.stage_checkpoint
        assert restored.emergency_operation == reset.state.emergency_operation
        duplicate = await actor.submit(
            MatchCommand(type="stage.reset", message_id="replayed-http", payload=payload)
        )
        assert duplicate.events == ()
        await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="resume",
                payload={"privileged": True, "reasons": []},
            )
        )
        now[0] += 3
        resumed = await actor.submit(MatchCommand(type="resume.elapsed", message_id="resumed"))
        assert resumed.state.status == "RUNNING"
        assert resumed.state.action_state == "FREE_SELECTING"
        assert resumed.state.current_speech_id is None
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_legacy_active_match_without_checkpoint_cannot_stage_reset():
    now = [100.0]
    actor, human, _ = mixed_free_actor(now)
    await actor.start()
    try:
        await start_first_human(actor, human)
        before = actor.state
        with pytest.raises(MatchDomainError, match="match_state_conflict"):
            await actor.submit(
                MatchCommand(
                    type="stage.reset",
                    message_id="unavailable-reset",
                    payload=operator_payload(actor),
                )
            )
        assert actor.state == before
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_skip_failure_unblocks_recovery_and_rejects_wrong_operation():
    now = [100.0]
    actor, human, _ = mixed_free_actor(now)
    await actor.start()
    try:
        await start_first_human(actor, human)
        await actor.submit(
            MatchCommand(
                type="speech.skip.begin", message_id="begin", payload=operator_payload(actor)
            )
        )
        operation = actor.state.emergency_operation
        args = {
            "privileged": True,
            "operation_id": operation["id"],
            "operation_generation": operation["generation"],
            "context_version": operation["context_version"],
            "error_code": "llm_stream_failed",
        }
        with pytest.raises(MatchDomainError, match="match_state_conflict"):
            await actor.submit(
                MatchCommand(
                    type="match.resume", message_id="premature-resume", payload={"privileged": True}
                )
            )
        with pytest.raises(MatchDomainError, match="stale_callback"):
            await actor.submit(
                MatchCommand(
                    type="speech.skip.complete",
                    message_id="wrong-complete",
                    payload={**args, "operation_id": str(uuid4()), "text": "错误操作"},
                )
            )
        await actor.submit(MatchCommand(type="speech.skip.fail", message_id="failed", payload=args))
        assert actor.state.status == "PAUSED"
        assert actor.state.emergency_operation["status"] == "FAILED"
        assert actor.state.emergency_operation["error_code"] == "llm_stream_failed"
        await actor.submit(
            MatchCommand(
                type="match.resume",
                message_id="resume",
                payload={"privileged": True, "reasons": []},
            )
        )
        assert actor.state.action_state == "RESUME_COUNTDOWN"
    finally:
        await actor.close()

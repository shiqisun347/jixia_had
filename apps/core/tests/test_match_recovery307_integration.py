"""Actual PG, routes and Actor; ASR ownership/provider edge is an explicit controlled boundary."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from jx_core.admin_routes import router as admin_router
from jx_core.auth.dependencies import get_admin_auth, get_database_session
from jx_core.matches.domain import MatchCommand
from jx_core.models import Match, MatchParticipant, RoomMember, Speech
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe
from test_match_emergency_domain import (
    operator_payload,  # pyright: ignore[reportUnknownVariableType]
)
from test_match_handoff_recovery_integration import (  # pyright: ignore[reportUnknownVariableType]
    admin_auth,  # pyright: ignore[reportUnknownVariableType]
    prepare,
    start_human,  # pyright: ignore[reportUnknownVariableType]
)

pytestmark = pytest.mark.integration


async def wait(predicate: Any) -> None:
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_exhausted_asr_preserves_original_then_retry_saves_same_speech(
    agent_fault_probe: Any,
) -> None:
    probe = agent_fault_probe
    now, first, _second, envelopes = await prepare(probe, fixed=True)
    probe.actor.state = replace(
        probe.actor.state,
        speech_remaining_ms=180_000,
        actions=tuple(
            replace(action, duration_seconds=180) for action in probe.actor.state.actions
        ),
    )
    speech_id = cast(UUID, await start_human(probe, first, free=False))
    envelope = envelopes[speech_id]
    now[0] = 131.7
    await probe.manager.handle_asr_failure(envelope=envelope, code="asr_pcm_queue_full")
    assert probe.actor.state.status == "ERROR"
    assert probe.actor.state.current_speech_id == speech_id
    assert probe.actor.state.speech_remaining_ms == 148_300
    async with probe.factory() as session:
        speech = await session.get(Speech, speech_id)
        row = await session.get(Match, probe.actor.state.match_id)
        assert speech is not None and speech.status == "FAILED" and speech.attempt_no == 1
        assert row is not None
        context = row.context_version
    # Explicit replay uses the same durable attempt, with no new microphone start.
    media = probe.manager._speech_runtime
    assert media is not None
    calls = media.start_speech.await_count

    async def replay(_match: Any, _speech: Any, captured: Any, *, deadline_mono: float) -> None:
        assert captured is envelope and deadline_mono > asyncio.get_running_loop().time()
        await probe.manager.finalize_asr_speech(
            envelope=captured,
            final_text="实际恢复原发言",
            first_interim_latency_ms=1,
            final_latency_ms=2,
            audio_duration_ms=31_700,
            audio_storage_path=None,
            audio_recording_error=None,
        )

    media.retry_from_recording.side_effect = replay
    payload = cast(dict[str, Any], operator_payload(probe.actor, context_version=context))
    await probe.manager.submit(
        probe.actor.state.match_id,
        MatchCommand("asr.retry.begin", "explicit-replay", payload=payload),
    )
    await wait(
        lambda: bool(
            probe.actor.state.emergency_operation
            and probe.actor.state.emergency_operation.get("status") == "SUCCEEDED"
        )
    )
    async with probe.factory() as session:
        speech = await session.get(Speech, speech_id)
        rows = list(
            (
                await session.scalars(
                    select(Speech).where(Speech.match_id == probe.actor.state.match_id)
                )
            ).all()
        )
    assert speech is not None and speech.status == "FINALIZED" and speech.attempt_no == 1
    assert speech.display_text == "实际恢复原发言"
    assert len(rows) == 1 and media.start_speech.await_count == calls


async def test_admin_resume_uses_same_current_person_checks_as_authoritative_manager(
    agent_fault_probe: Any,
) -> None:
    probe = agent_fault_probe
    _now, first, _second, _envelopes = await prepare(probe, fixed=True)
    # The helper supplies a synthetic clock, while production Actor construction
    # installs exactly this real serialized command preparation hook.
    probe.actor._prepare_command = probe.manager._prepare_command
    await probe.manager.submit(
        probe.actor.state.match_id,
        MatchCommand(
            "match.pause",
            "pause",
            actor_user_id=first,
            payload={"authorized": True, "privileged": True},
        ),
    )
    auth = await admin_auth(probe)
    async with probe.factory() as session:
        match = await session.get(Match, probe.actor.state.match_id)
        assert match is not None
        people = list(
            (
                await session.scalars(
                    select(MatchParticipant).where(
                        MatchParticipant.match_id == match.id, MatchParticipant.kind == "HUMAN"
                    )
                )
            ).all()
        )
        for person in people:
            session.add(
                RoomMember(
                    room_id=match.room_id,
                    user_id=person.user_id,
                    member_role="DEBATER",
                    online=False,
                    ready=True,
                )
            )
        await session.commit()
    app = FastAPI()
    app.include_router(admin_router)
    app.state.match_runtime_manager = probe.manager
    app.state.runtime = SimpleNamespace(
        settings=SimpleNamespace(app_env="test", cors_origin_list=["https://owned.invalid"])
    )

    async def context() -> Any:
        return auth

    async def database() -> Any:
        async with probe.factory() as session:
            yield session

    app.dependency_overrides[get_admin_auth] = context
    app.dependency_overrides[get_database_session] = database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://owned.invalid",
        headers={"Origin": "https://owned.invalid"},
    ) as client:
        response = await client.post(
            f"/api/admin/matches/{probe.actor.state.match_id}/control", json={"action": "resume"}
        )
    assert response.status_code == 200
    assert (
        probe.actor.state.status == "PAUSED"
        and probe.actor.state.action_state == "RECOVERY_REQUIRED"
    )


@pytest.mark.parametrize("human_priority", [False, True])
async def test_model_wait_persists_absolute_cutoff_and_preserves_human_priority(
    agent_fault_probe: Any, human_priority: bool
) -> None:
    from jx_core.models import AgentFreeDebateDecision, FreeDebateOpportunity
    from jx_core.runtime_identity import CallbackEnvelope

    probe = agent_fault_probe
    now, first, _second, _envelopes = await prepare(probe)
    now[0] = 103
    await probe.manager.submit(
        probe.actor.state.match_id, MatchCommand("hand.window_closed", "three-seconds")
    )
    state = probe.actor.state
    assert state.selection_phase == "MODEL_WAIT" and state.agent_decision_deadline_mono == 108
    async with probe.factory() as session:
        opportunity = await session.get(FreeDebateOpportunity, state.opportunity_id)
        decisions = list(
            (
                await session.scalars(
                    select(AgentFreeDebateDecision).where(
                        AgentFreeDebateDecision.opportunity_id == state.opportunity_id
                    )
                )
            ).all()
        )
    assert opportunity is not None and opportunity.selection_phase == "MODEL_WAIT"
    assert decisions and all(
        row.deadline_at == opportunity.selection_deadline_at for row in decisions
    )
    if human_priority:
        now[0] = 104
        await probe.manager.submit(
            state.match_id, MatchCommand("hand.raise", "human-model-priority", actor_user_id=first)
        )
        assert probe.actor.state.current_speaker_user_id == first
        assert probe.actor.state.action_state == "HUMAN_READY_TO_START"
    else:
        selected = state.agent_decisions[0]
        now[0] = 108
        await probe.manager.report_free_decision(
            envelope=CallbackEnvelope(
                state.match_id,
                None,
                1,
                None,
                None,
                decisions[0].context_version,
                state.opportunity_id,
                state.opportunity_generation,
            ),
            action_key=state.current_action.action_key,
            agent_profile_id=selected.agent_profile_id,
            side=selected.side,
            decision_round_id=state.agent_decision_round_id,
            should_speak=True,
            willingness=None,
            failed=False,
            attempt_no=1,
            duration_ms=8000,
            error_code=None,
            decision_reason="提出反驳",
        )
        assert not probe.actor.state.agent_hand_queue
        await probe.manager.submit(
            state.match_id, MatchCommand("hand.window_closed", "absolute-cutoff")
        )
        assert probe.actor.state.selection_phase == "HUMAN_ONLY_WAIT"

"""Real PostgreSQL text completion gates with explicitly seeded run ownership."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from jx_core.agent.runtime import AgentRun
from jx_core.matches.domain import DebateParticipant
from jx_core.matches.service import state_snapshot
from jx_core.models import AgentGeneration, Match, MatchEvent, MatchParticipant, Room, Speech
from jx_core.runtime_identity import CallbackEnvelope
from test_agent_runtime_fault_integration import AgentFaultProbe
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe

pytestmark = pytest.mark.integration
# The registered run is an explicit callback fixture; no LLM/TTS/PCM is claimed.
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
@pytest.mark.parametrize("history_context", [0, 1])
@pytest.mark.parametrize("before_speech", [True, False])
async def test_full_text_uses_current_generation_and_preserves_pending_identity(
    agent_fault_probe: AgentFaultProbe, history_context: int, before_speech: bool
) -> None:
    probe = agent_fault_probe
    match_id = probe.actor.state.match_id
    current_id = uuid4()
    async with probe.factory() as session:
        participants = list(
            (
                await session.scalars(
                    select(MatchParticipant).where(MatchParticipant.match_id == match_id)
                )
            ).all()
        )
    action = probe.actor.state.current_action
    assert action is not None
    action = replace(
        action,
        action_kind="FREE_DEBATE",
        participants=tuple(
            DebateParticipant(
                side=row.side,
                seat_no=row.seat_no,
                user_id=row.user_id,
                agent_profile_id=row.agent_profile_id,
            )
            for row in participants
        ),
    )
    probe.actor.state = replace(
        probe.actor.state,
        actions=(action,),
        free_affirmative_remaining_ms=60_000,
        free_negative_remaining_ms=60_000,
    )
    async with probe.factory() as session:
        async with session.begin():
            match = await session.get(Match, match_id)
            assert match is not None
            match.context_version = 1
            match.runtime_snapshot = state_snapshot(probe.actor.state)
            room = await session.get(Room, match.room_id)
            assert room is not None
            room.rule_snapshot = {
                "side_size": 4,
                "stages": [{"position": 1, "stage_kind": "FREE_DEBATE", "name": "自由辩论"}],
            }
            session.add_all(
                [
                    AgentGeneration(
                        id=current_id,
                        match_id=match_id,
                        action_key=action.action_key,
                        agent_profile_id=probe.agent_id,
                        context_version=1,
                        attempt_no=1,
                        status="LLM_READY",
                    ),
                    AgentGeneration(
                        match_id=match_id,
                        action_key=action.action_key,
                        agent_profile_id=probe.agent_id,
                        context_version=history_context,
                        attempt_no=2,
                        status="FAILED",
                        error_code="synthetic_previous_run_failed",
                    ),
                ]
            )
    original = CallbackEnvelope(match_id, None, 1, current_id, None, 1, None, None)
    run = AgentRun(
        match_id,
        action.action_key,
        probe.agent_id,
        30_000,
        side="AFFIRMATIVE",
        generation_id=current_id,
        callback_envelope=original,
    )
    probe.runtime._runs[match_id] = run
    probe.run = run
    body = "当前唯一有效完整正文。"
    if before_speech:
        await probe.manager.agent_text_generated(envelope=original, text=body)
        assert probe.manager._pending_agent_full_text[(match_id, current_id)] == (original, body)
        assert original.speech_id is None
    run.speech_id = uuid4()
    run.callback_envelope = replace(original, speech_id=run.speech_id)
    await probe.manager.start_agent_playback(
        envelope=run.callback_envelope,
        agent_profile_id=probe.agent_id,
        audio_storage_path="synthetic-unopened-audio.ogg",
    )
    if not before_speech:
        await probe.manager.agent_text_generated(envelope=run.callback_envelope, text=body)
    async with asyncio.timeout(2):
        while True:
            async with probe.factory() as session:
                speech = await session.get(Speech, run.speech_id)
                assert speech is not None
                decision = await session.scalar(
                    select(MatchEvent.id).where(
                        MatchEvent.match_id == match_id,
                        MatchEvent.event_type == "agent.decision_started",
                    )
                )
                if speech.llm_draft_text == body and decision:
                    break
            await asyncio.sleep(0.005)
    assert speech.display_text == body and speech.generation_id == current_id
    assert (match_id, current_id) not in probe.manager._pending_agent_full_text
    assert original.speech_id is None
    before = probe.actor.state
    changed: dict[str, Any] = {"context_version": 0, "attempt_no": 2, "generation_id": uuid4()}
    for field, value in changed.items():
        await probe.manager.agent_text_generated(
            envelope=replace(original, **{field: value}), text="过期正文不可写入"
        )
        assert probe.actor.state is before
    async with probe.factory() as session:
        speech = await session.get(Speech, run.speech_id)
        assert speech is not None and speech.llm_draft_text == body

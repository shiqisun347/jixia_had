"""Paused Agent retirement is bounded to the old immutable speech identity."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from jx_core.matches.domain import MatchAction, MatchRuntimeState
from jx_core.matches.service import MatchRuntimeManager
from jx_core.models import AgentGeneration, Speech
from jx_core.runtime_identity import CallbackEnvelope


def owned_rows() -> tuple[Any, Any, Any, MatchRuntimeState, Any]:
    match_id, speech_id, agent_id, generation_id = (uuid4() for _ in range(4))
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="AGENT_SPEECH",
        duration_seconds=30,
        agent_profile_id=agent_id,
        speaker_kind="AGENT",
    )
    previous = MatchRuntimeState(
        match_id=match_id,
        status="RUNNING",
        action_state="AGENT_SPEAKING",
        actions=(action,),
        current_speech_id=speech_id,
        current_agent_profile_id=agent_id,
    )
    match = SimpleNamespace(id=match_id, context_version=2)
    speech = SimpleNamespace(
        id=speech_id,
        match_id=match_id,
        agent_profile_id=agent_id,
        generation_id=generation_id,
        speaker_kind="AGENT",
        action_key="1:1",
        status="STARTED",
        ended_at=None,
    )
    generation = SimpleNamespace(
        id=generation_id,
        match_id=match_id,
        agent_profile_id=agent_id,
        action_key="1:1",
        context_version=2,
        call_type="LLM_SPEECH",
        status="PLAYING",
    )

    async def get(model: Any, key: Any, **_: Any) -> Any:
        if model is Speech and key == speech.id:
            return speech
        if model is AgentGeneration and key == generation.id:
            return generation
        return None

    return match, speech, generation, previous, SimpleNamespace(get=AsyncMock(side_effect=get))


@pytest.mark.asyncio
async def test_pause_retires_only_locked_current_agent_speech() -> None:
    match, speech, generation, previous, session = owned_rows()
    await MatchRuntimeManager(None)._retire_paused_agent_speech(session, match, previous)
    assert speech.status == "RESET" and speech.ended_at is not None
    assert generation.status == "PLAYING"
    assert session.get.await_args_list[0].args == (Speech, speech.id)
    assert session.get.await_args_list[1].args == (AgentGeneration, generation.id)
    assert all(call.kwargs == {"with_for_update": True} for call in session.get.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["FINALIZING", "FINALIZED", "FAILED", "RESET"])
async def test_pause_preserves_formal_finalizer_and_terminal_rows(status: str) -> None:
    match, speech, _, previous, session = owned_rows()
    speech.status = status
    ended_at = speech.ended_at = datetime(2026, 10, 15, tzinfo=UTC)
    await MatchRuntimeManager(None)._retire_paused_agent_speech(session, match, previous)
    assert speech.status == status and speech.ended_at == ended_at
    assert session.get.await_count == 1


@pytest.mark.asyncio
async def test_pause_preserves_human_same_speech_and_inconsistent_finalizer_state() -> None:
    match, speech, _, previous, session = owned_rows()
    manager = MatchRuntimeManager(None)
    await manager._retire_paused_agent_speech(
        session,
        match,
        replace(previous, current_agent_profile_id=None, action_state="HUMAN_SPEAKING"),
    )
    session.get.assert_not_awaited()
    await manager._retire_paused_agent_speech(
        session, match, replace(previous, action_state="AGENT_FINALIZING")
    )
    assert session.get.await_count == 1
    assert speech.status == "STARTED" and speech.ended_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["RECOVERY_REQUIRED", "AGENT_SPEAKING"])
async def test_free_finalizer_cannot_revive_reset_speech_or_change_new_context(phase: str) -> None:
    match, speech, generation, _, _ = owned_rows()
    speech.status = "RESET"
    speech.display_text = ""
    speech.finalized_at = None

    class Session:
        def __init__(self) -> None:
            self.get = AsyncMock(return_value=speech)

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        def begin(self) -> Any:
            return self

    session = Session()
    manager = MatchRuntimeManager(lambda: session)
    actor = SimpleNamespace(
        state=SimpleNamespace(
            free_competition_enabled=True,
            action_state=phase,
            current_speech_id=speech.id if phase == "RECOVERY_REQUIRED" else uuid4(),
            current_action=SimpleNamespace(action_kind="FREE_DEBATE"),
        ),
        view=lambda: "unchanged-view",
    )
    manager.get_actor = AsyncMock(return_value=actor)
    manager.submit = AsyncMock()
    result = await manager.finalize_agent_speech(
        envelope=CallbackEnvelope(
            match_id=match.id,
            speech_id=speech.id,
            generation_id=generation.id,
            attempt_no=1,
            context_version=2,
            connection_epoch=None,
            opportunity_id=None,
            opportunity_generation=None,
        ),
        final_text="迟到旧正文",
        llm_draft_text="迟到旧正文",
        audio_storage_path="synthetic-old.ogg",
        audio_duration_ms=100,
        audio_truncated=False,
    )
    assert result == "unchanged-view"
    assert speech.status == "RESET" and speech.display_text == "" and speech.finalized_at is None
    assert match.context_version == 2 and generation.status == "PLAYING"
    manager.submit.assert_not_awaited()
    session.get.assert_awaited_once_with(Speech, speech.id, with_for_update=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("generation_status", ["PLAYING", "FINALIZED"])
async def test_pause_retires_finalizer_speech_without_claiming_generation(
    generation_status: str,
) -> None:
    match, speech, generation, previous, session = owned_rows()
    speech.status = "FINALIZING"
    generation.status = generation_status
    await MatchRuntimeManager(None)._retire_paused_agent_speech(
        session, match, replace(previous, action_state="AGENT_FINALIZING")
    )
    assert speech.status == "RESET" and speech.ended_at is not None
    assert generation.status == generation_status


@pytest.mark.asyncio
async def test_normal_history_edit_does_not_prevent_old_speech_retirement() -> None:
    match, speech, generation, previous, session = owned_rows()
    match.context_version += 1
    await MatchRuntimeManager(None)._retire_paused_agent_speech(session, match, previous)
    assert speech.status == "RESET" and speech.ended_at is not None
    assert generation.context_version == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "field", "value"),
    [
        ("speech", "match_id", "other_uuid"),
        ("speech", "agent_profile_id", "other_uuid"),
        ("speech", "action_key", "2:1"),
        ("speech", "generation_id", "other_uuid"),
        ("speech", "speaker_kind", "HUMAN"),
        ("generation", "match_id", "other_uuid"),
        ("generation", "agent_profile_id", "other_uuid"),
        ("generation", "action_key", "2:1"),
        ("generation", "call_type", "LLM_DECISION"),
        ("generation", "status", "FINALIZED"),
    ],
)
async def test_old_identity_cannot_retire_unrelated_or_finalized_generation(
    row: str, field: str, value: Any
) -> None:
    match, speech, generation, previous, session = owned_rows()
    setattr(
        speech if row == "speech" else generation,
        field,
        uuid4() if value == "other_uuid" else value,
    )
    await MatchRuntimeManager(None)._retire_paused_agent_speech(session, match, previous)
    assert speech.status == "STARTED" and speech.ended_at is None

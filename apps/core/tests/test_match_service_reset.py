from __future__ import annotations

import asyncio
from typing import Any, cast
from uuid import uuid4

import pytest

from jx_core.matches.domain import (
    MatchAction,
    MatchActor,
    MatchCommand,
    MatchDomainError,
    MatchRuntimeState,
)
from jx_core.matches.service import MatchRuntimeManager

# This test deliberately wires the manager's internal actor registry and hook without a database.
# The production typecheck excludes test-only implementation details.
# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false


class ResetRecorder:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def reset_speech(self, match_id: object) -> None:
        self.calls.append(match_id)

    async def reset_agent(self, match_id: object) -> None:
        self.calls.append(match_id)

    async def cancel_free_decision(self, match_id: object) -> None:
        self.calls.append(("decision", match_id))


@pytest.mark.asyncio
async def test_manager_cleans_runtimes_only_after_valid_nonduplicate_reset() -> None:
    match_id = uuid4()
    speaker_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        side="AFFIRMATIVE",
        seat_no=1,
        speaker_user_id=speaker_id,
    )
    manager = MatchRuntimeManager(cast(Any, None))
    speech_runtime = ResetRecorder()
    agent_runtime = ResetRecorder()
    manager.set_speech_runtime(cast(Any, speech_runtime))
    manager.set_agent_runtime(cast(Any, agent_runtime))
    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="RUNNING",
            action_state="HUMAN_SPEAKING",
            actions=(action,),
            current_speech_id=uuid4(),
            current_speaker_user_id=speaker_id,
        ),
        pre_commit=manager._pre_commit,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    manager._actors[match_id] = actor
    try:
        with pytest.raises(MatchDomainError, match="forbidden"):
            await manager.submit(
                match_id,
                MatchCommand(
                    type="speech.reset",
                    message_id="unauthorized-reset",
                    actor_user_id=uuid4(),
                ),
            )
        assert speech_runtime.calls == []
        assert agent_runtime.calls == []

        command = MatchCommand(
            type="speech.reset",
            message_id="authorized-reset",
            actor_user_id=speaker_id,
        )
        first = await manager.submit(match_id, command)
        duplicate = await manager.submit(match_id, command)
        assert first.duplicate is False
        assert duplicate.duplicate is True
        assert speech_runtime.calls == [match_id]
        assert agent_runtime.calls == [match_id]
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_resume_cleans_interrupted_agent_before_countdown() -> None:
    match_id = uuid4()
    agent_id = uuid4()
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="AGENT_SPEECH",
        duration_seconds=30,
        side="AFFIRMATIVE",
        seat_no=1,
        speaker_kind="AGENT",
        agent_profile_id=agent_id,
    )
    manager = MatchRuntimeManager(cast(Any, None))
    runtime = ResetRecorder()
    manager.set_agent_runtime(cast(Any, runtime))
    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="ERROR",
            action_state="RECOVERY_REQUIRED",
            actions=(action,),
            current_agent_profile_id=agent_id,
            paused_from_status="RUNNING",
            paused_from_action_state="AGENT_SPEAKING",
            pause_initiator_user_id=uuid4(),
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    manager._actors[match_id] = actor
    try:
        result = await manager.submit(
            match_id,
            MatchCommand(
                type="match.resume",
                message_id="resume-agent",
                payload={"privileged": True, "reasons": []},
            ),
        )
        assert result.state.action_state == "RESUME_COUNTDOWN"
        assert runtime.calls == [("decision", match_id), match_id]
    finally:
        await actor.close()

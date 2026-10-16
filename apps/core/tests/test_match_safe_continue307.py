"""Shared checked continuation and cancellation of stale automatic intent."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest

from jx_core.matches.domain import MatchActor, MatchCommand
from test_match_asr_recovery307 import active_state


@pytest.mark.asyncio
async def test_resume_rechecks_required_person_when_countdown_expires() -> None:
    reasons: list[str] = []

    async def prepare(_state: Any, command: MatchCommand) -> MatchCommand:
        return replace(command, payload={**command.payload, "reasons": list(reasons)})

    actor = MatchActor(
        replace(
            active_state(),
            status="PAUSED",
            action_state="RECOVERY_REQUIRED",
            paused_from_action_state="HUMAN_SPEAKING",
        ),
        prepare_command=prepare,
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        started = await actor.submit(
            MatchCommand("match.resume", "resume", payload={"privileged": True})
        )
        assert started.state.action_state == "RESUME_COUNTDOWN"
        reasons.append("当前麦克风尚未恢复")
        ended = await actor.submit(MatchCommand("resume.elapsed", "countdown-elapsed"))
        assert ended.state.status == "PAUSED" and ended.state.action_state == "RECOVERY_REQUIRED"
        assert [event.type for event in ended.events] == ["match.resume_check_failed"]
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_new_manual_pause_retires_completed_skip_auto_continue_intent() -> None:
    operation_id = str(uuid4())
    actor = MatchActor(
        replace(
            active_state(),
            status="PAUSED",
            action_state="RECOVERY_REQUIRED",
            emergency_operation={
                "id": operation_id,
                "kind": "speech.skip",
                "status": "SUCCEEDED",
                "auto_continue": True,
            },
        ),
        sleep=lambda _: asyncio.sleep(60),
    )
    await actor.start()
    try:
        await actor.submit(MatchCommand("match.pause", "new-pause", payload={"authorized": True}))
        with pytest.raises(Exception, match="stale_callback"):
            await actor.submit(
                MatchCommand(
                    "match.resume",
                    "late-worker",
                    payload={"privileged": True, "completed_operation_id": operation_id},
                )
            )
        assert actor.state.status == "PAUSED" and actor.state.action_state == "RECOVERY_REQUIRED"
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_noncurrent_offline_person_cannot_pause_current_speaker() -> None:
    state = active_state()
    other = uuid4()
    actor = MatchActor(state, wall_clock=lambda: 100, sleep=lambda _: asyncio.sleep(60))
    await actor.start()
    try:
        await actor.submit(
            MatchCommand(
                "member.offline",
                "other-offline",
                actor_user_id=other,
                payload={"connection_epoch": 1, "offline_since_ms": 1},
            )
        )
        await actor.submit(
            MatchCommand("offline.expired", "other-timeout", payload={"user_id": str(other)})
        )
        assert actor.state.status == "RUNNING"
        assert actor.state.current_speech_id == state.current_speech_id
    finally:
        await actor.close()

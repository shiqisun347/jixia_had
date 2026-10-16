"""Human priority and model decisions share explicit absolute cutoffs."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

from jx_core.matches.domain import MatchCommand, MatchDomainError
from test_experiment_free_debate_domain import _actor, _decision_command


@pytest.mark.asyncio
async def test_pending_decision_gets_independent_window_and_new_human_has_priority() -> None:
    now = [100.0]
    actor, _action, human, _agent = _actor(now)
    await actor.start()
    try:
        now[0] = 103
        result = await actor.submit(MatchCommand("hand.window_closed", "human-three-seconds"))
        assert result.state.selection_phase == "MODEL_WAIT"
        assert result.state.selection_deadline_mono == 108
        now[0] = 104
        raised = await actor.submit(
            MatchCommand("hand.raise", "human-during-model-wait", actor_user_id=cast(UUID, human))
        )
        assert raised.state.action_state == "HUMAN_READY_TO_START"
        assert raised.state.current_speaker_user_id == human
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_model_deadline_rejects_late_result_even_before_queued_timer() -> None:
    now = [100.0]
    actor, action, _human, agent = _actor(now)
    await actor.start()
    try:
        now[0] = 103
        await actor.submit(MatchCommand("hand.window_closed", "human-three-seconds"))
        now[0] = 108
        command = _decision_command(actor, action, agent, should_speak=True)
        command = replace(command, payload={**command.payload, "decision_reason": "存在反驳"})
        with pytest.raises(MatchDomainError, match="stale_callback"):
            await actor.submit(command)
        assert not actor.state.agent_hand_queue
    finally:
        await actor.close()

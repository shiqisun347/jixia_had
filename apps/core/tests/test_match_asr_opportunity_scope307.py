"""Fixed ASR identities must remain valid after an emergency retires an opportunity."""

from dataclasses import replace
from typing import Any, cast
from uuid import uuid4

import pytest

from jx_core.matches.service import MatchRuntimeManager
from test_match_recovery_p0_regressions import mixed_free_actor

# pyright: reportPrivateUsage=false


@pytest.mark.parametrize("generation", [0, 1, 17])
async def test_fixed_callback_has_no_opportunity_generation_without_opportunity(
    generation: int,
) -> None:
    actor, human, _ = mixed_free_actor([100.0])
    actor.state = replace(
        actor.state,
        opportunity_id=None,
        allocated_opportunity_id=None,
        opportunity_generation=generation,
    )
    manager = MatchRuntimeManager(cast(Any, None))
    manager._actors[actor.state.match_id] = actor
    envelope = await manager._speech_callback_envelope(
        match_id=actor.state.match_id, speech_id=uuid4(), user_id=human
    )
    assert envelope.opportunity_id is None
    assert envelope.opportunity_generation is None
    assert actor.state.opportunity_generation == generation


async def test_actual_free_opportunity_keeps_its_generation() -> None:
    actor, human, _ = mixed_free_actor([100.0])
    opportunity = uuid4()
    actor.state = replace(
        actor.state,
        opportunity_id=opportunity,
        allocated_opportunity_id=None,
        opportunity_generation=17,
    )
    manager = MatchRuntimeManager(cast(Any, None))
    manager._actors[actor.state.match_id] = actor
    envelope = await manager._speech_callback_envelope(
        match_id=actor.state.match_id, speech_id=uuid4(), user_id=human
    )
    assert envelope.opportunity_id == opportunity
    assert envelope.opportunity_generation == 17

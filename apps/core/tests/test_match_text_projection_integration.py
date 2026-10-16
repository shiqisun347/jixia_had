"""Real PG projection controls; these do not claim a complete media match."""

from dataclasses import replace

import pytest

from jx_core.matches.routes import _snapshot_text_metadata
from jx_core.models import Match, Speech
from test_agent_runtime_fault_integration import AgentFaultProbe
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe

pytestmark = pytest.mark.integration
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
async def test_pending_source_is_scoped_and_drops_after_finalization(
    agent_fault_probe: AgentFaultProbe,
) -> None:
    probe = agent_fault_probe
    match_id = probe.actor.state.match_id
    async with probe.factory() as session:
        async with session.begin():
            source = Speech(
                match_id=match_id,
                action_key="1:1",
                speaker_kind="AGENT",
                agent_profile_id=probe.agent_id,
                side="AFFIRMATIVE",
                seat_no=2,
                status="FINALIZING",
            )
            session.add(source)
            match = await session.get(Match, match_id)
            assert match is not None
            match.context_version = 7
        source_id = source.id
    view = replace(
        probe.actor.view(),
        state=replace(probe.actor.state, free_source_speech_id=source_id, current_speech_id=None),
    )
    async with probe.factory() as session:
        metadata = await _snapshot_text_metadata(session, view)
    assert metadata["context_version"] == 7
    assert metadata["pending_source"].model_dump() == {
        "speech_id": source_id,
        "speaker_kind": "AGENT",
        "side": "AFFIRMATIVE",
        "seat_no": 2,
        "status": "FINALIZING",
    }
    current_view = replace(view, state=replace(view.state, current_speech_id=source_id))
    async with probe.factory() as session:
        assert (await _snapshot_text_metadata(session, current_view))["pending_source"] is None
        source = await session.get(Speech, source_id)
        assert source is not None
        source.status = "FINALIZED"
        await session.commit()
    async with probe.factory() as session:
        assert (await _snapshot_text_metadata(session, view))["pending_source"] is None

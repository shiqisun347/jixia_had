"""Actual PG history selection and capture input; no provider or media calls."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from jx_core.agent.runtime import AgentRun
from jx_core.models import (
    FreeDebateOpportunity,
    Match,
    MatchParticipant,
    Speech,
)
from test_agent_runtime_fault_integration import AgentFaultProbe
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe

pytestmark = pytest.mark.integration
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_status", "source_text", "content_state"),
    [
        ("STARTED", "PG已生成的对方完整论点", "PROVISIONAL"),
        ("FINALIZING", "PG正在收尾的对方论点", "PROVISIONAL"),
        ("FINALIZING", None, "PENDING"),
    ],
)
async def test_real_pg_source_context_preserves_status_and_excludes_revoked_attempts(
    agent_fault_probe: AgentFaultProbe,
    source_status: str,
    source_text: str | None,
    content_state: str,
) -> None:
    probe = agent_fault_probe
    match_id = probe.actor.state.match_id
    async with probe.factory() as session:
        async with session.begin():
            human = await session.scalar(
                select(MatchParticipant).where(
                    MatchParticipant.match_id == match_id,
                    MatchParticipant.side == "NEGATIVE",
                    MatchParticipant.seat_no == 1,
                )
            )
            assert human is not None
            match = await session.get(Match, match_id, with_for_update=True)
            assert match is not None
            rows = [
                Speech(
                    match_id=match_id,
                    action_key="1:1",
                    user_id=human.user_id,
                    speaker_kind="HUMAN",
                    side="NEGATIVE",
                    seat_no=1,
                    status=status,
                    display_text=content,
                    origin=origin,
                )
                for status, content, origin in [
                    ("FINALIZED", "PG正式有效历史", "ORIGINAL"),
                    ("RESET", "PG已作废不应出现", "ORIGINAL"),
                    ("FAILED", "PG失败草稿不应出现", "ORIGINAL"),
                    ("STARTED", "PG无关在途不应出现", "ORIGINAL"),
                    ("FINALIZED", "PG管理员AI辅助占位", "ADMIN_PLACEHOLDER"),
                    (source_status, source_text, "ORIGINAL"),
                ]
            ]
            session.add_all(rows)
            await session.flush()
            source = rows[-1]
            opportunity = FreeDebateOpportunity(
                id=uuid4(),
                match_id=match_id,
                sequence_no=1,
                side="AFFIRMATIVE",
                trigger_kind="HUMAN_SPEECH",
                source_speech_id=source.id,
                context_version=match.context_version,
                opportunity_generation=1,
            )
            session.add(opportunity)
            match.runtime_snapshot = {
                **match.runtime_snapshot,
                "opportunity_id": str(opportunity.id),
                "opportunity_generation": 1,
            }
            source_id = source.id
            source_before = (source.status, source.display_text, source.origin)
            snapshot_before = json.dumps(match.runtime_snapshot, sort_keys=True)
    config = await probe.runtime._load_config(
        AgentRun(
            match_id=match_id,
            action_key="1:1",
            agent_profile_id=probe.agent_id,
            duration_ms=30_000,
            side="AFFIRMATIVE",
        )
    )
    assert [item["role"] for item in config.messages] == ["system", "user", "user"]
    context = json.loads(config.messages[-1]["content"])
    assert len(context["history"]) == 3
    by_id = {item["speech_id"]: item for item in context["history"]}
    assert by_id[str(source_id)]["status"] == source_status
    assert by_id[str(source_id)]["content_state"] == content_state
    assert context["source_speech"]["speech_id"] == str(source_id)
    assert context["source_speech"]["included_in_history"]
    assert all(item["participant_id"] for item in context["history"])
    placeholders = [item for item in context["history"] if item["origin"] == "ADMIN_PLACEHOLDER"]
    assert len(placeholders) == 1
    assert placeholders[0]["delivery"] == "NOT_SPOKEN_ADMIN_PLACEHOLDER"
    assert all(
        forbidden not in config.messages[-1]["content"]
        for forbidden in ["PG已作废不应出现", "PG失败草稿不应出现", "PG无关在途不应出现"]
    )
    async with probe.factory() as session:
        source = await session.get(Speech, source_id)
        match = await session.get(Match, match_id)
        assert source is not None and match is not None
        assert (source.status, source.display_text, source.origin) == source_before
        assert json.dumps(match.runtime_snapshot, sort_keys=True) == snapshot_before

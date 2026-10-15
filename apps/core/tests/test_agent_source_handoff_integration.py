"""Held human final -> original Agent callbacks, with real Actor/Manager/PG."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

import jx_core.agent.runtime as runtime_module
from jx_core.agent.llm import LlmStreamResult
from jx_core.agent.tts import TtsStreamResult
from jx_core.matches.domain import MatchCommand
from jx_core.models import AgentGeneration, Match, MatchParticipant, Room, Speech
from test_agent_failure_recovery_integration import (
    RecoveryDecoder,
    RecoverySource,
    wait_for_condition,
)
from test_agent_runtime_fault_integration import AgentFaultProbe
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe
from test_match_handoff_recovery_integration import (
    finalize,  # pyright: ignore[reportUnknownVariableType]
    prepare,
    start_human,  # pyright: ignore[reportUnknownVariableType]
)

pytestmark = pytest.mark.integration
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
@pytest.mark.parametrize("source_terminal", ["FINALIZED", "FAILED"])
async def test_held_source_then_actual_agent_finishes_with_committed_context(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    source_terminal: str,
) -> None:
    """Delay actual ASR final past the legal window, without bypassing its fence.

    The shared fixture contains eight real PG participant rows, two humans and
    two Agents per side. Only provider, decoder and RTC boundaries are synthetic.
    No generation, final Agent Speech or context increment is test-manufactured.
    """
    probe = agent_fault_probe
    match_id = probe.actor.state.match_id
    body_requests: list[dict[str, Any]] = []
    sources: list[RecoverySource] = []

    async def stream_chat(**values: Any) -> LlmStreamResult:
        if values["max_tokens"] == 32:
            return LlmStreamResult('{"should_speak":false,"decision_reason":"已有回应"}', 1, 2, 8)
        body_requests.append(json.loads(values["messages"][-1]["content"]))
        await values["on_delta"]("这是依据已完成对方发言的合成回应。")
        await asyncio.sleep(0.02)
        return LlmStreamResult("这是依据已完成对方发言的合成回应。", 1, 2, 16)

    async def close() -> None:
        return None

    class TTS:
        def __init__(self, **_: Any) -> None:
            pass

        async def synthesize(self, chunks: Any, **values: Any) -> TtsStreamResult:
            async for _ in chunks:
                await values["on_audio"](b"owned-synthetic-audio")
            await asyncio.sleep(0.02)
            return TtsStreamResult(uuid4(), 21, 1, 2)

        async def close(self) -> None:
            return None

    async def publisher(*_: Any, **__: Any) -> Any:
        source = RecoverySource()
        source.allow_drain.set()
        sources.append(source)
        return SimpleNamespace(disconnect=close), source

    def client(**_: Any) -> SimpleNamespace:
        return SimpleNamespace(stream_chat=stream_chat, close=close)

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", client)
    monkeypatch.setattr(runtime_module, "QwenTtsConnection", TTS)
    monkeypatch.setattr(runtime_module, "IncrementalOpusDecoder", RecoveryDecoder)
    monkeypatch.setattr(probe.runtime, "_connect_publisher", publisher)

    now, first, _, envelopes = await prepare(probe)
    async with probe.factory() as session:
        async with session.begin():
            agent = await session.scalar(
                select(MatchParticipant).where(
                    MatchParticipant.match_id == match_id,
                    MatchParticipant.side == "NEGATIVE",
                    MatchParticipant.seat_no == 3,
                )
            )
            match = await session.get(Match, match_id)
            assert agent is not None and agent.agent_profile_id is not None and match is not None
            agent_id = agent.agent_profile_id
            room = await session.get(Room, match.room_id)
            assert room is not None
            room.rule_snapshot = {
                "side_size": 4,
                "stages": [{"position": 7, "name": "自由辩论", "stage_kind": "FREE_DEBATE"}],
            }
    source_id = cast(UUID, await start_human(probe, first))
    assert source_id is not None
    state = probe.actor.state
    assert probe.actor.state.current_action is not None
    await probe.manager.submit(
        match_id,
        MatchCommand(
            type="free.agent_decision_start",
            message_id="controlled-decision-start",
            payload={
                "opportunity_id": str(state.opportunity_id),
                "opportunity_generation": state.opportunity_generation,
                "source_speech_id": str(source_id),
                "trigger_kind": "HUMAN_SPEECH",
            },
        ),
    )
    await probe.manager.submit(
        match_id,
        MatchCommand(
            type="free.agent_decision_result",
            message_id="controlled-agent-hand",
            payload={
                "action_key": probe.actor.state.current_action.action_key,
                "agent_profile_id": str(agent_id),
                "decision_round_id": str(probe.actor.state.agent_decision_round_id),
                "should_speak": True,
                "decision_reason": "受控候选就绪",
            },
        ),
    )
    now[0] = 110
    await probe.manager.submit(
        match_id, MatchCommand(type="speech.deadline", message_id="source-deadline")
    )
    probe.manager.set_agent_runtime(probe.runtime)
    now[0] = 113
    await probe.manager.submit(
        match_id, MatchCommand(type="hand.window_closed", message_id="legal-window-cutoff")
    )
    await asyncio.sleep(0.15)
    assert probe.actor.state.action_state == "AGENT_PREPARING"
    assert not body_requests and match_id not in probe.runtime._runs
    async with probe.factory() as session:
        match = await session.get(Match, match_id)
        source = await session.get(Speech, source_id)
        assert match is not None and source is not None and source.status == "FINALIZING"
        context_before = match.context_version
        assert not (
            await session.scalars(
                select(AgentGeneration).where(AgentGeneration.match_id == match_id)
            )
        ).all()
    # This is the original public callback with its immutable envelope from the
    # actual speech.start event. It must update only the original human row.
    if source_terminal == "FINALIZED":
        await finalize(probe.manager, envelopes[source_id])
    else:
        await probe.manager.handle_asr_failure(
            envelope=envelopes[source_id], code="asr_task_failed"
        )
    await wait_for_condition(lambda: bool(sources and sources[0].closed), timeout=3)
    await wait_for_condition(lambda: match_id not in probe.runtime._runs, timeout=3)
    expected_context = context_before + (source_terminal == "FINALIZED")
    assert body_requests and body_requests[0]["context_version"] == expected_context
    if source_terminal == "FINALIZED":
        assert body_requests[0]["source_speech"]["speech_id"] == str(source_id)
        assert body_requests[0]["source_speech"]["status"] == source_terminal
        assert any(
            item["speech_id"] == str(source_id) and item["content_state"] == "FINAL"
            for item in body_requests[0]["history"]
        )
    else:
        assert body_requests[0]["source_speech"] is None
        assert all(
            item["speech_id"] != str(source_id) for item in body_requests[0]["history"]
        )
    assert sources[0].frames
    assert probe.actor.state.status == "RUNNING"
    assert probe.actor.state.action_state == "FREE_SELECTING"
    async with probe.factory() as session:
        generation = await session.scalar(
            select(AgentGeneration).where(AgentGeneration.match_id == match_id)
        )
        assert generation is not None and generation.status == "FINALIZED"
        assert generation.context_version == expected_context
        new_speech = await session.scalar(
            select(Speech).where(Speech.generation_id == generation.id)
        )
        old_speech = await session.get(Speech, source_id)
        assert new_speech is not None and new_speech.status == "FINALIZED"
        assert new_speech.agent_profile_id == agent_id and new_speech.side == "NEGATIVE"
        assert old_speech is not None and old_speech.status == source_terminal
        assert old_speech.side == "AFFIRMATIVE" and old_speech.user_id == first

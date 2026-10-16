"""ASR completion preserves the authoritative microphone end metadata in PG."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from typing import Any, cast

import pytest

from jx_core.matches.domain import MatchCommand
from jx_core.models import Match, Speech
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe
from test_match_emergency_domain import (
    operator_payload,  # pyright: ignore[reportUnknownVariableType]
)
from test_match_handoff_recovery_integration import prepare, start_human

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("end_command", "final_reason", "retry"),
    [
        ("speech.deadline", None, False),
        ("speech.finish", "", False),
        ("speech.finish", "EARLY", False),
        ("speech.finish", None, True),
    ],
)
async def test_asr_final_does_not_rewrite_capture_end(
    agent_fault_probe: Any, end_command: str, final_reason: str | None, retry: bool
) -> None:
    probe = agent_fault_probe
    now, user, _next, owners = await prepare(probe, fixed=True)
    manager, actor = probe.manager, probe.actor
    match_id = actor.state.match_id
    speech_id = await start_human(probe, user, free=False)
    assert speech_id is not None
    owner = owners[speech_id]
    now[0] = 110 if end_command == "speech.deadline" else 104
    ending = await manager.submit(
        match_id, MatchCommand(end_command, "capture-end", actor_user_id=user)
    )
    expected_reason = "TIME_LIMIT" if end_command == "speech.deadline" else "EARLY"
    assert any(
        event.type == "speech.finalizing" and event.payload["reason"] == expected_reason
        for event in ending.events
    )
    async with probe.factory() as session:
        captured = await session.get(Speech, speech_id)
        assert captured is not None and captured.status == "FINALIZING"
        assert captured.finish_reason == expected_reason and captured.ended_at is not None
        ended_at, attempt = captured.ended_at, captured.attempt_no
    if retry:
        await manager.handle_asr_failure(envelope=owner, code="asr_task_failed")
        assert actor.state.status == "ERROR" and actor.state.current_speech_id == speech_id
        async with probe.factory() as session:
            failed = await session.get(Speech, speech_id)
            match = await session.get(Match, match_id)
            assert failed is not None and failed.status == "FAILED"
            assert failed.ended_at == ended_at and failed.finish_reason == expected_reason
            assert match is not None
            payload = cast(
                dict[str, Any], operator_payload(actor, context_version=match.context_version)
            )
        failed_remaining = actor.state.speech_remaining_ms
        await manager.submit(
            match_id, MatchCommand("asr.retry.begin", "explicit-recording-retry", payload=payload)
        )
        assert actor.state.current_speech_id == speech_id
        assert actor.state.speech_remaining_ms == failed_remaining
    # Simulate delayed ASR without waiting out the speech's authoritative clock.
    now[0] += 9
    await asyncio.sleep(0.01)
    payload = {"speech_id": str(speech_id), "final_text": "公开受控识别完成正文"}
    if final_reason is not None:
        payload["reason"] = final_reason
    result = await manager.submit(
        match_id,
        MatchCommand(
            "asr.finalized", "delayed-asr-final", payload=payload, asr_callback_envelope=owner
        ),
    )
    assert any(event.type == "speech.finished" for event in result.events)
    async with probe.factory() as session:
        stored = await session.get(Speech, speech_id)
        assert stored is not None and stored.status == "FINALIZED"
        assert stored.id == speech_id and stored.attempt_no == attempt
        assert stored.display_text == payload["final_text"]
        assert stored.finish_reason == expected_reason
        assert stored.ended_at == ended_at
        assert stored.finalized_at is not None and stored.finalized_at > ended_at
    assert manager._speech_runtime.start_speech.await_count == 1

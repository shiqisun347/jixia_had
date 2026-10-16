"""Actual Actor/PG/Receiver admission; fake PCM/provider, no network or Provider."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from jx_core.matches.domain import MatchCommand
from jx_core.models import MatchEvent, Speech
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe
from test_asr import FakeSocket
from test_match_asr_receiver307_integration import connect, wait

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("mode", ["fractional_before", "aligned_before", "fractional_expired"])
async def test_fractional_authority_budget_cannot_fail_last_in_time_pcm_frame(
    agent_fault_probe: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    socket = FakeSocket()

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        return socket

    probe = agent_fault_probe
    runtime, receiver, stream, user = await connect(probe, monkeypatch, factory)
    actor, manager = probe.actor, probe.manager
    actor._prepare_command = manager._prepare_command
    now = [100.0]
    actor._clock = lambda: now[0]
    budget_ms = 88900 if mode == "aligned_before" else 88889
    actor.state = replace(actor.state, speech_remaining_ms=budget_ms)
    baseline = set(asyncio.all_tasks())
    out = tmp_path
    try:
        await manager.submit(
            actor.state.match_id,
            MatchCommand("speech.start", "fractional-owned-start", actor_user_id=user),
        )
        speech_id = actor.state.current_speech_id
        session = receiver._speech
        assert speech_id is not None and session is not None
        assert session.max_audio_duration_ms == budget_ms
        deadline = actor.state.speech_deadline_mono
        assert deadline is not None and deadline == session.input_deadline_mono
        # Accelerated preceding input is explicit: only this final admission
        # boundary is controlled, not an 88-second media wallclock qualification.
        for _ in range(888):
            await stream.emit()
        await wait(lambda: len(session.pcm_snapshot()) == 88800 * 32)
        before_bytes = len(session.pcm_snapshot())
        now[0] = deadline + 0.001 if mode == "fractional_expired" else deadline - 0.002
        frames_before = stream.frames
        await stream.emit()  # Actual Receiver receives exactly one 100ms/3200B chunk.
        await wait(lambda: stream.frames > frames_before)
        await wait(
            lambda: (
                receiver._retained_speech is not None
                or len(session.pcm_snapshot()) > before_bytes
                or session._input_sealed.is_set()
            )
        )
        if receiver._retained_speech is not None:
            await wait(lambda: actor.state.status == "ERROR")
        async with probe.factory() as sql:
            speech = await sql.get(Speech, speech_id)
            assert speech is not None
            events = list(
                (
                    await sql.scalars(
                        select(MatchEvent)
                        .where(MatchEvent.match_id == actor.state.match_id)
                        .order_by(MatchEvent.sequence)
                    )
                ).all()
            )
        row = {
            "mode": mode,
            "budget_ms": budget_ms,
            "input_deadline_mono": deadline,
            "observed_input_clock": now[0],
            "clock_before_deadline": now[0] < deadline,
            "remaining_ms": actor.view().speech_remaining_ms,
            "max_history_bytes": session._max_bytes,
            "history_before_bytes": before_bytes,
            "history_after_bytes": len(session.pcm_snapshot()),
            "last_frame_samples": 1600,
            "last_frame_bytes": 3200,
            "actor_status": actor.state.status,
            "action_state": actor.state.action_state,
            "error_code": actor.state.error_code,
            "speech_status": speech.status,
            "speech_error_code": speech.asr_error_code,
            "recorded_duration_ms": speech.audio_duration_ms,
            "input_sealed": session._input_sealed.is_set(),
            "retained_source": runtime.recovery_source_available(actor.state.match_id, speech_id),
            "events": [{"sequence": e.sequence, "type": e.event_type} for e in events],
            "finish_task_sent": "finish-task" in socket.sent_actions,
            "provider_calls": 0,
            "actual_media_wallclock_test": False,
        }
        (out / f"{mode}.json").write_text(json.dumps(row, indent=2) + "\n")
        # Product assertions precede fixture cleanup; original bad state is retained.
        assert actor.state.status == "RUNNING", row
        assert speech.status == "STARTED", row
        assert not any(
            e.event_type in {"asr.failed", "match.error", "speech.reset"} for e in events
        )
        assert "finish-task" not in socket.sent_actions
        if mode == "fractional_expired":
            assert len(session.pcm_snapshot()) == before_bytes and not session.input_admitted
        else:
            assert len(session.pcm_snapshot()) == before_bytes + 3200
    finally:
        await runtime.close()
        await manager.close()
        await wait(lambda: not [t for t in asyncio.all_tasks() - baseline if not t.done()])
        assert stream.closed and not runtime._receivers
        (out / f"{mode}-fixture.json").write_text(
            json.dumps({"stream_closed": True, "receivers": 0, "pending_owned_tasks": 0}) + "\n"
        )

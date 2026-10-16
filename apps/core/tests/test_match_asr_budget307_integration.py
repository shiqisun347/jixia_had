"""Actual Actor/PG/receiver input deadlines; only provider and RTC transports controlled."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from dataclasses import replace
from time import monotonic
from typing import Any

import pytest
from sqlalchemy import select

from jx_core.matches.domain import MatchCommand
from jx_core.models import Match, MatchEvent, Speech
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe
from test_asr import FakeSocket
from test_match_asr_receiver307_integration import connect, wait

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("mode", ["queued", "precommit", "overflow", "held_timeout"])
async def test_deadline_pcm_boundary_preserves_exact_audio_and_recovery(
    agent_fault_probe: Any, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    socket = FakeSocket()

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        return socket

    probe = agent_fault_probe
    runtime, receiver, stream, user = await connect(probe, monkeypatch, factory)
    actor, manager = probe.actor, probe.manager
    actor._prepare_command = manager._prepare_command
    now = [100.0]
    actor._clock = monotonic if mode == "held_timeout" else lambda: now[0]
    audio_ms = 500 if mode == "held_timeout" else 10_000
    if mode == "held_timeout":
        actor.state = replace(actor.state, speech_remaining_ms=audio_ms)
    release, entered = asyncio.Event(), asyncio.Event()
    baseline = set(asyncio.all_tasks())
    pending: asyncio.Task[Any] | None = None
    try:
        await manager.submit(
            actor.state.match_id,
            MatchCommand("speech.start", "deadline-budget-start", actor_user_id=user),
        )
        speech_id = actor.state.current_speech_id
        assert speech_id is not None
        session = receiver._speech
        assert (
            session is not None and session.input_deadline_mono == actor.state.speech_deadline_mono
        )
        for _ in range(audio_ms // 100):
            await stream.emit()
        await wait(lambda: len(session.pcm_snapshot()) == audio_ms * 32)
        if mode != "overflow":
            now[0] = 110
        if mode == "held_timeout":
            await asyncio.sleep(0.51)
        if mode in {"precommit", "held_timeout"}:
            original = actor._pre_commit

            async def held(previous: Any, candidate: Any, events: Any, command: Any) -> None:
                if command.type == "speech.deadline":
                    entered.set()
                    await release.wait()
                await original(previous, candidate, events, command)

            actor._pre_commit = held
            pending = asyncio.create_task(
                manager.submit(
                    actor.state.match_id, MatchCommand("speech.deadline", "deadline-held")
                )
            )
            await asyncio.wait_for(entered.wait(), 2)
        if mode != "held_timeout":
            await stream.emit()
        await wait(
            lambda: (
                session._input_sealed.is_set()
                if mode != "overflow"
                else receiver._retained_speech is not None
            )
        )
        if mode == "overflow":
            await wait(lambda: actor.state.status == "ERROR")
            expected_error = "asr_pcm_queue_full"
        else:
            assert len(session.pcm_snapshot()) == audio_ms * 32
            assert not session.input_closed and "finish-task" not in socket.sent_actions
            assert actor.state.status == "RUNNING" and actor.state.action_state == "HUMAN_SPEAKING"
            if mode == "held_timeout":
                # Real unique 20s bound, not a patched short policy or fixture cleanup.
                started = asyncio.get_running_loop().time()
                await wait(lambda: receiver._speech is None, seconds=22)
                elapsed = asyncio.get_running_loop().time() - started
                assert 19 <= elapsed <= 22
                assert runtime.recovery_source_available(actor.state.match_id, speech_id)
                closed_at_detach = socket.closed
                await wait(lambda: socket.closed, seconds=2)
                assert socket.closed
                print(
                    {
                        "logical_retirement_seconds": elapsed,
                        "socket_closed_at_detach": closed_at_detach,
                        "socket_closed_within_existing_cleanup_budget": socket.closed,
                    }
                )
                release.set()
                assert pending is not None
                await asyncio.wait_for(pending, 3)
                await wait(lambda: actor.state.status == "ERROR")
                expected_error = "asr_finalization_timeout"
            else:
                release.set()
                if pending is not None:
                    await asyncio.wait_for(pending, 3)
                else:
                    await manager.submit(
                        actor.state.match_id,
                        MatchCommand("speech.deadline", "deadline-after-queued-frame"),
                    )
                await wait(lambda: actor.state.current_action_index == 1)
                expected_error = None
        async with probe.factory() as sql:
            speech = await sql.get(Speech, speech_id)
            assert speech is not None
            events = list(
                (
                    await sql.scalars(
                        select(MatchEvent).where(MatchEvent.match_id == actor.state.match_id)
                    )
                ).all()
            )
        assert speech.audio_duration_ms == audio_ms
        assert speech.asr_error_code == expected_error
        assert speech.status == ("FAILED" if expected_error else "FINALIZED")
        assert not any(e.event_type == "speech.reset" for e in events)
        if expected_error:
            assert actor.state.status == "ERROR" and actor.state.asr_failed_speech_id == speech_id
            assert runtime.recovery_source_available(actor.state.match_id, speech_id)
        else:
            assert actor.state.status == "RUNNING" and speech.finish_reason == "TIME_LIMIT"
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await runtime.close()
        await manager.close()
        await wait(lambda: not [t for t in asyncio.all_tasks() - baseline if not t.done()])
        assert stream.closed and not runtime._receivers


@pytest.mark.parametrize("acknowledged", [False, True])
async def test_real_timer_commit_failure_preserves_recording_without_overwriting_unknown_ack(
    agent_fault_probe: Any, monkeypatch: pytest.MonkeyPatch, acknowledged: bool
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
    timer_release = asyncio.Event()
    original_commit = actor._commit
    observed = asyncio.Event()
    baseline = set(asyncio.all_tasks())

    async def sleep(_seconds: float) -> None:
        await timer_release.wait()

    async def failed_commit(previous: Any, candidate: Any, events: Any, command: Any) -> None:
        if command.type == "speech.deadline":
            if acknowledged:
                await original_commit(previous, candidate, events, command)
            observed.set()
            raise OSError(
                "controlled lost commit acknowledgement"
                if acknowledged
                else "controlled rolled back commit"
            )
        await original_commit(previous, candidate, events, command)

    actor._sleep = sleep
    actor._commit = failed_commit
    try:
        await manager.submit(
            actor.state.match_id,
            MatchCommand("speech.start", "failure-budget-start", actor_user_id=user),
        )
        speech_id = actor.state.current_speech_id
        assert speech_id is not None
        await stream.emit()
        session = receiver._speech
        assert session is not None
        await wait(lambda: len(session.pcm_snapshot()) == 3200)
        now[0] = 110
        timer_release.set()
        await asyncio.wait_for(observed.wait(), 2)
        await wait(lambda: receiver._speech is None)
        if not acknowledged:
            await wait(lambda: actor.state.status == "ERROR")
        else:
            await wait(lambda: actor._timer is None and not actor._processing)
            await asyncio.sleep(0.05)
        async with probe.factory() as sql:
            speech = await sql.get(Speech, speech_id)
            match = await sql.get(Match, actor.state.match_id)
            events = list(
                (
                    await sql.scalars(
                        select(MatchEvent).where(MatchEvent.match_id == actor.state.match_id)
                    )
                ).all()
            )
        assert speech is not None and match is not None
        info = runtime.recovery_source_info(actor.state.match_id, speech_id)
        assert info is not None and info["audio_duration_ms"] == 100
        assert runtime.recovery_source_available(actor.state.match_id, speech_id)
        if acknowledged:
            assert speech.status == "FINALIZING" and speech.finish_reason == "TIME_LIMIT"
            assert match.runtime_snapshot["action_state"] == "SPEECH_FINALIZING"
            assert not any(e.event_type in {"asr.failed", "match.error"} for e in events)
            assert actor.state.sequence < match.runtime_snapshot["sequence"]
        else:
            assert (
                speech.status == "FAILED"
                and speech.audio_storage_path == info["audio_storage_path"]
            )
            assert actor.state.asr_failed_speech_id == speech_id
            assert actor.state.action_state == "RECOVERY_REQUIRED"
        assert not any(e.event_type == "speech.reset" for e in events)
    finally:
        await runtime.close()
        await manager.close()
        await wait(lambda: not [t for t in asyncio.all_tasks() - baseline if not t.done()])
        assert stream.closed and not runtime._receivers

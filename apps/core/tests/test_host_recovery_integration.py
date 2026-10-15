"""Spec 267 host identity fences at actual Actor/PostgreSQL commit boundaries.

Only native audio/decoder boundaries are synthetic; the existing isolated
formal 4v4 fixture supplies two humans and two Agents per side. These tests
exercise a host action and its human handoff, not a complete match or live mic.
"""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.engine import Connection

from jx_core.agent.runtime import HostAudioRun
from jx_core.matches.domain import MatchCommand, MatchRuntimeState
from jx_core.matches.service import state_snapshot
from jx_core.models import Match, MatchEvent, MatchParticipant, Speech
from test_agent_runtime_fault_integration import AgentFaultProbe
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe
from test_host_recovery import host_action, next_action, release_owned, runtime, start_host

pytestmark = pytest.mark.integration


async def install_host(
    probe: AgentFaultProbe, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, HostAudioRun]:
    async with probe.factory() as session:
        human_id = await session.scalar(
            select(MatchParticipant.user_id)
            .where(
                MatchParticipant.match_id == probe.actor.state.match_id,
                MatchParticipant.kind == "HUMAN",
            )
            .limit(1)
        )
    assert human_id is not None
    state = replace(
        probe.actor.state,
        action_state="HOST_ANNOUNCING",
        actions=(host_action(), replace(next_action(), speaker_user_id=human_id)),
        current_agent_profile_id=None,
        current_speaker_side=None,
        current_speaker_seat_no=None,
        host_audio_remaining_ms=60_000,
        speech_remaining_ms=None,
    )
    async with probe.factory() as session:
        async with session.begin():
            await session.execute(
                update(Match)
                .where(Match.id == state.match_id)
                .values(runtime_snapshot=state_snapshot(state))
            )
    probe.actor.state = state
    await probe.runtime.close()
    service, sources, _ = runtime(tmp_path, monkeypatch, probe.manager)
    probe.runtime = service
    probe.manager.set_agent_runtime(service)
    run = await start_host(service, state.match_id)
    async with asyncio.timeout(1):
        while not sources:
            await asyncio.sleep(0)
        await sources[0].draining.wait()
    return sources, run


async def assert_durable_state(probe: AgentFaultProbe, expected: MatchRuntimeState) -> None:
    async with probe.factory() as session:
        match = await session.get(Match, expected.match_id)
        events = list(
            (
                await session.scalars(
                    select(MatchEvent)
                    .where(MatchEvent.match_id == expected.match_id)
                    .order_by(MatchEvent.sequence)
                )
            ).all()
        )
    assert match is not None and match.status == expected.status
    assert match.sequence == expected.sequence
    assert match.runtime_snapshot == state_snapshot(expected)
    assert [event.sequence for event in events] == list(range(1, expected.sequence + 1))
    for event_row in events:
        public_json = json.dumps(event_row.payload)
        assert "CallbackEnvelope" not in public_json
        assert "_host_callback_envelope" not in public_json
        assert "host_guard_id" not in public_json


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True], ids=["drained-success", "two-failures"])
async def test_current_host_authority_commit_and_human_handoff(
    agent_fault_probe: AgentFaultProbe, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    probe = agent_fault_probe
    sources, run = await install_host(probe, tmp_path, monkeypatch)
    try:
        if fail:
            sources[0].fail_code = "host_audio_unavailable"
        sources[0].release.set()
        if fail:
            async with asyncio.timeout(2):
                while len(sources) < 2:
                    await asyncio.sleep(0)
                await sources[1].draining.wait()
            sources[1].fail_code = "host_audio_unavailable"
            sources[1].release.set()
        assert run.task is not None
        await asyncio.wait_for(run.task, timeout=3)
        assert probe.actor.state.action_state == (
            "RECOVERY_REQUIRED" if fail else "HUMAN_READY_TO_START"
        )
        assert probe.actor.state.sequence == (1 if fail else 2)
        assert probe.actor.state.error_code == ("host_audio_unavailable" if fail else None)
        await assert_durable_state(probe, probe.actor.state)
        async with probe.factory() as session:
            speeches = list(
                (
                    await session.scalars(
                        select(Speech).where(Speech.match_id == probe.actor.state.match_id)
                    )
                ).all()
            )
        # Host completion only hands authority to the human's manual start;
        # no speech or microphone is started by this handoff.
        assert len(speeches) == 0 and probe.actor.state.current_speech_id is None
        assert probe.provider_calls == 0 and all(source.closed for source in sources)
    finally:
        await release_owned(probe.runtime, sources, [run])


@pytest.mark.asyncio
@pytest.mark.parametrize("callback", ["finished", "failed"])
async def test_old_host_callback_queued_after_real_pause_commit_cannot_change_resumed_pg_state(
    agent_fault_probe: AgentFaultProbe,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    callback: str,
) -> None:
    probe = agent_fault_probe
    sources, old = await install_host(probe, tmp_path, monkeypatch)
    assert old.callback_envelope is not None
    envelope = old.callback_envelope
    entered, release = asyncio.Event(), asyncio.Event()
    original_commit, original_publish = probe.actor._commit, probe.actor._publish
    assert original_commit is not None and original_publish is not None

    async def commit(*args: Any) -> None:
        if args[3].type == "match.pause":
            entered.set()
            await release.wait()
        await original_commit(*args)

    async def publish(state: MatchRuntimeState, events: Any) -> None:
        await original_publish(state, events)
        if any(event.type == "match.resumed" for event in events):
            async with asyncio.timeout(2):
                while len(sources) < 2:
                    await asyncio.sleep(0)
                await sources[1].draining.wait()

    monkeypatch.setattr(probe.actor, "_commit", commit)
    monkeypatch.setattr(probe.actor, "_publish", publish)
    runs = [old]
    tasks: list[asyncio.Task[Any]] = []
    match_id = probe.actor.state.match_id
    try:
        tasks.append(
            asyncio.create_task(
                probe.manager.submit(
                    match_id,
                    MatchCommand(
                        type="match.pause", message_id="pg-host-pause", payload={"authorized": True}
                    ),
                )
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        tasks.append(
            asyncio.create_task(
                probe.manager.submit(
                    match_id,
                    MatchCommand(
                        type="match.resume",
                        message_id="pg-host-resume",
                        payload={"privileged": True},
                    ),
                )
            )
        )
        elapsed = asyncio.create_task(
            probe.manager.submit(
                match_id, MatchCommand(type="resume.elapsed", message_id="pg-host-resume-elapsed")
            )
        )
        tasks.append(elapsed)
        late = asyncio.create_task(
            probe.manager.host_audio_finished(envelope=envelope, action_key="1:1")
            if callback == "finished"
            else probe.manager.host_audio_failed(
                envelope=envelope, action_key="1:1", error_code="host_audio_unavailable"
            )
        )
        tasks.append(late)
        async with asyncio.timeout(2):
            while probe.actor._queue.qsize() < 3:
                await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=4)
        replacement = probe.runtime._host_runs[match_id]
        runs.append(replacement)
        expected = elapsed.result().state
        assert expected.status == "RUNNING" and expected.action_state == "HOST_ANNOUNCING"
        assert replacement.generation_id != old.generation_id
        assert probe.actor.state == expected
        await assert_durable_state(probe, expected)
        async with probe.factory() as session:
            assert (
                await session.scalar(select(Speech.id).where(Speech.match_id == match_id)) is None
            )
        assert probe.provider_calls == 0 and len(sources) == 2
        assert replacement.task is not None and not replacement.task.done()
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        current = probe.runtime._host_runs.get(match_id)
        if current is not None and current not in runs:
            runs.append(current)
        await release_owned(probe.runtime, sources, runs)


@pytest.mark.asyncio
@pytest.mark.parametrize("callback", ["finished", "failed", "context-changed"])
async def test_host_callback_rechecks_identity_after_real_match_row_lock_wait(
    agent_fault_probe: AgentFaultProbe,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    callback: str,
) -> None:
    probe = agent_fault_probe
    sources, old = await install_host(probe, tmp_path, monkeypatch)
    assert old.callback_envelope is not None
    envelope, before = old.callback_envelope, probe.actor.state
    lock_query = asyncio.Event()

    def query_started(
        _: Connection, __: Any, statement: str, ___: Any, ____: Any, _____: bool
    ) -> None:
        if "FROM matches" in statement and "FOR UPDATE" in statement:
            lock_query.set()

    runs = [old]
    task: asyncio.Task[None] | None = None
    async with probe.factory() as blocker:
        async with blocker.begin():
            row = await blocker.get(Match, before.match_id, with_for_update=True)
            assert row is not None
            event.listen(probe.engine.sync_engine, "before_cursor_execute", query_started)
            try:
                task = asyncio.create_task(
                    probe.manager.host_audio_failed(
                        envelope=envelope, action_key="1:1", error_code="host_audio_unavailable"
                    )
                    if callback == "failed"
                    else probe.manager.host_audio_finished(envelope=envelope, action_key="1:1")
                )
                await asyncio.wait_for(lock_query.wait(), timeout=2)
                assert not task.done() and probe.actor.state == before
                if callback == "context-changed":
                    row.context_version += 1
                else:
                    await probe.runtime.reset_host(before.match_id)
                    replacement = await start_host(probe.runtime, before.match_id)
                    runs.append(replacement)
                    async with asyncio.timeout(1):
                        while len(sources) < 2:
                            await asyncio.sleep(0)
                        await sources[1].draining.wait()
                    assert replacement.generation_id != old.generation_id
            finally:
                event.remove(probe.engine.sync_engine, "before_cursor_execute", query_started)
    try:
        await asyncio.wait_for(task, timeout=3)
        assert probe.actor.state == before
        await assert_durable_state(probe, before)
        async with probe.factory() as session:
            assert (
                await session.scalar(select(Speech.id).where(Speech.match_id == before.match_id))
                is None
            )
        assert probe.provider_calls == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        probe.manager._actors.clear()
        await release_owned(probe.runtime, sources, runs)


@pytest.mark.asyncio
async def test_old_host_timer_commit_failure_fallback_leaves_real_resumed_pg_state_unchanged(
    agent_fault_probe: AgentFaultProbe, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = agent_fault_probe
    sources, old = await install_host(probe, tmp_path, monkeypatch)
    entered, release, wake = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_commit, original_publish = probe.actor._commit, probe.actor._publish
    assert original_commit is not None and original_publish is not None
    first_guard = True
    sleeps = 0

    async def sleep(_: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            await wake.wait()
        else:
            await asyncio.Event().wait()

    async def commit(*args: Any) -> None:
        nonlocal first_guard
        if first_guard and args[3].type == "host.elapsed":
            first_guard = False
            entered.set()
            await release.wait()
            raise RuntimeError("synthetic_old_host_guard_commit_failed")
        await original_commit(*args)

    async def publish(state: MatchRuntimeState, events: Any) -> None:
        await original_publish(state, events)
        if any(event.type == "match.resumed" for event in events):
            async with asyncio.timeout(2):
                while len(sources) < 2:
                    await asyncio.sleep(0)
                await sources[1].draining.wait()

    monkeypatch.setattr(probe.actor, "_sleep", sleep)
    monkeypatch.setattr(probe.actor, "_commit", commit)
    monkeypatch.setattr(probe.actor, "_publish", publish)
    # Install the actual host timer on the already committed synthetic host
    # state. Its exception fallback and all later authority commits are real.
    probe.actor._schedule_internal(
        0.01, "host.elapsed", "pg-fallback-guard", payload={"guard": True}
    )
    old_timer = probe.actor._timer
    assert old_timer is not None
    tasks: list[asyncio.Task[Any]] = []
    runs = [old]
    match_id = probe.actor.state.match_id
    try:
        wake.set()
        await asyncio.wait_for(entered.wait(), timeout=2)
        tasks.append(
            asyncio.create_task(
                probe.manager.submit(
                    match_id,
                    MatchCommand(
                        type="match.pause",
                        message_id="pg-fallback-pause",
                        payload={"authorized": True},
                    ),
                )
            )
        )
        tasks.append(
            asyncio.create_task(
                probe.manager.submit(
                    match_id,
                    MatchCommand(
                        type="match.resume",
                        message_id="pg-fallback-resume",
                        payload={"privileged": True},
                    ),
                )
            )
        )
        elapsed = asyncio.create_task(
            probe.manager.submit(
                match_id,
                MatchCommand(type="resume.elapsed", message_id="pg-fallback-resume-elapsed"),
            )
        )
        tasks.append(elapsed)
        async with asyncio.timeout(2):
            while probe.actor._queue.qsize() < 3:
                await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=4)
        await asyncio.wait_for(old_timer, timeout=2)
        expected = elapsed.result().state
        assert probe.actor.state == expected
        assert expected.status == "RUNNING" and expected.action_state == "HOST_ANNOUNCING"
        await assert_durable_state(probe, expected)
        replacement = probe.runtime._host_runs[match_id]
        runs.append(replacement)
        assert replacement.generation_id != old.generation_id and probe.provider_calls == 0
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        probe.manager._actors.clear()
        current = probe.runtime._host_runs.get(match_id)
        if current is not None and current not in runs:
            runs.append(current)
        await release_owned(probe.runtime, sources, runs)

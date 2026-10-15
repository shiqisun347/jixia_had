"""Explicit pure Actor/Runtime Spec 267 probes. No settings/env/DB/providers.

Only decoder and LiveKit native boundaries are synthetic. Public host startup,
replacement/reset, actual host PCM scheduling, manager callbacks and Actor
commands remain real. Current product expectations are deliberately strong.
"""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import json
import shutil
import threading
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

import jx_core.agent.runtime as runtime_module
from jx_core.agent.runtime import AgentRuntime, HostAudioRun
from jx_core.agent.tts import TtsProviderError
from jx_core.host_audio_timing import host_audio_guard_seconds
from jx_core.matches.domain import (
    MatchAction,
    MatchActor,
    MatchCommand,
    MatchDomainError,
    MatchRuntimeState,
)
from jx_core.matches.service import MatchRuntimeManager
from jx_core.runtime_identity import CallbackEnvelope


class ContextSession:
    """Only the public host callback's context-version scalar read is synthetic."""

    async def __aenter__(self) -> ContextSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def scalar(self, _: Any) -> int:
        return 0


async def idle_sleep(_: float) -> None:
    await asyncio.Event().wait()


class SyntheticDecoder:
    body_frames = 5

    async def start(self, _: Path) -> None:
        return None

    def notify_written(self, _: int) -> None:
        return None

    def finish_input(self) -> None:
        return None

    async def frames(self) -> AsyncIterator[bytes]:
        pcm = b"\x01\x00" * 960
        for _ in range(self.body_frames):
            yield pcm

    async def close(self) -> None:
        return None


class SyntheticSource:
    def __init__(self, *, cancellation_resistant: bool = False) -> None:
        self.samples = 0
        self.draining = asyncio.Event()
        self.release = asyncio.Event()
        self.cancel_seen = asyncio.Event()
        self.cancellation_resistant = cancellation_resistant
        self.closed = False
        self.fail_code: str | None = None
        self.clear_count = 0

    async def capture_frame(self, frame: Any) -> None:
        self.samples += frame.samples_per_channel

    async def wait_for_playout(self) -> None:
        self.draining.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancel_seen.set()
                if not self.cancellation_resistant:
                    raise
        if self.fail_code is not None:
            raise TtsProviderError(self.fail_code)

    def clear_queue(self) -> None:
        self.clear_count += 1

    async def aclose(self) -> None:
        self.closed = True


def host_action() -> MatchAction:
    return MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HOST_AUDIO",
        duration_seconds=0,
        host_audio_path="host.ogg",
        host_audio_duration_ms=60_000,
    )


def next_action() -> MatchAction:
    return MatchAction(
        stage_position=2,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=10,
        speaker_kind="HUMAN",
        speaker_user_id=uuid4(),
        side="AFFIRMATIVE",
        seat_no=1,
    )


def runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, callbacks: Any
) -> tuple[AgentRuntime, list[SyntheticSource], asyncio.Event]:
    host_root = tmp_path / "hosts"
    host_root.mkdir()
    (host_root / "host.ogg").write_bytes(b"synthetic-decoder-boundary-only")
    service = AgentRuntime(
        settings=cast(
            Any,
            SimpleNamespace(
                llm_global_concurrency=50,
                host_audio_storage_dir=str(host_root),
                match_audio_storage_dir=str(tmp_path / "matches"),
            ),
        ),
        session_factory=cast(Any, None),
        callbacks=callbacks,
    )
    sources: list[SyntheticSource] = []
    connected = asyncio.Event()

    async def publisher(*_: Any, **__: Any) -> tuple[Any, Any]:
        source = SyntheticSource()
        sources.append(source)
        connected.set()
        return SimpleNamespace(disconnect=AsyncMock()), source

    monkeypatch.setattr(service, "_connect_publisher", publisher)
    monkeypatch.setattr(runtime_module, "IncrementalOpusDecoder", SyntheticDecoder)
    return service, sources, connected


async def start_host(service: AgentRuntime, match_id: UUID) -> HostAudioRun:
    await service.start_host_playback(
        match_id=match_id,
        action_key="1:1",
        storage_path="host.ogg",
        duration_ms=60_000,
        context_version=0,
    )
    return service._host_runs[match_id]


async def release_owned(
    service: AgentRuntime, sources: list[SyntheticSource], runs: list[HostAudioRun]
) -> None:
    for source in sources:
        source.release.set()
    tasks = [run.task for run in runs if run.task is not None]
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=1)
    await asyncio.wait_for(service.close(), timeout=1)


@pytest.mark.asyncio
async def test_old_host_task_finally_preserves_public_replacement_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    callbacks = AsyncMock()
    service, sources, connected = runtime(tmp_path, monkeypatch, callbacks)
    # Only shorten the cleanup observation budget; scheduling/retry/identity
    # semantics are production and cancellation resistance remains explicit.
    monkeypatch.setattr(runtime_module, "AGENT_TASK_CANCEL_TIMEOUT_SECONDS", 0.03)
    match_id = uuid4()
    old = await start_host(service, match_id)
    runs = [old]
    try:
        await asyncio.wait_for(connected.wait(), timeout=1)
        await asyncio.wait_for(sources[0].draining.wait(), timeout=1)
        sources[0].cancellation_resistant = True
        replacement = await start_host(service, match_id)
        runs.append(replacement)
        assert old.task is not None and not old.task.done()
        assert sources[0].cancel_seen.is_set()
        while len(sources) < 2:
            await asyncio.sleep(0)
        await asyncio.wait_for(sources[1].draining.wait(), timeout=1)
        assert service._host_runs[match_id] is replacement
        sources[0].release.set()
        await asyncio.wait_for(asyncio.gather(old.task, return_exceptions=True), timeout=1)
        proof = {
            "old_task_done": old.task.done(),
            "new_task_done": replacement.task.done() if replacement.task else None,
            "new_registry_preserved": service._host_runs.get(match_id) is replacement,
        }
        print(json.dumps(proof, sort_keys=True))
        assert service._host_runs.get(match_id) is replacement, proof
        assert sources[0].closed
        assert old.decoder is None and old.source is None and old.room is None
        callbacks.host_audio_finished.assert_not_awaited()
        callbacks.host_audio_failed.assert_not_awaited()
    finally:
        await release_owned(service, sources, runs)


@pytest.mark.asyncio
@pytest.mark.parametrize("callback", ["finished", "failed"])
async def test_public_host_callback_rejects_retired_generation_after_same_action_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, callback: str
) -> None:
    manager = MatchRuntimeManager(cast(Any, ContextSession))
    match_id = uuid4()
    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="RUNNING",
            action_state="HOST_ANNOUNCING",
            actions=(host_action(), next_action()),
        ),
        sleep=idle_sleep,
    )
    manager._actors[match_id] = actor
    await actor.start()
    service, sources, connected = runtime(tmp_path, monkeypatch, manager)
    manager.set_agent_runtime(service)
    old = await start_host(service, match_id)
    runs = [old]
    try:
        await asyncio.wait_for(connected.wait(), timeout=1)
        await asyncio.wait_for(sources[0].draining.wait(), timeout=1)
        old_envelope = CallbackEnvelope(
            match_id=match_id,
            speech_id=None,
            attempt_no=1,
            generation_id=old.generation_id,
            connection_epoch=None,
            context_version=0,
            opportunity_id=None,
            opportunity_generation=None,
        )
        await service.reset_host(match_id)
        replacement = await start_host(service, match_id)
        runs.append(replacement)
        assert replacement.generation_id != old.generation_id
        async with asyncio.timeout(1):
            while len(sources) < 2:
                await asyncio.sleep(0)
            await sources[1].draining.wait()
        before = actor.state
        if callback == "finished":
            await manager.host_audio_finished(envelope=old_envelope, action_key="1:1")
        else:
            await manager.host_audio_failed(
                envelope=old_envelope, action_key="1:1", error_code="host_audio_unavailable"
            )
        proof = {
            "callback": callback,
            "old_generation_equals_new": old.generation_id == replacement.generation_id,
            "before_status": before.status,
            "before_action": before.action_state,
            "after_status": actor.state.status,
            "after_action": actor.state.action_state,
            "before_sequence": before.sequence,
            "after_sequence": actor.state.sequence,
        }
        print(json.dumps(proof, sort_keys=True))
        assert actor.state == before, proof
    finally:
        # Fence manager callbacks while draining only the test-owned resources.
        manager._actors.clear()
        await release_owned(service, sources, runs)
        await actor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("callback", ["finished", "failed"])
async def test_host_callback_checked_before_pause_cannot_commit_after_queued_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, callback: str
) -> None:
    manager = MatchRuntimeManager(cast(Any, ContextSession))
    service, sources, connected = runtime(tmp_path, monkeypatch, manager)
    manager.set_agent_runtime(service)
    match_id = uuid4()
    pause_commit_entered = asyncio.Event()
    pause_commit_release = asyncio.Event()

    async def commit(*args: Any) -> None:
        command = args[3]
        if command.type == "match.pause":
            pause_commit_entered.set()
            await pause_commit_release.wait()

    async def publish(state: MatchRuntimeState, events: Any) -> None:
        await manager._publish(state, events)
        if any(event.type == "match.resumed" for event in events):
            # Ensure the authority's own real resume publication has replaced
            # the old run before the already-enqueued callback is processed.
            async with asyncio.timeout(1):
                while len(sources) < 2:
                    await asyncio.sleep(0)
                await sources[1].draining.wait()

    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="RUNNING",
            action_state="HOST_ANNOUNCING",
            actions=(host_action(), next_action()),
            host_audio_remaining_ms=60_000,
        ),
        sleep=idle_sleep,
        commit=commit,
        pre_commit=manager._pre_commit,
        publish=publish,
    )
    manager._actors[match_id] = actor
    await actor.start()
    old = await start_host(service, match_id)
    runs = [old]
    commands: list[asyncio.Task[Any]] = []
    try:
        await asyncio.wait_for(connected.wait(), timeout=1)
        await asyncio.wait_for(sources[0].draining.wait(), timeout=1)
        envelope = CallbackEnvelope(
            match_id=match_id,
            speech_id=None,
            attempt_no=1,
            generation_id=old.generation_id,
            connection_epoch=None,
            context_version=0,
            opportunity_id=None,
            opportunity_generation=None,
        )
        pause = asyncio.create_task(
            manager.submit(
                match_id,
                MatchCommand(
                    type="match.pause", message_id="queue-pause", payload={"authorized": True}
                ),
            )
        )
        commands.append(pause)
        await asyncio.wait_for(pause_commit_entered.wait(), timeout=1)
        assert actor.state.status == "RUNNING" and actor.state.action_state == "HOST_ANNOUNCING"
        resume = asyncio.create_task(
            manager.submit(
                match_id,
                MatchCommand(
                    type="match.resume", message_id="queue-resume", payload={"privileged": True}
                ),
            )
        )
        elapsed = asyncio.create_task(
            manager.submit(
                match_id, MatchCommand(type="resume.elapsed", message_id="queue-elapsed")
            )
        )
        commands.extend((resume, elapsed))
        if callback == "finished":
            late = asyncio.create_task(
                manager.host_audio_finished(envelope=envelope, action_key="1:1")
            )
        else:
            late = asyncio.create_task(
                manager.host_audio_failed(
                    envelope=envelope, action_key="1:1", error_code="host_audio_unavailable"
                )
            )
        commands.append(late)
        async with asyncio.timeout(1):
            while actor._queue.qsize() < 3:
                await asyncio.sleep(0)
        pause_commit_release.set()
        await asyncio.wait_for(asyncio.gather(*commands), timeout=2)
        replacement = service._host_runs[match_id]
        runs.append(replacement)
        expected = elapsed.result().state
        assert expected.action_state == "HOST_ANNOUNCING"
        assert replacement.generation_id != old.generation_id
        proof = {
            "callback": callback,
            "callback_checked_before_pause_commit": True,
            "callback_queued_after_pause_resume_elapsed": True,
            "old_generation_equals_new": old.generation_id == replacement.generation_id,
            "resumed_sequence": expected.sequence,
            "after_sequence": actor.state.sequence,
            "after_status": actor.state.status,
            "after_action": actor.state.action_state,
        }
        print(json.dumps(proof, sort_keys=True))
        assert actor.state == expected, proof
    finally:
        pause_commit_release.set()
        for task in commands:
            if not task.done():
                task.cancel()
        await asyncio.gather(*commands, return_exceptions=True)
        manager._actors.clear()
        current = service._host_runs.get(match_id)
        if current is not None and current not in runs:
            runs.append(current)
        await release_owned(service, sources, runs)
        await asyncio.gather(*manager._background_tasks, return_exceptions=True)
        await actor.close()


@pytest.mark.asyncio
async def test_already_queued_old_host_guard_does_not_fail_resumed_cycle() -> None:
    pause_commit_entered = asyncio.Event()
    pause_commit_release = asyncio.Event()
    timer_sleeps: list[tuple[float, asyncio.Event]] = []

    async def sleep(delay: float) -> None:
        release = asyncio.Event()
        timer_sleeps.append((delay, release))
        await release.wait()

    async def commit(*args: Any) -> None:
        if args[3].type == "match.pause":
            pause_commit_entered.set()
            await pause_commit_release.wait()

    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="START_COUNTDOWN",
            action_state="NOT_STARTED",
            actions=(host_action(), next_action()),
        ),
        clock=lambda: 100.0,
        wall_clock=lambda: 1000.0,
        sleep=sleep,
        commit=commit,
    )
    await actor.start()
    commands: list[asyncio.Task[Any]] = []
    try:
        await actor.submit(MatchCommand(type="countdown.elapsed", message_id="old-guard-start"))
        async with asyncio.timeout(1):
            while not timer_sleeps:
                await asyncio.sleep(0)
        pause = asyncio.create_task(
            actor.submit(
                MatchCommand(
                    type="match.pause", message_id="guard-pause", payload={"authorized": True}
                )
            )
        )
        commands.append(pause)
        await asyncio.wait_for(pause_commit_entered.wait(), timeout=1)
        resume = asyncio.create_task(
            actor.submit(
                MatchCommand(
                    type="match.resume", message_id="guard-resume", payload={"privileged": True}
                )
            )
        )
        elapsed = asyncio.create_task(
            actor.submit(MatchCommand(type="resume.elapsed", message_id="guard-elapsed"))
        )
        commands.extend((resume, elapsed))
        async with asyncio.timeout(1):
            while actor._queue.qsize() < 2:
                await asyncio.sleep(0)
        # Wake the actual initial guard task while pause's commit is pending.
        # It clears its timer reference and enqueues after resume/elapsed.
        timer_sleeps[0][1].set()
        async with asyncio.timeout(1):
            while actor._queue.qsize() < 3:
                await asyncio.sleep(0)
        pause_commit_release.set()
        await asyncio.wait_for(asyncio.gather(*commands), timeout=1)
        await asyncio.wait_for(actor._queue.join(), timeout=1)
        expected = elapsed.result().state
        proof = {
            "actual_old_timer_woke": True,
            "old_guard_queued_after_pause_resume_elapsed": True,
            "old_guard_delay_seconds": timer_sleeps[0][0],
            "resumed_sequence": expected.sequence,
            "after_sequence": actor.state.sequence,
            "after_status": actor.state.status,
            "error_code": actor.state.error_code,
        }
        print(json.dumps(proof, sort_keys=True))
        assert expected.action_state == "HOST_ANNOUNCING"
        assert actor.state == expected, proof
    finally:
        pause_commit_release.set()
        for task in commands:
            if not task.done():
                task.cancel()
        await asyncio.gather(*commands, return_exceptions=True)
        await actor.close()


@pytest.mark.asyncio
async def test_initial_host_guard_covers_two_actual_full_pcm_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = MatchRuntimeManager(cast(Any, ContextSession))
    service, sources, connected = runtime(tmp_path, monkeypatch, manager)
    manager.set_agent_runtime(service)
    monkeypatch.setattr(SyntheticDecoder, "body_frames", 3000)
    match_id = uuid4()
    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="START_COUNTDOWN",
            action_state="NOT_STARTED",
            actions=(host_action(), next_action()),
        ),
        clock=lambda: 100.0,
        wall_clock=lambda: 1000.0,
        sleep=idle_sleep,
        publish=manager._publish,
    )
    manager._actors[match_id] = actor
    await actor.start()
    runs: list[HostAudioRun] = []
    try:
        await manager.submit(
            match_id, MatchCommand(type="countdown.elapsed", message_id="initial-host-start")
        )
        await asyncio.wait_for(connected.wait(), timeout=1)
        run = service._host_runs[match_id]
        runs.append(run)
        await asyncio.wait_for(sources[0].draining.wait(), timeout=1)
        sources[0].fail_code = "host_audio_unavailable"
        sources[0].release.set()
        async with asyncio.timeout(1):
            while len(sources) < 2:
                await asyncio.sleep(0)
            await sources[1].draining.wait()
        assert actor.state.status == "RUNNING" and actor.state.action_state == "HOST_ANNOUNCING"
        assert actor._timer_deadline_mono is not None
        guard_budget = actor._timer_deadline_mono - 100.0
        actual_pcm_seconds = sum(source.samples for source in sources) / 48_000
        proof = {
            "initial_actor_guard_seconds": guard_budget,
            "actual_host_attempts": len(sources),
            "actual_pcm_playout_seconds": actual_pcm_seconds,
            "retry_backoff_seconds": 0.05,
            "virtual_actor_clock": True,
        }
        print(json.dumps(proof, sort_keys=True))
        assert guard_budget >= actual_pcm_seconds + 0.05, proof
    finally:
        manager._actors.clear()
        await release_owned(service, sources, runs)
        await asyncio.gather(*manager._background_tasks, return_exceptions=True)
        await actor.close()


@pytest.mark.asyncio
async def test_public_resume_host_guard_covers_full_restarted_pcm_playout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = MatchRuntimeManager(cast(Any, ContextSession))
    service, sources, connected = runtime(tmp_path, monkeypatch, manager)
    manager.set_agent_runtime(service)
    monkeypatch.setattr(SyntheticDecoder, "body_frames", 3000)  # exactly 60 seconds of PCM
    match_id = uuid4()
    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="PAUSED",
            action_state="RECOVERY_REQUIRED",
            actions=(host_action(), next_action()),
            host_audio_remaining_ms=1000,
            paused_from_status="RUNNING",
            paused_from_action_state="HOST_ANNOUNCING",
        ),
        clock=lambda: 100.0,
        wall_clock=lambda: 1000.0,
        sleep=idle_sleep,
        publish=manager._publish,
    )
    manager._actors[match_id] = actor
    await actor.start()
    runs: list[HostAudioRun] = []
    try:
        await manager.submit(
            match_id,
            MatchCommand(type="match.resume", message_id="qa-resume", payload={"privileged": True}),
        )
        await manager.submit(
            match_id, MatchCommand(type="resume.elapsed", message_id="qa-resume-elapsed")
        )
        await asyncio.wait_for(connected.wait(), timeout=1)
        run = service._host_runs[match_id]
        runs.append(run)
        await asyncio.wait_for(sources[0].draining.wait(), timeout=1)
        assert run.duration_ms == 60_000
        assert sources[0].samples == 48_000 * 61_400 // 1000  # actual 800/600ms transitions
        assert actor._timer_deadline_mono is not None
        guard_budget = actor._timer_deadline_mono - 100.0
        playout_seconds = sources[0].samples / 48_000
        proof = {
            "resumed_actor_guard_seconds": guard_budget,
            "runtime_restart_duration_ms": run.duration_ms,
            "actual_pcm_samples": sources[0].samples,
            "actual_pcm_playout_seconds": playout_seconds,
            "virtual_actor_clock": True,
        }
        print(json.dumps(proof, sort_keys=True))
        assert guard_budget >= playout_seconds, proof
    finally:
        manager._actors.clear()
        await release_owned(service, sources, runs)
        for task in tuple(manager._background_tasks):
            if not task.done():
                task.cancel()
        await asyncio.gather(*manager._background_tasks, return_exceptions=True)
        await actor.close()


async def active_host_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[MatchRuntimeManager, MatchActor, AgentRuntime, list[SyntheticSource], HostAudioRun]:
    manager = MatchRuntimeManager(cast(Any, ContextSession))
    service, sources, _ = runtime(tmp_path, monkeypatch, manager)
    manager.set_agent_runtime(service)
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HOST_ANNOUNCING",
            actions=(host_action(), next_action()),
        ),
        pre_commit=manager._pre_commit,
        sleep=idle_sleep,
    )
    manager._actors[actor.state.match_id] = actor
    await actor.start()
    run = await start_host(service, actor.state.match_id)
    async with asyncio.timeout(1):
        while not sources:
            await asyncio.sleep(0)
        await sources[0].draining.wait()
    return manager, actor, service, sources, run


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True], ids=["drained-success", "two-failures"])
async def test_current_host_attempt_commits_completion_or_original_failure_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    manager, actor, service, sources, run = await active_host_manager(tmp_path, monkeypatch)
    try:
        if fail:
            sources[0].fail_code = "host_audio_unavailable"
        sources[0].release.set()
        if fail:
            async with asyncio.timeout(1):
                while len(sources) < 2:
                    await asyncio.sleep(0)
                await sources[1].draining.wait()
            assert run.attempt_no == 2 and run.callback_envelope is not None
            before = actor.state
            await manager.host_audio_finished(
                envelope=replace(run.callback_envelope, attempt_no=1), action_key="1:1"
            )
            assert actor.state == before
            sources[1].fail_code = "host_audio_unavailable"
            sources[1].release.set()
        assert run.task is not None
        await asyncio.wait_for(run.task, timeout=2)
        assert actor.state.sequence == (1 if fail else 2)
        assert actor.state.status == ("ERROR" if fail else "RUNNING")
        assert actor.state.action_state == ("RECOVERY_REQUIRED" if fail else "HUMAN_READY_TO_START")
        assert actor.state.error_code == ("host_audio_unavailable" if fail else None)
        assert len(sources) == (2 if fail else 1)
        assert all(source.closed for source in sources)
        assert actor.state.match_id not in service._host_runs
        assert not list((tmp_path / "matches").rglob(".host-*.ogg"))
    finally:
        manager._actors.clear()
        await release_owned(service, sources, [run])
        await actor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "match_id",
        "generation_id",
        "attempt_no",
        "context_version",
        "speech_id",
        "connection_epoch",
        "opportunity_id",
        "opportunity_generation",
        "action_key",
    ],
)
async def test_host_callback_rejects_each_wrong_identity_without_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    manager, actor, service, sources, run = await active_host_manager(tmp_path, monkeypatch)
    assert run.callback_envelope is not None
    values: dict[str, Any] = {
        "match_id": uuid4(),
        "generation_id": uuid4(),
        "attempt_no": 2,
        "context_version": 1,
        "speech_id": uuid4(),
        "connection_epoch": 1,
        "opportunity_id": uuid4(),
        "opportunity_generation": 1,
    }
    envelope = run.callback_envelope
    action_key = "9:9" if field == "action_key" else "1:1"
    if field != "action_key":
        envelope = replace(envelope, **{field: values[field]})
    before = actor.state
    try:
        await manager.host_audio_finished(envelope=envelope, action_key=action_key)
        await manager.host_audio_failed(
            envelope=envelope, action_key=action_key, error_code="host_audio_unavailable"
        )
        assert actor.state == before
        assert actor._queue.empty()
        assert len(sources) == 1 and service._host_runs[before.match_id] is run
    finally:
        manager._actors.clear()
        await release_owned(service, sources, [run])
        await actor.close()


@pytest.mark.asyncio
async def test_host_callback_rechecks_registry_after_context_read_await(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, actor, service, sources, run = await active_host_manager(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedSession(ContextSession):
        async def scalar(self, _: Any) -> int:
            entered.set()
            await release.wait()
            return 0

    manager._session_factory = cast(Any, DelayedSession)
    assert run.callback_envelope is not None
    old_callback = asyncio.create_task(
        manager.host_audio_finished(envelope=run.callback_envelope, action_key="1:1")
    )
    runs = [run]
    before = actor.state
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        await service.reset_host(before.match_id)
        replacement = await start_host(service, before.match_id)
        runs.append(replacement)
        async with asyncio.timeout(1):
            while len(sources) < 2:
                await asyncio.sleep(0)
            await sources[1].draining.wait()
        release.set()
        await asyncio.wait_for(old_callback, timeout=1)
        assert actor.state == before and actor._queue.empty()
        assert service._host_runs[before.match_id] is replacement
    finally:
        release.set()
        await asyncio.gather(old_callback, return_exceptions=True)
        manager._actors.clear()
        await release_owned(service, sources, runs)
        await actor.close()


@pytest.mark.asyncio
async def test_runtime_does_not_replay_when_callback_reports_stale_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    callbacks = AsyncMock()
    callbacks.host_audio_finished.side_effect = MatchDomainError("stale_callback")
    service, sources, _ = runtime(tmp_path, monkeypatch, callbacks)
    run = await start_host(service, uuid4())
    try:
        async with asyncio.timeout(1):
            while not sources:
                await asyncio.sleep(0)
            await sources[0].draining.wait()
        sources[0].release.set()
        assert run.task is not None
        await asyncio.wait_for(run.task, timeout=1)
        callbacks.host_audio_finished.assert_awaited_once()
        callbacks.host_audio_failed.assert_not_awaited()
        assert len(sources) == 1 and sources[0].closed
        assert not service._host_runs
    finally:
        await release_owned(service, sources, [run])


@pytest.mark.asyncio
async def test_late_physical_spool_copy_cleans_only_old_attempt_after_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    callbacks = AsyncMock()
    service, sources, _ = runtime(tmp_path, monkeypatch, callbacks)
    entered, release, physical_done = threading.Event(), threading.Event(), threading.Event()
    original_copy = shutil.copyfile
    copies: list[Path] = []

    def delayed_copy(source: Any, target: Any) -> Any:
        copies.append(Path(target))
        old_copy = len(copies) == 1
        if old_copy:
            entered.set()
            assert release.wait(timeout=2), "test-owned thread barrier was not released"
        result = original_copy(source, target)
        if old_copy:
            physical_done.set()
        return result

    monkeypatch.setattr(shutil, "copyfile", delayed_copy)
    match_id = uuid4()
    old = await start_host(service, match_id)
    runs = [old]
    try:
        async with asyncio.timeout(1):
            while not entered.is_set():
                await asyncio.sleep(0)
        replacement = await start_host(service, match_id)
        runs.append(replacement)
        async with asyncio.timeout(1):
            while not sources:
                await asyncio.sleep(0)
            await sources[0].draining.wait()
        assert old.task is not None and old.task.done()
        assert len(copies) == 2 and copies[0] != copies[1]
        assert not copies[0].exists() and copies[1].exists()
        new_payload = copies[1].read_bytes()
        release.set()
        async with asyncio.timeout(1):
            # The old actual thread must complete and its owned late cleanup
            # must run. The replacement remains actively awaiting playout.
            while not physical_done.is_set():
                await asyncio.sleep(0)
            while copies[0].exists():
                await asyncio.sleep(0)
        assert physical_done.is_set() and not copies[0].exists()
        assert copies[1].read_bytes() == new_payload
        assert (tmp_path / "hosts" / "host.ogg").read_bytes() == new_payload
        assert service._host_runs[match_id] is replacement
        assert len(sources) == 1 and replacement.task is not None and not replacement.task.done()
        callbacks.host_audio_finished.assert_not_awaited()
        callbacks.host_audio_failed.assert_not_awaited()
    finally:
        release.set()
        await release_owned(service, sources, runs)


@pytest.mark.asyncio
async def test_host_guard_identity_survives_failed_pause_commit_and_current_timer_fires() -> None:
    release = asyncio.Event()
    fail_pause = True

    async def commit(*args: Any) -> None:
        if fail_pause and args[3].type == "match.pause":
            raise RuntimeError("synthetic_authority_commit_failed")

    async def sleep(_: float) -> None:
        await release.wait()

    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="START_COUNTDOWN",
            action_state="NOT_STARTED",
            actions=(host_action(), next_action()),
        ),
        sleep=sleep,
        commit=commit,
    )
    await actor.start()
    try:
        await actor.submit(MatchCommand(type="countdown.elapsed", message_id="rollback-host-start"))
        original = actor.state
        timer, guard = actor._timer, actor._host_guard_id
        assert timer is not None and guard is not None
        with pytest.raises(RuntimeError, match="synthetic_authority_commit_failed"):
            await actor.submit(
                MatchCommand(
                    type="match.pause", message_id="rollback-pause", payload={"authorized": True}
                )
            )
        assert actor.state == original and actor._timer is timer and actor._host_guard_id == guard
        assert host_audio_guard_seconds(60_000) >= 2 * (72 + 3) + 0.05 + 30
        release.set()
        await asyncio.wait_for(timer, timeout=1)
        assert actor.state.status == "ERROR" and actor.state.error_code == "host_audio_timeout"
        assert actor.state.sequence == original.sequence + 1
    finally:
        await actor.close()


@pytest.mark.asyncio
async def test_cancel_resistant_old_publisher_return_cannot_make_decoder_or_publish_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    callbacks = AsyncMock()
    service, _, _ = runtime(tmp_path, monkeypatch, callbacks)
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    sources: list[SyntheticSource] = []
    monkeypatch.setattr(runtime_module, "AGENT_TASK_CANCEL_TIMEOUT_SECONDS", 0.03)

    async def publisher(*_: Any, **__: Any) -> tuple[Any, Any]:
        source = SyntheticSource()
        sources.append(source)
        if len(sources) == 1:
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
        return SimpleNamespace(disconnect=AsyncMock()), source

    monkeypatch.setattr(service, "_connect_publisher", publisher)
    old = await start_host(service, uuid4())
    runs = [old]
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        replacement = await start_host(service, old.match_id)
        runs.append(replacement)
        async with asyncio.timeout(1):
            while len(sources) < 2:
                await asyncio.sleep(0)
            await sources[1].draining.wait()
        assert cancelled.is_set() and old.task is not None and not old.task.done()
        release.set()
        await asyncio.wait_for(asyncio.gather(old.task, return_exceptions=True), timeout=1)
        assert service._host_runs[old.match_id] is replacement
        assert len(sources) == 2 and sources[0].samples == 0 and sources[0].closed
        assert old.decoder is None and old.room is None and old.source is None
        assert sources[1].samples > 0 and not sources[1].closed
        callbacks.host_audio_finished.assert_not_awaited()
        callbacks.host_audio_failed.assert_not_awaited()
    finally:
        release.set()
        await release_owned(service, sources, runs)


@pytest.mark.asyncio
async def test_fence_during_first_attempt_retry_backoff_prevents_new_publisher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    callbacks = AsyncMock()
    service, sources, _ = runtime(tmp_path, monkeypatch, callbacks)
    monkeypatch.setattr(runtime_module, "HOST_AUDIO_RETRY_BACKOFF_SECONDS", 0.15)
    run = await start_host(service, uuid4())
    try:
        async with asyncio.timeout(1):
            while not sources:
                await asyncio.sleep(0)
            await sources[0].draining.wait()
        sources[0].fail_code = "host_audio_unavailable"
        sources[0].release.set()
        async with asyncio.timeout(1):
            while not any(
                getattr(record, "error_code", None) == "host_audio_unavailable"
                for record in caplog.records
            ):
                await asyncio.sleep(0)
        await service.fence_host(run.match_id)
        assert run.task is not None
        outcome = await asyncio.wait_for(
            asyncio.gather(run.task, return_exceptions=True), timeout=1
        )
        assert isinstance(outcome[0], asyncio.CancelledError)
        assert run.attempt_no == 1 and len(sources) == 1 and sources[0].closed
        assert not service._host_runs
        callbacks.host_audio_finished.assert_not_awaited()
        callbacks.host_audio_failed.assert_not_awaited()
    finally:
        await release_owned(service, sources, [run])


@pytest.mark.asyncio
async def test_failed_old_host_timer_fallback_cannot_error_queued_resumed_cycle() -> None:
    entered, release, initial_wake = asyncio.Event(), asyncio.Event(), asyncio.Event()
    first_guard = True
    sleep_count = 0

    async def sleep(_: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count == 1:
            await initial_wake.wait()
        else:
            await asyncio.Event().wait()

    async def commit(*args: Any) -> None:
        nonlocal first_guard
        if first_guard and args[3].type == "host.elapsed":
            first_guard = False
            entered.set()
            await release.wait()
            raise RuntimeError("synthetic_old_host_guard_commit_failed")

    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="START_COUNTDOWN",
            action_state="NOT_STARTED",
            actions=(host_action(), next_action()),
        ),
        sleep=sleep,
        commit=commit,
    )
    await actor.start()
    commands: list[asyncio.Task[Any]] = []
    try:
        await actor.submit(MatchCommand(type="countdown.elapsed", message_id="fallback-host-start"))
        old_timer = actor._timer
        assert old_timer is not None
        initial_wake.set()
        await asyncio.wait_for(entered.wait(), timeout=1)
        commands.append(
            asyncio.create_task(
                actor.submit(
                    MatchCommand(
                        type="match.pause",
                        message_id="fallback-pause",
                        payload={"authorized": True},
                    )
                )
            )
        )
        commands.append(
            asyncio.create_task(
                actor.submit(
                    MatchCommand(
                        type="match.resume",
                        message_id="fallback-resume",
                        payload={"privileged": True},
                    )
                )
            )
        )
        elapsed = asyncio.create_task(
            actor.submit(MatchCommand(type="resume.elapsed", message_id="fallback-resume-elapsed"))
        )
        commands.append(elapsed)
        async with asyncio.timeout(1):
            while actor._queue.qsize() < 3:
                await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(*commands), timeout=1)
        await asyncio.wait_for(old_timer, timeout=1)
        expected = elapsed.result().state
        assert expected.status == "RUNNING" and expected.action_state == "HOST_ANNOUNCING"
        assert actor.state == expected
    finally:
        release.set()
        for task in commands:
            task.cancel()
        await asyncio.gather(*commands, return_exceptions=True)
        await actor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("resists_cancel", [False, True], ids=["cooperative", "resists-cancel"])
@pytest.mark.parametrize("operation", ["fence", "reset", "replace", "pause"])
async def test_blocked_host_capture_frame_does_not_block_public_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resists_cancel: bool, operation: str
) -> None:
    entered, release, cancel_seen = asyncio.Event(), asyncio.Event(), asyncio.Event()
    manager = MatchRuntimeManager(cast(Any, ContextSession))
    service, _, _ = runtime(tmp_path, monkeypatch, manager)
    manager.set_agent_runtime(service)
    monkeypatch.setattr(runtime_module, "AGENT_TASK_CANCEL_TIMEOUT_SECONDS", 0.03)
    sources: list[SyntheticSource] = []

    class BlockedSource(SyntheticSource):
        async def capture_frame(self, frame: Any) -> None:
            if not entered.is_set():
                entered.set()
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        cancel_seen.set()
                        if not resists_cancel:
                            raise
            await super().capture_frame(frame)

    async def publisher(*_: Any, **__: Any) -> tuple[Any, Any]:
        source = BlockedSource() if not sources else SyntheticSource()
        sources.append(source)
        return SimpleNamespace(disconnect=AsyncMock()), source

    monkeypatch.setattr(service, "_connect_publisher", publisher)
    actor = MatchActor(
        MatchRuntimeState(
            match_id=uuid4(),
            status="RUNNING",
            action_state="HOST_ANNOUNCING",
            actions=(host_action(), next_action()),
        ),
        sleep=idle_sleep,
        publish=manager._publish,
    )
    manager._actors[actor.state.match_id] = actor
    queue = await manager.subscribe(actor.state.match_id)
    await actor.start()
    old = await start_host(service, actor.state.match_id)
    runs = [old]
    op: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        if operation == "fence":
            op = asyncio.create_task(service.fence_host(old.match_id))
        elif operation == "reset":
            op = asyncio.create_task(service.reset_host(old.match_id))
        elif operation == "replace":
            op = asyncio.create_task(start_host(service, old.match_id))
        else:
            op = asyncio.create_task(
                manager.submit(
                    old.match_id,
                    MatchCommand(
                        type="match.pause",
                        message_id="blocked-frame-pause",
                        payload={"authorized": True},
                    ),
                )
            )
        done, _ = await asyncio.wait({op}, timeout=0.2)
        assert done, f"public {operation} remained blocked behind old native capture_frame"
        op.result()
        assert old.media_fenced
        if operation == "pause":
            assert actor.state.status == "PAUSED"
            event = queue.get_nowait()
            assert event.type == "match.paused"
        if operation == "replace":
            replacement = op.result()
            runs.append(replacement)
            async with asyncio.timeout(1):
                while len(sources) < 2:
                    await asyncio.sleep(0)
                await sources[1].draining.wait()
            assert service._host_runs[old.match_id] is replacement and sources[1].samples > 0
        release.set()
        assert old.task is not None
        await asyncio.wait_for(asyncio.gather(old.task, return_exceptions=True), timeout=1)
        assert sources[0].closed and sources[0].samples <= 960
        assert sources[0].clear_count >= 2
        assert actor.state.action_state in {"HOST_ANNOUNCING", "RECOVERY_REQUIRED"}
        assert actor.state.sequence == (1 if operation == "pause" else 0)
        if operation in {"reset", "replace"}:
            assert cancel_seen.is_set()
    finally:
        release.set()
        if op is not None:
            await asyncio.wait_for(asyncio.gather(op, return_exceptions=True), timeout=1)
        manager._actors.clear()
        current = service._host_runs.get(old.match_id)
        if current is not None and current not in runs:
            runs.append(current)
            async with asyncio.timeout(1):
                while len(sources) < 2:
                    await asyncio.sleep(0)
                await sources[1].draining.wait()
        await release_owned(service, sources, runs)
        await actor.close()

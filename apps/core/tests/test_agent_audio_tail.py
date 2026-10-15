"""Natural Agent finalization protects real output without extending content time."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from livekit import rtc
from pydantic import SecretStr

import jx_core.agent.runtime as runtime_module
from jx_core.agent.runtime import AgentConfig, AgentRun, AgentRuntime
from jx_core.config import Settings
from jx_core.runtime_identity import CallbackEnvelope

# Exercise the actual producer/finalizer ownership and source capture boundaries.
# pyright: reportPrivateUsage=false


@dataclass
class TailSource:
    hold_capture: int | None = None
    hold_drain: bool = False
    fail_capture: bool = False
    capture_calls: int = 0
    clear_calls: int = 0
    close_calls: int = 0
    drain_calls: int = 0
    frames: list[bytes] = field(default_factory=lambda: list[bytes]())
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        self.capture_calls += 1
        if self.fail_capture:
            raise RuntimeError("synthetic capture failure")
        if self.capture_calls == self.hold_capture:
            self.entered.set()
            await self.release.wait()
        self.frames.append(bytes(frame.data))

    async def wait_for_playout(self) -> None:
        self.drain_calls += 1
        if self.hold_drain:
            self.entered.set()
            await self.release.wait()

    def clear_queue(self) -> None:
        self.clear_calls += 1

    async def aclose(self) -> None:
        self.close_calls += 1


@dataclass
class TailProbe:
    runtime: AgentRuntime
    run: AgentRun
    source: TailSource
    callbacks: AsyncMock
    persist: AsyncMock
    envelope: CallbackEnvelope

    async def finalize(self, reason: str = "COMPLETED") -> None:
        assert self.run.speech_id is not None
        await self.runtime.finalize_agent(self.run.match_id, self.run.speech_id, reason)


@pytest.fixture
def tail_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TailProbe:
    callbacks = AsyncMock()
    runtime = AgentRuntime(
        settings=Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            database_url=SecretStr("postgresql+psycopg://synthetic.invalid/unused"),
        ),
        session_factory=cast(Any, None),
        callbacks=callbacks,
    )
    envelope = CallbackEnvelope(
        match_id=uuid4(),
        speech_id=uuid4(),
        attempt_no=2,
        generation_id=uuid4(),
        connection_epoch=None,
        context_version=7,
        opportunity_id=uuid4(),
        opportunity_generation=3,
    )
    source = TailSource()
    spool, storage = tmp_path / "source.ogg.part", tmp_path / "source.ogg"
    spool.write_bytes(b"original compressed content")
    run = AgentRun(
        match_id=envelope.match_id,
        action_key="7:1",
        agent_profile_id=uuid4(),
        duration_ms=30_000,
        speech_id=envelope.speech_id,
        generation_id=envelope.generation_id,
        callback_envelope=envelope,
        natural_complete=True,
        played_samples=48_173,
        draft_parts=["完整正文与最后一句。"],
        spool_path=spool,
        storage_path=storage,
        source=cast(Any, source),
    )
    runtime._runs[run.match_id] = run
    persist = AsyncMock()
    monkeypatch.setattr(runtime, "_persist_final", persist)
    return TailProbe(runtime, run, source, callbacks, persist, envelope)


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["AFFIRMATIVE", "NEGATIVE"])
@pytest.mark.parametrize("action_key", ["1:0", "7:1"])
async def test_natural_tail_submits_600ms_same_source_without_changing_content(
    tail_probe: TailProbe,
    side: str,
    action_key: str,
) -> None:
    probe = tail_probe
    probe.run.side, probe.run.action_key = side, action_key
    await probe.finalize()
    assert len(probe.source.frames) == 30
    assert all(frame == b"\0" * 1920 for frame in probe.source.frames)
    assert probe.source.drain_calls == 1
    assert probe.run.played_samples == 48_173
    assert probe.run.duration_ms == 30_000
    assert probe.run.storage_path is not None
    assert probe.run.storage_path.read_bytes() == b"original compressed content"
    probe.persist.assert_awaited_once_with(probe.run)
    probe.callbacks.finalize_agent_speech.assert_awaited_once()
    payload = probe.callbacks.finalize_agent_speech.await_args.kwargs
    assert payload["audio_duration_ms"] == 48_173 * 1000 // 48_000
    assert payload["audio_truncated"] is False
    assert payload["final_text"] == "完整正文与最后一句。"
    assert payload["envelope"] == probe.envelope
    assert probe.source.close_calls == 1
    assert probe.run.match_id not in probe.runtime._runs


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["first_capture", "last_capture", "drain"])
async def test_finalizer_does_not_persist_or_complete_before_tail_drains(
    tail_probe: TailProbe,
    phase: str,
) -> None:
    probe = tail_probe
    probe.source.hold_capture = 1 if phase == "first_capture" else 30
    probe.source.hold_drain = phase == "drain"
    if phase == "drain":
        probe.source.hold_capture = None
    task = asyncio.create_task(probe.finalize())
    try:
        await asyncio.wait_for(probe.source.entered.wait(), 0.3)
        assert probe.runtime._runs[probe.run.match_id] is probe.run
        probe.persist.assert_not_awaited()
        probe.callbacks.finalize_agent_speech.assert_not_awaited()
        assert probe.source.close_calls == 0
        # The duplicate authority event cannot create a second protection task.
        await probe.finalize()
        assert not task.done()
        probe.source.release.set()
        await asyncio.wait_for(task, 0.3)
        assert probe.source.capture_calls == 30
        probe.persist.assert_awaited_once()
        probe.callbacks.finalize_agent_speech.assert_awaited_once()
    finally:
        probe.source.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def producer_config(run: AgentRun) -> AgentConfig:
    return AgentConfig(
        match_id=run.match_id,
        action_key=run.action_key,
        agent_profile_id=run.agent_profile_id,
        context_version=7,
        match_seed=1,
        model_key="synthetic",
        base_url="https://synthetic.invalid",
        model_id="synthetic",
        api_key="synthetic",
        model_limit=1,
        generation_params={},
        max_tokens=10,
        messages=[],
        voice="synthetic",
        rate=1,
        chars_per_second=4,
        playback_gain=1,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before_finalizer_starts", "capture", "drain"])
async def test_producer_done_hands_exact_run_to_pending_finalizer(
    tail_probe: TailProbe,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    probe = tail_probe
    speech_id = probe.run.speech_id
    probe.source.hold_capture = 1 if phase == "capture" else None
    probe.source.hold_drain = phase == "drain"
    finalizers: list[asyncio.Task[None]] = []

    async def execute(run: AgentRun, _: AgentConfig, generation_id: UUID) -> None:
        run.speech_id = speech_id
        run.callback_envelope = replace(probe.envelope, generation_id=generation_id)
        run.natural_complete = True
        if phase != "before_finalizer_starts":
            finalizers.append(asyncio.create_task(probe.finalize()))
            await asyncio.wait_for(probe.source.entered.wait(), 0.3)

    monkeypatch.setattr(
        probe.runtime, "_load_config", AsyncMock(return_value=producer_config(probe.run))
    )
    monkeypatch.setattr(
        probe.runtime, "_create_generation", AsyncMock(return_value=probe.run.generation_id)
    )
    monkeypatch.setattr(probe.runtime, "_execute", execute)
    producer = asyncio.create_task(probe.runtime._run_with_retry(probe.run))
    probe.run.task = producer
    try:
        await asyncio.wait_for(producer, 0.5)
        assert producer.done() and not producer.cancelled()
        assert probe.runtime._runs.get(probe.run.match_id) is probe.run
        if phase == "before_finalizer_starts":
            finalizers.append(asyncio.create_task(probe.finalize()))
        probe.source.release.set()
        await asyncio.wait_for(asyncio.gather(*finalizers), 0.5)
        assert probe.source.capture_calls == 30
        probe.callbacks.finalize_agent_speech.assert_awaited_once()
        assert probe.run.match_id not in probe.runtime._runs
    finally:
        probe.source.release.set()
        for task in finalizers:
            task.cancel()
        await asyncio.gather(*finalizers, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,natural", [("TIME_LIMIT", True), ("EARLY", True), ("COMPLETED", False)]
)
async def test_interrupted_or_incomplete_audio_skips_tail(
    tail_probe: TailProbe,
    reason: str,
    natural: bool,
) -> None:
    probe = tail_probe
    probe.run.natural_complete = natural
    await probe.finalize(reason)
    assert not probe.source.frames and probe.source.drain_calls == 0
    assert probe.source.clear_calls == 1
    assert probe.callbacks.finalize_agent_speech.await_args.kwargs["audio_truncated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["capture", "drain"])
@pytest.mark.parametrize(
    "retirement",
    [
        "fenced",
        "replacement",
        "runtime_closed",
        "context_changed",
        "generation_changed",
        "source_changed",
        "same_identity_replacement",
    ],
)
async def test_retired_tail_clears_only_old_source_and_never_completes_new_owner(
    tail_probe: TailProbe,
    phase: str,
    retirement: str,
) -> None:
    probe = tail_probe
    probe.source.hold_capture = 1 if phase == "capture" else None
    probe.source.hold_drain = phase == "drain"
    successor_source = TailSource()
    successor = replace(probe.run, generation_id=uuid4(), source=cast(Any, successor_source))
    if retirement == "same_identity_replacement":
        successor.generation_id = probe.run.generation_id
    task = asyncio.create_task(probe.finalize())
    try:
        await asyncio.wait_for(probe.source.entered.wait(), 0.3)
        if retirement == "fenced":
            probe.run.media_fenced = True
        elif retirement in {"replacement", "same_identity_replacement"}:
            probe.runtime._runs[probe.run.match_id] = successor
        elif retirement == "runtime_closed":
            probe.runtime._closed = True
        elif retirement == "context_changed":
            probe.run.callback_envelope = replace(probe.envelope, context_version=8)
        elif retirement == "source_changed":
            probe.run.source = cast(Any, successor_source)
        else:
            probe.run.generation_id = uuid4()
        probe.source.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.3)
        assert probe.source.clear_calls == 1
        assert probe.source.capture_calls == (1 if phase == "capture" else 30)
        assert not successor_source.frames
        assert successor_source.clear_calls == successor_source.close_calls == 0
        if retirement in {"replacement", "same_identity_replacement"}:
            assert probe.runtime._runs[probe.run.match_id] is successor
        probe.persist.assert_not_awaited()
        probe.callbacks.finalize_agent_speech.assert_not_awaited()
        probe.callbacks.handle_agent_failure.assert_not_awaited()
    finally:
        probe.source.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["capture", "drain"])
async def test_cancelled_tail_is_bounded_and_never_waits_for_producer_ack(
    tail_probe: TailProbe,
    phase: str,
) -> None:
    probe = tail_probe
    probe.source.hold_capture = 1 if phase == "capture" else None
    probe.source.hold_drain = phase == "drain"
    producer = asyncio.create_task(asyncio.Event().wait())
    probe.run.task = cast(Any, producer)
    task = asyncio.create_task(probe.finalize())
    try:
        await asyncio.wait_for(probe.source.entered.wait(), 0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.3)
        assert not producer.done()
        assert probe.source.clear_calls == 1
        assert probe.source.close_calls == 1
        probe.persist.assert_not_awaited()
        probe.callbacks.finalize_agent_speech.assert_not_awaited()
    finally:
        probe.source.release.set()
        producer.cancel()
        task.cancel()
        await asyncio.gather(producer, task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["capture_failed", "capture_timeout", "drain_timeout"])
async def test_tail_fault_uses_existing_finalization_failure_policy(
    tail_probe: TailProbe,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    probe = tail_probe
    probe.source.fail_capture = fault == "capture_failed"
    probe.source.hold_capture = 1 if fault == "capture_timeout" else None
    probe.source.hold_drain = fault == "drain_timeout"
    monkeypatch.setattr(runtime_module, "AGENT_FINALIZATION_TIMEOUT_SECONDS", 0.03)
    await asyncio.wait_for(probe.finalize(), 0.3)
    probe.persist.assert_not_awaited()
    probe.callbacks.finalize_agent_speech.assert_not_awaited()
    probe.callbacks.handle_agent_failure.assert_awaited_once()
    expected = (
        "agent_finalization_failed" if fault == "capture_failed" else "agent_finalization_timeout"
    )
    assert probe.callbacks.handle_agent_failure.await_args.kwargs["error_code"] == expected
    assert probe.source.close_calls == 1


@pytest.mark.asyncio
async def test_no_successful_handoff_does_not_leave_a_completed_run_registered(
    tail_probe: TailProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = tail_probe

    async def failed_ack(_: AgentRun, __: AgentConfig, ___: UUID) -> None:
        # Audio generation finished, but the authority did not accept its end.
        # Merely natural_complete is not a successful ownership handoff.
        probe.run.natural_complete = True
        raise RuntimeError("synthetic rejected playback acknowledgement")

    monkeypatch.setattr(
        probe.runtime, "_load_config", AsyncMock(return_value=producer_config(probe.run))
    )
    monkeypatch.setattr(
        probe.runtime, "_create_generation", AsyncMock(return_value=probe.run.generation_id)
    )
    monkeypatch.setattr(probe.runtime, "_execute", failed_ack)
    monkeypatch.setattr(probe.runtime, "_mark_generation_failed", AsyncMock())
    await probe.runtime._run_with_retry(probe.run)
    assert probe.run.match_id not in probe.runtime._runs
    probe.callbacks.handle_agent_failure.assert_awaited_once()
    assert probe.source.close_calls == 1


@pytest.mark.asyncio
async def test_pause_after_tail_during_persist_cannot_submit_a_stale_completion(
    tail_probe: TailProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = tail_probe
    entered, release = asyncio.Event(), asyncio.Event()

    async def persist(_: AgentRun) -> None:
        entered.set()
        await release.wait()

    monkeypatch.setattr(probe.runtime, "_persist_final", persist)
    task = asyncio.create_task(probe.finalize())
    try:
        await asyncio.wait_for(entered.wait(), 0.3)
        probe.run.media_fenced = True
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.3)
        probe.callbacks.finalize_agent_speech.assert_not_awaited()
        probe.callbacks.handle_agent_failure.assert_not_awaited()
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_natural_finalizer_can_complete_while_producer_still_waits_for_ack(
    tail_probe: TailProbe,
) -> None:
    probe = tail_probe
    producer = asyncio.create_task(asyncio.Event().wait())
    probe.run.task = cast(Any, producer)
    try:
        await asyncio.wait_for(probe.finalize(), 0.3)
        assert not producer.done()
        assert probe.source.capture_calls == 30
        probe.callbacks.finalize_agent_speech.assert_awaited_once()
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("match_id", uuid4()),
        ("speech_id", uuid4()),
        ("attempt_no", 1),
        ("generation_id", uuid4()),
        ("connection_epoch", 1),
        ("context_version", 8),
        ("opportunity_id", uuid4()),
        ("opportunity_generation", 4),
    ],
)
async def test_full_callback_identity_is_frozen_across_tail_capture(
    tail_probe: TailProbe,
    field: str,
    value: object,
) -> None:
    probe = tail_probe
    probe.source.hold_capture = 1
    task = asyncio.create_task(probe.finalize())
    try:
        await asyncio.wait_for(probe.source.entered.wait(), 0.3)
        probe.run.callback_envelope = replace(probe.envelope, **{field: value})
        probe.source.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.3)
        assert probe.source.capture_calls == probe.source.clear_calls == 1
        probe.persist.assert_not_awaited()
        probe.callbacks.finalize_agent_speech.assert_not_awaited()
    finally:
        probe.source.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["source", "envelope"])
async def test_natural_completion_cannot_succeed_without_its_owned_media_identity(
    tail_probe: TailProbe,
    missing: str,
) -> None:
    probe = tail_probe
    if missing == "source":
        probe.run.source = None
    else:
        probe.run.callback_envelope = None
    await probe.finalize()
    probe.callbacks.handle_agent_failure.assert_awaited_once()
    probe.callbacks.finalize_agent_speech.assert_not_awaited()
    probe.persist.assert_not_awaited()
    assert probe.source.capture_calls == 0

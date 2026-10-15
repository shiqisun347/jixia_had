"""Retired Agent results cannot revive a cancelled generation or publish text."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

import jx_core.agent.runtime as runtime_module
from jx_core.agent.llm import LlmStreamResult
from jx_core.agent.runtime import AgentConfig, AgentRun, AgentRuntime
from jx_core.agent.tts import TtsStreamResult
from jx_core.config import Settings
from jx_core.models import AgentGeneration

# These probes deliberately drive private persistence and orchestration boundaries.
# pyright: reportPrivateUsage=false


class SyntheticSession:
    def __init__(self, generations: dict[UUID, Any], calls: dict[UUID, Any]) -> None:
        self.generations = generations
        self.calls = calls
        self.current_generation: UUID | None = None
        self.locked_reads: list[UUID] = []

    async def __aenter__(self) -> SyntheticSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    def begin(self) -> SyntheticSession:
        return self

    async def flush(self) -> None:
        return

    async def get(self, model: object, key: UUID, *, with_for_update: bool = False) -> Any:
        assert model is AgentGeneration
        self.current_generation = key
        if with_for_update:
            self.locked_reads.append(key)
        return self.generations.get(key)

    async def scalar(self, _: object) -> Any:
        assert self.current_generation is not None
        return self.calls.get(self.current_generation)

    async def scalars(self, _: object) -> Any:
        assert self.current_generation is not None
        call = self.calls.get(self.current_generation)
        return SimpleNamespace(all=lambda: [call] if call and call.status == "STARTED" else [])


class SyntheticDecoder:
    def __init__(self) -> None:
        self.start = AsyncMock()
        self.finish_input = Mock()
        self.close = AsyncMock()
        self.notify_written = Mock()


@dataclass
class LateResultProbe:
    runtime: AgentRuntime
    config: AgentConfig
    callbacks: AsyncMock
    session: SyntheticSession
    store_blob: AsyncMock
    provider_entered: asyncio.Event = field(default_factory=asyncio.Event)
    release_provider: asyncio.Event = field(default_factory=asyncio.Event)
    cancellation_seen: asyncio.Event = field(default_factory=asyncio.Event)
    clients: list[Any] = field(default_factory=lambda: list[Any]())
    generations: list[UUID] = field(default_factory=lambda: list[UUID]())
    runs: list[AgentRun] = field(default_factory=lambda: list[AgentRun]())
    late_delta: bool = False
    audio_callback: Any = None

    async def start(self) -> AgentRun:
        await self.runtime.start_agent(
            match_id=self.config.match_id,
            action_key=self.config.action_key,
            agent_profile_id=self.config.agent_profile_id,
            duration_ms=1000,
        )
        run = self.runtime._runs[self.config.match_id]
        self.runs.append(run)
        return run


@pytest.fixture
async def late_result_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncGenerator[LateResultProbe, None]:
    session = SyntheticSession({}, {})
    callbacks = AsyncMock()
    runtime = AgentRuntime(
        settings=Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            database_url=SecretStr("postgresql+psycopg://test:test@localhost/test"),
            agent_audio_storage_dir=str(tmp_path),
            tts_api_key=SecretStr("synthetic"),
            llm_global_concurrency=2,
        ),
        session_factory=cast(Any, lambda: session),
        callbacks=callbacks,
    )
    config = AgentConfig(
        match_id=uuid4(),
        action_key="1:1",
        agent_profile_id=uuid4(),
        context_version=1,
        match_seed=1,
        model_key="synthetic-model-id",
        base_url="https://synthetic.invalid",
        model_id="synthetic-model",
        api_key="synthetic",
        model_limit=1,
        generation_params={},
        max_tokens=32,
        messages=[],
        voice="synthetic",
        rate=1,
        chars_per_second=4,
        playback_gain=1,
    )
    store_blob = AsyncMock(return_value=uuid4())
    probe = LateResultProbe(runtime, config, callbacks, session, store_blob)

    async def create_generation(_config: AgentConfig, attempt: int) -> UUID:
        generation_id = uuid4()
        probe.generations.append(generation_id)
        session.generations[generation_id] = SimpleNamespace(
            status="STARTED",
            error_code=None,
            match_id=config.match_id,
            completed_at=None,
            response_blob_id=None,
        )
        session.calls[generation_id] = SimpleNamespace(
            id=uuid4(),
            status="STARTED",
            error_code=None,
            call_kind="LLM_SPEECH",
            started_at=datetime.now(UTC),
        )
        return generation_id

    async def stream(**kwargs: Any) -> LlmStreamResult:
        await kwargs["on_delta"]("合成测试。")
        probe.provider_entered.set()
        while not probe.release_provider.is_set():
            try:
                await probe.release_provider.wait()
            except asyncio.CancelledError:
                probe.cancellation_seen.set()
        if probe.late_delta:
            await kwargs["on_delta"]("迟到内容。")
        return LlmStreamResult("合成测试。", 1, 2, 3)

    def client(**_: Any) -> Any:
        value = SimpleNamespace(stream_chat=AsyncMock(side_effect=stream), close=AsyncMock())
        probe.clients.append(value)
        return value

    async def synthesize(chunks: Any, **arguments: Any) -> TtsStreamResult:
        probe.audio_callback = arguments["on_audio"]
        async for _chunk in chunks:
            pass
        return TtsStreamResult(uuid4(), 1, 1, 2)

    def tts_connection(**_: Any) -> Any:
        return SimpleNamespace(synthesize=AsyncMock(side_effect=synthesize), close=AsyncMock())

    async def playback(*_: Any) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "_load_config", AsyncMock(return_value=config))
    monkeypatch.setattr(runtime, "_create_generation", create_generation)
    monkeypatch.setattr(runtime, "_start_generation_call", AsyncMock(return_value=uuid4()))
    monkeypatch.setattr(runtime, "_start_tts_call", AsyncMock(return_value=uuid4()))
    monkeypatch.setattr(runtime, "_capture_generation_llm_ready", AsyncMock())
    monkeypatch.setattr(runtime, "_play_pcm", playback)
    monkeypatch.setattr(
        runtime,
        "_connect_publisher",
        AsyncMock(
            return_value=(
                SimpleNamespace(disconnect=AsyncMock()),
                SimpleNamespace(clear_queue=Mock(), aclose=AsyncMock()),
            )
        ),
    )
    monkeypatch.setattr(runtime_module, "store_content_blob", store_blob)
    monkeypatch.setattr(runtime_module, "IncrementalOpusDecoder", SyntheticDecoder)
    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", client)
    monkeypatch.setattr(runtime_module, "QwenTtsConnection", tts_connection)
    monkeypatch.setattr(runtime_module, "AGENT_TASK_CANCEL_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(runtime_module, "AGENT_RESOURCE_CLEANUP_TIMEOUT_SECONDS", 0.02)
    try:
        yield probe
    finally:
        probe.release_provider.set()
        await runtime.close()
        tasks = [run.task for run in probe.runs if run.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for run in probe.runs:
            await runtime._cleanup_attempt(run)


async def assert_capacity_reusable(probe: LateResultProbe) -> None:
    lease = await probe.runtime.capacity_limiter.acquire(
        probe.config.model_key, 1, timeout_seconds=0.05
    )
    probe.runtime.capacity_limiter.release(lease)


@pytest.mark.asyncio
@pytest.mark.parametrize("late_delta", [False, True], ids=["late-success", "late-delta"])
async def test_public_reset_rejects_cancellation_resistant_llm_success(
    late_result_probe: LateResultProbe, late_delta: bool
) -> None:
    probe = late_result_probe
    probe.late_delta = late_delta
    run = await probe.start()
    await asyncio.wait_for(probe.provider_entered.wait(), 0.5)
    assert run.task is not None and run.generation_id is not None
    generation = probe.session.generations[run.generation_id]
    call = probe.session.calls[run.generation_id]
    await asyncio.wait_for(probe.runtime.reset_agent(run.match_id), 0.3)
    assert probe.cancellation_seen.is_set() and not run.task.done()
    assert generation.status == "FAILED" and generation.error_code == "agent_cancelled"
    probe.release_provider.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run.task, 0.5)
    assert generation.status == "FAILED" and generation.error_code == "agent_cancelled"
    assert generation.response_blob_id is None
    assert call.status == "FAILED" and call.error_code == "agent_cancelled"
    assert run.draft == "合成测试。"
    assert run.decoder is None and run.source is None and run.tts_task is None
    probe.callbacks.publish_agent_text_delta.assert_awaited_once()
    probe.callbacks.agent_text_generated.assert_not_awaited()
    probe.callbacks.handle_agent_failure.assert_not_awaited()
    probe.store_blob.assert_not_awaited()
    probe.clients[0].close.assert_awaited_once()
    await assert_capacity_reusable(probe)


@pytest.mark.asyncio
@pytest.mark.parametrize("fence", ["media", "generation", "envelope", "registry", "closed"])
async def test_result_rechecks_identity_after_provider_close(
    late_result_probe: LateResultProbe, fence: str
) -> None:
    probe = late_result_probe
    run = await probe.start()
    await asyncio.wait_for(probe.provider_entered.wait(), 0.5)
    assert run.generation_id is not None and run.task is not None
    original_generation = run.generation_id

    async def close() -> None:
        if fence == "media":
            await probe.runtime.fence_agent(run.match_id)
        elif fence == "generation":
            run.generation_id = uuid4()
        elif fence == "envelope":
            assert run.callback_envelope is not None
            run.callback_envelope = replace(run.callback_envelope, generation_id=uuid4())
        elif fence == "registry":
            probe.runtime._runs.pop(run.match_id)
        else:
            probe.runtime._closed = True

    probe.clients[0].close.side_effect = close
    probe.release_provider.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run.task, 0.5)
    assert probe.session.generations[original_generation].status == "STARTED"
    probe.store_blob.assert_not_awaited()
    probe.callbacks.agent_text_generated.assert_not_awaited()
    probe.callbacks.handle_agent_failure.assert_not_awaited()
    if fence == "media":
        assert probe.runtime._runs.get(run.match_id) is run
        assert run.spool_path is not None and run.spool_path.exists()
        await probe.runtime.reset_agent(run.match_id)
        assert run.decoder is None and run.source is None and run.room is None
        assert run.tts_task is None and run.text_queue is None
    await assert_capacity_reusable(probe)


@pytest.mark.asyncio
async def test_fence_at_llm_ready_commit_return_blocks_full_text_callback(
    late_result_probe: LateResultProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = late_result_probe
    entered, release = asyncio.Event(), asyncio.Event()

    mark_ready = probe.runtime._mark_generation_llm_ready

    async def committed_ready(*arguments: Any, **keywords: Any) -> None:
        await mark_ready(*arguments, **keywords)
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass

    monkeypatch.setattr(probe.runtime, "_mark_generation_llm_ready", committed_ready)
    run = await probe.start()
    await asyncio.wait_for(probe.provider_entered.wait(), 0.5)
    probe.release_provider.set()
    await asyncio.wait_for(entered.wait(), 0.5)
    assert run.task is not None and run.generation_id is not None
    generation = probe.session.generations[run.generation_id]
    assert generation.status == "PLAYING"
    await probe.runtime.fence_agent(run.match_id)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run.task, 0.5)
    assert generation.status == "PLAYING" and generation.error_code is None
    probe.callbacks.agent_text_generated.assert_not_awaited()
    assert probe.runtime._runs.get(run.match_id) is run
    assert run.spool_path is not None and run.spool_path.exists()
    await probe.runtime.reset_agent(run.match_id)
    assert run.decoder is None and run.source is None and run.room is None
    assert run.tts_task is None and run.text_queue is None
    await assert_capacity_reusable(probe)


@pytest.mark.asyncio
async def test_retired_audio_callback_rejected_before_touching_closed_spool(
    late_result_probe: LateResultProbe,
) -> None:
    probe = late_result_probe
    run = await probe.start()
    await asyncio.wait_for(probe.provider_entered.wait(), 0.5)
    assert probe.audio_callback is not None and run.spool_path is not None
    # A live callback still writes and notifies its own decoder normally.
    await probe.audio_callback(b"synthetic audio")
    assert run.byte_count == len(b"synthetic audio")
    assert run.decoder is not None
    cast(Any, run.decoder).notify_written.assert_called_once_with(run.byte_count)
    await probe.runtime.reset_agent(run.match_id)
    with pytest.raises(asyncio.CancelledError):
        await probe.audio_callback(b"late audio")
    assert run.byte_count == len(b"synthetic audio")
    assert run.decoder is None and run.source is None
    assert run.task is not None
    probe.release_provider.set()
    with pytest.raises(asyncio.CancelledError):
        await run.task


@pytest.mark.asyncio
async def test_retired_success_does_not_touch_replacement_run(
    late_result_probe: LateResultProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = late_result_probe
    old_run = await probe.start()
    await asyncio.wait_for(probe.provider_entered.wait(), 0.5)
    assert old_run.generation_id is not None and old_run.task is not None
    old_generation = old_run.generation_id
    await probe.runtime.reset_agent(old_run.match_id)
    new_provider_entered = asyncio.Event()

    async def replacement_stream(**_: Any) -> LlmStreamResult:
        new_provider_entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    new_client = SimpleNamespace(
        stream_chat=AsyncMock(side_effect=replacement_stream), close=AsyncMock()
    )

    def replacement_client(**_: Any) -> SimpleNamespace:
        return new_client

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", replacement_client)
    new_run = await probe.start()
    async with asyncio.timeout(0.5):
        while new_run.generation_id is None:
            await asyncio.sleep(0)
    assert new_run is not old_run and new_run.generation_id != old_generation
    probe.release_provider.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(old_run.task, 0.5)
    await asyncio.wait_for(new_provider_entered.wait(), 0.5)
    assert probe.runtime._runs[new_run.match_id] is new_run
    assert new_run.task is not None and not new_run.task.done()
    assert not new_run.media_fenced
    assert probe.session.generations[new_run.generation_id].status == "STARTED"
    assert probe.session.generations[old_generation].status == "FAILED"
    assert probe.session.generations[old_generation].error_code == "agent_cancelled"
    probe.callbacks.agent_text_generated.assert_not_awaited()
    probe.store_blob.assert_not_awaited()
    await probe.runtime.reset_agent(new_run.match_id)
    await assert_capacity_reusable(probe)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "retirement"),
    [
        ("tts", "reset"),
        ("tts", "fence"),
        ("tts_capture", "fence"),
        ("playback", "reset"),
        ("playback", "fence"),
        ("playout", "reset"),
        ("playout", "fence"),
    ],
)
async def test_public_retirement_rejects_late_media_tail_without_retry_or_new_generation(
    late_result_probe: LateResultProbe,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    retirement: str,
) -> None:
    probe = late_result_probe
    entered, release = asyncio.Event(), asyncio.Event()

    async def block() -> None:
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass

    async def synthesize(chunks: Any, **_: Any) -> TtsStreamResult:
        async for _chunk in chunks:
            pass
        if phase == "tts":
            await block()
        else:
            await asyncio.sleep(0.01)
        return TtsStreamResult(uuid4(), 16, 10, 20)

    def connection(**_: Any) -> SimpleNamespace:
        return SimpleNamespace(synthesize=synthesize, close=AsyncMock())

    async def playback(run: AgentRun, _: UUID) -> None:
        if phase != "tts":
            run.speech_id = uuid4()
            assert run.callback_envelope is not None
            run.callback_envelope = replace(run.callback_envelope, speech_id=run.speech_id)
        if phase == "playback":
            await block()

    async def capture(*_: Any, **__: Any) -> None:
        if phase == "tts_capture":
            await block()

    async def playout() -> None:
        if phase == "playout":
            await block()

    finish_capture = AsyncMock(side_effect=capture)
    monkeypatch.setattr(runtime_module, "QwenTtsConnection", connection)
    monkeypatch.setattr(probe.runtime, "_play_pcm", playback)
    monkeypatch.setattr(probe.runtime, "_finish_external_call", finish_capture)
    monkeypatch.setattr(
        probe.runtime,
        "_connect_publisher",
        AsyncMock(
            return_value=(
                SimpleNamespace(disconnect=AsyncMock()),
                SimpleNamespace(clear_queue=Mock(), aclose=AsyncMock(), wait_for_playout=playout),
            )
        ),
    )
    probe.release_provider.set()
    run = await probe.start()
    assert run.task is not None
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        # Playback is started before LLM generation. This checkpoint ensures
        # that its late-result probe is actually awaiting the playback tail.
        async with asyncio.timeout(0.5):
            while probe.callbacks.agent_text_generated.await_count == 0 or (
                phase == "playback" and finish_capture.await_count == 0
            ):
                await asyncio.sleep(0)
        assert run.generation_id is not None
        generation = probe.session.generations[run.generation_id]
        assert generation.status == "PLAYING"
        if retirement == "fence":
            # Cancellation already propagates from the bounded capture. A
            # public media fence exercises the independent stale-result guard.
            await probe.runtime.fence_agent(run.match_id)
        else:
            await asyncio.wait_for(probe.runtime.reset_agent(run.match_id), 0.3)
            assert generation.status == "FAILED"
            assert generation.error_code == "agent_cancelled"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run.task, 0.9)
        if retirement == "reset":
            assert generation.status == "FAILED"
            assert generation.error_code == "agent_cancelled"
        assert len(probe.generations) == 1
        assert not run.natural_complete
        probe.callbacks.agent_text_generated.assert_awaited_once()
        probe.callbacks.finish_agent_playback.assert_not_awaited()
        probe.callbacks.handle_agent_failure.assert_not_awaited()
        probe.callbacks.publish_agent_retry.assert_not_awaited()
        if phase == "tts":
            finish_capture.assert_not_awaited()
            assert run.tts_task_id is None and run.first_audio_latency_ms is None
        if retirement == "fence":
            assert probe.runtime._runs.get(run.match_id) is run
            assert run.spool_path is not None and run.spool_path.exists()
            await probe.runtime.reset_agent(run.match_id)
        assert run.decoder is None and run.source is None and run.room is None
        assert run.tts_task is None and run.text_queue is None
        await assert_capacity_reusable(probe)
    finally:
        release.set()
        run.task.cancel()
        await asyncio.gather(run.task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "FINALIZED", "missing", "error"])
async def test_llm_ready_transaction_rejects_retired_generation(
    late_result_probe: LateResultProbe, status: str
) -> None:
    probe = late_result_probe
    generation_id = uuid4()
    generation = SimpleNamespace(
        status="STARTED" if status == "error" else status,
        error_code="agent_cancelled" if status in {"FAILED", "CANCELLED", "error"} else None,
        match_id=probe.config.match_id,
        response_blob_id=None,
    )
    if status != "missing":
        probe.session.generations[generation_id] = generation
    original_status, original_error = generation.status, generation.error_code
    with pytest.raises(asyncio.CancelledError):
        await probe.runtime._mark_generation_llm_ready(
            generation_id, LlmStreamResult("迟到成功。", 1, 2, 3), None
        )
    assert probe.session.locked_reads == [generation_id]
    assert (generation.status, generation.error_code) == (original_status, original_error)
    assert generation.response_blob_id is None
    probe.store_blob.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["STARTED", "LLM_READY", "PLAYING"])
async def test_llm_ready_transaction_accepts_only_live_generation(
    late_result_probe: LateResultProbe, status: str
) -> None:
    probe = late_result_probe
    generation_id = uuid4()
    generation = SimpleNamespace(
        status=status, error_code=None, match_id=probe.config.match_id, response_blob_id=None
    )
    probe.session.generations[generation_id] = generation
    await probe.runtime._mark_generation_llm_ready(
        generation_id, LlmStreamResult("当前成功。", 1, 2, 3), None
    )
    assert generation.status == "PLAYING" and generation.response_blob_id is not None
    probe.store_blob.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "FINALIZED"])
async def test_cancel_cleanup_preserves_terminal_generation_failure_metadata(
    late_result_probe: LateResultProbe,
    status: str,
) -> None:
    probe = late_result_probe
    generation_id = uuid4()
    completed_at = datetime(2026, 10, 13, 1, 2, 3, tzinfo=UTC)
    original_error = None if status == "FINALIZED" else "llm_stream_interrupted"
    generation = SimpleNamespace(
        status=status,
        error_code=original_error,
        completed_at=completed_at,
        match_id=probe.config.match_id,
    )
    call = SimpleNamespace(
        id=uuid4(),
        status="STARTED",
        error_code=None,
        completed_at=None,
        call_kind="LLM_SPEECH",
    )
    probe.session.generations[generation_id] = generation
    probe.session.calls[generation_id] = call
    await probe.runtime._mark_generation_failed(generation_id, "agent_cancelled")
    assert generation.status == status
    assert generation.error_code == original_error
    assert generation.completed_at == completed_at
    if status == "FAILED":
        assert call.status == "FAILED" and call.error_code == original_error
        assert call.completed_at is not None
    else:
        assert call.status == "STARTED" and call.error_code is None
        assert call.completed_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("retirement", ["fenced", "missing", "replaced"])
async def test_retired_run_cannot_enter_retry_and_allocate_another_generation(
    late_result_probe: LateResultProbe,
    retirement: str,
) -> None:
    probe = late_result_probe
    run = AgentRun(
        match_id=probe.config.match_id,
        action_key=probe.config.action_key,
        agent_profile_id=probe.config.agent_profile_id,
        duration_ms=1000,
    )
    if retirement == "fenced":
        probe.runtime._runs[run.match_id] = run
        await probe.runtime.fence_agent(run.match_id)
    elif retirement == "replaced":
        probe.runtime._runs[run.match_id] = AgentRun(
            match_id=run.match_id,
            action_key="1:2",
            agent_profile_id=run.agent_profile_id,
            duration_ms=1000,
        )
    await probe.runtime._run_with_retry(run)
    cast(AsyncMock, probe.runtime._load_config).assert_not_awaited()
    assert not probe.generations
    probe.callbacks.publish_agent_retry.assert_not_awaited()
    probe.callbacks.handle_agent_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_fenced_timeout_finalization_retains_owned_spool_until_persisted(
    late_result_probe: LateResultProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = late_result_probe
    entered = asyncio.Event()

    async def stream(**arguments: Any) -> LlmStreamResult:
        await arguments["on_delta"]("已播放的合成发言。")
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    client = SimpleNamespace(stream_chat=stream, close=AsyncMock())

    def provider(**_: Any) -> SimpleNamespace:
        return client

    async def playback(run: AgentRun, _: UUID) -> None:
        run.speech_id = uuid4()
        run.played_samples = 48_000
        assert run.callback_envelope is not None
        run.callback_envelope = replace(run.callback_envelope, speech_id=run.speech_id)
        await asyncio.Event().wait()

    persist_final = AsyncMock()
    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", provider)
    monkeypatch.setattr(probe.runtime, "_play_pcm", playback)
    monkeypatch.setattr(probe.runtime, "_persist_final", persist_final)
    run = await probe.start()
    await asyncio.wait_for(entered.wait(), 0.5)
    assert run.speech_id is not None and probe.audio_callback is not None
    await probe.audio_callback(b"synthetic audio")
    assert run.storage_path is not None
    await probe.runtime.fence_agent(run.match_id)
    await probe.runtime.finalize_agent(run.match_id, run.speech_id, "TIMEOUT")
    assert run.finalizing
    persist_final.assert_awaited_once_with(run)
    probe.callbacks.finalize_agent_speech.assert_awaited_once()
    probe.callbacks.handle_agent_failure.assert_not_awaited()
    assert run.storage_path.read_bytes() == b"synthetic audio"
    final = probe.callbacks.finalize_agent_speech.await_args
    assert final is not None and final.kwargs["audio_truncated"] is True
    assert final.kwargs["final_text"] == "已播放的合成发言。"


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", ["finalize", "reset"])
async def test_body_retiring_during_fence_dispatch_window_keeps_authority_handler_ownership(
    late_result_probe: LateResultProbe,
    monkeypatch: pytest.MonkeyPatch,
    handler: str,
) -> None:
    probe = late_result_probe

    async def playback(run: AgentRun, _: UUID) -> None:
        run.speech_id = uuid4()
        run.played_samples = 48_000
        assert run.callback_envelope is not None
        run.callback_envelope = replace(run.callback_envelope, speech_id=run.speech_id)
        await asyncio.Event().wait()

    persist_final = AsyncMock()
    monkeypatch.setattr(probe.runtime, "_play_pcm", playback)
    monkeypatch.setattr(probe.runtime, "_persist_final", persist_final)
    run = await probe.start()
    await asyncio.wait_for(probe.provider_entered.wait(), 0.5)
    assert run.task is not None and run.speech_id is not None
    assert probe.audio_callback is not None and run.storage_path is not None
    await probe.audio_callback(b"synthetic audio")
    await probe.runtime.fence_agent(run.match_id)
    # Deterministically model a body completing after a public fence, before
    # the authority's dispatched finalizer takes ownership of its resources.
    probe.release_provider.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run.task, 0.5)
    assert probe.runtime._runs.get(run.match_id) is run
    assert run.spool_path is not None and run.spool_path.exists()
    if handler == "finalize":
        await probe.runtime.finalize_agent(run.match_id, run.speech_id, "TIMEOUT")
        persist_final.assert_awaited_once_with(run)
        probe.callbacks.finalize_agent_speech.assert_awaited_once()
        probe.callbacks.handle_agent_failure.assert_not_awaited()
        assert run.storage_path.read_bytes() == b"synthetic audio"
    else:
        await probe.runtime.reset_agent(run.match_id)
        assert run.generation_id is not None
        generation = probe.session.generations[run.generation_id]
        assert generation.status == "FAILED" and generation.error_code == "agent_cancelled"
        assert run.spool_path is None and run.decoder is None and run.source is None
        persist_final.assert_not_awaited()
        probe.callbacks.finalize_agent_speech.assert_not_awaited()
    assert probe.runtime._runs.get(run.match_id) is None

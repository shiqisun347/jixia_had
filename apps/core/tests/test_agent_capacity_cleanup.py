from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

import jx_core.agent.runtime as runtime_module
import jx_core.data_capture.provider_writer as writer_module
from jx_core.agent.llm import LlmCapacityLimiter, LlmProviderError, LlmStreamResult
from jx_core.agent.runtime import AgentConfig, AgentRun, AgentRuntime
from jx_core.agent.tts import TtsProviderError, TtsStreamResult
from jx_core.config import Settings
from jx_core.data_capture.provider_writer import CaptureSubmission, ProviderCaptureWriter
from jx_core.models import ExternalCall
from jx_core.runtime_identity import CallbackEnvelope


@dataclass
class PipelineExecution:
    runtime: AgentRuntime
    run: AgentRun
    config: AgentConfig
    spool: io.BytesIO
    client: SimpleNamespace


@pytest.fixture
def pipeline_execution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> PipelineExecution:
    runtime = AgentRuntime(
        settings=cast(Any, Settings)(
            _env_file=None,
            database_url=SecretStr("postgresql+psycopg://test:test@localhost/test"),
            agent_audio_storage_dir=str(tmp_path),
            llm_global_concurrency=2,
        ),
        session_factory=cast(Any, None),
        callbacks=AsyncMock(),
    )
    run = AgentRun(
        match_id=uuid4(),
        action_key="1:1",
        agent_profile_id=uuid4(),
        duration_ms=1000,
        generation_id=uuid4(),
    )
    config = AgentConfig(
        match_id=run.match_id,
        action_key=run.action_key,
        agent_profile_id=run.agent_profile_id,
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
    runtime._runs[run.match_id] = run  # pyright: ignore[reportPrivateUsage]
    runtime._tts_connections[run.match_id] = cast(  # pyright: ignore[reportPrivateUsage]
        Any, SimpleNamespace(close=AsyncMock())
    )
    monkeypatch.setattr(
        runtime,
        "_connect_publisher",
        AsyncMock(
            return_value=(
                SimpleNamespace(disconnect=AsyncMock()),
                SimpleNamespace(
                    clear_queue=Mock(), aclose=AsyncMock(), wait_for_playout=AsyncMock()
                ),
            )
        ),
    )
    monkeypatch.setattr(runtime, "_start_generation_call", AsyncMock())
    spool = io.BytesIO()
    open_path = Path.open

    def open_spool(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if mode == "ab":
            return spool
        return open_path(path, mode, *args, **kwargs)

    def decoder() -> SimpleNamespace:
        return SimpleNamespace(start=AsyncMock(), finish_input=Mock(), close=AsyncMock())

    client = SimpleNamespace(stream_chat=AsyncMock(), close=AsyncMock())

    def construct(**_: Any) -> SimpleNamespace:
        return client

    monkeypatch.setattr(Path, "open", open_spool)
    monkeypatch.setattr(runtime_module, "IncrementalOpusDecoder", decoder)
    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", construct)
    return PipelineExecution(runtime, run, config, spool, client)


@dataclass
class SpokenPipelineExecution:
    pipeline: PipelineExecution
    generation_id: UUID
    callbacks: AsyncMock
    synthesize: AsyncMock
    llm_start: AsyncMock
    tts_start: AsyncMock
    finish_capture: AsyncMock
    mark_ready: AsyncMock

    def start(self) -> asyncio.Task[None]:
        case = self.pipeline
        execution = case.runtime._execute(  # pyright: ignore[reportPrivateUsage]
            case.run, case.config, self.generation_id
        )
        case.run.task = asyncio.create_task(execution)
        return case.run.task


@pytest.fixture
def spoken_pipeline_execution(
    monkeypatch: pytest.MonkeyPatch, pipeline_execution: PipelineExecution
) -> SpokenPipelineExecution:
    case = pipeline_execution
    generation_id = uuid4()
    case.run.generation_id = generation_id
    case.run.callback_envelope = CallbackEnvelope(
        match_id=case.run.match_id,
        speech_id=None,
        attempt_no=1,
        generation_id=generation_id,
        connection_epoch=None,
        context_version=case.config.context_version,
        opportunity_id=uuid4(),
        opportunity_generation=1,
    )

    async def stream_chat(**kwargs: Any) -> LlmStreamResult:
        await kwargs["on_delta"]("合成测试。")
        return LlmStreamResult("合成测试。", 1, 2, 5)

    async def synthesize(chunks: Any, **_: Any) -> TtsStreamResult:
        async for _chunk in chunks:
            pass
        await asyncio.sleep(0.01)
        return TtsStreamResult(uuid4(), 1, 1, 2)

    async def playback(run: AgentRun, _generation_id: UUID) -> None:
        run.speech_id = uuid4()
        assert run.callback_envelope is not None
        run.callback_envelope = replace(run.callback_envelope, speech_id=run.speech_id)

    synthesize_mock = AsyncMock(side_effect=synthesize)
    case.client.stream_chat = AsyncMock(side_effect=stream_chat)
    case.runtime._tts_connections[case.run.match_id] = cast(  # pyright: ignore[reportPrivateUsage]
        Any, SimpleNamespace(synthesize=synthesize_mock, close=AsyncMock())
    )
    llm_start, tts_start = (AsyncMock(return_value=uuid4()) for _ in range(2))
    finish_capture, mark_ready = AsyncMock(), AsyncMock()
    monkeypatch.setattr(case.runtime, "_start_generation_call", llm_start)
    monkeypatch.setattr(case.runtime, "_start_tts_call", tts_start)
    monkeypatch.setattr(case.runtime, "_finish_external_call", finish_capture)
    monkeypatch.setattr(case.runtime, "_mark_generation_llm_ready", mark_ready)
    monkeypatch.setattr(case.runtime, "_mark_generation_failed", AsyncMock())
    monkeypatch.setattr(case.runtime, "_play_pcm", playback)
    monkeypatch.setattr(runtime_module, "AGENT_RESOURCE_CLEANUP_TIMEOUT_SECONDS", 0.05)
    return SpokenPipelineExecution(
        case,
        generation_id,
        cast(AsyncMock, case.runtime._callbacks),  # pyright: ignore[reportPrivateUsage]
        synthesize_mock,
        llm_start,
        tts_start,
        finish_capture,
        mark_ready,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_agent_capacity_failure_closes_client_and_spool(
    monkeypatch: pytest.MonkeyPatch, pipeline_execution: PipelineExecution, cancelled: bool
) -> None:
    case = pipeline_execution
    error = asyncio.CancelledError() if cancelled else LlmProviderError("llm_capacity_full")
    monkeypatch.setattr(case.runtime.capacity_limiter, "acquire", AsyncMock(side_effect=error))
    with pytest.raises(type(error)):
        execution = case.runtime._execute(  # pyright: ignore[reportPrivateUsage]
            case.run, case.config, cast(UUID, case.run.generation_id)
        )
        await execution

    assert case.spool.closed
    case.client.close.assert_awaited_once()
    case.client.stream_chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_constructor_failure_closes_spool(
    monkeypatch: pytest.MonkeyPatch, pipeline_execution: PipelineExecution
) -> None:
    case = pipeline_execution

    def construct(**_: Any) -> None:
        raise RuntimeError("synthetic-constructor-failed")

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", construct)
    with pytest.raises(RuntimeError, match="synthetic-constructor-failed"):
        execution = case.runtime._execute(  # pyright: ignore[reportPrivateUsage]
            case.run, case.config, cast(UUID, case.run.generation_id)
        )
        await execution
    assert case.spool.closed


@pytest.mark.asyncio
async def test_agent_spool_close_error_does_not_mask_provider_error_or_skip_cleanup(
    monkeypatch: pytest.MonkeyPatch, pipeline_execution: PipelineExecution
) -> None:
    case = pipeline_execution
    close_spool = case.spool.close
    playback_cancelled = asyncio.Event()

    def failing_close() -> None:
        close_spool()
        raise OSError("synthetic-spool-close-failed")

    async def playback(*_: Any) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            playback_cancelled.set()

    monkeypatch.setattr(case.spool, "close", failing_close)
    monkeypatch.setattr(case.runtime, "_play_pcm", playback)
    case.client.stream_chat = AsyncMock(side_effect=LlmProviderError("llm_first_token_timeout"))
    with pytest.raises(LlmProviderError, match="llm_first_token_timeout"):
        execution = case.runtime._execute(  # pyright: ignore[reportPrivateUsage]
            case.run, case.config, cast(UUID, case.run.generation_id)
        )
        await execution
    assert case.spool.closed
    case.client.close.assert_awaited_once()
    await asyncio.wait_for(playback_cancelled.wait(), timeout=0.5)
    limiter = case.runtime.capacity_limiter
    leases = await limiter.acquire(case.config.model_key, 1, timeout_seconds=0.1)
    limiter.release(leases)


@pytest.mark.asyncio
async def test_agent_repeated_cancel_during_close_releases_spool_and_capacity(
    monkeypatch: pytest.MonkeyPatch, pipeline_execution: PipelineExecution
) -> None:
    case = pipeline_execution
    stream_started = asyncio.Event()
    close_started = asyncio.Event()
    close_cancelled = asyncio.Event()

    async def stream_chat(**_: Any) -> None:
        stream_started.set()
        await asyncio.Event().wait()

    async def close() -> None:
        close_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            close_cancelled.set()

    async def playback(*_: Any) -> None:
        await asyncio.Event().wait()

    case.client.stream_chat = stream_chat
    case.client.close = close
    monkeypatch.setattr(case.runtime, "_play_pcm", playback)
    execution = case.runtime._execute(  # pyright: ignore[reportPrivateUsage]
        case.run, case.config, cast(UUID, case.run.generation_id)
    )
    task = asyncio.create_task(execution)
    try:
        await asyncio.wait_for(stream_started.wait(), timeout=0.5)
        task.cancel()
        await asyncio.wait_for(close_started.wait(), timeout=0.5)
        assert case.spool.closed
        limiter = case.runtime.capacity_limiter
        first = await limiter.acquire(case.config.model_key, 1, timeout_seconds=0.1)
        try:
            second = await limiter.acquire("capacity-reuse-probe", 1, timeout_seconds=0.1)
            limiter.release(second)
        finally:
            limiter.release(first)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(close_cancelled.wait(), timeout=0.5)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@dataclass
class DecisionExecution:
    pipeline: PipelineExecution
    callbacks: AsyncMock
    envelope: CallbackEnvelope

    def start(self) -> asyncio.Task[None]:
        case = self.pipeline
        return asyncio.create_task(
            case.runtime.decide_free_debate(
                match_id=case.run.match_id,
                action_key=case.run.action_key,
                side="AFFIRMATIVE",
                agent_profile_ids=[case.run.agent_profile_id],
                decision_round_id=uuid4(),
                envelope=self.envelope,
            )
        )


@pytest.fixture
def decision_execution(
    monkeypatch: pytest.MonkeyPatch, pipeline_execution: PipelineExecution
) -> DecisionExecution:
    case = pipeline_execution
    case.config = replace(case.config, formal_4v4=True)
    callbacks = AsyncMock()
    monkeypatch.setattr(case.runtime, "_callbacks", callbacks)
    monkeypatch.setattr(case.runtime, "_load_config", AsyncMock(return_value=case.config))
    monkeypatch.setattr(case.runtime, "_start_decision_call", AsyncMock(return_value=uuid4()))
    monkeypatch.setattr(case.runtime, "_finish_external_call", AsyncMock())
    monkeypatch.setattr(case.runtime, "_fail_external_call", AsyncMock())
    monkeypatch.setattr(runtime_module, "AGENT_RESOURCE_CLEANUP_TIMEOUT_SECONDS", 0.05)
    case.client.stream_chat = AsyncMock(
        return_value=SimpleNamespace(text='{"should_speak":true,"decision_reason":"回应论点"}')
    )
    return DecisionExecution(
        case,
        callbacks,
        CallbackEnvelope(
            match_id=case.run.match_id,
            speech_id=None,
            attempt_no=1,
            generation_id=None,
            connection_epoch=None,
            context_version=case.config.context_version,
            opportunity_id=uuid4(),
            opportunity_generation=1,
        ),
    )


class BlockingDecisionCapture:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.tasks: list[asyncio.Task[Any]] = []

    async def __call__(self, *_: Any, **__: Any) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.append(task)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            # A diagnostic driver may complete late even after cancellation.
            await self.release.wait()
        raise RuntimeError("synthetic-private-diagnostic-payload")

    async def finish_and_check_consumed(self) -> None:
        self.release.set()
        done, pending = await asyncio.wait(self.tasks, timeout=0.5)
        assert not pending
        assert len(done) == len(self.tasks)
        await asyncio.sleep(0)
        for task in self.tasks:
            assert not task._log_traceback  # pyright: ignore[reportPrivateUsage]


class CaptureAuthority:
    """Run the real result methods against a local, explicitly committed unit fixture."""

    def __init__(self) -> None:
        self.calls: dict[UUID, ExternalCall] = {}
        self.committed: list[tuple[UUID, str, str | None]] = []
        self.commit_started = asyncio.Event()
        self.commit_release = asyncio.Event()
        self.commit_release.set()
        self.hold_success_commit = False
        self.storage = BlockingDecisionCapture()

    def call(self, call_id: UUID, *, attempt_no: int = 1) -> ExternalCall:
        if call_id not in self.calls:
            self.calls[call_id] = ExternalCall(
                id=call_id,
                call_kind="AGENT_DECISION",
                provider="synthetic",
                operation="chat.completions",
                attempt_no=attempt_no,
                status="STARTED",
                started_at=datetime.now(UTC),
            )
        return self.calls[call_id]

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[Any]:
        @asynccontextmanager
        async def transaction() -> AsyncGenerator[None]:
            fields = (
                "status",
                "error_code",
                "request_blob_id",
                "response_blob_id",
                "completed_at",
                "capture_storage_mode",
            )
            before = {
                key: tuple(getattr(call, field) for field in fields)
                for key, call in self.calls.items()
            }
            try:
                yield
                if self.hold_success_commit and any(
                    call.status == "SUCCEEDED" for call in self.calls.values()
                ):
                    self.commit_started.set()
                    await self.commit_release.wait()
                self.committed.extend(
                    (key, call.status, call.error_code)
                    for key, call in self.calls.items()
                    if tuple(getattr(call, field) for field in fields) != before[key]
                )
            except BaseException:
                for key, values in before.items():
                    for field, value in zip(fields, values, strict=True):
                        setattr(self.calls[key], field, value)
                raise

        async def get(_model: Any, call_id: UUID, **_: Any) -> ExternalCall:
            return self.calls[call_id]

        yield SimpleNamespace(begin=transaction, get=get, flush=AsyncMock())

    async def persist(self, batch: list[CaptureSubmission]) -> None:
        # Raw storage may start only after the real result method has committed.
        committed_ids = {key for key, _status, _error in self.committed}
        assert all(item.identity.external_call_id in committed_ids for item in batch)
        await self.storage()


@asynccontextmanager
async def capture_storage_fault(
    monkeypatch: pytest.MonkeyPatch, case: PipelineExecution, *, raises: bool = False
) -> AsyncGenerator[tuple[CaptureAuthority, ProviderCaptureWriter]]:
    authority = CaptureAuthority()
    if raises:
        authority.storage.release.set()
    writer = ProviderCaptureWriter("postgresql+psycopg://test:test@localhost/test")
    monkeypatch.setattr(writer_module, "BATCH_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(writer_module, "FLUSH_SECONDS", 0.001)
    monkeypatch.setattr(writer_module, "RETRY_SECONDS", 0.001)
    monkeypatch.setattr(writer_module, "SHUTDOWN_SECONDS", 0.2)
    monkeypatch.setattr(writer, "_persist_batch", authority.persist)
    monkeypatch.setattr(case.runtime, "_capture_writer", writer)
    monkeypatch.setattr(case.runtime, "_session_factory", authority.session)
    monkeypatch.setattr(runtime_module, "store_content_blob", AsyncMock(return_value=uuid4()))
    for name in ("_finish_external_call", "_fail_external_call"):
        monkeypatch.setattr(case.runtime, name, getattr(AgentRuntime, name).__get__(case.runtime))
    mark_ready = case.runtime._mark_generation_llm_ready  # pyright: ignore[reportPrivateUsage]
    if isinstance(mark_ready, AsyncMock):

        async def mark_ready_and_release(*args: Any, **kwargs: Any) -> None:
            try:
                await mark_ready(*args, **kwargs)
            finally:
                args[2].close()

        monkeypatch.setattr(case.runtime, "_mark_generation_llm_ready", mark_ready_and_release)

    async def start_decision(**kwargs: Any) -> UUID:
        call_id = uuid4()
        authority.call(call_id, attempt_no=kwargs["attempt_no"])
        return call_id

    monkeypatch.setattr(case.runtime, "_start_decision_call", AsyncMock(side_effect=start_decision))
    tts_start = case.runtime._start_tts_call  # pyright: ignore[reportPrivateUsage]
    if isinstance(tts_start, AsyncMock) and isinstance(tts_start.return_value, UUID):
        authority.call(tts_start.return_value)
    await writer.start()
    try:
        yield authority, writer
    finally:
        authority.commit_release.set()
        authority.storage.release.set()
        await writer.close()
        if authority.storage.tasks:
            await authority.storage.finish_and_check_consumed()
        assert writer.stats()["items"] == 0
        assert writer.stats()["retained_bytes"] == 0


async def assert_capture_storage_failed(writer: ProviderCaptureWriter) -> None:
    async def observed() -> None:
        while not writer.stats().get("persist_failed", 0):
            await asyncio.sleep(0.001)

    await asyncio.wait_for(observed(), timeout=0.5)


async def assert_decision_capacity_reusable(case: PipelineExecution) -> None:
    limiter = case.runtime.capacity_limiter
    first = await limiter.acquire(case.config.model_key, 1, timeout_seconds=0.01)
    try:
        second = await limiter.acquire("decision-global-probe", 1, timeout_seconds=0.01)
        limiter.release(second)
    finally:
        limiter.release(first)


@pytest.mark.asyncio
async def test_agent_blocked_start_capture_owns_no_client_or_capacity(
    monkeypatch: pytest.MonkeyPatch,
    spoken_pipeline_execution: SpokenPipelineExecution,
    caplog: pytest.LogCaptureFixture,
) -> None:
    spoken = spoken_pipeline_execution
    case = spoken.pipeline
    capture = BlockingDecisionCapture()
    construct = Mock(return_value=case.client)
    case.client.stream_chat = AsyncMock(side_effect=LlmProviderError("llm_first_token_timeout"))
    monkeypatch.setattr(case.runtime, "_start_generation_call", capture)
    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", construct)
    task = spoken.start()
    try:
        await asyncio.wait_for(capture.started.wait(), timeout=0.5)
        construct.assert_not_called()
        await assert_decision_capacity_reusable(case)
        with pytest.raises(LlmProviderError, match="llm_first_token_timeout"):
            await asyncio.wait_for(task, timeout=0.5)
        construct.assert_called_once()
        case.client.stream_chat.assert_awaited_once()
        case.client.close.assert_awaited_once()
        assert case.spool.closed
        await assert_decision_capacity_reusable(case)
        assert any(
            getattr(record, "error_code", None) == "llm_capture_timeout"
            for record in caplog.records
        )
        assert "synthetic-private-diagnostic-payload" not in caplog.text
        await capture.finish_and_check_consumed()
    finally:
        capture.release.set()
        task.cancel()
        await asyncio.gather(task, *capture.tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["llm", "tts"])
async def test_agent_start_capture_failure_preserves_provider_error(
    monkeypatch: pytest.MonkeyPatch,
    spoken_pipeline_execution: SpokenPipelineExecution,
    caplog: pytest.LogCaptureFixture,
    provider: str,
) -> None:
    spoken = spoken_pipeline_execution
    case = spoken.pipeline
    capture = AsyncMock(side_effect=RuntimeError("synthetic-private-diagnostic-payload"))
    if provider == "llm":
        monkeypatch.setattr(case.runtime, "_start_generation_call", capture)
        case.client.stream_chat = AsyncMock(side_effect=LlmProviderError("llm_first_token_timeout"))
        expected_error = LlmProviderError
        expected_code = "llm_first_token_timeout"
    else:
        monkeypatch.setattr(case.runtime, "_start_tts_call", capture)
        spoken.synthesize.side_effect = TtsProviderError("tts_first_audio_timeout")
        expected_error = TtsProviderError
        expected_code = "tts_first_audio_timeout"
    with pytest.raises(expected_error, match=expected_code):
        await asyncio.wait_for(spoken.start(), timeout=0.5)
    capture.assert_awaited_once()
    case.client.stream_chat.assert_awaited_once()
    case.client.close.assert_awaited_once()
    assert case.spool.closed
    spoken.mark_ready.assert_not_awaited()
    assert any(
        getattr(record, "error_code", None) == f"{provider}_capture_failed"
        for record in caplog.records
    )
    assert "synthetic-private-diagnostic-payload" not in caplog.text
    await assert_decision_capacity_reusable(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["llm_start", "tts_start"])
async def test_reset_during_start_capture_never_restarts_retired_provider(
    monkeypatch: pytest.MonkeyPatch,
    spoken_pipeline_execution: SpokenPipelineExecution,
    phase: str,
) -> None:
    spoken = spoken_pipeline_execution
    case = spoken.pipeline
    capture = BlockingDecisionCapture()
    construct = Mock(return_value=case.client)
    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", construct)
    monkeypatch.setattr(
        case.runtime,
        "_start_generation_call" if phase == "llm_start" else "_start_tts_call",
        capture,
    )
    task = spoken.start()
    try:
        await asyncio.wait_for(capture.started.wait(), timeout=0.5)
        await asyncio.wait_for(case.runtime.reset_agent(case.run.match_id), timeout=0.5)
        assert task.cancelled() and case.run.media_fenced
        with pytest.raises(asyncio.CancelledError):
            await task
        assert case.spool.closed
        if phase == "llm_start":
            construct.assert_not_called()
            case.client.stream_chat.assert_not_awaited()
            case.client.close.assert_not_awaited()
        else:
            construct.assert_called_once()
            case.client.stream_chat.assert_awaited_once()
            case.client.close.assert_awaited_once()
        spoken.synthesize.assert_not_awaited()
        spoken.callbacks.finish_agent_playback.assert_not_awaited()
        await assert_decision_capacity_reusable(case)
        await capture.finish_and_check_consumed()
        assert case.client.stream_chat.await_count == (0 if phase == "llm_start" else 1)
        spoken.synthesize.assert_not_awaited()
        spoken.callbacks.finish_agent_playback.assert_not_awaited()
    finally:
        capture.release.set()
        task.cancel()
        await asyncio.gather(task, *capture.tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["start_capture", "capacity"])
async def test_agent_fenced_before_stream_does_not_call_provider(
    monkeypatch: pytest.MonkeyPatch,
    spoken_pipeline_execution: SpokenPipelineExecution,
    phase: str,
) -> None:
    spoken = spoken_pipeline_execution
    case = spoken.pipeline
    construct = Mock(return_value=case.client)
    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", construct)
    if phase == "start_capture":

        async def fenced_capture(*_: Any, **__: Any) -> UUID:
            case.run.media_fenced = True
            return uuid4()

        monkeypatch.setattr(case.runtime, "_start_generation_call", fenced_capture)
    else:
        original_acquire = case.runtime.capacity_limiter.acquire

        async def fenced_acquire(
            model_key: str, model_limit: int, timeout_seconds: float = 3.0
        ) -> tuple[asyncio.Semaphore, asyncio.Semaphore]:
            leases = await original_acquire(model_key, model_limit, timeout_seconds)
            case.run.media_fenced = True
            return leases

        monkeypatch.setattr(case.runtime.capacity_limiter, "acquire", fenced_acquire)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(spoken.start(), timeout=0.5)
    case.client.stream_chat.assert_not_awaited()
    spoken.synthesize.assert_not_awaited()
    assert construct.call_count == (0 if phase == "start_capture" else 1)
    assert case.client.close.await_count == (0 if phase == "start_capture" else 1)
    assert case.spool.closed
    await assert_decision_capacity_reusable(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["start", "completion"])
async def test_agent_tts_diagnostic_deadline_does_not_block_speech_or_eof(
    monkeypatch: pytest.MonkeyPatch,
    spoken_pipeline_execution: SpokenPipelineExecution,
    caplog: pytest.LogCaptureFixture,
    phase: str,
) -> None:
    spoken = spoken_pipeline_execution
    case = spoken.pipeline
    capture = BlockingDecisionCapture()
    async with AsyncExitStack() as stack:
        writer = None
        authority = None
        if phase == "start":
            monkeypatch.setattr(case.runtime, "_start_tts_call", capture)
        else:
            authority, writer = await stack.enter_async_context(
                capture_storage_fault(monkeypatch, case)
            )
            capture = authority.storage
        task = spoken.start()
        try:
            await asyncio.wait_for(capture.started.wait(), timeout=0.5)
            if phase == "completion":
                case.client.close.assert_awaited_once()
                await assert_decision_capacity_reusable(case)
            await asyncio.wait_for(task, timeout=0.5)
            spoken.synthesize.assert_awaited_once()
            spoken.mark_ready.assert_awaited_once()
            assert case.run.decoder is not None
            cast(Mock, case.run.decoder.finish_input).assert_called_once()
            assert case.run.source is not None
            cast(AsyncMock, case.run.source.wait_for_playout).assert_awaited_once()
            spoken.callbacks.finish_agent_playback.assert_awaited_once()
            case.client.close.assert_awaited_once()
            assert case.spool.closed
            if writer is None:
                assert any(
                    getattr(record, "error_code", None) == "tts_capture_timeout"
                    for record in caplog.records
                )
            else:
                await assert_capture_storage_failed(writer)
                assert writer.stats()["quarantined"] is True
                assert authority is not None
                assert authority.committed == [(case.run.tts_call_id, "SUCCEEDED", None)]
            assert "synthetic-private-diagnostic-payload" not in caplog.text
            await assert_decision_capacity_reusable(case)
            await capture.finish_and_check_consumed()
        finally:
            capture.release.set()
            task.cancel()
            await asyncio.gather(task, *capture.tasks, return_exceptions=True)
            if case.run.tts_task is not None:
                case.run.tts_task.cancel()
                await asyncio.gather(case.run.tts_task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["llm_start", "tts_start", "completion"])
async def test_agent_capture_errors_do_not_change_successful_speech(
    monkeypatch: pytest.MonkeyPatch,
    spoken_pipeline_execution: SpokenPipelineExecution,
    caplog: pytest.LogCaptureFixture,
    phase: str,
) -> None:
    spoken = spoken_pipeline_execution
    case = spoken.pipeline
    async with capture_storage_fault(monkeypatch, case, raises=True) as (authority, writer):
        capture = AsyncMock(side_effect=RuntimeError("synthetic-private-diagnostic-payload"))
        if phase != "completion":
            monkeypatch.setattr(
                case.runtime,
                "_start_generation_call" if phase == "llm_start" else "_start_tts_call",
                capture,
            )
        await asyncio.wait_for(spoken.start(), timeout=0.5)
        if phase == "completion":
            await assert_capture_storage_failed(writer)
            assert writer.stats()["batch_failed"] == 2
            assert authority.committed == [(case.run.tts_call_id, "SUCCEEDED", None)]
        else:
            capture.assert_awaited_once()
        spoken.synthesize.assert_awaited_once()
        spoken.mark_ready.assert_awaited_once()
        spoken.callbacks.finish_agent_playback.assert_awaited_once()
        assert "synthetic-private-diagnostic-payload" not in caplog.text
        assert case.spool.closed
        await assert_decision_capacity_reusable(case)


@pytest.mark.asyncio
async def test_agent_normal_capture_records_keep_call_associations(
    spoken_pipeline_execution: SpokenPipelineExecution,
) -> None:
    spoken = spoken_pipeline_execution
    case = spoken.pipeline
    await asyncio.wait_for(spoken.start(), timeout=0.5)
    spoken.llm_start.assert_awaited_once_with(
        spoken.generation_id, case.config, logical_call_id=case.run.logical_call_id
    )
    spoken.tts_start.assert_awaited_once_with(case.run, case.config, spoken.generation_id)
    assert case.run.tts_call_id == spoken.tts_start.return_value
    spoken.finish_capture.assert_awaited_once()
    finished = spoken.finish_capture.await_args
    assert finished is not None and finished.args == (case.run.tts_call_id,)
    assert finished.kwargs["provider_capture"] is case.run.tts_capture
    spoken.mark_ready.assert_awaited_once()
    ready = spoken.mark_ready.await_args
    assert ready is not None and ready.args[0] == spoken.generation_id
    assert ready.args[2] is case.run.llm_capture
    spoken.callbacks.finish_agent_playback.assert_awaited_once()
    assert case.spool.closed
    await assert_decision_capacity_reusable(case)


@pytest.mark.asyncio
async def test_authoritative_generation_creation_failure_still_retries_then_fails(
    monkeypatch: pytest.MonkeyPatch, pipeline_execution: PipelineExecution
) -> None:
    case = pipeline_execution
    create_generation = AsyncMock(side_effect=RuntimeError("synthetic-generation-commit-failed"))
    monkeypatch.setattr(case.runtime, "_load_config", AsyncMock(return_value=case.config))
    monkeypatch.setattr(case.runtime, "_create_generation", create_generation)
    monkeypatch.setattr(case.runtime, "_mark_generation_failed", AsyncMock())
    monkeypatch.setattr(case.runtime, "_cleanup_attempt", AsyncMock())
    monkeypatch.setattr(runtime_module, "AGENT_PROVIDER_RETRY_BACKOFF_SECONDS", 0)
    await case.runtime._run_with_retry(case.run)  # pyright: ignore[reportPrivateUsage]
    assert create_generation.await_count == 2
    case.client.stream_chat.assert_not_awaited()
    callbacks = cast(AsyncMock, case.runtime._callbacks)  # pyright: ignore[reportPrivateUsage]
    callbacks.handle_agent_failure.assert_awaited_once()
    failure = callbacks.handle_agent_failure.await_args
    assert failure is not None and failure.kwargs["error_code"] == "agent_pipeline_failed"
    assert failure.kwargs["envelope"].attempt_no == 2
    await assert_decision_capacity_reusable(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_commits", [1, 2])
async def test_free_decision_authority_commit_failure_retries_without_reporting_rolled_back_value(
    monkeypatch: pytest.MonkeyPatch,
    decision_execution: DecisionExecution,
    failed_commits: int,
) -> None:
    case = decision_execution.pipeline
    finish = AsyncMock(
        side_effect=[RuntimeError("synthetic-authority-commit-failed")] * failed_commits
        + [None] * (2 - failed_commits)
    )
    fail = AsyncMock()
    monkeypatch.setattr(case.runtime, "_finish_external_call", finish)
    monkeypatch.setattr(case.runtime, "_fail_external_call", fail)

    await asyncio.wait_for(decision_execution.start(), timeout=0.5)

    assert finish.await_count == 2
    assert fail.await_count == failed_commits
    assert case.client.stream_chat.await_count == 2
    assert case.client.close.await_count == 2
    decision_execution.callbacks.report_free_decision.assert_awaited_once()
    report = decision_execution.callbacks.report_free_decision.await_args
    assert report is not None
    assert report.kwargs["attempt_no"] == 2
    assert report.kwargs["failed"] is (failed_commits == 2)
    assert report.kwargs["should_speak"] is (None if failed_commits == 2 else True)
    assert report.kwargs["error_code"] == ("agent_pipeline_failed" if failed_commits == 2 else None)
    await assert_decision_capacity_reusable(case)


@pytest.mark.asyncio
async def test_free_decision_cancel_closes_provider_before_isolated_cancellation_capture(
    monkeypatch: pytest.MonkeyPatch,
    decision_execution: DecisionExecution,
    caplog: pytest.LogCaptureFixture,
) -> None:
    case = decision_execution.pipeline
    stream_started = asyncio.Event()

    async def stream_chat(**_: Any) -> None:
        stream_started.set()
        await asyncio.Event().wait()

    case.client.stream_chat = stream_chat
    async with capture_storage_fault(monkeypatch, case) as (authority, writer):
        task = decision_execution.start()
        try:
            await asyncio.wait_for(stream_started.wait(), timeout=0.5)
            await asyncio.wait_for(
                case.runtime.cancel_free_decision(case.run.match_id), timeout=0.5
            )
            await asyncio.wait_for(authority.storage.started.wait(), timeout=0.5)
            case.client.close.assert_awaited_once()
            await assert_decision_capacity_reusable(case)
            assert task.done()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert [(status, error) for _key, status, error in authority.committed] == [
                ("CANCELLED", "cancelled")
            ]
            decision_execution.callbacks.report_free_decision.assert_not_awaited()
            await assert_capture_storage_failed(writer)
            assert writer.stats()["quarantined"] is True
            assert "synthetic-private-diagnostic-payload" not in caplog.text
            await authority.storage.finish_and_check_consumed()
            decision_execution.callbacks.report_free_decision.assert_not_awaited()
        finally:
            authority.storage.release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_failed", [False, True])
async def test_free_decision_finished_provider_released_before_blocked_capture(
    monkeypatch: pytest.MonkeyPatch,
    decision_execution: DecisionExecution,
    provider_failed: bool,
) -> None:
    case = decision_execution.pipeline
    if provider_failed:
        case.client.stream_chat = AsyncMock(side_effect=LlmProviderError("llm_first_token_timeout"))
    async with capture_storage_fault(monkeypatch, case) as (authority, writer):
        task = decision_execution.start()
        try:
            await asyncio.wait_for(authority.storage.started.wait(), timeout=0.5)
            await assert_decision_capacity_reusable(case)
            await asyncio.wait_for(task, timeout=0.5)
            report = decision_execution.callbacks.report_free_decision.await_args
            assert report is not None
            assert report.kwargs["failed"] is provider_failed
            expected_error = "llm_first_token_timeout" if provider_failed else None
            expected_attempts = 2 if provider_failed else 1
            assert report.kwargs["error_code"] == expected_error
            assert report.kwargs["attempt_no"] == expected_attempts
            assert report.kwargs["should_speak"] is (None if provider_failed else True)
            assert case.client.stream_chat.await_count == expected_attempts
            assert case.client.close.await_count == expected_attempts
            assert writer.stats()["accepted"] == expected_attempts
            assert [(status, error) for _key, status, error in authority.committed] == [
                ("FAILED" if provider_failed else "SUCCEEDED", expected_error)
            ] * expected_attempts
            assert [call.attempt_no for call in authority.calls.values()] == list(
                range(1, expected_attempts + 1)
            )
            if not provider_failed:
                assert (
                    report.kwargs["envelope"].opportunity_id
                    == decision_execution.envelope.opportunity_id
                )
                assert report.kwargs["envelope"].opportunity_generation == 1
                assert report.kwargs["envelope"].context_version == case.config.context_version
            await assert_capture_storage_failed(writer)
            assert writer.stats()["quarantined"] is True
            await authority.storage.finish_and_check_consumed()
            decision_execution.callbacks.report_free_decision.assert_awaited_once()
        finally:
            authority.storage.release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_free_decision_cancel_during_authority_completion_fences_success_callback(
    monkeypatch: pytest.MonkeyPatch, decision_execution: DecisionExecution
) -> None:
    case = decision_execution.pipeline
    async with capture_storage_fault(monkeypatch, case) as (authority, writer):
        authority.hold_success_commit = True
        authority.commit_release.clear()
        task = decision_execution.start()
        try:
            await asyncio.wait_for(authority.commit_started.wait(), timeout=0.5)
            assert authority.committed == []
            assert writer.stats().get("accepted", 0) == 0
            case.client.close.assert_awaited_once()
            await assert_decision_capacity_reusable(case)
            await asyncio.wait_for(
                case.runtime.cancel_free_decision(case.run.match_id), timeout=0.5
            )
            assert task.done()
            with pytest.raises(asyncio.CancelledError):
                await task
            decision_execution.callbacks.report_free_decision.assert_not_awaited()
            assert [(status, error) for _key, status, error in authority.committed] == [
                ("CANCELLED", "cancelled")
            ]
            assert writer.stats()["accepted"] == 1
            await asyncio.wait_for(authority.storage.started.wait(), timeout=0.5)
            await assert_capture_storage_failed(writer)
            await authority.storage.finish_and_check_consumed()
            decision_execution.callbacks.report_free_decision.assert_not_awaited()
        finally:
            authority.commit_release.set()
            authority.storage.release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_free_decision_blocked_start_capture_owns_no_provider_capacity(
    monkeypatch: pytest.MonkeyPatch, decision_execution: DecisionExecution
) -> None:
    case = decision_execution.pipeline
    capture = BlockingDecisionCapture()
    construct = Mock()

    def client(**kwargs: Any) -> SimpleNamespace:
        construct(**kwargs)
        return case.client

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", client)
    monkeypatch.setattr(case.runtime, "_start_decision_call", capture)
    task = decision_execution.start()
    try:
        await asyncio.wait_for(capture.started.wait(), timeout=0.5)
        construct.assert_not_called()
        await assert_decision_capacity_reusable(case)
        await asyncio.wait_for(task, timeout=0.5)
        construct.assert_called_once()
        assert construct.call_args.kwargs["connection_timeout_seconds"] == 3.0
        assert construct.call_args.kwargs["first_token_timeout_seconds"] == 3.0
        assert construct.call_args.kwargs["idle_timeout_seconds"] == 3.0
        report = decision_execution.callbacks.report_free_decision.await_args
        assert report is not None and report.kwargs["failed"] is False
        case.client.close.assert_awaited_once()
        await capture.finish_and_check_consumed()
    finally:
        capture.release.set()
        task.cancel()
        await asyncio.gather(task, *capture.tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_agent_llm_ready_failure_is_preserved_after_provider_close_failure(
    monkeypatch: pytest.MonkeyPatch, pipeline_execution: PipelineExecution
) -> None:
    case = pipeline_execution

    async def playback(*_: Any) -> None:
        await asyncio.Event().wait()

    case.client.stream_chat = AsyncMock(return_value=LlmStreamResult("合成测试。", 1, 2, 5))
    case.client.close = AsyncMock(side_effect=RuntimeError("synthetic-client-close-failed"))
    mark_ready = AsyncMock(side_effect=RuntimeError("synthetic-llm-ready-failed"))
    callbacks = AsyncMock()
    monkeypatch.setattr(case.runtime, "_callbacks", callbacks)
    monkeypatch.setattr(case.runtime, "_mark_generation_llm_ready", mark_ready)
    monkeypatch.setattr(case.runtime, "_play_pcm", playback)
    case.run.generation_id = uuid4()
    execution = case.runtime._execute(  # pyright: ignore[reportPrivateUsage]
        case.run, case.config, case.run.generation_id
    )
    with pytest.raises(RuntimeError, match="synthetic-llm-ready-failed"):
        await execution
    mark_ready.assert_awaited_once()
    case.client.close.assert_awaited_once()
    callbacks.agent_text_generated.assert_not_awaited()
    assert case.spool.closed
    await assert_decision_capacity_reusable(case)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blocked_phase",
    ["stream", "llm_ready", "tts", "tts_result_commit", "playback", "playback_callback"],
)
async def test_five_agent_speeches_limit_only_inflight_llm_streams(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_execution: PipelineExecution,
    blocked_phase: str,
) -> None:
    """Exercise real _execute orchestration with synthetic media/provider tails."""

    case = pipeline_execution
    runtime = case.runtime
    monkeypatch.setattr(runtime, "_limiter", LlmCapacityLimiter(global_limit=50))
    limiter = runtime.capacity_limiter
    original_acquire = limiter.acquire
    gate, first_four_blocked, all_five_blocked = (asyncio.Event() for _ in range(3))
    first_four_completed = asyncio.Event()
    first_four_clients_closed, all_five_clients_closed = (asyncio.Event() for _ in range(2))
    counters = {"inflight": 0, "maximum_inflight": 0, "completed": 0, "blocked": 0, "closed": 0}
    clients: list[SimpleNamespace] = []
    tasks: list[asyncio.Task[None]] = []
    runs: list[AgentRun] = []
    by_match: dict[UUID, AgentRun] = {}
    events_by_capture: dict[int, list[str]] = {}
    spools: list[io.BytesIO] = []

    async def short_queue(
        model_key: str, model_limit: int, timeout_seconds: float = 3.0
    ) -> tuple[asyncio.Semaphore, asyncio.Semaphore]:
        assert timeout_seconds == 3.0
        return await original_acquire(model_key, model_limit, timeout_seconds=0.03)

    async def block_phase() -> None:
        counters["blocked"] += 1
        if counters["blocked"] >= 4:
            first_four_blocked.set()
        if counters["blocked"] >= 5:
            all_five_blocked.set()
        await gate.wait()

    def construct(**_: Any) -> SimpleNamespace:
        events: list[str] = []

        async def close() -> None:
            events.append("client_closed")
            counters["closed"] += 1
            if counters["closed"] >= 4:
                first_four_clients_closed.set()
            if counters["closed"] >= 5:
                all_five_clients_closed.set()

        async def stream_chat(**kwargs: Any) -> LlmStreamResult:
            events_by_capture[id(kwargs["capture"])] = events
            events.append("stream_started")
            counters["inflight"] += 1
            counters["maximum_inflight"] = max(counters["maximum_inflight"], counters["inflight"])
            try:
                if blocked_phase == "stream":
                    await block_phase()
                await kwargs["on_delta"]("合成测试。")
                events.append("stream_completed")
                counters["completed"] += 1
                return LlmStreamResult("合成测试。", 1, 2, 5)
            finally:
                counters["inflight"] -= 1
                if counters["completed"] >= 4:
                    first_four_completed.set()

        client = SimpleNamespace(
            close=AsyncMock(side_effect=close), stream_chat=AsyncMock(side_effect=stream_chat)
        )
        clients.append(client)
        return client

    def decoder() -> SimpleNamespace:
        return SimpleNamespace(start=AsyncMock(), finish_input=Mock(), close=AsyncMock())

    async def synthesize(chunks: Any, **_: Any) -> TtsStreamResult:
        async for _chunk in chunks:
            pass
        # Provider completion follows the queue producer's sentinel commit.
        await asyncio.sleep(0.01)
        if blocked_phase == "tts":
            await block_phase()
        return TtsStreamResult(uuid4(), 1, 1, 2)

    async def mark_ready(_generation_id: UUID, _result: object, capture: object, **_: Any) -> None:
        events = events_by_capture[id(capture)]
        assert events == ["stream_started", "text_delta", "stream_completed", "client_closed"]
        events.append("llm_ready")
        if blocked_phase == "llm_ready":
            await block_phase()

    def callback_events(envelope: CallbackEnvelope) -> list[str]:
        run = by_match[envelope.match_id]
        assert envelope == run.callback_envelope
        assert envelope.context_version == case.config.context_version
        assert envelope.generation_id == run.generation_id
        assert envelope.opportunity_generation == 1
        return events_by_capture[id(run.llm_capture)]

    async def publish_delta(*, envelope: CallbackEnvelope, text: str) -> None:
        assert text == "合成测试。"
        callback_events(envelope).append("text_delta")

    async def text_generated(*, envelope: CallbackEnvelope, text: str) -> None:
        assert text == "合成测试。"
        events = callback_events(envelope)
        assert events[-1] == "llm_ready"
        events.append("text_generated")

    async def finish_playback(*, envelope: CallbackEnvelope) -> None:
        events = callback_events(envelope)
        assert events[-1] == "text_generated"
        events.append("playback_finished_callback")
        if blocked_phase == "playback_callback":
            await block_phase()

    async def playback(run: AgentRun, _generation_id: UUID) -> None:
        run.speech_id = uuid4()
        assert run.callback_envelope is not None
        run.callback_envelope = replace(run.callback_envelope, speech_id=run.speech_id)
        if blocked_phase == "playback":
            await block_phase()

    async def finish_capture(*_: Any, **__: Any) -> None:
        if blocked_phase == "tts_result_commit":
            await block_phase()

    callbacks = AsyncMock(
        publish_agent_text_delta=AsyncMock(side_effect=publish_delta),
        agent_text_generated=AsyncMock(side_effect=text_generated),
        finish_agent_playback=AsyncMock(side_effect=finish_playback),
    )
    original_open = Path.open

    def open_spool(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if mode == "ab":
            spool = io.BytesIO()
            spools.append(spool)
            return spool
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_spool)
    monkeypatch.setattr(limiter, "acquire", short_queue)
    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", construct)
    monkeypatch.setattr(runtime_module, "IncrementalOpusDecoder", decoder)
    monkeypatch.setattr(runtime, "_callbacks", callbacks)
    monkeypatch.setattr(runtime, "_start_tts_call", AsyncMock(return_value=uuid4()))
    monkeypatch.setattr(runtime, "_mark_generation_llm_ready", mark_ready)
    monkeypatch.setattr(runtime, "_mark_generation_failed", AsyncMock())
    monkeypatch.setattr(runtime, "_finish_external_call", finish_capture)
    monkeypatch.setattr(runtime, "_play_pcm", playback)
    monkeypatch.setattr(
        runtime,
        "_connect_publisher",
        AsyncMock(
            return_value=(
                SimpleNamespace(disconnect=AsyncMock()),
                SimpleNamespace(
                    wait_for_playout=AsyncMock(), clear_queue=Mock(), aclose=AsyncMock()
                ),
            )
        ),
    )

    def launch(run: AgentRun | None = None) -> asyncio.Task[None]:
        if run is None:
            run = AgentRun(
                match_id=uuid4(), action_key="1:1", agent_profile_id=uuid4(), duration_ms=1000
            )
            runs.append(run)
            by_match[run.match_id] = run
        generation_id = uuid4()
        run.generation_id = generation_id
        run.callback_envelope = CallbackEnvelope(
            match_id=run.match_id,
            speech_id=None,
            attempt_no=1,
            generation_id=generation_id,
            connection_epoch=None,
            context_version=case.config.context_version,
            opportunity_id=uuid4(),
            opportunity_generation=1,
        )
        config = replace(
            case.config, match_id=run.match_id, agent_profile_id=run.agent_profile_id, model_limit=4
        )
        runtime._runs[run.match_id] = run  # pyright: ignore[reportPrivateUsage]
        runtime._tts_connections[run.match_id] = cast(  # pyright: ignore[reportPrivateUsage]
            Any, SimpleNamespace(synthesize=synthesize, close=AsyncMock())
        )
        execution = runtime._execute(run, config, generation_id)  # pyright: ignore[reportPrivateUsage]
        task = asyncio.create_task(execution)
        run.task = task
        tasks.append(task)
        return task

    try:
        for _ in range(4):
            launch()
        await asyncio.wait_for(first_four_blocked.wait(), timeout=0.5)
        if blocked_phase == "stream":
            assert counters["inflight"] == 4 and counters["completed"] == 0
            assert all(client.close.await_count == 0 for client in clients)
            fifth = launch()
            with pytest.raises(LlmProviderError, match="llm_capacity_full"):
                await asyncio.wait_for(fifth, timeout=0.5)
            clients[-1].stream_chat.assert_not_awaited()
            clients[-1].close.assert_awaited_once()
            assert counters["maximum_inflight"] == 4
            gate.set()
            await asyncio.wait_for(asyncio.gather(*tasks[:4]), timeout=0.5)
            await asyncio.wait_for(launch(runs[-1]), timeout=0.5)
            assert counters["completed"] == 5
        else:
            await asyncio.wait_for(first_four_completed.wait(), timeout=0.5)
            await asyncio.wait_for(first_four_clients_closed.wait(), timeout=0.5)
            assert counters["inflight"] == 0 and counters["completed"] == 4
            assert all(not task.done() for task in tasks)
            assert all(client.close.await_count == 1 for client in clients)
            launch()
            await asyncio.wait_for(all_five_blocked.wait(), timeout=0.5)
            await asyncio.wait_for(all_five_clients_closed.wait(), timeout=0.5)
            assert counters["inflight"] == 0 and counters["completed"] == 5
            assert all(client.close.await_count == 1 for client in clients)
            if blocked_phase == "tts":
                first_events = events_by_capture[id(runs[0].llm_capture)]
                await asyncio.wait_for(runtime.reset_agent(runs[0].match_id), timeout=0.5)
                assert tasks[0].cancelled() and runs[0].media_fenced
                assert "playback_finished_callback" not in first_events
            gate.set()
            completed = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=0.5
            )
            assert all(
                result is None or (index == 0 and isinstance(result, asyncio.CancelledError))
                for index, result in enumerate(completed)
            )
        assert all(client.close.await_count == 1 for client in clients)
        assert all(spool.closed for spool in spools)
        assert callbacks.finish_agent_playback.await_count == (4 if blocked_phase == "tts" else 5)
        probe_leases: list[tuple[asyncio.Semaphore, asyncio.Semaphore]] = []
        extra_leases: tuple[asyncio.Semaphore, asyncio.Semaphore] | None = None
        try:
            for _ in range(4):
                probe_leases.append(
                    await original_acquire(case.config.model_key, 4, timeout_seconds=0.1)
                )
            with pytest.raises(LlmProviderError, match="llm_capacity_full"):
                extra_leases = await original_acquire(
                    case.config.model_key, 4, timeout_seconds=0.01
                )
        finally:
            if extra_leases is not None:
                limiter.release(extra_leases)
            for lease in probe_leases:
                limiter.release(lease)
    finally:
        gate.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for run in runs:
            if run.tts_task is not None:
                run.tts_task.cancel()
                await asyncio.gather(run.tts_task, return_exceptions=True)

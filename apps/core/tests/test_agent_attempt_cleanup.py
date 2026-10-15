from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

import jx_core.agent.runtime as runtime_module
from jx_core.agent.llm import LlmProviderError
from jx_core.agent.runtime import AgentRun, AgentRuntime
from jx_core.agent.tts import TtsStreamResult
from jx_core.config import Settings


@dataclass
class AttemptCleanupCase:
    runtime: AgentRuntime
    run: AgentRun
    spool_path: Path
    release: asyncio.Event = field(default_factory=asyncio.Event)
    tts_started: asyncio.Event = field(default_factory=asyncio.Event)
    tts_cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    close_started: asyncio.Event = field(default_factory=asyncio.Event)
    events: list[str] = field(default_factory=lambda: list[str]())
    blocking: set[str] = field(default_factory=lambda: set[str]())
    failures: set[str] = field(default_factory=lambda: set[str]())
    late_failures: set[str] = field(default_factory=lambda: set[str]())
    tasks: list[asyncio.Task[Any]] = field(default_factory=lambda: list[asyncio.Task[Any]]())

    def sync_step(self, name: str) -> None:
        self.events.append(name)
        if name in self.failures:
            raise RuntimeError("synthetic-private-resource-error")

    async def close_resource(self, name: str) -> None:
        self.events.append(name)
        self.close_started.set()
        task = asyncio.current_task()
        assert task is not None
        self.tasks.append(task)
        if name in self.blocking:
            # Synthetic native/FFI boundary: cancellation is cooperative, and
            # this close completes only after the test explicitly releases it.
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    continue
        if name in self.failures or name in self.late_failures:
            raise RuntimeError("synthetic-private-resource-error")

    async def resistant_tts(self) -> TtsStreamResult:
        self.tts_started.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.tts_cancelled.set()
        if "tts" in self.late_failures:
            raise RuntimeError("synthetic-private-late-tts-error")
        return TtsStreamResult(uuid4(), 1, 1, 2)

    async def start_tts(self) -> asyncio.Task[TtsStreamResult]:
        task = asyncio.create_task(self.resistant_tts())
        self.run.tts_task = task
        self.tasks.append(task)
        await self.tts_started.wait()
        return task

    def start_reset(self) -> asyncio.Task[None]:
        task = asyncio.create_task(self.runtime.reset_agent(self.run.match_id))
        self.tasks.append(task)
        return task

    async def drain(self) -> None:
        # Never use wait_for against these cancellation-resistant fakes. The
        # finally block releases them before consuming their final outcomes.
        self.release.set()
        if self.tasks:
            _, pending = await asyncio.wait(set(self.tasks), timeout=0.5)
            assert not pending, "synthetic cleanup fixture failed to release its tasks"
            await asyncio.gather(*self.tasks, return_exceptions=True)

    def assert_all_resources_started(self) -> None:
        for name in ("decoder_close", "source_close", "room_close"):
            assert self.events.count(name) == 1, self.events

    def assert_refs_detached(self) -> None:
        assert self.run.tts_task is None
        assert self.run.decoder is None
        assert self.run.source is None
        assert self.run.room is None
        assert self.run.text_queue is None
        assert self.run.spool_path is None


@pytest.fixture
def attempt_cleanup_case(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AttemptCleanupCase:
    runtime = AgentRuntime(
        settings=cast(Any, Settings)(
            _env_file=None,
            database_url=SecretStr("postgresql+psycopg://test:test@localhost/test"),
            agent_audio_storage_dir=str(tmp_path),
        ),
        session_factory=cast(Any, None),
        callbacks=AsyncMock(),
    )
    run = AgentRun(match_id=uuid4(), action_key="1:1", agent_profile_id=uuid4(), duration_ms=1000)
    case = AttemptCleanupCase(runtime, run, tmp_path / "synthetic-attempt.opus")
    run.decoder = cast(
        Any,
        SimpleNamespace(
            finish_input=lambda: case.sync_step("finish_input"),
            close=lambda: case.close_resource("decoder_close"),
        ),
    )
    run.source = cast(
        Any,
        SimpleNamespace(
            clear_queue=lambda: case.sync_step("clear_queue"),
            aclose=lambda: case.close_resource("source_close"),
        ),
    )
    run.room = cast(Any, SimpleNamespace(disconnect=lambda: case.close_resource("room_close")))
    run.text_queue = asyncio.Queue()
    run.spool_path = case.spool_path
    run.spool_path.write_bytes(b"synthetic")
    runtime._runs[run.match_id] = run  # pyright: ignore[reportPrivateUsage]
    # Scale existing budgets to make failure injection deterministic and fast;
    # no production timeout or provider configuration is changed.
    monkeypatch.setattr(runtime_module, "AGENT_TASK_CANCEL_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(runtime_module, "AGENT_RESOURCE_CLEANUP_TIMEOUT_SECONDS", 0.01)
    return case


@pytest.mark.asyncio
async def test_public_reset_bounds_resistant_tts_and_starts_all_resource_cleanup(
    attempt_cleanup_case: AttemptCleanupCase,
) -> None:
    case = attempt_cleanup_case
    await case.start_tts()
    reset = case.start_reset()
    try:
        done, _ = await asyncio.wait({reset}, timeout=0.1)
        assert reset in done, f"reset still pending after budget; cleanup events={case.events}"
        assert reset.exception() is None
        assert case.run.media_fenced
        assert case.run.match_id not in case.runtime._runs  # pyright: ignore[reportPrivateUsage]
        case.assert_refs_detached()
        case.assert_all_resources_started()
        assert not case.spool_path.exists()
    finally:
        await case.drain()


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["decoder_close", "source_close", "room_close"])
async def test_public_reset_starts_independent_cleanup_before_one_native_wait_exhausts_budget(
    monkeypatch: pytest.MonkeyPatch,
    attempt_cleanup_case: AttemptCleanupCase,
    resource: str,
) -> None:
    case = attempt_cleanup_case
    case.blocking.add(resource)
    monkeypatch.setattr(runtime_module, "AGENT_RESOURCE_CLEANUP_TIMEOUT_SECONDS", 0.08)
    reset = case.start_reset()
    try:
        done, _ = await asyncio.wait({reset}, timeout=0.1)
        assert reset in done
        assert reset.exception() is None
        case.assert_all_resources_started()
        case.assert_refs_detached()
    finally:
        await case.drain()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_step", ["finish_input", "clear_queue", "decoder_close", "source_close", "room_close"]
)
async def test_attempt_cleanup_error_preserves_original_provider_failure_and_cleans_other_resources(
    attempt_cleanup_case: AttemptCleanupCase, failed_step: str, caplog: pytest.LogCaptureFixture
) -> None:
    case = attempt_cleanup_case
    case.failures.add(failed_step)
    original = LlmProviderError("llm_provider_failed")

    async def failed_attempt() -> None:
        try:
            raise original
        finally:
            await case.runtime._cleanup_attempt(case.run)  # pyright: ignore[reportPrivateUsage]

    try:
        with pytest.raises(LlmProviderError) as captured:
            await failed_attempt()
        assert captured.value is original
        case.assert_all_resources_started()
        case.assert_refs_detached()
        assert "synthetic-private-resource-error" not in caplog.text
    finally:
        await case.drain()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resource", "reference", "method"),
    [
        ("decoder_close", "decoder", "close"),
        ("source_close", "source", "aclose"),
        ("room_close", "room", "disconnect"),
    ],
)
async def test_synchronous_close_factory_failure_does_not_skip_other_retired_resources(
    attempt_cleanup_case: AttemptCleanupCase,
    resource: str,
    reference: str,
    method: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    case = attempt_cleanup_case

    def fail_before_awaitable() -> Any:
        case.events.append(resource)
        raise RuntimeError("synthetic-private-close-factory-error")

    setattr(getattr(case.run, reference), method, fail_before_awaitable)
    reset = case.start_reset()
    try:
        await reset
        case.assert_all_resources_started()
        case.assert_refs_detached()
        assert "synthetic-private-close-factory-error" not in caplog.text
        assert any(
            getattr(record, "error_code", None) == "agent_resource_cleanup_failed"
            for record in caplog.records
        )
    finally:
        await case.drain()


@pytest.mark.asyncio
async def test_retired_spool_unlink_failure_does_not_skip_media_cleanup_or_mask_provider_error(
    monkeypatch: pytest.MonkeyPatch,
    attempt_cleanup_case: AttemptCleanupCase,
    caplog: pytest.LogCaptureFixture,
) -> None:
    case = attempt_cleanup_case
    unlink = Path.unlink

    def unlink_with_failure(path: Path, missing_ok: bool = False) -> None:
        if path == case.spool_path:
            raise OSError("synthetic-private-spool-error")
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink_with_failure)
    original = LlmProviderError("llm_provider_failed")

    async def failed_attempt() -> None:
        try:
            raise original
        finally:
            await case.runtime._cleanup_attempt(case.run)  # pyright: ignore[reportPrivateUsage]

    try:
        with pytest.raises(LlmProviderError) as captured:
            await failed_attempt()
        assert captured.value is original
        case.assert_all_resources_started()
        case.assert_refs_detached()
        assert case.spool_path.exists()
        assert "synthetic-private-spool-error" not in caplog.text
    finally:
        await case.drain()


@pytest.mark.asyncio
async def test_public_reset_clear_queue_error_does_not_skip_resource_cleanup(
    attempt_cleanup_case: AttemptCleanupCase,
) -> None:
    case = attempt_cleanup_case
    case.failures.add("clear_queue")
    reset = case.start_reset()
    try:
        done, _ = await asyncio.wait({reset}, timeout=0.1)
        assert reset in done
        assert reset.exception() is None
        assert case.run.media_fenced
        case.assert_all_resources_started()
        case.assert_refs_detached()
    finally:
        await case.drain()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_step", ["tts", "decoder_close"])
async def test_public_reset_propagates_external_cancel_after_starting_retired_resource_cleanup(
    attempt_cleanup_case: AttemptCleanupCase, blocked_step: str
) -> None:
    case = attempt_cleanup_case
    if blocked_step == "tts":
        await case.start_tts()
    else:
        case.blocking.add(blocked_step)
    reset = case.start_reset()
    try:
        entered = case.tts_cancelled if blocked_step == "tts" else case.close_started
        await asyncio.wait_for(entered.wait(), timeout=0.1)
        reset.cancel()
        done, _ = await asyncio.wait({reset}, timeout=0.1)
        assert reset in done, f"external cancellation stuck; cleanup events={case.events}"
        assert reset.cancelled()
        case.assert_all_resources_started()
        case.assert_refs_detached()
    finally:
        await case.drain()


@pytest.mark.asyncio
@pytest.mark.parametrize("late_step", ["tts", "decoder_close"])
async def test_public_reset_consumes_cancel_resistant_task_late_error(
    attempt_cleanup_case: AttemptCleanupCase, late_step: str
) -> None:
    case = attempt_cleanup_case
    case.late_failures.add(late_step)
    if late_step == "tts":
        late_task = await case.start_tts()
    else:
        case.blocking.add(late_step)
        late_task = None
    reset = case.start_reset()
    try:
        done, _ = await asyncio.wait({reset}, timeout=0.1)
        assert reset in done
        assert reset.exception() is None
        if late_task is None:
            late_task = next(task for task in case.tasks if task is not reset)
        case.release.set()
        completed, _ = await asyncio.wait({late_task}, timeout=0.1)
        assert late_task in completed
        # asyncio.wait doesn't consume the error. Let the production late-result
        # callback run, then inspect the same flag used by asyncio's warning.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not late_task._log_traceback  # pyright: ignore[reportPrivateUsage]
    finally:
        await case.drain()


@pytest.mark.asyncio
async def test_public_reset_stops_media_input_before_starting_native_close(
    attempt_cleanup_case: AttemptCleanupCase,
) -> None:
    case = attempt_cleanup_case
    reset = case.start_reset()
    try:
        await reset
        case.assert_all_resources_started()
        first_close = min(case.events.index(name) for name in ("decoder_close", "source_close"))
        assert case.events.index("finish_input") < first_close
        assert case.events.index("clear_queue") < first_close
        case.assert_refs_detached()
    finally:
        await case.drain()


@pytest.mark.asyncio
async def test_late_retired_attempt_cleanup_does_not_touch_replacement_references(
    attempt_cleanup_case: AttemptCleanupCase, tmp_path: Path
) -> None:
    case = attempt_cleanup_case
    case.blocking.add("decoder_close")
    await case.start_tts()
    cleanup = asyncio.create_task(
        case.runtime._cleanup_attempt(case.run)  # pyright: ignore[reportPrivateUsage]
    )
    case.tasks.append(cleanup)
    try:
        done, _ = await asyncio.wait({cleanup}, timeout=0.1)
        assert cleanup in done
        assert cleanup.exception() is None
        case.assert_refs_detached()
        replacement_decoder = object()
        replacement_source = object()
        replacement_room = object()
        replacement_queue: asyncio.Queue[str | None] = asyncio.Queue()
        replacement_path = tmp_path / "synthetic-next-attempt.opus"
        replacement_path.write_bytes(b"next-attempt")
        case.run.decoder = cast(Any, replacement_decoder)
        case.run.source = cast(Any, replacement_source)
        case.run.room = cast(Any, replacement_room)
        case.run.text_queue = replacement_queue
        case.run.spool_path = replacement_path
        await case.drain()
        assert case.run.decoder is replacement_decoder
        assert case.run.source is replacement_source
        assert case.run.room is replacement_room
        assert case.run.text_queue is replacement_queue
        assert case.run.spool_path is replacement_path
        assert replacement_path.read_bytes() == b"next-attempt"
    finally:
        await case.drain()

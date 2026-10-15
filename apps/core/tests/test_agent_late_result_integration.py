"""Retired LLM results at the actual PostgreSQL transaction boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import select, update

import jx_core.agent.runtime as runtime_module
from jx_core.agent.llm import LlmStreamResult
from jx_core.matches.domain import MatchCommand
from jx_core.models import (
    AgentGeneration,
    ExternalCall,
    Match,
    MatchEvent,
    MatchParticipant,
    Speech,
)
from test_agent_runtime_fault_integration import AgentFaultProbe, create_generation_call
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe

pytestmark = pytest.mark.integration
# These probes deliberately use the existing private transaction hooks.
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "FINALIZED", "error", "missing"])
async def test_actual_llm_ready_rejects_retired_generation_without_business_writes(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    probe = agent_fault_probe
    generation_id, call_id = await create_generation_call(probe)
    if status == "missing":
        generation_id = uuid4()
    else:
        async with probe.factory() as session:
            async with session.begin():
                await session.execute(
                    update(AgentGeneration)
                    .where(AgentGeneration.id == generation_id)
                    .values(
                        status="STARTED" if status == "error" else status,
                        error_code="agent_cancelled" if status != "FINALIZED" else None,
                    )
                )
    blob, capture = AsyncMock(), Mock()
    monkeypatch.setattr(runtime_module, "store_content_blob", blob)
    monkeypatch.setattr(runtime_module, "submit_capture", capture)
    with pytest.raises(asyncio.CancelledError):
        await probe.runtime._mark_generation_llm_ready(
            generation_id, LlmStreamResult("迟到成功。", 1, 2, 3), None
        )
    blob.assert_not_awaited()
    capture.assert_not_called()
    async with probe.factory() as session:
        generation = await session.get(AgentGeneration, generation_id)
        call = await session.get(ExternalCall, call_id)
        if status == "missing":
            assert generation is None
        else:
            assert generation is not None
            assert generation.status == ("STARTED" if status == "error" else status)
            assert generation.error_code == (None if status == "FINALIZED" else "agent_cancelled")
            assert generation.response_blob_id is None
            assert generation.first_token_latency_ms is None
            assert generation.completion_tokens is None
        assert call is not None and call.status == "STARTED" and call.completed_at is None
    assert probe.actor.state.status == "RUNNING" and probe.actor.state.sequence == 0


@pytest.mark.asyncio
async def test_llm_ready_guard_rechecks_status_after_waiting_for_generation_row_lock(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = agent_fault_probe
    generation_id, call_id = await create_generation_call(probe)
    blob = AsyncMock()
    monkeypatch.setattr(runtime_module, "store_content_blob", blob)
    task: asyncio.Task[None] | None = None
    async with probe.factory() as retiring_session:
        async with retiring_session.begin():
            generation = await retiring_session.get(
                AgentGeneration, generation_id, with_for_update=True
            )
            assert generation is not None
            task = asyncio.create_task(
                probe.runtime._mark_generation_llm_ready(
                    generation_id, LlmStreamResult("迟到成功。", 1, 2, 3), None
                )
            )
            # The real second session must wait for the authority row lock.
            done, _ = await asyncio.wait({task}, timeout=0.05)
            assert not done
            generation.status = "FAILED"
            generation.error_code = "agent_cancelled"
    assert task is not None
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 0.5)
    blob.assert_not_awaited()
    async with probe.factory() as session:
        generation = await session.get(AgentGeneration, generation_id)
        call = await session.get(ExternalCall, call_id)
        assert generation is not None and generation.status == "FAILED"
        assert generation.error_code == "agent_cancelled" and generation.response_blob_id is None
        assert call is not None and call.status == "STARTED"


@pytest.mark.asyncio
async def test_public_reset_late_success_preserves_actual_failed_generation(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Actual runtime/manager/PG, with a synthetic cancellation-resistant provider."""

    probe = agent_fault_probe
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def stream_chat(**arguments: Any) -> LlmStreamResult:
        await arguments["on_delta"]("合成取消测试。")
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
        return LlmStreamResult("合成取消测试。", 1, 2, 3)

    client = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock())

    def provider_client(**_: Any) -> SimpleNamespace:
        probe.provider_calls += 1
        return client

    async def synthesize(chunks: Any, **_: Any) -> None:
        async for _chunk in chunks:
            pass
        await asyncio.Event().wait()

    text_callback = AsyncMock(wraps=probe.manager.agent_text_generated)
    monkeypatch.setattr(probe.manager, "agent_text_generated", text_callback)
    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", provider_client)
    monkeypatch.setattr(runtime_module, "AGENT_TASK_CANCEL_TIMEOUT_SECONDS", 0.03)
    probe.runtime._tts_connections[probe.actor.state.match_id] = cast(
        Any, SimpleNamespace(synthesize=synthesize, close=AsyncMock())
    )
    await probe.runtime.start_agent(
        match_id=probe.actor.state.match_id,
        action_key="1:1",
        agent_profile_id=probe.agent_id,
        duration_ms=30_000,
        side="AFFIRMATIVE",
    )
    run = probe.run = probe.runtime._runs[probe.actor.state.match_id]
    task = run.task
    assert task is not None
    try:
        await asyncio.wait_for(entered.wait(), 0.8)
        await asyncio.wait_for(probe.runtime.reset_agent(run.match_id), 0.8)
        assert cancelled.is_set() and not task.done()
        async with probe.factory() as session:
            generation = await session.get(AgentGeneration, run.generation_id)
            assert generation is not None and generation.status == "FAILED"
            assert generation.error_code == "agent_cancelled"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.5)
        text_callback.assert_not_awaited()
        client.close.assert_awaited_once()
        async with probe.factory() as session:
            generation = await session.get(AgentGeneration, run.generation_id)
            assert generation is not None and generation.status == "FAILED"
            assert generation.error_code == "agent_cancelled"
            assert generation.response_blob_id is None and generation.completion_tokens is None
            calls = list(
                (
                    await session.scalars(
                        select(ExternalCall).where(
                            ExternalCall.agent_generation_id == run.generation_id
                        )
                    )
                ).all()
            )
            assert {call.call_kind for call in calls} == {"LLM_SPEECH", "TTS"}
            assert all(
                call.status == "FAILED" and call.error_code == "agent_cancelled" for call in calls
            )
            assert (
                await session.scalar(select(Speech.id).where(Speech.match_id == run.match_id))
                is None
            )
            assert (
                await session.scalar(
                    select(MatchEvent.id).where(MatchEvent.match_id == run.match_id)
                )
                is None
            )
        assert probe.actor.state.sequence == 0
        assert probe.actor.state.action_state == "AGENT_PREPARING"
        assert probe.provider_calls == 1
        assert run.match_id not in probe.runtime._runs
        lease = await probe.runtime.capacity_limiter.acquire(
            str(probe.model_id), 1, timeout_seconds=0.1
        )
        probe.runtime.capacity_limiter.release(lease)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "FINALIZED"])
async def test_actual_cancel_cleanup_preserves_terminal_failure_and_first_timestamp(
    agent_fault_probe: AgentFaultProbe,
    status: str,
) -> None:
    probe = agent_fault_probe
    generation_id, call_id = await create_generation_call(probe)
    completed_at = datetime(2026, 10, 13, 1, 2, 3, tzinfo=UTC)
    original_error = (
        "llm_stream_interrupted"
        if status == "FAILED"
        else ("agent_cancelled" if status == "CANCELLED" else None)
    )
    async with probe.factory() as session:
        async with session.begin():
            await session.execute(
                update(AgentGeneration)
                .where(AgentGeneration.id == generation_id)
                .values(status=status, error_code=original_error, completed_at=completed_at)
            )
    await probe.runtime._mark_generation_failed(generation_id, "agent_cancelled")
    async with probe.factory() as session:
        generation = await session.get(AgentGeneration, generation_id)
        call = await session.get(ExternalCall, call_id)
        assert generation is not None
        assert generation.status == status and generation.error_code == original_error
        assert generation.completed_at == completed_at
        assert call is not None
        if status == "FAILED":
            assert call.status == "FAILED" and call.error_code == original_error
            assert call.completed_at is not None
        else:
            assert call.status == "STARTED" and call.error_code is None
            assert call.completed_at is None
    assert probe.actor.state.status == "RUNNING" and probe.actor.state.sequence == 0


@pytest.mark.asyncio
async def test_actual_actor_pause_dispatch_retains_fenced_run_until_authority_reset(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = agent_fault_probe
    provider_entered, provider_release = asyncio.Event(), asyncio.Event()
    fence_host_entered, fence_host_release = asyncio.Event(), asyncio.Event()

    async def stream(**arguments: Any) -> LlmStreamResult:
        await arguments["on_delta"]("合成暂停窗口。")
        provider_entered.set()
        await provider_release.wait()
        return LlmStreamResult("合成暂停窗口。", 1, 2, 3)

    client = SimpleNamespace(stream_chat=stream, close=AsyncMock())

    def provider(**_: Any) -> SimpleNamespace:
        probe.provider_calls += 1
        return client

    async def synthesize(chunks: Any, **_: Any) -> None:
        async for _chunk in chunks:
            pass
        await asyncio.Event().wait()

    async def fence_host(_: Any) -> None:
        fence_host_entered.set()
        await fence_host_release.wait()

    text_callback = AsyncMock(wraps=probe.manager.agent_text_generated)
    monkeypatch.setattr(probe.manager, "agent_text_generated", text_callback)
    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", provider)
    monkeypatch.setattr(probe.runtime, "fence_host", fence_host)
    probe.runtime._tts_connections[probe.actor.state.match_id] = cast(
        Any, SimpleNamespace(synthesize=synthesize, close=AsyncMock())
    )
    await probe.runtime.start_agent(
        match_id=probe.actor.state.match_id,
        action_key="1:1",
        agent_profile_id=probe.agent_id,
        duration_ms=30_000,
        side="AFFIRMATIVE",
    )
    run = probe.run = probe.runtime._runs[probe.actor.state.match_id]
    assert run.task is not None
    pause: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(provider_entered.wait(), 0.8)
        async with probe.factory() as session:
            pause_user_id = await session.scalar(
                select(MatchParticipant.user_id)
                .where(
                    MatchParticipant.match_id == run.match_id,
                    MatchParticipant.user_id.is_not(None),
                )
                .limit(1)
            )
        assert pause_user_id is not None
        pause = asyncio.create_task(
            probe.actor.submit(
                MatchCommand(
                    message_id=str(uuid4()),
                    type="match.pause",
                    actor_user_id=pause_user_id,
                    payload={"authorized": True},
                )
            )
        )
        try:
            await asyncio.wait_for(fence_host_entered.wait(), 0.8)
        except TimeoutError:
            if pause.done():
                await pause
            raise
        assert run.media_fenced and not pause.done()
        # The actual Actor commits before _publish applies its output fence;
        # hold only the later dispatch, without moving any authority boundary.
        async with probe.factory() as session:
            match = await session.get(Match, run.match_id)
            generation = await session.get(AgentGeneration, run.generation_id)
            assert match is not None and match.status == "PAUSED"
            assert generation is not None and generation.status == "STARTED"
        provider_release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run.task, 0.5)
        assert probe.runtime._runs.get(run.match_id) is run
        assert run.spool_path is not None and run.spool_path.exists()
        text_callback.assert_not_awaited()
        fence_host_release.set()
        await asyncio.wait_for(pause, 0.8)
        async with asyncio.timeout(0.8):
            while True:
                async with probe.factory() as session:
                    generation = await session.get(AgentGeneration, run.generation_id)
                    if generation is not None and generation.status == "FAILED":
                        assert generation.error_code == "agent_cancelled"
                        break
                await asyncio.sleep(0.005)
        assert run.match_id not in probe.runtime._runs
        assert run.spool_path is None and run.decoder is None and run.source is None
        assert run.room is None and run.tts_task is None
        async with probe.factory() as session:
            match = await session.get(Match, run.match_id)
            calls = list(
                (
                    await session.scalars(
                        select(ExternalCall).where(
                            ExternalCall.agent_generation_id == run.generation_id
                        )
                    )
                ).all()
            )
            assert match is not None and match.status == "PAUSED"
            assert probe.actor.state.status == "PAUSED"
            assert all(
                call.status == "FAILED" and call.error_code == "agent_cancelled" for call in calls
            )
            assert len(calls) == 2
        assert probe.provider_calls == 1
        client.close.assert_awaited_once()
    finally:
        provider_release.set()
        fence_host_release.set()
        if pause is not None:
            await asyncio.gather(pause, return_exceptions=True)

"""Probe the actual LLM-success transaction using isolated PostgreSQL."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import httpx
import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

import jx_core.agent.runtime as runtime_module
import jx_core.data_capture.provider_writer as writer_module
from jx_core.agent.llm import LlmStreamResult, OpenAIStreamingClient
from jx_core.agent.tts import TtsStreamResult
from jx_core.data_capture.provider import ProviderCallCapture
from jx_core.data_capture.provider_writer import CaptureSubmission
from jx_core.models import AgentGeneration, ExternalCall, Match, MatchEvent
from jx_core.models import ProviderCallCapture as CaptureRow
from test_agent_runtime_fault_integration import (
    AgentFaultProbe,
    create_generation_call,
    hold_writer_capture,
)
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import (
    agent_fault_probe as agent_fault_probe,
)

pytestmark = pytest.mark.integration
# This probe deliberately runs production private transaction hooks.
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_fault", ["normal", "storage_raise", "boundary_raise", "hang"])
async def test_successful_llm_is_not_retried_or_blocked_by_pure_capture(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capture_fault: Literal["normal", "storage_raise", "boundary_raise", "hang"],
) -> None:
    probe = agent_fault_probe
    capture_entered = asyncio.Event()
    storage_failures = 0

    async def successful_provider(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "synthetic.invalid"
        probe.provider_calls += 1
        event = {
            "choices": [{"delta": {"content": "合成成功响应。"}}],
            "usage": {"completion_tokens": 3},
        }
        return httpx.Response(200, content=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")

    def provider_client(**arguments: Any) -> OpenAIStreamingClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(successful_provider))
        probe.http_clients.append(client)
        return OpenAIStreamingClient(**arguments, client=client)

    async def synthesize(chunks: Any, **_: Any) -> TtsStreamResult:
        async for _chunk in chunks:
            pass
        # No synthetic audio is emitted: hold the provider so this test is
        # focused on LLM readiness before any playback/finalization boundary.
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    original_blob = writer_module._store_encoded_blob

    async def persist_blob(
        session: AsyncSession, kind: str, raw: bytes, prepared: tuple[bytes, str]
    ) -> UUID:
        nonlocal storage_failures
        if kind == "RESPONSE":
            storage_failures += 1
            raise RuntimeError("synthetic_raw_capture_storage_failed")
        return await original_blob(session, kind, raw, prepared)

    original_item = probe.writer._persist_item

    async def capture(
        session: AsyncSession,
        item: CaptureSubmission,
        prepared: tuple[tuple[bytes, str], tuple[bytes, str]],
    ) -> None:
        if item.identity.associations[0] == "LLM_SPEECH":
            capture_entered.set()
            if capture_fault == "boundary_raise":
                raise RuntimeError("synthetic_capture_boundary_failed")
            if capture_fault == "hang":
                await asyncio.Event().wait()
        await original_item(session, item, prepared)

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", provider_client)
    monkeypatch.setattr(probe.writer, "_persist_item", capture)
    if capture_fault == "storage_raise":
        monkeypatch.setattr(writer_module, "_store_encoded_blob", persist_blob)
    monkeypatch.setattr(runtime_module, "AGENT_RESOURCE_CLEANUP_TIMEOUT_SECONDS", 0.1)
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
    probe.run = probe.runtime._runs[probe.actor.state.match_id]
    task = probe.run.task
    assert task is not None
    await asyncio.wait_for(capture_entered.wait(), 0.5)

    async def wait_for_readiness_or_task_exit() -> None:
        while not task.done():
            async with probe.factory() as session:
                status = await session.scalar(
                    select(AgentGeneration.status)
                    .where(AgentGeneration.match_id == probe.actor.state.match_id)
                    .order_by(AgentGeneration.attempt_no.desc())
                    .limit(1)
                )
            if status == "PLAYING":
                return
            await asyncio.sleep(0.01)

    waiter = asyncio.create_task(wait_for_readiness_or_task_exit())
    done, _ = await asyncio.wait({waiter}, timeout=0.6)
    if not done:
        waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    # Greater than the scaled pure-diagnostic cleanup budget, so the
    # committed authority must still converge when capture itself hangs.
    await asyncio.sleep(0.2)
    async with probe.factory() as session:
        generations = list(
            (
                await session.scalars(
                    select(AgentGeneration)
                    .where(AgentGeneration.match_id == probe.actor.state.match_id)
                    .order_by(AgentGeneration.attempt_no)
                )
            ).all()
        )
        calls = list(
            (
                await session.scalars(
                    select(ExternalCall)
                    .where(
                        ExternalCall.match_id == probe.actor.state.match_id,
                        ExternalCall.call_kind == "LLM_SPEECH",
                    )
                    .order_by(ExternalCall.attempt_no)
                )
            ).all()
        )
        match = await session.get(Match, probe.actor.state.match_id)
        events = list(
            (
                await session.scalars(
                    select(MatchEvent).where(MatchEvent.match_id == probe.actor.state.match_id)
                )
            ).all()
        )
    assert match is not None
    evidence = {
        "capture_fault": capture_fault,
        "storage_failure_count": storage_failures,
        "provider_successes": probe.provider_calls,
        "task_done": task.done(),
        "actor_status": probe.actor.state.status,
        "actor_action_state": probe.actor.state.action_state,
        "database_status": match.status,
        "generation_statuses": [row.status for row in generations],
        "generation_error_codes": [row.error_code for row in generations],
        "generation_response_blob_present": [
            row.response_blob_id is not None for row in generations
        ],
        "call_statuses": [row.status for row in calls],
        "call_capture_errors": [row.capture_error_code for row in calls],
        "event_types": [row.event_type for row in events],
        "pipeline_error_codes": [
            getattr(row, "error_code", None)
            for row in caplog.records
            if row.message == "agent pipeline failed"
        ],
    }
    print(json.dumps(evidence, sort_keys=True))
    leases = await probe.runtime.capacity_limiter.acquire(
        str(probe.model_id), 1, timeout_seconds=0.1
    )
    probe.runtime.capacity_limiter.release(leases)
    assert probe.provider_calls == 1, evidence
    assert len(generations) == len(calls) == 1, evidence
    assert generations[0].status == "PLAYING" and generations[0].response_blob_id is not None, (
        evidence
    )
    assert calls[0].status == "SUCCEEDED", evidence
    assert generations[0].completion_tokens == calls[0].completion_tokens == 3
    assert generations[0].first_token_latency_ms is not None
    assert generations[0].completed_latency_ms is not None
    assert calls[0].first_result_at is not None and calls[0].completed_at is not None
    if capture_fault == "normal":
        async with probe.factory() as session:
            raw = await session.scalar(
                select(CaptureRow).where(CaptureRow.external_call_id == calls[0].id)
            )
            assert (
                raw is not None
                and raw.request_blob_id is not None
                and raw.response_blob_id is not None
            )
    elif capture_fault in {"storage_raise", "boundary_raise"}:
        assert probe.writer.stats().get("retry") == 1, evidence
        assert probe.writer.stats().get("persist_failed") == 1, evidence
    assert calls[0].capture_storage_mode == "ASYNC_V1"
    assert probe.actor.state.status == match.status == "RUNNING", evidence
    assert not any(row.event_type == "match.error" for row in events), evidence
    assert not task.done(), evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("authority_fault", ["response_blob", "generation_update"])
async def test_llm_ready_authoritative_write_error_propagates_and_rolls_back(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    authority_fault: str,
) -> None:
    probe = agent_fault_probe
    generation_id, call_id = await create_generation_call(probe)
    authority_error = RuntimeError("synthetic_llm_ready_authority_failed")
    capture = Mock()
    monkeypatch.setattr(runtime_module, "submit_capture", capture)
    injected = False

    def fail_generation_write(
        _: Connection,
        __: Any,
        statement: str,
        ___: Any,
        ____: Any,
        _____: bool,
    ) -> None:
        nonlocal injected
        if not injected and statement.lstrip().startswith("UPDATE agent_generations"):
            injected = True
            raise authority_error

    if authority_fault == "response_blob":
        monkeypatch.setattr(
            runtime_module, "store_content_blob", AsyncMock(side_effect=authority_error)
        )
    else:
        event.listen(probe.engine.sync_engine, "before_cursor_execute", fail_generation_write)
    try:
        with pytest.raises(RuntimeError) as caught:
            await probe.runtime._mark_generation_llm_ready(
                generation_id, LlmStreamResult("合成成功响应。", 1, 2, 3), ProviderCallCapture()
            )
        assert caught.value is authority_error
    finally:
        if authority_fault == "generation_update":
            event.remove(probe.engine.sync_engine, "before_cursor_execute", fail_generation_write)
    assert injected or authority_fault == "response_blob"
    capture.assert_not_called()
    async with probe.factory() as session:
        generation = await session.get(AgentGeneration, generation_id)
        call = await session.get(ExternalCall, call_id)
        assert generation is not None and generation.status == "STARTED"
        assert generation.response_blob_id is None
        assert call is not None and call.status == "STARTED"
        assert call.completed_at is None and call.response_blob_id is None
    assert probe.actor.state.status == "RUNNING" and probe.actor.state.sequence == 0
    assert probe.queue.empty()


@pytest.mark.asyncio
async def test_llm_ready_capture_cancel_preserves_committed_authority(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = agent_fault_probe
    generation_id, call_id = await create_generation_call(probe)
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    hold_writer_capture(probe, monkeypatch, entered, release, cancelled)
    task = asyncio.create_task(
        probe.runtime._mark_generation_llm_ready(
            generation_id, LlmStreamResult("合成成功响应。", 1, 2, 3), ProviderCallCapture()
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        async with probe.factory() as session:
            generation = await session.get(AgentGeneration, generation_id)
            call = await session.get(ExternalCall, call_id)
            assert generation is not None and generation.status == "PLAYING"
            assert generation.response_blob_id is not None
            assert call is not None and call.status == "SUCCEEDED"
            assert call.completed_at is not None
    finally:
        assert task.done() and task.cancel() is False
        await task
        release.set()
        await probe.writer.close()
        await asyncio.wait_for(cancelled.wait(), 0.5)
    async with probe.factory() as session:
        generation = await session.get(AgentGeneration, generation_id)
        call = await session.get(ExternalCall, call_id)
        assert generation is not None and generation.status == "PLAYING"
        assert call is not None and call.status == "SUCCEEDED"
        assert call.request_blob_id is None and call.response_blob_id is None


@pytest.mark.asyncio
async def test_llm_ready_capture_does_not_lock_or_overwrite_authoritative_generation(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = agent_fault_probe
    generation_id, call_id = await create_generation_call(probe)
    entered, release = asyncio.Event(), asyncio.Event()

    hold_writer_capture(probe, monkeypatch, entered, release)
    task = asyncio.create_task(
        probe.runtime._mark_generation_llm_ready(
            generation_id, LlmStreamResult("合成成功响应。", 1, 2, 3), ProviderCallCapture()
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        async with probe.factory() as session:
            async with session.begin():
                await session.execute(text("SET LOCAL lock_timeout = '150ms'"))
                await session.execute(
                    update(AgentGeneration)
                    .where(AgentGeneration.id == generation_id)
                    .values(status="FAILED", error_code="agent_cancelled")
                )
        assert task.done() and not task.cancelled()
    finally:
        release.set()
        await asyncio.wait_for(task, 0.5)
        await probe.writer.close()
    async with probe.factory() as session:
        generation = await session.get(AgentGeneration, generation_id)
        call = await session.get(ExternalCall, call_id)
        assert generation is not None and generation.status == "FAILED"
        assert generation.error_code == "agent_cancelled"
        assert call is not None and call.status == "SUCCEEDED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "guard",
    ["generation_status", "generation_error", "call_status", "call_error", "call_generation"],
)
async def test_llm_ready_late_capture_rejects_retired_or_unrelated_entry(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    guard: str,
) -> None:
    probe = agent_fault_probe
    generation_id, call_id = await create_generation_call(probe)
    await probe.runtime._mark_generation_llm_ready(
        generation_id, LlmStreamResult("合成成功响应。", 1, 2, 3), None
    )
    if guard == "call_generation":
        other_generation_id, call_id = await create_generation_call(probe, attempt_no=2)
        await probe.runtime._mark_generation_llm_ready(
            other_generation_id, LlmStreamResult("另一个合成响应。", 1, 2, 3), None
        )
    else:
        model_type = AgentGeneration if guard.startswith("generation") else ExternalCall
        row_id: UUID = generation_id if model_type is AgentGeneration else call_id
        field_name = "status" if guard.endswith("status") else "error_code"
        async with probe.factory() as session:
            async with session.begin():
                await session.execute(
                    update(model_type)
                    .where(model_type.id == row_id)
                    .values(**{field_name: "FAILED" if field_name == "status" else "other_failure"})
                )
    capture = Mock()
    monkeypatch.setattr(runtime_module, "submit_capture", capture)
    await probe.runtime._capture_generation_llm_ready(generation_id, call_id, ProviderCallCapture())
    capture.assert_not_called()
    async with probe.factory() as session:
        call = await session.get(ExternalCall, call_id)
        assert call is not None and call.request_blob_id is None and call.response_blob_id is None

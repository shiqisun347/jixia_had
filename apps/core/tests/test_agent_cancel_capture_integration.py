"""Actual cancellation/capture transactions with owned PG and synthetic HTTP."""

from __future__ import annotations

import asyncio
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

import jx_core.agent.runtime as runtime_module
from jx_core.agent.llm import LlmStreamResult, OpenAIStreamingClient
from jx_core.agent.runtime import AgentRun
from jx_core.data_capture.content import load_content_blob
from jx_core.data_capture.provider_projection import capture_projection
from jx_core.matches.domain import MatchCommand
from jx_core.models import AgentGeneration, ExternalCall, Match
from jx_core.models import ProviderCallCapture as CaptureRow
from test_agent_runtime_fault_integration import AgentFaultProbe, create_generation_call
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe

pytestmark = pytest.mark.integration
# This probe intentionally checks original private transaction ownership.
# pyright: reportPrivateUsage=false


async def wait_capture(probe: AgentFaultProbe, generation_id: Any) -> tuple[Any, Any]:
    async with asyncio.timeout(4):
        while True:
            async with probe.factory() as session:
                call = await session.scalar(
                    select(ExternalCall).where(ExternalCall.agent_generation_id == generation_id)
                )
                if call is not None and call.status != "STARTED":
                    projection = await capture_projection(session, call)
                    if projection["request_blob_id"] is not None:
                        raw = await load_content_blob(session, projection["request_blob_id"])
                        return call, raw
            await asyncio.sleep(0.01)


async def drained(probe: AgentFaultProbe) -> None:
    async with asyncio.timeout(4):
        while probe.writer.stats()["items"] or probe.writer.stats()["queued"]:
            await asyncio.sleep(0.01)
    assert probe.writer.stats()["retained_bytes"] == 0
    assert probe.writer.stats()["workspace_bytes"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", [False, True])
async def test_authoritative_pause_preserves_sent_request_or_explicit_presend_unavailable(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    sent: bool,
) -> None:
    probe = agent_fault_probe
    entered, cancelled = asyncio.Event(), asyncio.Event()
    calls: list[bool] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "synthetic.invalid"
        calls.append(True)
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("held original HTTP must be cancelled")

    def client(**arguments: Any) -> OpenAIStreamingClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        probe.http_clients.append(http)
        return OpenAIStreamingClient(**arguments, client=http)

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", client)
    held = None if sent else await probe.runtime.capacity_limiter.acquire(str(probe.model_id), 1)
    match_id = probe.actor.state.match_id
    try:
        await probe.runtime.start_agent(
            match_id=match_id,
            action_key="1:1",
            agent_profile_id=probe.agent_id,
            duration_ms=30_000,
            side="AFFIRMATIVE",
        )
        probe.run = run = probe.runtime._runs[match_id]
        if sent:
            async with asyncio.timeout(3):
                await entered.wait()
        else:
            async with asyncio.timeout(3):
                while True:
                    generation_id = run.generation_id
                    if generation_id is None:
                        await asyncio.sleep(0.01)
                        continue
                    async with probe.factory() as session:
                        count = await session.scalar(
                            select(ExternalCall.id).where(
                                ExternalCall.match_id == match_id,
                                ExternalCall.agent_generation_id == generation_id,
                                ExternalCall.call_kind == "LLM_SPEECH",
                                ExternalCall.status == "STARTED",
                            )
                        )
                    if count is not None:
                        assert run.generation_id == generation_id
                        break
                    await asyncio.sleep(0.01)
        assert run.llm_capture is not None
        assert (run.llm_capture.request is not None) == sent
        # This observation is BEFORE cleanup. It proves whether the real
        # adapter reached its synchronous request capture/HTTP entry boundary.
        assert len(calls) == int(sent)
        generation_id = run.generation_id
        assert generation_id is not None
        await probe.manager.submit(
            match_id,
            MatchCommand(
                type="match.pause",
                message_id="owned-provider-capture-pause",
                actor_user_id=uuid4(),
                payload={"authorized": True},
            ),
        )
        call, raw = await wait_capture(probe, generation_id)
        assert probe.actor.state.status == "PAUSED"
        assert call.status == "FAILED" and call.error_code == "agent_cancelled"
        if sent:
            assert cancelled.is_set()
            assert raw["capture"]["status"] == "COMPLETE"
            assert raw["body"]["messages"][0]["role"] == "system"
            assert "Authorization" not in str(raw) and "Bearer synthetic" not in str(raw)
        else:
            assert not cancelled.is_set() and not calls
            assert raw["body"] is None
            assert raw["capture"]["status"] == "UNAVAILABLE"
            assert raw["capture"]["error_code"] == "provider_request_unavailable"
        await drained(probe)
        async with probe.factory() as session:
            generation = await session.get(AgentGeneration, generation_id)
            match = await session.get(Match, match_id)
            assert generation is not None and generation.status == "FAILED"
            assert match is not None and match.context_version == 0
            assert match.status == "PAUSED" and probe.actor.state.current_speech_id is None
    finally:
        if held is not None:
            probe.runtime.capacity_limiter.release(held)
        leases = await probe.runtime.capacity_limiter.acquire(str(probe.model_id), 1, 0.2)
        probe.runtime.capacity_limiter.release(leases)


@pytest.mark.asyncio
@pytest.mark.parametrize("helper", ["llm_ready", "tts_finish"])
@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
async def test_cancel_during_original_success_transaction_retains_request_and_result_identity(
    agent_fault_probe: AgentFaultProbe,
    helper: str,
    boundary: str,
) -> None:
    probe = agent_fault_probe
    generation_id, call_id = await create_generation_call(probe)
    if helper == "tts_finish":
        async with probe.factory() as session:
            async with session.begin():
                call = await session.get(ExternalCall, call_id)
                assert call is not None
                call.call_kind = "TTS"
    entered = asyncio.Event()
    used = False

    class Transaction:
        def __init__(self, original: Any, hold: bool) -> None:
            self.original, self.hold = original, hold

        async def __aenter__(self) -> Any:
            return await self.original.__aenter__()

        async def __aexit__(self, kind: Any, error: Any, trace: Any) -> Any:
            if self.hold and kind is None and boundary == "before_commit":
                entered.set()
                try:
                    await asyncio.Event().wait()
                except BaseException as failure:
                    await self.original.__aexit__(type(failure), failure, failure.__traceback__)
                    raise
            result = await self.original.__aexit__(kind, error, trace)
            if self.hold and kind is None and boundary == "after_commit":
                entered.set()
                await asyncio.Event().wait()
            return result

    class Session:
        def __init__(self) -> None:
            self.original = probe.factory()

        async def __aenter__(self) -> Any:
            await self.original.__aenter__()
            return self

        async def __aexit__(self, *values: Any) -> Any:
            return await self.original.__aexit__(*values)

        def begin(self) -> Transaction:
            nonlocal used
            hold = not used
            used = True
            return Transaction(self.original.begin(), hold)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.original, name)

    probe.runtime._session_factory = cast(Any, Session)
    owned = probe.writer.new_capture()
    owned.capture_request(
        transport="SSE" if helper == "llm_ready" else "WEBSOCKET",
        url="https://synthetic.invalid/v1",
        body={"messages": [{"role": "system", "content": "Original committed request."}]},
    )
    run = AgentRun(
        match_id=probe.actor.state.match_id,
        action_key="1:1",
        agent_profile_id=probe.agent_id,
        duration_ms=30_000,
        generation_id=generation_id,
        llm_capture=owned if helper == "llm_ready" else None,
        tts_capture=owned if helper == "tts_finish" else None,
    )
    probe.runtime._runs[run.match_id] = run
    probe.run = run
    if helper == "llm_ready":
        action = probe.runtime._mark_generation_llm_ready(
            generation_id,
            LlmStreamResult("Owned response.", 1, 2, 3),
            owned,
            capture_owner=run,
        )
    else:
        action = probe.runtime._finish_external_call(
            call_id, provider_capture=owned, capture_owner=run
        )
    run.task = asyncio.create_task(action)
    async with asyncio.timeout(3):
        await entered.wait()
    # Both helpers must leave capture owned until their real commit returns.
    assert owned.request is not None and owned.reservation is not None
    await probe.runtime.reset_agent(run.match_id)
    call, raw = await wait_capture(probe, generation_id)
    assert raw["capture"]["status"] == "COMPLETE"
    assert raw["body"]["messages"][0]["content"] == "Original committed request."
    assert call.status == ("SUCCEEDED" if boundary == "after_commit" else "FAILED")
    assert call.error_code == (None if boundary == "after_commit" else "agent_cancelled")
    async with probe.factory() as session:
        captures = list(
            (
                await session.scalars(
                    select(CaptureRow).where(CaptureRow.external_call_id == call_id)
                )
            ).all()
        )
        assert len(captures) == 1
        generation = await session.get(AgentGeneration, generation_id)
        assert generation is not None and generation.status == "FAILED"
        match = await session.get(Match, run.match_id)
        assert match is not None and match.context_version == 0
    await drained(probe)

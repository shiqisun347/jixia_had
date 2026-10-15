"""Real Manager/admin/PG startup retirement, with synthetic provider/media edges."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from starlette.requests import Request

import jx_core.agent.runtime as runtime_module
from jx_core.admin_routes import MatchControlRequest, control_match, match_control_target
from jx_core.agent.llm import OpenAIStreamingClient
from jx_core.agent.runtime import HostAudioRun
from jx_core.agent.tts import TtsStreamResult
from jx_core.matches.domain import MatchEvent
from jx_core.models import AgentGeneration, ExternalCall, Match, Speech
from test_agent_failure_recovery_integration import (
    RecoveryDecoder,
    RecoverySource,
    wait_for_condition,
)
from test_agent_runtime_fault_integration import AgentFaultProbe
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe
from test_match_handoff_recovery_integration import (
    admin_auth,  # pyright: ignore[reportUnknownVariableType]
)

pytestmark = pytest.mark.integration
# The held boundary is old media cleanup, never an Actor/DB commit or callback.
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["pause", "reset_speech"])
async def test_pending_host_cleanup_cannot_admit_agent_after_admin_pause_then_resumes_normally(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    probe = agent_fault_probe
    actor, runtime, match_id = probe.actor, probe.runtime, probe.actor.state.match_id
    entered, release = asyncio.Event(), asyncio.Event()
    sources: list[RecoverySource] = []
    tts_entries: list[str] = []
    text = "这是正常恢复以后，依据当前持方完成的合成发言。"

    async def provider(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "synthetic.invalid"
        probe.provider_calls += 1
        event = {"choices": [{"delta": {"content": text}}]}
        return httpx.Response(200, content=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")

    def adapter(**values: Any) -> OpenAIStreamingClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        probe.http_clients.append(client)
        return OpenAIStreamingClient(**values, client=client)

    class TTS:
        def __init__(self, **_: Any) -> None:
            pass

        async def synthesize(self, chunks: Any, **values: Any) -> TtsStreamResult:
            tts_entries.append(actor.state.status)
            async for _ in chunks:
                await values["on_audio"](b"owned-synthetic-audio")
            # A real provider awaits its final transport response after EOF;
            # preserve that boundary instead of returning in the queue-put tick.
            await asyncio.sleep(0.02)
            return TtsStreamResult(uuid4(), 21, 1, 2)

        async def close(self) -> None:
            return None

    async def close() -> None:
        return None

    async def publisher(*_: Any, **__: Any) -> Any:
        source = RecoverySource()
        source.allow_drain.set()
        sources.append(source)
        return SimpleNamespace(disconnect=close), source

    original_cleanup = runtime._cancel_host_run

    async def held_cleanup(run: HostAudioRun) -> None:
        entered.set()
        await release.wait()
        await original_cleanup(run)

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", adapter)
    monkeypatch.setattr(runtime_module, "QwenTtsConnection", TTS)
    monkeypatch.setattr(runtime_module, "IncrementalOpusDecoder", RecoveryDecoder)
    monkeypatch.setattr(runtime, "_connect_publisher", publisher)
    monkeypatch.setattr(runtime, "_cancel_host_run", held_cleanup)
    runtime._host_runs[match_id] = HostAudioRun(
        match_id=match_id,
        action_key="1:0",
        storage_path="unused-synthetic-old-host",
        duration_ms=6000,
        context_version=0,
    )
    # The fixture's current Agent action is authoritative and has all eight
    # participant rows. Use the production handoff helper, not a new callback.
    event = MatchEvent(
        type="agent.preparing",
        match_id=match_id,
        sequence=actor.state.sequence,
        server_time_ms=0,
        payload={
            "action_key": "1:1",
            "agent_profile_id": str(probe.agent_id),
            "duration_ms": 30_000,
            "side": "AFFIRMATIVE",
        },
    )
    startup = asyncio.create_task(probe.manager._start_agent_after_source_finalized(event))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert match_id not in runtime._runs
        request = Request(
            {
                "type": "http",
                "app": SimpleNamespace(state=SimpleNamespace(match_runtime_manager=probe.manager)),
            }
        )
        auth = await admin_auth(probe)
        async with probe.factory() as session:
            target = await match_control_target(match_id, request, auth, session)
            if action == "reset_speech":
                body = MatchControlRequest(
                    action="reset_speech",
                    operation_id=uuid4(),
                    expected_sequence=target.sequence,
                    expected_action_key=target.action_key,
                    expected_stage_generation=target.stage_generation,
                    expected_speech_id=target.speech_id,
                    expected_context_version=target.context_version,
                )
            else:
                body = MatchControlRequest(action="pause")
            result = await control_match(match_id, body, request, auth, session)
        assert result["status"] == actor.state.status == "PAUSED"
        acknowledged_state = actor.state
        release.set()
        await asyncio.wait_for(startup, timeout=2)
        await asyncio.sleep(0.1)
        assert actor.state is acknowledged_state
        assert not runtime._runs and not runtime._pending_media_starts
        assert probe.provider_calls == 0 and not tts_entries and not sources
        async with probe.factory() as session:
            for model in (AgentGeneration, ExternalCall, Speech):
                assert not list(
                    (await session.scalars(select(model).where(model.match_id == match_id))).all()
                )
            match = await session.get(Match, match_id)
            assert match is not None and match.status == "PAUSED"
            context_at_pause = match.context_version

        # Resume must create a new admission only after the original 3-second
        # authoritative countdown. No direct elapsed event or local timer jump.
        before_resume = asyncio.get_running_loop().time()
        async with probe.factory() as session:
            await control_match(
                match_id, MatchControlRequest(action="resume"), request, auth, session
            )
        assert actor.state.action_state == "RESUME_COUNTDOWN"
        await asyncio.sleep(2.5)
        assert not probe.provider_calls and not tts_entries
        await wait_for_condition(lambda: actor.state.status == "FINISHED", timeout=3)
        assert asyncio.get_running_loop().time() - before_resume >= 2.9
        assert probe.provider_calls == 1 and tts_entries == ["RUNNING"]
        assert len(sources) == 1 and sources[0].frames
        await wait_for_condition(lambda: sources[0].closed)
        assert not runtime._pending_media_starts and not runtime._runs
        async with probe.factory() as session:
            generations = list(
                (
                    await session.scalars(
                        select(AgentGeneration).where(AgentGeneration.match_id == match_id)
                    )
                ).all()
            )
            speeches = list(
                (await session.scalars(select(Speech).where(Speech.match_id == match_id))).all()
            )
            calls = list(
                (
                    await session.scalars(
                        select(ExternalCall).where(ExternalCall.match_id == match_id)
                    )
                ).all()
            )
            match = await session.get(Match, match_id)
            assert len(generations) == len(speeches) == 1
            assert generations[0].status == speeches[0].status == "FINALIZED"
            assert generations[0].context_version == context_at_pause
            assert speeches[0].generation_id == generations[0].id
            assert speeches[0].display_text == text and speeches[0].origin == "ORIGINAL"
            assert len(calls) == 2 and all(call.status == "SUCCEEDED" for call in calls)
            assert {call.call_kind for call in calls} == {"LLM_SPEECH", "TTS"}
            assert match is not None and match.context_version == context_at_pause + 1
    finally:
        release.set()
        startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)

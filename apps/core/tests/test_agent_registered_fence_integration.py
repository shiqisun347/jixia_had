"""Registered preparing Agent retirement before bounded cleanup, using real PG."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from starlette.requests import Request

import jx_core.agent.runtime as runtime_module
from jx_core.admin_routes import MatchControlRequest, control_match, match_control_target
from jx_core.agent.llm import OpenAIStreamingClient
from jx_core.agent.tts import TtsStreamResult
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
# Only the external HTTP, codec, RTC and controlled cleanup wait are synthetic.
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
@pytest.mark.parametrize("held_boundary", ["asr_cleanup", "media_lock"])
async def test_registered_preparing_agent_rejects_first_delta_during_admin_cleanup_then_recovers(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    held_boundary: str,
) -> None:
    probe = agent_fault_probe
    actor, runtime, match_id = probe.actor, probe.runtime, probe.actor.state.match_id
    provider_entered, delta_release = asyncio.Event(), asyncio.Event()
    asr_entered, asr_release = asyncio.Event(), asyncio.Event()
    sources: list[RecoverySource] = []
    tts_entries: list[str] = []
    text = "这是正常恢复后第一次有效发言。"

    class HeldFirstDelta(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            provider_entered.set()
            await delta_release.wait()
            event = {"choices": [{"delta": {"content": "这是旧代次第一次迟到文本。"}}]}
            yield f"data: {json.dumps(event)}\n\n".encode()
            await asyncio.Event().wait()

    async def provider(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "synthetic.invalid"
        probe.provider_calls += 1
        if probe.provider_calls == 1:
            return httpx.Response(200, stream=HeldFirstDelta())
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

    async def reset_asr(_: Any) -> None:
        asr_entered.set()
        await asr_release.wait()

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", adapter)
    monkeypatch.setattr(runtime_module, "QwenTtsConnection", TTS)
    monkeypatch.setattr(runtime_module, "IncrementalOpusDecoder", RecoveryDecoder)
    monkeypatch.setattr(runtime, "_connect_publisher", publisher)
    original_speech_runtime = probe.manager._speech_runtime
    probe.manager.set_speech_runtime(cast(Any, SimpleNamespace(reset_speech=reset_asr)))
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=SimpleNamespace(match_runtime_manager=probe.manager)),
        }
    )
    auth = await admin_auth(probe)
    await runtime.start_agent(
        match_id=match_id,
        action_key="1:1",
        agent_profile_id=probe.agent_id,
        duration_ms=30_000,
        side="AFFIRMATIVE",
    )
    old = probe.run = runtime._runs[match_id]
    await asyncio.wait_for(provider_entered.wait(), 2)
    assert old.task is not None and old.generation_id is not None
    assert actor.state.action_state == "AGENT_PREPARING" and old.speech_id is None
    assert len(sources) == 1 and not sources[0].frames and not tts_entries

    async def reset() -> Any:
        async with probe.factory() as session:
            target = await match_control_target(match_id, request, auth, session)
            return await control_match(
                match_id,
                MatchControlRequest(
                    action="reset_speech",
                    operation_id=uuid4(),
                    expected_sequence=target.sequence,
                    expected_action_key=target.action_key,
                    expected_stage_generation=target.stage_generation,
                    expected_speech_id=target.speech_id,
                    expected_context_version=target.context_version,
                ),
                request,
                auth,
                session,
            )

    locked = held_boundary == "media_lock"
    if locked:
        await runtime._media_lock.acquire()
    control = asyncio.create_task(reset())
    try:
        if locked:
            await wait_for_condition(lambda: actor.state.status == "PAUSED", timeout=2)
            assert not asr_entered.is_set()
        else:
            await asyncio.wait_for(asr_entered.wait(), 2)
        assert actor.state.status == "PAUSED" and not control.done()
        assert old.media_fenced, "existing provider owner must retire before cleanup waits"
        delta_release.set()
        # The actual HTTP stream delivers its first content after authority has
        # paused. The production on_delta guard, not a fabricated callback,
        # must stop it before text/TTS/PCM while cleanup is still blocked.
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(old.task, 2)
        assert old.draft == "" and not tts_entries and not sources[0].frames
        async with probe.factory() as session:
            calls = list(
                (
                    await session.scalars(
                        select(ExternalCall).where(ExternalCall.match_id == match_id)
                    )
                ).all()
            )
            assert len(calls) == 1 and calls[0].call_kind == "LLM_SPEECH"
            assert not list(
                (await session.scalars(select(Speech).where(Speech.match_id == match_id))).all()
            )
        if locked:
            runtime._media_lock.release()
            locked = False
        asr_release.set()
        result = await asyncio.wait_for(control, 2)
        assert result["status"] == actor.state.status == "PAUSED"
        assert match_id not in runtime._runs
        # The held receiver exists only for the reset cleanup boundary. Restore
        # the fixture's actual lifecycle owner before normal resume/finish.
        probe.manager._speech_runtime = original_speech_runtime
        async with probe.factory() as session:
            match = await session.get(Match, match_id)
            generation = await session.get(AgentGeneration, old.generation_id)
            assert match is not None and generation is not None
            assert generation.status == "FAILED" and generation.error_code == "agent_cancelled"
            context_at_pause = match.context_version

        before_resume = asyncio.get_running_loop().time()
        async with probe.factory() as session:
            await control_match(
                match_id, MatchControlRequest(action="resume"), request, auth, session
            )
        assert actor.state.action_state == "RESUME_COUNTDOWN"
        await asyncio.sleep(2.5)
        assert probe.provider_calls == 1 and not tts_entries
        await wait_for_condition(lambda: actor.state.status == "FINISHED", timeout=3)
        assert asyncio.get_running_loop().time() - before_resume >= 2.9
        assert probe.provider_calls == 2 and tts_entries == ["RUNNING"]
        assert len(sources) == 2 and not sources[0].frames and sources[1].frames
        await wait_for_condition(lambda: all(source.closed for source in sources))
        assert not runtime._runs and not runtime._pending_media_starts
        async with probe.factory() as session:
            speeches = list(
                (await session.scalars(select(Speech).where(Speech.match_id == match_id))).all()
            )
            generation = await session.get(AgentGeneration, old.generation_id)
            match = await session.get(Match, match_id)
            assert len(speeches) == 1 and speeches[0].status == "FINALIZED"
            assert (
                speeches[0].display_text == text and speeches[0].generation_id != old.generation_id
            )
            assert generation is not None and generation.status == "FAILED"
            assert match is not None and match.context_version == context_at_pause + 1
    finally:
        delta_release.set()
        asr_release.set()
        if locked:
            runtime._media_lock.release()
        await asyncio.gather(control, return_exceptions=True)
        probe.manager._speech_runtime = original_speech_runtime

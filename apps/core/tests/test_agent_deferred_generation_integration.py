"""A committed generation returning after reset must not reopen media resources."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from starlette.requests import Request

import jx_core.agent.runtime as runtime_module
from jx_core.admin_routes import MatchControlRequest, control_match, match_control_target
from jx_core.agent.llm import OpenAIStreamingClient
from jx_core.agent.runtime import AgentConfig
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
# Only provider HTTP, codec, RTC and delayed return of a real PG commit are synthetic.
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
@pytest.mark.parametrize("release_phase", ["before_ack", "after_ack"])
async def test_generation_return_after_admin_reset_opens_no_resources_then_resumes_normally(
    agent_fault_probe: AgentFaultProbe, monkeypatch: pytest.MonkeyPatch, release_phase: str
) -> None:
    probe = agent_fault_probe
    actor, runtime, match_id = probe.actor, probe.runtime, probe.actor.state.match_id
    committed, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    resources: list[str] = []
    sources: list[RecoverySource] = []
    tts_entries: list[str] = []
    text = "这是明确恢复以后，依据当前上下文完成的唯一有效发言。"
    generation_ids: list[UUID] = []
    original_generation = runtime._create_generation

    async def held_generation(config: AgentConfig, attempt_no: int) -> UUID:
        generation_id = await original_generation(config, attempt_no)
        generation_ids.append(generation_id)
        if len(generation_ids) == 1:
            committed.set()
            # The DB operation has committed. Its result is already owned and
            # may return after cooperative cancellation, exactly the boundary
            # whose authority must be rechecked before the pipeline starts.
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
        return generation_id

    class Decoder(RecoveryDecoder):
        def __init__(self) -> None:
            resources.append("decoder_construct")
            super().__init__()

        async def start(self, _: Path) -> None:
            resources.append("decoder_start")
            await super().start(_)

    async def provider(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "synthetic.invalid"
        probe.provider_calls += 1
        event = {"choices": [{"delta": {"content": text}}]}
        return httpx.Response(200, content=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")

    def adapter(**values: Any) -> OpenAIStreamingClient:
        resources.append("llm_client")
        client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        probe.http_clients.append(client)
        return OpenAIStreamingClient(**values, client=client)

    class TTS:
        def __init__(self, **_: Any) -> None:
            resources.append("tts_connection")

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
        resources.append("publisher")
        source = RecoverySource()
        source.allow_drain.set()
        sources.append(source)
        return SimpleNamespace(disconnect=close), source

    monkeypatch.setattr(runtime, "_create_generation", held_generation)
    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", adapter)
    monkeypatch.setattr(runtime_module, "QwenTtsConnection", TTS)
    monkeypatch.setattr(runtime_module, "IncrementalOpusDecoder", Decoder)
    monkeypatch.setattr(runtime, "_connect_publisher", publisher)
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
    await asyncio.wait_for(committed.wait(), 2)
    assert old.generation_id is None and not resources

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

    control = asyncio.create_task(reset())
    try:
        await asyncio.wait_for(cancelled.wait(), 2)
        assert actor.state.status == "PAUSED" and old.media_fenced and not control.done()
        if release_phase == "after_ack":
            # Keep the real commit result beyond the normal bounded task
            # cancellation budget. The public ACK may complete, but that
            # generation still needs an exact terminal receipt when it returns.
            result = await asyncio.wait_for(control, 4)
            assert actor.state.status == "PAUSED" and not resources
            assert old.generation_id is None and old.task is not None and not old.task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(old.task, 2)
        else:
            release.set()
            result = await asyncio.wait_for(control, 2)
        assert result["status"] == actor.state.status == "PAUSED"
        assert not resources, "retired generation opened a media or provider resource"
        assert not tts_entries and not sources and probe.provider_calls == 0
        assert not runtime._runs and not runtime._pending_media_starts
        assert not list(Path(runtime._settings.agent_audio_storage_dir).rglob("*.ogg*"))
        async with probe.factory() as session:
            generation = await session.get(AgentGeneration, generation_ids[0])
            match = await session.get(Match, match_id)
            assert generation is not None and generation.status == "FAILED"
            assert generation.error_code == "agent_cancelled"
            assert match is not None and match.status == "PAUSED"
            context_at_pause = match.context_version
            for model in (ExternalCall, Speech):
                assert not list(
                    (await session.scalars(select(model).where(model.match_id == match_id))).all()
                )

        before_resume = asyncio.get_running_loop().time()
        async with probe.factory() as session:
            await control_match(
                match_id, MatchControlRequest(action="resume"), request, auth, session
            )
        assert actor.state.action_state == "RESUME_COUNTDOWN"
        await asyncio.sleep(2.5)
        assert not resources and probe.provider_calls == 0 and not tts_entries
        await wait_for_condition(lambda: actor.state.status == "FINISHED", timeout=3)
        assert asyncio.get_running_loop().time() - before_resume >= 2.9
        assert probe.provider_calls == 1 and tts_entries == ["RUNNING"]
        assert resources == [
            "decoder_construct", "decoder_start", "publisher", "tts_connection", "llm_client"
        ]
        assert len(sources) == 1 and sources[0].frames
        await wait_for_condition(lambda: sources[0].closed)
        assert not runtime._runs and not runtime._pending_media_starts
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
            assert len(generations) == 2 and len(speeches) == 1
            assert {item.status for item in generations} == {"FAILED", "FINALIZED"}
            assert speeches[0].status == "FINALIZED" and speeches[0].display_text == text
            assert speeches[0].generation_id == generation_ids[1]
            assert len(calls) == 2 and all(call.status == "SUCCEEDED" for call in calls)
            assert {call.call_kind for call in calls} == {"LLM_SPEECH", "TTS"}
            assert match is not None and match.context_version == context_at_pause + 1
    finally:
        release.set()
        await asyncio.gather(control, return_exceptions=True)

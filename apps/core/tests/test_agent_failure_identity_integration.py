"""Current Agent callbacks use run identity across wall-clock and retry cycles."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

import jx_core.agent.runtime as runtime_module
from jx_core.agent.llm import OpenAIStreamingClient
from jx_core.agent.tts import TtsStreamResult
from jx_core.matches.domain import MatchCommand
from jx_core.models import AgentGeneration, Match, MatchEvent, ModelProfile, Room, Speech
from jx_core.runtime_identity import CallbackEnvelope
from test_agent_failure_recovery_integration import RecoveryDecoder, RecoverySource
from test_agent_failure_recovery_integration import (
    test_actual_failed_agent_recovers_and_finalizes_from_authoritative_resume as execute_recovery,
)
from test_agent_runtime_fault_integration import AgentFaultProbe
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("clock_order", ["forward", "reverse", "equal"])
async def test_current_failure_survives_wall_clock_order_and_recovers(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    clock_order: str,
) -> None:
    probe = agent_fault_probe
    original = probe.manager.handle_agent_failure
    exercised = False

    async def controlled(
        *, envelope: CallbackEnvelope, error_code: str, logical_call_id: UUID | None = None
    ) -> None:
        nonlocal exercised
        assert not exercised
        exercised = True
        async with probe.factory() as session:
            async with session.begin():
                generations = list(
                    (
                        await session.scalars(
                            select(AgentGeneration).where(
                                AgentGeneration.match_id == envelope.match_id
                            )
                        )
                    ).all()
                )
                assert len(generations) == 2
                first = next(row for row in generations if row.attempt_no == 1)
                second = next(row for row in generations if row.attempt_no == 2)
                assert second.id == envelope.generation_id
                second.created_at = first.created_at + timedelta(
                    milliseconds={"forward": 1, "reverse": -1, "equal": 0}[clock_order]
                )
        await original(envelope=envelope, error_code=error_code, logical_call_id=logical_call_id)

    monkeypatch.setattr(probe.manager, "handle_agent_failure", controlled)
    await execute_recovery(probe, monkeypatch, "first_token", "llm_first_token_timeout")
    assert exercised


@pytest.mark.asyncio
async def test_stale_callback_fields_cannot_pause_the_current_retry(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = agent_fault_probe
    original = probe.manager.handle_agent_failure
    rejected = 0

    async def controlled(
        *, envelope: CallbackEnvelope, error_code: str, logical_call_id: UUID | None = None
    ) -> None:
        nonlocal rejected
        async with probe.factory() as session:
            first = await session.scalar(
                select(AgentGeneration).where(
                    AgentGeneration.match_id == envelope.match_id,
                    AgentGeneration.attempt_no == 1,
                )
            )
            assert first is not None
        mutations: list[dict[str, Any]] = [
            {"generation_id": first.id, "attempt_no": 1},
            {"generation_id": uuid4()},
            {"match_id": uuid4()},
            {"speech_id": uuid4()},
            {"attempt_no": envelope.attempt_no + 1},
            {"context_version": envelope.context_version + 1},
            {"connection_epoch": 1},
            {"opportunity_id": uuid4()},
            {"opportunity_generation": 1},
        ]
        before = probe.actor.state
        for mutation in mutations:
            await original(
                envelope=replace(envelope, **mutation),
                error_code=error_code,
                logical_call_id=logical_call_id,
            )
            assert probe.actor.state is before
            rejected += 1
        await original(envelope=envelope, error_code=error_code, logical_call_id=logical_call_id)

    monkeypatch.setattr(probe.manager, "handle_agent_failure", controlled)
    await execute_recovery(probe, monkeypatch, "first_token", "llm_first_token_timeout")
    assert rejected == 9


async def wait_task(task: asyncio.Task[None] | None) -> None:
    assert task is not None
    result = (await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3))[0]
    assert result is None or isinstance(result, asyncio.CancelledError)


async def resume(probe: AgentFaultProbe) -> None:
    async with probe.factory() as session:
        match = await session.get(Match, probe.actor.state.match_id)
        assert match is not None
        room = await session.get(Room, match.room_id)
        assert room is not None
        organizer = room.organizer_user_id
    result = await probe.manager.submit(
        probe.actor.state.match_id,
        MatchCommand(
            type="match.resume",
            message_id=f"identity-resume-{uuid4()}",
            actor_user_id=organizer,
            payload={"privileged": True, "reasons": []},
        ),
    )
    assert result.state.status == "PAUSED"


@pytest.mark.asyncio
async def test_previous_higher_attempt_cannot_override_a_new_run_in_same_context(
    agent_fault_probe: AgentFaultProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = agent_fault_probe
    held, release = asyncio.Event(), asyncio.Event()

    async def provider(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "synthetic.invalid"
        probe.provider_calls += 1
        if probe.provider_calls == 3:
            held.set()
            await release.wait()
        return httpx.Response(503, json={"code": "synthetic_unavailable"})

    def client(**arguments: Any) -> OpenAIStreamingClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        probe.http_clients.append(http)
        return OpenAIStreamingClient(**arguments, client=http)

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", client)
    match_id = probe.actor.state.match_id
    await probe.runtime.start_agent(
        match_id=match_id,
        action_key="1:1",
        agent_profile_id=probe.agent_id,
        duration_ms=30_000,
        side="AFFIRMATIVE",
    )
    old = probe.runtime._runs[match_id]  # pyright: ignore[reportPrivateUsage]
    probe.run = old
    await wait_task(old.task)
    assert probe.actor.state.status == "ERROR" and probe.provider_calls == 2
    assert old.callback_envelope is not None and old.callback_envelope.attempt_no == 2
    await resume(probe)
    try:
        await asyncio.wait_for(held.wait(), 4.5)
        current = probe.runtime._runs[match_id]  # pyright: ignore[reportPrivateUsage]
        probe.run = current
        assert current.callback_envelope is not None
        assert current.callback_envelope.attempt_no == 1
        assert current.callback_envelope.context_version == old.callback_envelope.context_version
        before = probe.actor.state
        await probe.manager.handle_agent_failure(
            envelope=old.callback_envelope,
            error_code="llm_provider_failed",
            logical_call_id=old.logical_call_id,
        )
        assert probe.actor.state is before and before.status == "RUNNING"
        release.set()
        await wait_task(current.task)
        assert probe.actor.state.status == "ERROR" and probe.provider_calls == 4
    finally:
        release.set()


@pytest.mark.asyncio
async def test_startup_config_failures_remain_distinct_across_same_context_resume(
    agent_fault_probe: AgentFaultProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = agent_fault_probe
    match_id = probe.actor.state.match_id
    async with probe.factory() as session:
        async with session.begin():
            match = await session.get(Match, match_id)
            model = await session.get(ModelProfile, probe.model_id)
            assert match is not None and model is not None
            match.context_version = 7
            model.status = "DISABLED"
    await probe.runtime.start_agent(
        match_id=match_id,
        action_key="1:1",
        agent_profile_id=probe.agent_id,
        duration_ms=30_000,
        side="AFFIRMATIVE",
        context_version=7,
    )
    old = probe.runtime._runs[match_id]  # pyright: ignore[reportPrivateUsage]
    probe.run = old
    await wait_task(old.task)
    assert probe.actor.state.status == "ERROR"
    assert old.callback_envelope is not None and old.callback_envelope.generation_id is None
    assert old.callback_envelope.context_version == 7
    held, release = asyncio.Event(), asyncio.Event()
    load_config = probe.runtime._load_config  # pyright: ignore[reportPrivateUsage]

    async def controlled(run: runtime_module.AgentRun) -> runtime_module.AgentConfig:
        if run.logical_call_id != old.logical_call_id and run.callback_envelope is not None:
            if run.callback_envelope.attempt_no == 2:
                held.set()
                await release.wait()
        return await load_config(run)

    monkeypatch.setattr(probe.runtime, "_load_config", controlled)
    await resume(probe)
    try:
        await asyncio.wait_for(held.wait(), 4.5)
        current = probe.runtime._runs[match_id]  # pyright: ignore[reportPrivateUsage]
        probe.run = current
        assert current.logical_call_id != old.logical_call_id
        assert current.callback_envelope == old.callback_envelope
        before = probe.actor.state
        await probe.manager.handle_agent_failure(
            envelope=old.callback_envelope,
            logical_call_id=old.logical_call_id,
            error_code="model_profile_unavailable",
        )
        assert probe.actor.state is before and before.status == "RUNNING"
        release.set()
        await wait_task(current.task)
        assert probe.actor.state.status == "ERROR"
        assert probe.actor.state.error_code == "model_profile_unavailable"
        async with probe.factory() as session:
            events = list(
                (
                    await session.scalars(select(MatchEvent).where(MatchEvent.match_id == match_id))
                ).all()
            )
            generations = list(
                (
                    await session.scalars(
                        select(AgentGeneration).where(AgentGeneration.match_id == match_id)
                    )
                ).all()
            )
        assert sum(event.event_type == "match.error" for event in events) == 2
        assert not generations and probe.provider_calls == 0
    finally:
        release.set()


@pytest.mark.asyncio
async def test_detached_finalizer_failure_preserves_authoritative_recovery(
    agent_fault_probe: AgentFaultProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = agent_fault_probe
    match_id = probe.actor.state.match_id
    callbacks = 0
    original_finalize = probe.manager.finalize_agent_speech

    async def provider(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "synthetic.invalid"
        probe.provider_calls += 1
        if probe.provider_calls == 1:
            return httpx.Response(503, json={"code": "synthetic_unavailable"})
        return httpx.Response(
            200,
            content=(
                'data: {"choices":[{"delta":{"content":"合成完整发言。"}}]}\n\ndata: [DONE]\n\n'
            ),
        )

    def client(**arguments: Any) -> OpenAIStreamingClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        probe.http_clients.append(http)
        return OpenAIStreamingClient(**arguments, client=http)

    class Tts:
        async def synthesize(self, chunks: Any, *, on_audio: Any, **_: Any) -> TtsStreamResult:
            assert "".join([part async for part in chunks]) == "合成完整发言。"
            await on_audio(b"synthetic-compressed-audio")
            await asyncio.sleep(0.02)
            return TtsStreamResult(uuid4(), len(b"synthetic-compressed-audio"), 1, 2)

        async def close(self) -> None:
            pass

    async def publisher(*_: Any, **__: Any) -> Any:
        source = RecoverySource()
        source.allow_drain.set()
        return SimpleNamespace(disconnect=AsyncMock()), source

    async def fail_finalize(**_: Any) -> None:
        nonlocal callbacks
        callbacks += 1
        assert match_id not in probe.runtime._runs  # pyright: ignore[reportPrivateUsage]
        raise RuntimeError("synthetic finalization callback failure")

    def tts(**_: Any) -> Tts:
        return Tts()

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", client)
    monkeypatch.setattr(runtime_module, "QwenTtsConnection", tts)
    monkeypatch.setattr(runtime_module, "IncrementalOpusDecoder", RecoveryDecoder)
    monkeypatch.setattr(probe.runtime, "_connect_publisher", publisher)
    monkeypatch.setattr(probe.manager, "finalize_agent_speech", fail_finalize)
    await probe.runtime.start_agent(
        match_id=match_id,
        action_key="1:1",
        agent_profile_id=probe.agent_id,
        duration_ms=30_000,
        side="AFFIRMATIVE",
    )
    old = probe.runtime._runs[match_id]  # pyright: ignore[reportPrivateUsage]
    probe.run = old
    async with asyncio.timeout(3):
        while probe.actor.state.status != "ERROR":
            await asyncio.sleep(0.005)
    assert callbacks == 2 and probe.actor.state.error_code == "agent_finalization_failed"
    assert probe.actor.state.current_speech_id == old.speech_id
    async with probe.factory() as session:
        generation = await session.get(AgentGeneration, old.generation_id)
        speech = await session.get(Speech, old.speech_id)
        assert generation is not None and generation.status == "FINALIZED"
        assert speech is not None and speech.status == "FINALIZING"
        assert generation.attempt_no == 2 and speech.attempt_no == 1
    monkeypatch.setattr(probe.manager, "finalize_agent_speech", original_finalize)
    await resume(probe)

    def finished() -> bool:
        return probe.actor.state.status == "FINISHED"

    async with asyncio.timeout(4.5):
        while not finished():
            await asyncio.sleep(0.005)
    async with probe.factory() as session:
        finalized = list(
            (
                await session.scalars(
                    select(Speech).where(Speech.match_id == match_id, Speech.status == "FINALIZED")
                )
            ).all()
        )
    assert len(finalized) == 1 and finalized[0].id != old.speech_id

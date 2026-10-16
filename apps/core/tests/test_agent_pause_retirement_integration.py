"""Actual Agent/Manager/Actor/PG pause retirement, with bounded synthetic media."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

import jx_core.agent.runtime as runtime_module
from jx_core.agent.llm import LlmStreamResult
from jx_core.agent.tts import TtsStreamResult
from jx_core.matches.domain import MatchAction, MatchCommand
from jx_core.matches.service import state_snapshot
from jx_core.models import AgentAudioAsset, AgentGeneration, Match, MatchParticipant, Speech
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

pytestmark = pytest.mark.integration
# Only provider/codec/RTC boundaries are synthetic; persistence and timers are original.
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
@pytest.mark.parametrize("held_finalizer", [False, True], ids=["speaking", "finalizing"])
async def test_normal_pause_retires_old_speech_and_preserves_finalizer_owner(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    held_finalizer: bool,
) -> None:
    probe = agent_fault_probe
    provider_release = asyncio.Event()
    finalizer_entered, finalizer_release = asyncio.Event(), asyncio.Event()
    sources: list[RecoverySource] = []
    original_persist = probe.runtime._persist_final

    async def stream_chat(**values: Any) -> LlmStreamResult:
        await values["on_delta"]("合成普通暂停恢复测试。")
        await provider_release.wait()
        return LlmStreamResult("合成普通暂停恢复测试。", 1, 2, 8)

    async def close() -> None:
        return None

    class TTS:
        def __init__(self, **_: Any) -> None:
            pass

        async def synthesize(self, chunks: Any, **values: Any) -> TtsStreamResult:
            async for _ in chunks:
                await values["on_audio"](b"owned-synthetic-audio")
            await asyncio.sleep(0.02)
            return TtsStreamResult(uuid4(), 21, 1, 2)

        async def close(self) -> None:
            return None

    async def publisher(*_: Any, **__: Any) -> Any:
        source = RecoverySource()
        source.allow_drain.set()
        sources.append(source)
        return SimpleNamespace(disconnect=close), source

    async def persist(run: Any) -> None:
        if held_finalizer and not finalizer_release.is_set():
            finalizer_entered.set()
            await finalizer_release.wait()
        await original_persist(run)

    def client(**_: Any) -> SimpleNamespace:
        return SimpleNamespace(stream_chat=stream_chat, close=close)

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", client)
    monkeypatch.setattr(runtime_module, "QwenTtsConnection", TTS)
    monkeypatch.setattr(runtime_module, "IncrementalOpusDecoder", RecoveryDecoder)
    monkeypatch.setattr(probe.runtime, "_connect_publisher", publisher)
    monkeypatch.setattr(probe.runtime, "_persist_final", persist)
    match_id = probe.actor.state.match_id
    await probe.runtime.start_agent(
        match_id=match_id,
        action_key="1:1",
        agent_profile_id=probe.agent_id,
        duration_ms=30_000,
        side="AFFIRMATIVE",
    )
    await wait_for_condition(lambda: probe.actor.state.action_state == "AGENT_SPEAKING")
    # The authoritative start callback commits before capture_frame is awaited.
    # This case pauses actual playback, so observe its first captured PCM too.
    await wait_for_condition(lambda: bool(sources and sources[0].frames))
    old_run = probe.runtime._runs[match_id]
    old_envelope = probe.runtime._run_envelope(old_run)
    assert old_run.speech_id is not None and old_run.generation_id is not None
    assert sources[0].frames
    if held_finalizer:
        provider_release.set()
        await asyncio.wait_for(finalizer_entered.wait(), 1)
        assert old_run.finalizing and old_run.storage_path is not None
        assert old_run.storage_path.exists()
    async with probe.factory() as session:
        participant = await session.scalar(
            select(MatchParticipant.user_id)
            .where(MatchParticipant.match_id == match_id, MatchParticipant.kind == "HUMAN")
            .limit(1)
        )
    assert participant is not None
    await probe.manager.submit(
        match_id,
        MatchCommand(
            type="match.pause",
            message_id=str(uuid4()),
            actor_user_id=participant,
            payload={"authorized": True},
        ),
    )
    async with probe.factory() as session:
        old_speech = await session.get(Speech, old_run.speech_id)
        assert old_speech is not None and old_speech.status == "RESET"
        assert old_speech.generation_id == old_run.generation_id and old_speech.ended_at is not None
    if held_finalizer:
        assert old_run.storage_path is not None and old_run.storage_path.exists()
        finalizer_release.set()
    await wait_for_condition(lambda: not probe.manager._background_tasks)
    if held_finalizer:
        async with probe.factory() as session:
            generation = await session.get(AgentGeneration, old_run.generation_id)
            asset = await session.scalar(
                select(AgentAudioAsset).where(
                    AgentAudioAsset.generation_id == old_run.generation_id
                )
            )
            assert generation is not None and generation.status == "FINALIZED"
            assert generation.error_code is None
            assert asset is not None and asset.status == "FINALIZED"
        provider_release = asyncio.Event()
    started = asyncio.get_running_loop().time()
    await probe.manager.submit(
        match_id,
        MatchCommand(
            type="match.resume",
            message_id=str(uuid4()),
            actor_user_id=participant,
            payload={"authorized": True, "reasons": []},
        ),
    )
    await wait_for_condition(
        lambda: probe.actor.state.action_state == "AGENT_SPEAKING", timeout=4.5
    )
    assert asyncio.get_running_loop().time() - started >= 2.9
    new_run = probe.run = probe.runtime._runs[match_id]
    assert new_run.speech_id != old_run.speech_id and new_run.generation_id != old_run.generation_id
    await probe.manager.publish_agent_subtitle(envelope=old_envelope, text="旧回调", played_ms=1)
    assert probe.actor.state.current_speech_id == new_run.speech_id
    with pytest.raises(asyncio.CancelledError):
        await probe.runtime._mark_generation_llm_ready(
            old_run.generation_id, LlmStreamResult("迟到结果", 1, 2, 8), None
        )
    provider_release.set()
    finalizer_release.set()
    await wait_for_condition(lambda: probe.actor.state.status == "FINISHED", timeout=2)
    await wait_for_condition(lambda: not probe.manager._background_tasks)
    async with probe.factory() as session:
        old_speech = await session.get(Speech, old_run.speech_id)
        new_speech = await session.get(Speech, new_run.speech_id)
        assert old_speech is not None and old_speech.status == "RESET"
        assert new_speech is not None and new_speech.status == "FINALIZED"
        assert new_speech.generation_id == new_run.generation_id
        finalized = await session.get(AgentGeneration, new_run.generation_id)
        assert finalized is not None and finalized.status == "FINALIZED"
        completed_at = finalized.completed_at
    await probe.runtime._mark_generation_failed(new_run.generation_id, "agent_cancelled")
    async with probe.factory() as session:
        finalized = await session.get(AgentGeneration, new_run.generation_id)
        assert finalized is not None and finalized.status == "FINALIZED"
        assert finalized.completed_at == completed_at


@pytest.mark.asyncio
async def test_human_normal_pause_resume_keeps_same_durable_speech(
    agent_fault_probe: AgentFaultProbe,
) -> None:
    probe = agent_fault_probe
    match_id = probe.actor.state.match_id
    async with probe.factory() as session:
        participant = await session.scalar(
            select(MatchParticipant).where(
                MatchParticipant.match_id == match_id,
                MatchParticipant.kind == "HUMAN",
                MatchParticipant.side == "AFFIRMATIVE",
                MatchParticipant.seat_no == 1,
            )
        )
    assert participant is not None and participant.user_id is not None
    user_id = participant.user_id
    # Select the Human initial action before any command/Speech exists in this
    # fresh fixture. All subsequent commands and durable writes remain real.
    action = MatchAction(
        stage_position=1,
        action_position=1,
        action_kind="HUMAN_SPEECH",
        duration_seconds=30,
        side="AFFIRMATIVE",
        seat_no=1,
        speaker_user_id=user_id,
    )
    probe.actor.state = replace(
        probe.actor.state,
        actions=(action,),
        action_state="HUMAN_READY_TO_START",
        current_agent_profile_id=None,
        current_speaker_user_id=user_id,
        current_speaker_seat_no=1,
    )
    async with probe.factory() as session:
        async with session.begin():
            match = await session.get(Match, match_id, with_for_update=True)
            assert match is not None
            match.runtime_snapshot = state_snapshot(probe.actor.state)
    media = AsyncMock()

    # These Runtime projections/fences are synchronous; an untyped AsyncMock
    # invents coroutines which this real Manager correctly does not await.
    def absent_projection(_match: UUID, _speech: UUID) -> None:
        return None

    def committed_input(_match: UUID, _speech: UUID, **_kwargs: object) -> None:
        return None

    media.speech_callback_envelope = absent_projection
    media.commit_speech_start = committed_input
    media.fence_speech_input = absent_projection
    probe.manager.set_speech_runtime(media)
    await probe.manager.submit(
        match_id,
        MatchCommand(type="speech.start", message_id=str(uuid4()), actor_user_id=user_id),
    )
    speech_id = probe.actor.state.current_speech_id
    assert speech_id is not None
    await probe.manager.submit(
        match_id,
        MatchCommand(
            type="match.pause",
            message_id=str(uuid4()),
            actor_user_id=user_id,
            payload={"authorized": True},
        ),
    )
    async with probe.factory() as session:
        speech = await session.get(Speech, speech_id)
        assert speech is not None and speech.status == "STARTED" and speech.ended_at is None
    started = asyncio.get_running_loop().time()
    await probe.manager.submit(
        match_id,
        MatchCommand(
            type="match.resume",
            message_id=str(uuid4()),
            actor_user_id=user_id,
            payload={"authorized": True, "reasons": []},
        ),
    )
    await wait_for_condition(lambda: probe.actor.state.status == "RUNNING", timeout=4.5)
    assert asyncio.get_running_loop().time() - started >= 2.9
    assert probe.actor.state.current_speech_id == speech_id
    async with probe.factory() as session:
        speeches = list(
            (await session.scalars(select(Speech).where(Speech.match_id == match_id))).all()
        )
        assert len(speeches) == 1 and speeches[0].id == speech_id
        assert speeches[0].speaker_kind == "HUMAN" and speeches[0].status == "STARTED"

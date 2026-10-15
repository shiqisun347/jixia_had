"""Actual failed Agent action -> operator recovery -> finalized PostgreSQL speech.

The reused formal 4v4 fixture retains two humans and two Agents per side. Only
one Agent action is exercised, with synthetic HTTP/media; this is not a full
mixed match, real microphone test, or provider/media pressure acceptance.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from pathlib import Path
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
from jx_core.data_capture.content import load_content_blob
from jx_core.matches.domain import MatchCommand
from jx_core.models import (
    AgentAudioAsset,
    AgentGeneration,
    ExternalCall,
    Match,
    MatchEvent,
    MatchParticipant,
    Room,
    Speech,
)
from test_agent_runtime_fault_integration import AgentFaultProbe
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import (
    agent_fault_probe as agent_fault_probe,
)

pytestmark = pytest.mark.integration
# The test observes production ownership and transaction hooks, without replacing them.
# pyright: reportPrivateUsage=false


class RecoveryDecoder:
    """Replace only compressed-audio decoding with one bounded synthetic frame."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=2)
        self.written = False
        self.finished = False

    async def start(self, _: Path) -> None:
        return None

    def notify_written(self, size: int) -> None:
        if size and not self.written:
            self.written = True
            self.queue.put_nowait(b"\x00\x00" * 9_600)

    async def frames(self) -> AsyncIterator[bytes]:
        while True:
            frame = await self.queue.get()
            if frame is None:
                return
            yield frame

    def finish_input(self) -> None:
        if not self.finished:
            self.finished = True
            self.queue.put_nowait(None)

    async def close(self) -> None:
        self.finish_input()


class RecoverySource:
    """Synthetic LiveKit boundary that permits observing the speaking state."""

    def __init__(self) -> None:
        self.frames: list[object] = []
        self.draining = asyncio.Event()
        self.allow_drain = asyncio.Event()
        self.closed = False

    async def capture_frame(self, frame: object) -> None:
        self.frames.append(frame)

    async def wait_for_playout(self) -> None:
        self.draining.set()
        await self.allow_drain.wait()

    def clear_queue(self) -> None:
        return None

    async def aclose(self) -> None:
        self.closed = True
        self.allow_drain.set()


async def wait_for_condition(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "error_code"),
    [
        ("unavailable", "llm_provider_failed"),
        ("first_token", "llm_first_token_timeout"),
        ("truncated", "llm_stream_interrupted"),
    ],
)
async def test_actual_failed_agent_recovers_and_finalizes_from_authoritative_resume(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    error_code: str,
) -> None:
    probe = agent_fault_probe
    match_id = probe.actor.state.match_id
    successful_text = "这是恢复后的合成完整发言。"
    tts_text_finished = asyncio.Event()
    allow_tts_completion = asyncio.Event()
    sources: list[RecoverySource] = []
    tts_inputs: list[str] = []
    tts_successes: list[UUID] = []

    class FailedStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            if failure == "first_token":
                await asyncio.Event().wait()
            else:
                event = {"choices": [{"delta": {"content": "合成截断响应。"}}]}
                yield f"data: {json.dumps(event)}\n\n".encode()

    async def provider(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "synthetic.invalid"
        probe.provider_calls += 1
        if probe.provider_calls <= 2:
            if failure == "unavailable":
                return httpx.Response(503, json={"code": "synthetic_unavailable"})
            return httpx.Response(200, stream=FailedStream())
        event = {
            "choices": [{"delta": {"content": successful_text}}],
            "usage": {"completion_tokens": 8},
        }
        return httpx.Response(200, content=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")

    def provider_client(**arguments: Any) -> OpenAIStreamingClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        probe.http_clients.append(client)
        return OpenAIStreamingClient(**arguments, client=client, first_token_timeout_seconds=0.02)

    class RecoveryTtsConnection:
        async def synthesize(
            self,
            chunks: AsyncIterable[str],
            *,
            on_audio: Callable[[bytes], Awaitable[None]],
            **_: Any,
        ) -> TtsStreamResult:
            # Failed/truncated generations never receive queue EOF; normal
            # cancellation must retire them before recovery creates a new task.
            text_parts = [chunk async for chunk in chunks]
            tts_inputs.append("".join(text_parts))
            await on_audio(b"synthetic-compressed-audio")
            tts_text_finished.set()
            # Keep the synthetic provider alive while observing the real
            # Agent queue, callback and speaking transitions.
            await allow_tts_completion.wait()
            task_id = uuid4()
            tts_successes.append(task_id)
            return TtsStreamResult(task_id, len(b"synthetic-compressed-audio"), 1, 2)

        async def close(self) -> None:
            return None

    def tts_connection(**_: Any) -> RecoveryTtsConnection:
        return RecoveryTtsConnection()

    async def publisher(*_: Any, **__: Any) -> tuple[object, RecoverySource]:
        source = RecoverySource()
        sources.append(source)
        return SimpleNamespace(disconnect=AsyncMock()), source

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", provider_client)
    monkeypatch.setattr(runtime_module, "QwenTtsConnection", tts_connection)
    monkeypatch.setattr(runtime_module, "IncrementalOpusDecoder", RecoveryDecoder)
    monkeypatch.setattr(probe.runtime, "_connect_publisher", publisher)
    await probe.runtime.start_agent(
        match_id=match_id,
        action_key="1:1",
        agent_profile_id=probe.agent_id,
        duration_ms=30_000,
        side="AFFIRMATIVE",
    )
    failed_run = probe.runtime._runs[match_id]
    probe.run = failed_run
    assert failed_run.task is not None
    outcome = (await asyncio.wait_for(asyncio.gather(failed_run.task, return_exceptions=True), 3))[
        0
    ]
    assert outcome is None or isinstance(outcome, asyncio.CancelledError)
    assert probe.provider_calls == 2
    assert probe.actor.state.status == "ERROR"
    assert probe.actor.state.action_state == "RECOVERY_REQUIRED"
    assert match_id not in probe.runtime._runs
    async with probe.factory() as session:
        failed_generations = list(
            (
                await session.scalars(
                    select(AgentGeneration).where(AgentGeneration.match_id == match_id)
                )
            ).all()
        )
        assert len(failed_generations) == 2
        assert {row.attempt_no for row in failed_generations} == {1, 2}
        assert all(
            row.status == "FAILED" and row.error_code == error_code for row in failed_generations
        )
        failed_ids = {row.id for row in failed_generations}
        match = await session.get(Match, match_id)
        assert match is not None and match.status == "ERROR"
        room = await session.get(Room, match.room_id)
        assert room is not None
        organizer_id = room.organizer_user_id
        assert not (await session.scalars(select(Speech).where(Speech.match_id == match_id))).all()

    # This exercises the manager's trusted privileged command path and its
    # real interrupted-task reset. HTTP authorization/device validation is
    # tested elsewhere; this test does not bypass the Actor resume timer.
    before_resume = asyncio.get_running_loop().time()
    result = await probe.manager.submit(
        match_id,
        MatchCommand(
            type="match.resume",
            message_id=f"qa-resume-{uuid4()}",
            actor_user_id=organizer_id,
            payload={"privileged": True, "reasons": []},
        ),
    )
    assert result.state.status == "PAUSED"
    assert result.state.action_state == "RESUME_COUNTDOWN"
    assert probe.provider_calls == 2
    async with probe.factory() as session:
        match = await session.get(Match, match_id)
        assert match is not None and match.status == "PAUSED"
        assert match.runtime_snapshot["action_state"] == "RESUME_COUNTDOWN"
        assert match.sequence == result.state.sequence

    # Natural production 3-second elapsed event must restart the same action.
    await asyncio.wait_for(tts_text_finished.wait(), 4.5)
    assert asyncio.get_running_loop().time() - before_resume >= 2.9
    await wait_for_condition(lambda: probe.actor.state.action_state == "AGENT_SPEAKING")
    recovered_run = probe.runtime._runs[match_id]
    probe.run = recovered_run
    assert recovered_run is not failed_run
    assert recovered_run.generation_id not in failed_ids
    assert recovered_run.callback_envelope is not None
    assert recovered_run.callback_envelope.attempt_no == 1
    assert recovered_run.speech_id is not None
    assert recovered_run.task is not None
    recovered_task = recovered_run.task
    source = sources[-1]
    await wait_for_condition(lambda: len(source.frames) == 1)
    assert probe.provider_calls == 3
    assert tts_inputs == [successful_text]
    assert probe.actor.state.status == "RUNNING"
    assert probe.actor.state.current_speech_id == recovered_run.speech_id

    allow_tts_completion.set()
    await asyncio.wait_for(source.draining.wait(), 1)
    async with probe.factory() as session:
        generation = await session.get(AgentGeneration, recovered_run.generation_id)
        speech = await session.get(Speech, recovered_run.speech_id)
        assert generation is not None and generation.status == "PLAYING"
        assert speech is not None and speech.status == "STARTED"
        assert speech.generation_id == generation.id
        # Fixed actions commit the full Speech text at finalization. The
        # LLM-ready transaction already commits its authoritative response.
        assert generation.response_blob_id is not None
        assert await load_content_blob(session, generation.response_blob_id) == {
            "text": successful_text
        }
        assert recovered_run.draft == successful_text
    source.allow_drain.set()
    await wait_for_condition(lambda: probe.actor.state.status == "FINISHED", timeout=2)
    outcome = (await asyncio.wait_for(asyncio.gather(recovered_task, return_exceptions=True), 1))[0]
    assert outcome is None

    async with probe.factory() as session:
        match = await session.get(Match, match_id)
        assert match is not None
        room = await session.get(Room, match.room_id)
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
                await session.scalars(select(ExternalCall).where(ExternalCall.match_id == match_id))
            ).all()
        )
        events = list(
            (
                await session.scalars(
                    select(MatchEvent)
                    .where(MatchEvent.match_id == match_id)
                    .order_by(MatchEvent.sequence)
                )
            ).all()
        )
        asset = await session.scalar(
            select(AgentAudioAsset).where(
                AgentAudioAsset.generation_id == recovered_run.generation_id
            )
        )
        participants = list(
            (
                await session.scalars(
                    select(MatchParticipant).where(MatchParticipant.match_id == match_id)
                )
            ).all()
        )
    assert match.status == probe.actor.state.status == "FINISHED"
    assert room is not None and room.status == "FINISHED"
    assert match.ended_at is not None and room.ended_at is not None
    assert match.sequence == probe.actor.state.sequence
    assert match.runtime_snapshot["action_state"] == "MATCH_FINISHED"
    assert len(generations) == 3
    assert all(
        row.status == "FAILED" and row.error_code == error_code
        for row in generations
        if row.id in failed_ids
    ), [
        {"attempt_no": row.attempt_no, "status": row.status, "error_code": row.error_code}
        for row in generations
        if row.id in failed_ids
    ]
    finalized = [row for row in generations if row.id not in failed_ids]
    assert len(finalized) == 1 and finalized[0].status == "FINALIZED"
    assert finalized[0].error_code is None
    assert len(speeches) == 1
    speech = speeches[0]
    assert speech.status == "FINALIZED" and speech.generation_id == finalized[0].id
    assert speech.display_text == speech.llm_draft_text == successful_text
    assert speech.finalized_at is not None and speech.audio_duration_ms == 200
    assert speech.audio_truncated is False
    assert speech.audio_storage_path is not None
    assert Path(speech.audio_storage_path).read_bytes() == b"synthetic-compressed-audio"
    assert asset is not None and asset.status == "FINALIZED"
    assert asset.duration_ms == 200 and asset.pcm_sample_count == 9_600
    assert asset.task_id == str(tts_successes[0])
    assert [
        row.status
        for row in calls
        if row.agent_generation_id in failed_ids and row.call_kind == "LLM_SPEECH"
    ] == ["FAILED", "FAILED"]
    assert {
        row.call_kind
        for row in calls
        if row.agent_generation_id == finalized[0].id and row.status == "SUCCEEDED"
    } == {"LLM_SPEECH", "TTS"}
    event_types = [row.event_type for row in events]
    required = [
        "match.error",
        "match.resume_countdown",
        "match.resumed",
        "agent.preparing",
        "agent.playback_started",
        "agent.finalizing",
        "agent.finalized",
        "match.finished",
    ]
    assert all(event in event_types for event in required), event_types
    assert [event_types.index(event) for event in required] == sorted(
        event_types.index(event) for event in required
    )
    broadcast_types: list[str] = []
    while not probe.queue.empty():
        broadcast_types.append(probe.queue.get_nowait().type)
    assert all(event in broadcast_types for event in required)
    assert len(participants) == 8
    for side in ("AFFIRMATIVE", "NEGATIVE"):
        assert sum(row.side == side and row.kind == "HUMAN" for row in participants) == 2
        assert sum(row.side == side and row.kind == "AGENT" for row in participants) == 2
    lease = await probe.runtime.capacity_limiter.acquire(
        str(probe.model_id), 1, timeout_seconds=0.1
    )
    probe.runtime.capacity_limiter.release(lease)
    print(
        json.dumps(
            {
                "failure": failure,
                "provider_calls": probe.provider_calls,
                "generation_statuses": [row.status for row in generations],
                "speech_count": len(speeches),
                "final_status": match.status,
                "event_types": event_types,
            },
            sort_keys=True,
        )
    )

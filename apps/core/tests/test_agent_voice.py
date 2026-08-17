from __future__ import annotations

import asyncio
import inspect
import io
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import av
import httpx
import numpy as np
import pytest

import jx_core.agent.runtime as agent_runtime_module
import jx_core.agent.tts as tts_module
from jx_core.agent.audio import IncrementalOpusDecoder, apply_ogg_opus_gain, apply_pcm16_gain
from jx_core.agent.llm import (
    LLM_CONNECTION_TIMEOUT_SECONDS,
    LLM_FAST_DECISION_TIMEOUT_SECONDS,
    LLM_FIRST_TOKEN_TIMEOUT_SECONDS,
    LLM_IDLE_TIMEOUT_SECONDS,
    LlmProviderError,
    OpenAIStreamingClient,
)
from jx_core.agent.runtime import (
    AgentConfig,
    AgentRun,
    AgentRuntime,
    _current_and_next_stage,
    _debate_position,
    _parse_decision,
    _played_prefix,
    _stage_name_for_action,
)
from jx_core.agent.tts import QwenTtsConnection, TtsStartRateLimiter
from jx_core.config import Settings
from jx_core.matches.domain import (
    MatchActor,
    MatchCommand,
    MatchRuntimeState,
    compile_linear_actions,
)


def _rule_with_agent() -> dict[str, object]:
    return {
        "stages": [
            {
                "position": 1,
                "stage_kind": "FIXED_SPEECH",
                "actions": [
                    {
                        "position": 1,
                        "side": "AFFIRMATIVE",
                        "seat_no": 1,
                        "duration_seconds": 2,
                    }
                ],
            }
        ]
    }


def test_free_decision_requires_strict_json_and_clamps_willingness() -> None:
    assert _parse_decision('{"should_speak":true,"willingness":1.2}') == (True, 1.0)
    assert _parse_decision('```json\n{"should_speak":false,"willingness":0.2}\n```') == (
        False,
        0.2,
    )
    with pytest.raises(Exception, match="agent_decision_invalid"):
        _parse_decision('{"should_speak":"yes","willingness":0.5}')


@pytest.mark.asyncio
async def test_free_decisions_report_each_agent_independently_and_degrade_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = uuid4()
    second = uuid4()
    callbacks = AsyncMock()
    runtime = AgentRuntime(
        settings=Settings(database_url="postgresql+psycopg://test:test@localhost/test"),
        session_factory=cast(Any, None),
        callbacks=callbacks,
    )

    async def load_config(run: AgentRun) -> AgentConfig:
        return AgentConfig(
            match_id=run.match_id,
            action_key=run.action_key,
            agent_profile_id=run.agent_profile_id,
            context_version=1,
            match_seed=1,
            model_key=str(run.agent_profile_id),
            base_url="https://llm.test/v1",
            model_id=str(run.agent_profile_id),
            api_key="test",
            model_limit=2,
            generation_params={},
            max_tokens=64,
            messages=[
                {"role": "system", "content": "test"},
                {"role": "user", "content": "debate-history-context"},
            ],
            voice="test",
            rate=1.0,
            chars_per_second=4.0,
            playback_gain=1.0,
        )

    attempts: dict[str, int] = {}
    request_messages: dict[str, list[dict[str, str]]] = {}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            self.model = str(kwargs["model"])

        async def stream_chat(self, **kwargs: Any):
            attempts[self.model] = attempts.get(self.model, 0) + 1
            request_messages[self.model] = kwargs["messages"]
            if self.model == str(second):
                raise LlmProviderError("llm_first_token_timeout")
            await asyncio.sleep(0)
            return type(
                "Result",
                (),
                {"text": '{"should_speak":true,"willingness":0.86}'},
            )()

        async def close(self) -> None:
            return None

    monkeypatch.setattr(runtime, "_load_config", load_config)
    monkeypatch.setattr(agent_runtime_module, "OpenAIStreamingClient", FakeClient)
    start_capture = AsyncMock(side_effect=[uuid4(), uuid4(), uuid4()])
    finish_capture = AsyncMock()
    fail_capture = AsyncMock()
    monkeypatch.setattr(runtime, "_start_decision_call", start_capture)
    monkeypatch.setattr(runtime, "_finish_external_call", finish_capture)
    monkeypatch.setattr(runtime, "_fail_external_call", fail_capture)
    round_id = uuid4()
    await runtime.decide_free_debate(
        match_id=uuid4(),
        action_key="7:0",
        side="AFFIRMATIVE",
        agent_profile_ids=[first, second],
        decision_round_id=round_id,
    )

    assert callbacks.report_free_decision.await_count == 2
    reported = {
        call.kwargs["agent_profile_id"]: call.kwargs
        for call in callbacks.report_free_decision.await_args_list
    }
    assert reported[first]["should_speak"] is True
    assert reported[first]["willingness"] == 0.86
    assert reported[first]["failed"] is False
    assert reported[second]["should_speak"] is None
    assert reported[second]["failed"] is True
    assert reported[second]["attempt_no"] == 2
    assert attempts[str(first)] == 1
    assert attempts[str(second)] == 2
    assert start_capture.await_count == 3
    assert finish_capture.await_count == 1
    assert fail_capture.await_count == 2
    assert request_messages[str(first)][1]["content"] == "debate-history-context"
    assert "自由辩论快速决策" in request_messages[str(first)][2]["content"]


def test_llm_stream_failure_budgets_are_ten_seconds() -> None:
    parameters = inspect.signature(OpenAIStreamingClient).parameters
    assert LLM_FIRST_TOKEN_TIMEOUT_SECONDS == 10.0
    assert LLM_IDLE_TIMEOUT_SECONDS == 10.0
    assert LLM_CONNECTION_TIMEOUT_SECONDS == 10.0
    assert LLM_FAST_DECISION_TIMEOUT_SECONDS == 3.0
    assert parameters["connection_timeout_seconds"].default == 10.0
    assert parameters["first_token_timeout_seconds"].default == 10.0
    assert parameters["idle_timeout_seconds"].default == 10.0


def test_llm_connection_timeout_is_configured_separately_from_stream_idle_timeout() -> None:
    timeout = httpx.Timeout(None, connect=LLM_CONNECTION_TIMEOUT_SECONDS)
    assert timeout.connect == 10.0
    assert timeout.read is None
    fast_timeout = httpx.Timeout(None, connect=LLM_FAST_DECISION_TIMEOUT_SECONDS)
    assert fast_timeout.connect == 3.0


def test_fixed_pcm_gain_is_peak_guarded_and_keeps_frame_length() -> None:
    pcm = np.array([1000, -1000, 32767, -32768], dtype=np.int16).tobytes()
    output = apply_pcm16_gain(pcm, 1.8)
    values = np.frombuffer(output, dtype=np.int16)
    assert len(output) == len(pcm)
    assert int(np.max(np.abs(values))) <= int(32767 * 0.97) + 1
    assert apply_pcm16_gain(pcm, 1.0) is pcm


def test_agent_subtitle_leads_buffered_audio_without_exceeding_draft() -> None:
    draft = "一二三四五六七八九十"
    assert _played_prefix(draft, played_ms=700, chars_per_second=5.0) == "一二三四五"
    assert _played_prefix(draft, played_ms=10_000, chars_per_second=5.0) == draft


def test_agent_prompt_context_has_readable_stage_position_and_next_stage() -> None:
    rule = {
        "stages": [
            {
                "position": 1,
                "name": "正方一辩立论",
                "actions": [{"position": 1, "side": "AFFIRMATIVE", "seat_no": 1}],
            },
            {"position": 2, "name": "反方一辩立论", "actions": []},
        ]
    }
    assert _stage_name_for_action("1:1", rule) == "正方一辩立论"
    assert _current_and_next_stage("1:1", rule) == ("正方一辩立论", "反方一辩立论")
    assert _debate_position("1:1", rule) == "正方1辩"


def _decode_ogg_pcm(encoded: bytes) -> np.ndarray[Any, np.dtype[np.int16]]:
    samples: list[np.ndarray[Any, np.dtype[np.int16]]] = []
    with av.open(io.BytesIO(encoded), mode="r", format="ogg") as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=48_000)
        for frame in container.decode(container.streams.audio[0]):
            for converted in resampler.resample(frame):
                values = converted.to_ndarray()
                samples.append(
                    np.asarray(values[0] if values.ndim == 2 else values, dtype=np.int16)
                )
        for converted in resampler.resample(None):
            values = converted.to_ndarray()
            samples.append(np.asarray(values[0] if values.ndim == 2 else values, dtype=np.int16))
    return np.concatenate(samples)


def test_offline_voice_preview_gain_matches_runtime_semantics(tmp_path: Path) -> None:
    source_path = tmp_path / "gain-source.ogg"
    with av.open(str(source_path), mode="w", format="ogg") as container:
        stream = container.add_stream("libopus", rate=48_000)
        stream.layout = "mono"
        for phase in range(8):
            values = (np.sin(np.linspace(phase, phase + np.pi * 2, 960)) * 4_000).astype(np.int16)
            frame = av.AudioFrame(format="s16", layout="mono", samples=960)
            frame.sample_rate = 48_000
            frame.planes[0].update(values.tobytes())
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    source = source_path.read_bytes()
    original = _decode_ogg_pcm(source).astype(np.float64)
    louder_encoded = apply_ogg_opus_gain(source, 1.5)
    quieter_encoded = apply_ogg_opus_gain(source, 0.6)
    with av.open(io.BytesIO(louder_encoded), mode="r", format="ogg") as container:
        codec = container.streams.audio[0].codec_context
        assert codec.sample_rate == 48_000
        assert codec.layout.name == "mono"
    louder = _decode_ogg_pcm(louder_encoded).astype(np.float64)
    quieter = _decode_ogg_pcm(quieter_encoded).astype(np.float64)
    common = min(len(original), len(louder), len(quieter))
    original_rms = float(np.sqrt(np.mean(np.square(original[:common]))))
    louder_rms = float(np.sqrt(np.mean(np.square(louder[:common]))))
    quieter_rms = float(np.sqrt(np.mean(np.square(quieter[:common]))))
    assert louder_rms > original_rms * 1.25
    assert quieter_rms < original_rms * 0.8
    assert np.max(np.abs(louder[:common])) <= 32767
    assert apply_ogg_opus_gain(source, 1.0) is source


@pytest.mark.asyncio
async def test_agent_action_starts_timer_only_after_first_pcm() -> None:
    match_id = uuid4()
    agent_id = uuid4()
    actions = compile_linear_actions(_rule_with_agent(), {}, {("AFFIRMATIVE", 1): agent_id})
    actor = MatchActor(
        MatchRuntimeState(
            match_id=match_id,
            status="START_PENDING_RUNTIME",
            action_state="NOT_STARTED",
            actions=actions,
        )
    )
    await actor.start()
    await actor.submit(MatchCommand(type="runtime.start", message_id="start"))
    ready = await actor.submit(MatchCommand(type="countdown.elapsed", message_id="countdown"))
    assert ready.state.action_state == "AGENT_PREPARING"
    assert ready.state.speech_remaining_ms == 2000
    speech_id = uuid4()
    started = await actor.submit(
        MatchCommand(
            type="agent.playback_started",
            message_id="play",
            payload={
                "speech_id": str(speech_id),
                "generation_id": str(uuid4()),
                "agent_profile_id": str(agent_id),
            },
        )
    )
    assert started.state.action_state == "AGENT_SPEAKING"
    assert started.state.current_speech_id == speech_id
    finished = await actor.submit(
        MatchCommand(
            type="agent.playback_finished",
            message_id="finished",
            payload={"speech_id": str(speech_id)},
        )
    )
    assert finished.state.action_state == "AGENT_FINALIZING"
    await actor.submit(
        MatchCommand(
            type="agent.finalized",
            message_id="finalized",
            payload={"speech_id": str(speech_id), "final_text": "完成"},
        )
    )
    await actor.close()


@pytest.mark.asyncio
async def test_openai_sse_stream_returns_text_and_usage() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = 'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'.encode()
        body += b"data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAIStreamingClient(
        base_url="https://llm.test/v1", api_key="test", model="demo", client=client
    )
    chunks: list[str] = []
    result = await adapter.stream_chat(
        messages=[{"role": "user", "content": "x"}],
        max_tokens=32,
        generation_params={},
        on_delta=lambda value: _append(chunks, value),
    )
    assert result.text == "你好"
    assert chunks == ["你好"]
    await adapter.close()


class _DelayedSseStream(httpx.AsyncByteStream):
    def __init__(self, events: list[tuple[float, bytes]]) -> None:
        self._events = events

    async def __aiter__(self):
        for delay, payload in self._events:
            await asyncio.sleep(delay)
            yield payload

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("events", "first_timeout", "idle_timeout", "expected_code"),
    [
        (
            [(0.05, b'data: {"choices":[{"delta":{"content":"late"}}]}\n\n')],
            0.01,
            1.0,
            "llm_first_token_timeout",
        ),
        (
            [
                (0.0, b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'),
                (0.05, b'data: {"choices":[{"delta":{"content":"late"}}]}\n\n'),
            ],
            1.0,
            0.01,
            "llm_stream_stalled",
        ),
    ],
)
async def test_openai_sse_stream_maps_first_and_idle_timeouts(
    events: list[tuple[float, bytes]],
    first_timeout: float,
    idle_timeout: float,
    expected_code: str,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_DelayedSseStream(events),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAIStreamingClient(
        base_url="https://llm.test/v1",
        api_key="test",
        model="demo",
        client=http_client,
        first_token_timeout_seconds=first_timeout,
        idle_timeout_seconds=idle_timeout,
    )
    with pytest.raises(LlmProviderError) as caught:
        await adapter.stream_chat(
            messages=[{"role": "user", "content": "x"}],
            max_tokens=32,
            generation_params={},
            on_delta=lambda value: _append([], value),
        )
    assert caught.value.code == expected_code
    await http_client.aclose()


async def _append(target: list[str], value: str) -> None:
    target.append(value)


class _FakeSocket:
    def __init__(self, events: list[str | bytes]) -> None:
        self.events = asyncio.Queue[str | bytes]()
        for event in events:
            self.events.put_nowait(event)
        self.sent: list[str | bytes] = []

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        return await self.events.get()

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_tts_duplex_adapter_writes_binary_audio_and_reuses_socket() -> None:
    task_id = uuid4()
    socket = _FakeSocket(
        [
            json.dumps({"header": {"task_id": str(task_id), "event": "task-started"}}),
            b"opus-audio",
            json.dumps({"header": {"task_id": str(task_id), "event": "task-finished"}}),
        ]
    )

    async def factory(_: str, __: dict[str, str]) -> _FakeSocket:
        return socket

    adapter = QwenTtsConnection(
        url="wss://tts.test", api_key="test", socket_factory=factory, timeout_seconds=1
    )
    received: list[bytes] = []

    async def text_chunks():
        yield "你好"

    result = await adapter.synthesize(
        text_chunks(),
        voice="voice",
        rate=1.0,
        on_audio=lambda value: _append_bytes(received, value),
        task_id=task_id,
    )
    assert result.byte_count == len(b"opus-audio")
    assert received == [b"opus-audio"]
    assert len(socket.sent) == 3
    run_task = json.loads(str(socket.sent[0]))
    assert run_task["payload"]["parameters"]["bit_rate"] == 32


async def _append_bytes(target: list[bytes], value: bytes) -> None:
    target.append(value)


@pytest.mark.asyncio
async def test_tts_start_rate_limiter_spaces_task_submissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]

    async def advance(delay: float) -> None:
        clock[0] += delay

    monkeypatch.setattr(tts_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(tts_module.asyncio, "sleep", advance)
    limiter = TtsStartRateLimiter(interval_seconds=0.35)
    await limiter.wait()
    await limiter.wait()
    await limiter.wait()
    assert clock[0] == pytest.approx(0.7)


def _make_ogg(path: Path) -> bytes:
    with av.open(str(path), mode="w", format="ogg") as container:
        stream = container.add_stream("libopus", rate=48_000)
        stream.layout = "mono"
        for _ in range(10):
            frame = av.AudioFrame(format="s16", layout="mono", samples=960)
            frame.sample_rate = 48_000
            frame.planes[0].update(np.zeros(960, dtype=np.int16).tobytes())
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return path.read_bytes()


@pytest.mark.asyncio
async def test_incremental_ogg_decoder_outputs_48k_mono_frames(tmp_path: Path) -> None:
    encoded = _make_ogg(tmp_path / "fixture.ogg")
    spool = tmp_path / "stream.ogg"
    spool.write_bytes(b"")
    decoder = IncrementalOpusDecoder()
    await decoder.start(spool)
    for offset in range(0, len(encoded), 37):
        chunk = encoded[offset : offset + 37]
        with spool.open("ab") as output:
            output.write(chunk)
        decoder.notify_written(offset + len(chunk))
        await asyncio.sleep(0)
    decoder.finish_input()
    frames = [frame async for frame in decoder.frames()]
    await decoder.wait_done()
    assert frames
    assert all(len(frame) % 2 == 0 for frame in frames)
    assert sum(len(frame) for frame in frames) > 0


@pytest.mark.asyncio
async def test_truncated_agent_audio_keeps_full_llm_draft_as_official_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    callbacks = AsyncMock()
    runtime = AgentRuntime(
        settings=Settings(database_url="postgresql+psycopg://test:test@localhost/test"),
        session_factory=cast(Any, None),
        callbacks=callbacks,
    )
    match_id = uuid4()
    speech_id = uuid4()
    generation_id = uuid4()
    spool_path = tmp_path / "speech.spool.ogg"
    storage_path = tmp_path / "speech.ogg"
    spool_path.write_bytes(b"audio")
    runtime._runs[match_id] = AgentRun(
        match_id=match_id,
        action_key="1:0",
        agent_profile_id=uuid4(),
        duration_ms=5_000,
        generation_id=generation_id,
        speech_id=speech_id,
        draft_parts=["第一句完整论点。", "第二句完整结论。"],
        spool_path=spool_path,
        storage_path=storage_path,
        played_samples=48_000,
        natural_complete=False,
    )
    monkeypatch.setattr(runtime, "_persist_final", AsyncMock())

    await runtime.finalize_agent(match_id, speech_id, "TIME_LIMIT")

    callbacks.finalize_agent_speech.assert_awaited_once()
    payload = callbacks.finalize_agent_speech.await_args.kwargs
    assert payload["final_text"] == "第一句完整论点。第二句完整结论。"
    assert payload["llm_draft_text"] == payload["final_text"]
    assert payload["audio_truncated"] is True


@pytest.mark.asyncio
async def test_truncated_agent_finalization_is_bounded_when_pipeline_cleanup_stalls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    callbacks = AsyncMock()
    runtime = AgentRuntime(
        settings=Settings(database_url="postgresql+psycopg://test:test@localhost/test"),
        session_factory=cast(Any, None),
        callbacks=callbacks,
    )
    match_id = uuid4()
    speech_id = uuid4()
    generation_id = uuid4()
    release_cleanup = asyncio.Event()

    async def stalled_cleanup() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release_cleanup.wait()

    pipeline = asyncio.create_task(stalled_cleanup(), name="stalled-agent-pipeline")
    await asyncio.sleep(0)
    spool_path = tmp_path / "speech.spool.ogg"
    storage_path = tmp_path / "speech.ogg"
    spool_path.write_bytes(b"audio")
    runtime._runs[match_id] = AgentRun(
        match_id=match_id,
        action_key="1:0",
        agent_profile_id=uuid4(),
        duration_ms=5_000,
        task=pipeline,
        generation_id=generation_id,
        speech_id=speech_id,
        draft_parts=["完整正式文字"],
        spool_path=spool_path,
        storage_path=storage_path,
        played_samples=48_000,
        natural_complete=False,
    )
    monkeypatch.setattr(agent_runtime_module, "AGENT_TASK_CANCEL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(runtime, "_persist_final", AsyncMock())

    await asyncio.wait_for(runtime.finalize_agent(match_id, speech_id, "TIME_LIMIT"), timeout=0.2)

    callbacks.finalize_agent_speech.assert_awaited_once()
    assert match_id not in runtime._runs
    assert storage_path.read_bytes() == b"audio"
    release_cleanup.set()
    await pipeline

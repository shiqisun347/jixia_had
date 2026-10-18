"""Spec305 actual Actor/Manager/PG controls; provider, codec and RTC are synthetic."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from starlette.requests import Request

import jx_core.agent.runtime as runtime_module
from jx_core.admin_routes import MatchControlRequest, control_match, match_control_target
from jx_core.agent.llm import LlmStreamResult
from jx_core.agent.runtime import AgentRun
from jx_core.agent.tts import TtsStreamResult
from jx_core.asr.protocol import FunAsrConnection
from jx_core.asr.runtime import AsrRuntime, MatchAudioReceiver
from jx_core.matches.domain import MatchCommand, MatchDomainError
from jx_core.models import (
    AgentAudioAsset,
    AgentGeneration,
    Match,
    MatchEvent,
    MatchParticipant,
    Speech,
)
from test_agent_failure_recovery_integration import RecoveryDecoder, wait_for_condition
from test_agent_runtime_fault_integration import AgentFaultProbe
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe
from test_asr import FakeSocket
from test_asr_queue_recovery import OwnedStream
from test_match_handoff_recovery_integration import (
    admin_auth,  # pyright: ignore[reportUnknownVariableType]
    finalize,  # pyright: ignore[reportUnknownVariableType]
    prepare,
    start_human,  # pyright: ignore[reportUnknownVariableType]
)

pytestmark = pytest.mark.integration
# The real runtime callbacks and transactional Actor hooks are never substituted.
# pyright: reportPrivateUsage=false

TEXT = "自然结束的正文和最后几个字必须完整保留。"


@dataclass
class TailSource:
    probe: AgentFaultProbe
    boundary: str
    frames: list[int] = field(default_factory=lambda: list[int]())
    tail_entered: asyncio.Event = field(default_factory=asyncio.Event)
    release_tail: asyncio.Event = field(default_factory=asyncio.Event)
    closed: bool = False
    drain_calls: int = 0
    clear_calls: int = 0
    state_at_tail: tuple[str, str, float | None] | None = None

    async def capture_frame(self, frame: Any) -> None:
        samples = int(frame.samples_per_channel)
        if samples == 960:
            state = self.probe.actor.state
            self.state_at_tail = (state.status, state.action_state, state.speech_deadline_mono)
            self.tail_entered.set()
            if self.boundary == "capture_fail":
                raise RuntimeError("owned_tail_capture_failure")
            if self.boundary in {"capture", "timeout"}:
                await self.release_tail.wait()
        self.frames.append(samples)

    async def wait_for_playout(self) -> None:
        self.drain_calls += 1
        if self.drain_calls == 2:
            self.tail_entered.set()
            if self.boundary == "drain":
                await self.release_tail.wait()

    def clear_queue(self) -> None:
        self.clear_calls += 1

    async def aclose(self) -> None:
        self.closed = True
        self.release_tail.set()


@dataclass
class TailEdges:
    sources: list[TailSource] = field(default_factory=lambda: list[TailSource]())
    provider_calls: int = 0
    decision_should_speak: bool = False


def install_edges(
    probe: AgentFaultProbe, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> TailEdges:
    edges = TailEdges()

    async def stream_chat(**values: Any) -> LlmStreamResult:
        if "自由辩论快速决策" in values["messages"][-1]["content"]:
            value = "true" if edges.decision_should_speak else "false"
            return LlmStreamResult(
                '{"should_speak":' + value + ',"decision_reason":"受控候选"}', 1, 2, 8
            )
        edges.provider_calls += 1
        await values["on_delta"](TEXT)
        await asyncio.sleep(0.02)
        return LlmStreamResult(TEXT, 1, 2, 20)

    async def close() -> None:
        return None

    class TTS:
        def __init__(self, **_: Any) -> None:
            pass

        async def synthesize(self, chunks: Any, **values: Any) -> TtsStreamResult:
            async for _ in chunks:
                await values["on_audio"](b"owned-compressed-body")
            await asyncio.sleep(0.02)
            return TtsStreamResult(uuid4(), 21, 1, 2)

        async def close(self) -> None:
            return None

    async def publisher(*_: Any, **__: Any) -> Any:
        source = TailSource(probe, boundary if not edges.sources else "unheld")
        edges.sources.append(source)
        return SimpleNamespace(disconnect=close), source

    def client(**_: Any) -> SimpleNamespace:
        return SimpleNamespace(stream_chat=stream_chat, close=close)

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", client)
    monkeypatch.setattr(runtime_module, "QwenTtsConnection", TTS)
    monkeypatch.setattr(runtime_module, "IncrementalOpusDecoder", RecoveryDecoder)
    monkeypatch.setattr(probe.runtime, "_connect_publisher", publisher)
    return edges


async def start(probe: AgentFaultProbe) -> AgentRun:
    action = probe.actor.state.current_action
    assert action is not None and action.agent_profile_id is not None
    await probe.runtime.start_agent(
        match_id=probe.actor.state.match_id,
        action_key=action.action_key,
        agent_profile_id=action.agent_profile_id,
        duration_ms=action.duration_seconds * 1000,
        side=action.side,
    )
    probe.run = probe.runtime._runs[probe.actor.state.match_id]
    return probe.run


async def assert_finalized_body(probe: AgentFaultProbe, generation_id: UUID) -> None:
    async with probe.factory() as session:
        speech = await session.scalar(select(Speech).where(Speech.generation_id == generation_id))
        asset = await session.scalar(
            select(AgentAudioAsset).where(AgentAudioAsset.generation_id == generation_id)
        )
        generation = await session.get(AgentGeneration, generation_id)
        assert speech is not None and asset is not None and generation is not None
        assert speech.status == generation.status == asset.status == "FINALIZED"
        assert speech.display_text == speech.llm_draft_text == TEXT
        assert speech.audio_duration_ms == asset.duration_ms == 200
        assert asset.pcm_sample_count == 9600
        assert not speech.audio_truncated


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["capture", "drain"])
@pytest.mark.parametrize("side", ["AFFIRMATIVE", "NEGATIVE"])
async def test_natural_tail_freezes_budget_and_hands_owner_to_finalizer_without_counting_silence(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    side: str,
) -> None:
    probe = agent_fault_probe
    action = probe.actor.state.current_action
    assert action is not None
    async with probe.factory() as session:
        profile_id = await session.scalar(
            select(MatchParticipant.agent_profile_id).where(
                MatchParticipant.match_id == probe.actor.state.match_id,
                MatchParticipant.side == side,
                MatchParticipant.seat_no == 3,
            )
        )
    assert profile_id is not None
    probe.actor.state = replace(
        probe.actor.state,
        actions=(replace(action, side=side, agent_profile_id=profile_id),),
        current_agent_profile_id=profile_id,
        current_speaker_side=side,
    )
    edges = install_edges(probe, monkeypatch, boundary)
    run = await start(probe)
    try:
        await wait_for_condition(lambda: bool(edges.sources), timeout=2)
        source = edges.sources[0]
        await asyncio.wait_for(source.tail_entered.wait(), 2)
        assert run.task is not None
        await asyncio.wait_for(asyncio.shield(run.task), 2)
        assert probe.runtime._runs.get(run.match_id) is run
        assert run.finalizing and run.natural_complete and not run.media_fenced
        assert probe.actor.state.action_state == "AGENT_FINALIZING"
        assert probe.actor.state.speech_deadline_mono is None
        assert probe.actor.view().speech_remaining_ms is None
        assert run.played_samples == 9600 and run.draft == TEXT
        assert not source.closed and source.frames.count(9600) == 1
        assert edges.provider_calls == 1
        async with probe.factory() as session:
            match = await session.get(Match, run.match_id)
            speech = await session.get(Speech, run.speech_id)
            events = list(
                (
                    await session.scalars(
                        select(MatchEvent).where(MatchEvent.match_id == run.match_id)
                    )
                ).all()
            )
            assert match is not None and match.status == "RUNNING" and match.context_version == 0
            assert speech is not None and speech.status == "FINALIZING"
            assert not any(
                event.event_type in {"agent.finalized", "match.finished"} for event in events
            )
        source.release_tail.set()
        await wait_for_condition(lambda: probe.actor.state.status == "FINISHED", timeout=3)
        await wait_for_condition(lambda: source.closed, timeout=2)
        assert source.frames == [9600, *([960] * 30)]
        assert source.drain_calls == 2 and run.played_samples == 9600
        assert run.match_id not in probe.runtime._runs
        assert run.generation_id is not None
        await assert_finalized_body(probe, run.generation_id)
    finally:
        for source in edges.sources:
            source.release_tail.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["pause", "reset", "terminate"])
async def test_authority_retires_held_natural_tail_without_late_finalization(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    probe = agent_fault_probe
    edges = install_edges(probe, monkeypatch, "drain")
    run = await start(probe)
    try:
        await wait_for_condition(lambda: bool(edges.sources), timeout=2)
        source = edges.sources[0]
        await asyncio.wait_for(source.tail_entered.wait(), 2)
        assert source.drain_calls == 2 and run.task is not None
        await asyncio.wait_for(asyncio.shield(run.task), 2)
        assert probe.runtime._runs.get(run.match_id) is run
        request = Request(
            {
                "type": "http",
                "app": SimpleNamespace(state=SimpleNamespace(match_runtime_manager=probe.manager)),
            }
        )
        auth = await admin_auth(probe)
        if operation == "reset":
            async with probe.factory() as session:
                target = await match_control_target(run.match_id, request, auth, session)
                await control_match(
                    run.match_id,
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
        elif operation == "terminate":
            await probe.manager.submit(
                run.match_id,
                MatchCommand(
                    type="match.terminate",
                    message_id=str(uuid4()),
                    payload={"privileged": True},
                ),
            )
        else:
            await probe.manager.submit(
                run.match_id,
                MatchCommand(
                    type="match.pause",
                    message_id=str(uuid4()),
                    actor_user_id=auth.user_id,
                    payload={"authorized": True},
                ),
            )
        assert probe.actor.state.status in {"PAUSED", "TERMINATED"}
        before = probe.actor.state.sequence
        source.release_tail.set()
        await wait_for_condition(lambda: source.closed, timeout=2)
        await asyncio.sleep(0.05)
        assert probe.actor.state.sequence == before
        assert edges.provider_calls == 1 and source.frames == [9600, *([960] * 30)]
        async with probe.factory() as session:
            speech = await session.get(Speech, run.speech_id)
            match = await session.get(Match, run.match_id)
            assert speech is not None and speech.status != "FINALIZED"
            assert match is not None
            context_at_retirement = match.context_version
            assert context_at_retirement == (1 if operation == "reset" else 0)
        if operation != "terminate":
            # Authority, not a manufactured callback, starts the replacement.
            async with probe.factory() as session:
                await control_match(
                    run.match_id, MatchControlRequest(action="resume"), request, auth, session
                )
            assert probe.actor.state.action_state == "RESUME_COUNTDOWN"
            await wait_for_condition(lambda: probe.actor.state.status == "FINISHED", timeout=5)
            assert len(edges.sources) == 2 and edges.provider_calls == 2
            assert edges.sources[1].frames == [9600, *([960] * 30)]
            assert edges.sources[0].frames == [9600, *([960] * 30)]
            assert run.match_id not in probe.runtime._runs
            async with probe.factory() as session:
                match = await session.get(Match, run.match_id)
                old = await session.get(Speech, run.speech_id)
                new = await session.scalar(
                    select(Speech).where(
                        Speech.match_id == run.match_id, Speech.status == "FINALIZED"
                    )
                )
                assert match is not None and match.context_version == context_at_retirement + 1
                assert old is not None and old.status == "RESET"
                assert new is not None and new.generation_id != run.generation_id
                assert new.generation_id is not None
                await assert_finalized_body(probe, new.generation_id)
    finally:
        for source in edges.sources:
            source.release_tail.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("next_kind", ["HUMAN", "AGENT"])
async def test_free_tail_crosses_selection_window_preserving_final_source_and_next_body_gate(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    next_kind: str,
) -> None:
    probe = agent_fault_probe
    edges = install_edges(probe, monkeypatch, "drain")
    edges.decision_should_speak = next_kind == "AGENT"
    now, first, _, envelopes = await prepare(probe)
    match_id = probe.actor.state.match_id
    async with probe.factory() as session:
        agent_id = await session.scalar(
            select(MatchParticipant.agent_profile_id).where(
                MatchParticipant.match_id == match_id,
                MatchParticipant.side == "NEGATIVE",
                MatchParticipant.seat_no == 3,
            )
        )
    assert agent_id is not None
    source_id = cast(UUID, await start_human(probe, first))
    state = probe.actor.state
    assert state.current_action is not None
    await probe.manager.submit(
        match_id,
        MatchCommand(
            type="free.agent_decision_start",
            message_id=str(uuid4()),
            payload={
                "opportunity_id": str(state.opportunity_id),
                "opportunity_generation": state.opportunity_generation,
                "source_speech_id": str(source_id),
                "trigger_kind": "HUMAN_SPEECH",
            },
        ),
    )
    await probe.manager.submit(
        match_id,
        MatchCommand(
            type="free.agent_decision_result",
            message_id=str(uuid4()),
            payload={
                "action_key": state.current_action.action_key,
                "agent_profile_id": str(agent_id),
                "decision_round_id": str(probe.actor.state.agent_decision_round_id),
                "should_speak": True,
                "decision_reason": "合成候选已准备",
            },
        ),
    )
    now[0] = 110
    await probe.manager.submit(
        match_id, MatchCommand(type="speech.deadline", message_id=str(uuid4()))
    )
    await finalize(probe.manager, envelopes[source_id])
    async with probe.factory() as session:
        match = await session.get(Match, match_id)
        assert match is not None
        context_before_agent = match.context_version
    probe.manager.set_agent_runtime(probe.runtime)
    now[0] = 113
    await probe.manager.submit(
        match_id, MatchCommand(type="hand.window_closed", message_id=str(uuid4()))
    )
    try:
        await wait_for_condition(lambda: bool(edges.sources), timeout=2)
        source = edges.sources[0]
        await asyncio.wait_for(source.tail_entered.wait(), 2)
        run = probe.run = probe.runtime._runs[match_id]
        assert run.task is not None and run.speech_id is not None
        await asyncio.wait_for(asyncio.shield(run.task), 2)
        old_speech_id = run.speech_id
        assert probe.actor.state.action_state == "AGENT_FINALIZING"
        if next_kind == "HUMAN":
            await probe.manager.submit(
                match_id,
                MatchCommand(
                    type="hand.raise",
                    message_id=str(uuid4()),
                    actor_user_id=first,
                ),
            )
        now[0] = 116
        await probe.manager.submit(
            match_id, MatchCommand(type="hand.window_closed", message_id=str(uuid4()))
        )
        expected = "HUMAN_READY_TO_START" if next_kind == "HUMAN" else "AGENT_PREPARING"
        assert probe.actor.state.action_state == expected
        assert probe.actor.state.free_source_speech_id == old_speech_id
        assert probe.runtime._runs.get(match_id) is run
        receiver: MatchAudioReceiver | None = None
        if next_kind == "HUMAN":
            settings = probe.runtime._settings.model_copy(
                update={
                    "match_audio_storage_dir": str(probe.runtime._settings.agent_audio_storage_dir)
                    + "/human",
                }
            )
            asr = AsrRuntime(settings=settings, callbacks=probe.manager)
            receiver = MatchAudioReceiver(
                match_id=match_id,
                settings=settings,
                callbacks=probe.manager,
                input_clock=lambda: probe.actor._clock(),
            )

            async def close() -> None:
                return None

            stream, socket = OwnedStream(), FakeSocket()

            async def socket_factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
                return socket

            def audio_stream(*_args: Any, **_kwargs: Any) -> OwnedStream:
                return stream

            # Real Session/Protocol task-started and reader readiness are part
            # of admission. Only the SDK stream and provider socket are owned fakes.
            receiver._connection = FunAsrConnection(
                url="wss://owned.invalid", api_key="synthetic", socket_factory=socket_factory
            )
            track: Any = SimpleNamespace(kind=runtime_module.rtc.TrackKind.KIND_AUDIO)
            publication: Any = SimpleNamespace(sid="owned-human-admission", track=track)
            epoch = dict(probe.actor.state.connection_epochs).get(first, 1)
            participant: Any = SimpleNamespace(
                identity=f"jx-human-{match_id}-{first}-{epoch}-owned",
                track_publications={publication.sid: publication},
            )
            room: Any = SimpleNamespace(
                remote_participants={participant.identity: participant}, disconnect=close
            )
            receiver._room = room
            monkeypatch.setattr(runtime_module.rtc, "AudioStream", audio_stream)
            receiver._on_track_subscribed(room, track, publication, participant)
            asr._receivers[match_id] = receiver
            probe.manager.set_speech_runtime(asr)
            assert receiver._speech is None
            with pytest.raises(MatchDomainError, match="asr_finalization_pending"):
                await probe.manager.submit(
                    match_id,
                    MatchCommand(
                        type="speech.start",
                        message_id=str(uuid4()),
                        actor_user_id=first,
                    ),
                )
                print(
                    {
                        "unexpected_real_receiver_admitted": receiver._speech is not None,
                        "preceding_agent_speech_id": str(old_speech_id),
                        "old_tail_waiting": not source.release_tail.is_set(),
                        "new_actor_state": probe.actor.state.action_state,
                    }
                )
            assert receiver._speech is None
        await asyncio.sleep(0.1)
        assert edges.provider_calls == 1 and len(edges.sources) == 1
        assert probe.actor.state.action_state == expected
        source.release_tail.set()
        await wait_for_condition(lambda: source.closed, timeout=2)
        assert run.generation_id is not None
        await assert_finalized_body(probe, run.generation_id)
        # Late finalizer may update only its row; selection remains valid.
        if next_kind == "HUMAN":
            assert receiver is not None
            assert probe.actor.state.action_state == "HUMAN_READY_TO_START"
            assert probe.actor.state.current_speaker_user_id == first
            await probe.manager.submit(
                match_id,
                MatchCommand(
                    type="speech.start",
                    message_id=str(uuid4()),
                    actor_user_id=first,
                ),
            )
            assert probe.actor.state.action_state == "HUMAN_SPEAKING"
            assert probe.actor.state.current_speech_id != old_speech_id
            assert receiver._speech is not None and receiver._speech_envelope is not None
            async with probe.factory() as session:
                match = await session.get(Match, match_id)
                assert match is not None and match.context_version == context_before_agent + 1
                assert receiver._speech_envelope.context_version == match.context_version
            assert edges.provider_calls == 1
        else:
            await wait_for_condition(
                lambda: bool(len(edges.sources) == 2 and edges.sources[1].closed),
                timeout=3,
            )
            assert edges.provider_calls == 2 and match_id not in probe.runtime._runs
            async with probe.factory() as session:
                generations = list(
                    (
                        await session.scalars(
                            select(AgentGeneration).where(
                                AgentGeneration.match_id == match_id,
                            )
                        )
                    ).all()
                )
                assert len(generations) == 2 and all(
                    row.status == "FINALIZED" for row in generations
                )
                assert {row.context_version for row in generations} == {
                    context_before_agent,
                    context_before_agent + 1,
                }
        assert source.frames == [9600, *([960] * 30)]
    finally:
        for source in edges.sources:
            source.release_tail.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["capture_fail", "timeout"])
async def test_natural_tail_failure_reaches_authoritative_error_with_frozen_content_budget(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    probe = agent_fault_probe
    edges = install_edges(probe, monkeypatch, boundary)
    if boundary == "timeout":
        monkeypatch.setattr(runtime_module, "AGENT_FINALIZATION_TIMEOUT_SECONDS", 0.15)
    run = await start(probe)
    try:
        await wait_for_condition(lambda: bool(edges.sources), timeout=2)
        source = edges.sources[0]
        await asyncio.wait_for(source.tail_entered.wait(), 2)
        await wait_for_condition(lambda: probe.actor.state.status == "ERROR", timeout=2)
        assert probe.actor.state.speech_deadline_mono is None
        assert run.played_samples == 9600 and edges.provider_calls == 1
        assert probe.actor.state.error_code == (
            "agent_finalization_timeout" if boundary == "timeout" else "agent_finalization_failed"
        )
        async with probe.factory() as session:
            match = await session.get(Match, run.match_id)
            speech = await session.get(Speech, run.speech_id)
            generation = await session.get(AgentGeneration, run.generation_id)
            assert match is not None and match.context_version == 0
            assert speech is not None and speech.status != "FINALIZED"
            assert generation is not None and generation.status == "FAILED"
            assert generation.error_code == probe.actor.state.error_code
            assert generation.completed_at is not None
    finally:
        for source in edges.sources:
            source.release_tail.set()

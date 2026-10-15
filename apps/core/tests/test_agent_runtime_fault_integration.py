"""Actual Agent/Actor/PostgreSQL failure-chain probes with synthetic media.

This focused formal 4v4 fixture contains two humans and two Agents per side.
It exercises one Agent action; it does not claim a complete match or real media.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import event, func, select, text, update
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import jx_core.agent.runtime as runtime_module
from jx_core.agent.llm import OpenAIStreamingClient
from jx_core.agent.runtime import AgentRun, AgentRuntime
from jx_core.config import Settings
from jx_core.data_capture.provider import ProviderCallCapture
from jx_core.data_capture.provider_writer import CaptureSubmission, ProviderCaptureWriter
from jx_core.matches.domain import MatchAction, MatchActor, MatchCommand, MatchRuntimeState
from jx_core.matches.domain import MatchEvent as DomainEvent
from jx_core.matches.service import MatchRuntimeManager, state_snapshot
from jx_core.models import (
    AgentGeneration,
    AgentProfile,
    ExternalCall,
    Match,
    MatchEvent,
    MatchParticipant,
    ModelProfile,
    Room,
    Rule,
    Seat,
    Speech,
    User,
    VoiceProfile,
)
from jx_core.models import ProviderCallCapture as CaptureRow
from jx_core.security.crypto import encrypt_secret

pytestmark = pytest.mark.integration
# The probe deliberately wires the production private Actor hooks.
# pyright: reportPrivateUsage=false


@pytest.fixture(scope="module")
def agent_fault_database_url() -> str:
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("set RUN_DATABASE_INTEGRATION=1 for the isolated Agent fault probe")
    value = os.environ.get("TEST_DATABASE_URL")
    if not value or value == os.environ.get("DATABASE_URL"):
        pytest.fail("an independent TEST_DATABASE_URL is required")
    parsed = make_url(value)
    if (
        parsed.host not in {"127.0.0.1", "localhost"}
        or parsed.database != "jx_debate_qa_agent_faults"
    ):
        pytest.fail("Agent fault probes require the dedicated local QA database")
    settings = Settings(_env_file=None, database_url=SecretStr(value), app_env="test")  # pyright: ignore[reportCallIssue]
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    config.attributes["settings"] = settings
    # env.py currently imports load_settings. Override only this process's
    # migration loader so its default dotenv path is never evaluated.
    with patch("jx_core.config.load_settings", return_value=config.attributes["settings"]):
        command.upgrade(config, "head")
    return value


class SyntheticDecoder:
    def __init__(self) -> None:
        self.finished = asyncio.Event()

    async def start(self, _: Path) -> None:
        return None

    async def frames(self) -> AsyncIterator[bytes]:
        await self.finished.wait()
        empty_frames: list[bytes] = []
        for pcm in empty_frames:
            yield pcm

    def finish_input(self) -> None:
        self.finished.set()

    async def close(self) -> None:
        self.finished.set()


def empty_http_clients() -> list[httpx.AsyncClient]:
    return []


@dataclass
class AgentFaultProbe:
    factory: async_sessionmaker[AsyncSession]
    manager: MatchRuntimeManager
    runtime: AgentRuntime
    actor: MatchActor
    agent_id: UUID
    model_id: UUID
    queue: asyncio.Queue[DomainEvent]
    engine: AsyncEngine
    writer: ProviderCaptureWriter
    http_clients: list[httpx.AsyncClient] = field(default_factory=empty_http_clients)
    provider_calls: int = 0
    run: AgentRun | None = None


async def terminalize_owned_fault_fixture(
    factory: async_sessionmaker[AsyncSession], *, match_id: UUID, room_id: UUID
) -> None:
    """Retain this fixture's evidence while releasing only its capacity slot."""
    async with factory() as session:
        async with session.begin():
            match = await session.get(Match, match_id, with_for_update=True)
            room = await session.get(Room, room_id, with_for_update=True)
            assert match is not None and room is not None and match.room_id == room_id
            terminal_status = (
                match.status if match.status in {"FINISHED", "TERMINATED"} else "TERMINATED"
            )
            match.status = terminal_status
            match.current_speech_id = None
            match.ended_at = match.ended_at or datetime.now(UTC)
            match.runtime_snapshot = {
                **match.runtime_snapshot,
                "status": terminal_status,
                "action_state": "MATCH_FINISHED",
                "current_speech_id": None,
                "current_speaker_user_id": None,
                "current_agent_profile_id": None,
                "current_speaker_side": None,
                "current_speaker_seat_no": None,
                "interim_text": "",
                "speech_remaining_ms": None,
                "host_audio_remaining_ms": None,
                "host_audio_deadline_ms": None,
                "hand_queue": [],
                "agent_hand_queue": [],
                "hand_window_open": False,
                "paused_from_status": None,
                "paused_from_action_state": None,
                "pause_initiator_user_id": None,
                "selection_phase": None,
                "selection_remaining_ms": None,
                "human_wait_remaining_ms": None,
            }
            room.status = terminal_status
            room.ended_at = room.ended_at or match.ended_at
    # Verify the committed state using a fresh session. No name/prefix search,
    # broad delete or global capacity modification is permitted here.
    async with factory() as session:
        active_rooms = await session.scalar(
            select(func.count())
            .select_from(Room)
            .where(
                Room.id == room_id,
                Room.status.in_(("START_PENDING_RUNTIME", "RUNNING", "PAUSED")),
            )
        )
        active_matches = await session.scalar(
            select(func.count())
            .select_from(Match)
            .where(Match.id == match_id, Match.status.not_in(("FINISHED", "TERMINATED")))
        )
        assert active_rooms == 0 and active_matches == 0
        match = await session.get(Match, match_id)
        room = await session.get(Room, room_id)
        assert match is not None and room is not None
        assert match.ended_at is not None and room.ended_at is not None
        assert match.runtime_snapshot["status"] == match.status == room.status
        assert match.runtime_snapshot["action_state"] == "MATCH_FINISHED"


async def finish_owned_fault_fixture(
    probe: AgentFaultProbe, *, seed_runtime: AgentRuntime, match_id: UUID, room_id: UUID
) -> None:
    """Attempt every owned close and still terminalize if a resource close raises."""
    failures: list[BaseException] = []
    closes: list[Callable[[], Awaitable[None]]] = [probe.manager.close, seed_runtime.close]
    if probe.runtime is not seed_runtime:
        closes.append(probe.runtime.close)
    if probe.run is not None:
        run = probe.run
        closes.append(lambda: seed_runtime._cleanup_attempt(run))
    closes.extend(client.aclose for client in probe.http_clients)
    closes.append(probe.writer.close)
    try:
        for close in closes:
            try:
                await close()
            except BaseException as error:
                failures.append(error)
    finally:
        try:
            await terminalize_owned_fault_fixture(probe.factory, match_id=match_id, room_id=room_id)
        finally:
            await probe.engine.dispose()
    if failures:
        raise BaseExceptionGroup("owned QA fixture cleanup failed", failures)


@pytest.fixture
async def agent_fault_probe(
    agent_fault_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[AgentFaultProbe, None]:
    engine = create_async_engine(agent_fault_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex[:12]
    master_key = base64.urlsafe_b64encode(b"x" * 32).decode()
    ciphertext, nonce, last4 = encrypt_secret("synthetic", master_key)
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url=SecretStr(agent_fault_database_url),
        app_env="test",
        llm_key_encryption_key=SecretStr(master_key),
        tts_api_key=SecretStr("synthetic"),
        asr_api_key=None,
        agent_audio_storage_dir=str(tmp_path),
        llm_global_concurrency=2,
    )
    writer = ProviderCaptureWriter(agent_fault_database_url)
    await writer.start()
    manager = MatchRuntimeManager(factory, capture_writer=writer)
    runtime = AgentRuntime(
        settings=settings, session_factory=factory, callbacks=manager, capture_writer=writer
    )
    manager.set_agent_runtime(runtime)

    async with factory() as session:
        async with session.begin():
            humans = [
                User(
                    username=f"fault-human-{suffix}-{index}",
                    username_normalized=f"fault-human-{suffix}-{index}",
                    real_name="Synthetic human",
                    password_hash="not-used",
                )
                for index in range(4)
            ]
            session.add_all(humans)
            await session.flush()
            model = ModelProfile(
                name=f"fault-model-{suffix}",
                config_ref="synthetic",
                base_url="https://synthetic.invalid/v1",
                model_id="synthetic-model",
                api_key_ciphertext=ciphertext,
                api_key_nonce=nonce,
                api_key_last4=last4,
                max_concurrency=1,
            )
            voices = [
                VoiceProfile(
                    name=f"fault-voice-{suffix}-{index}",
                    kind="AGENT",
                    provider_voice=f"synthetic-{suffix}-{index}",
                    avatar_key="agent-01",
                )
                for index in range(4)
            ]
            session.add_all([model, *voices])
            await session.flush()
            agents = [
                AgentProfile(
                    name=f"fault-agent-{suffix}-{index}",
                    model_profile_id=model.id,
                    voice_profile_id=voice.id,
                )
                for index, voice in enumerate(voices)
            ]
            rule = Rule(
                rule_key=f"fault-rule-{suffix}",
                version=1,
                name="Synthetic 4v4 rule",
                side_size=4,
                estimated_seconds=30,
                status="ENABLED",
                created_by=humans[0].id,
            )
            session.add_all([rule, *agents])
            await session.flush()
            room = Room(
                code=suffix[:6].upper(),
                title="Synthetic Agent fault",
                label="QA",
                status="RUNNING",
                topic_snapshot={"title": "Synthetic topic"},
                rule_id=rule.id,
                rule_snapshot={"side_size": 4, "stages": []},
                organizer_user_id=humans[0].id,
            )
            session.add(room)
            await session.flush()
            action = MatchAction(
                stage_position=1,
                action_position=1,
                action_kind="AGENT_SPEECH",
                duration_seconds=30,
                side="AFFIRMATIVE",
                seat_no=3,
                speaker_kind="AGENT",
                agent_profile_id=agents[0].id,
            )
            state = MatchRuntimeState(
                match_id=uuid4(),
                status="RUNNING",
                action_state="AGENT_PREPARING",
                actions=(action,),
                current_agent_profile_id=agents[0].id,
                current_speaker_side="AFFIRMATIVE",
                current_speaker_seat_no=3,
                speech_remaining_ms=30_000,
                formal_4v4=True,
            )
            session.add(
                Match(
                    id=state.match_id,
                    room_id=room.id,
                    status="RUNNING",
                    runtime_snapshot=state_snapshot(state),
                    current_stage_position=1,
                    current_action_position=1,
                )
            )
            await session.flush()
            for side_index, side in enumerate(("AFFIRMATIVE", "NEGATIVE")):
                for seat_no in range(1, 5):
                    human = humans[side_index * 2 + seat_no - 1] if seat_no <= 2 else None
                    agent = agents[side_index * 2 + seat_no - 3] if seat_no >= 3 else None
                    kind = "HUMAN" if human else "AGENT"
                    user_id = human.id if human else None
                    agent_id = agent.id if agent else None
                    session.add(
                        Seat(
                            room_id=room.id,
                            side=side,
                            seat_no=seat_no,
                            occupant_type=kind,
                            user_id=user_id,
                            agent_profile_id=agent_id,
                        )
                    )
                    session.add(
                        MatchParticipant(
                            match_id=state.match_id,
                            side=side,
                            seat_no=seat_no,
                            kind=kind,
                            user_id=user_id,
                            agent_profile_id=agent_id,
                            display_name="Synthetic seat",
                        )
                    )

    actor = MatchActor(
        state,
        commit=manager._commit,
        publish=manager._publish,  # pyright: ignore[reportPrivateUsage]
        pre_commit=manager._pre_commit,  # pyright: ignore[reportPrivateUsage]
    )
    manager._actors[state.match_id] = actor  # pyright: ignore[reportPrivateUsage]
    await actor.start()
    queue = await manager.subscribe(state.match_id)
    probe = AgentFaultProbe(
        factory, manager, runtime, actor, agents[0].id, model.id, queue, engine, writer
    )
    owned_match_id, owned_room_id = state.match_id, room.id

    async def unavailable_provider(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "synthetic.invalid"
        probe.provider_calls += 1
        return httpx.Response(503, json={"code": "synthetic_unavailable"})

    def provider_client(**arguments: Any) -> OpenAIStreamingClient:
        http_client = httpx.AsyncClient(transport=httpx.MockTransport(unavailable_provider))
        probe.http_clients.append(http_client)
        return OpenAIStreamingClient(**arguments, client=http_client)

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", provider_client)
    monkeypatch.setattr(runtime_module, "IncrementalOpusDecoder", SyntheticDecoder)
    monkeypatch.setattr(runtime_module, "AGENT_PROVIDER_RETRY_BACKOFF_SECONDS", 0.01)
    monkeypatch.setattr(
        runtime,
        "_connect_publisher",
        AsyncMock(
            return_value=(
                SimpleNamespace(disconnect=AsyncMock()),
                SimpleNamespace(clear_queue=Mock(), aclose=AsyncMock()),
            )
        ),
    )
    try:
        yield probe
    finally:
        await finish_owned_fault_fixture(
            probe, seed_runtime=runtime, match_id=owned_match_id, room_id=owned_room_id
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capture_fault",
    ["normal", "raise", "hang"],
    ids=["baseline", "capture-write-fails", "capture-write-hangs"],
)
async def test_provider_failure_converges_when_failure_capture_write_fails_once(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capture_fault: Literal["normal", "raise", "hang"],
) -> None:
    probe = agent_fault_probe
    original_capture = probe.writer._persist_item
    fault_count = 0

    async def capture(
        session: AsyncSession,
        item: CaptureSubmission,
        prepared: tuple[tuple[bytes, str], tuple[bytes, str]],
    ) -> None:
        nonlocal fault_count
        if (
            capture_fault != "normal"
            and fault_count == 0
            and item.identity.associations[0] == "LLM_SPEECH"
        ):
            assert session.in_transaction()
            call = await session.get(ExternalCall, item.identity.external_call_id)
            assert call is not None
            assert call.status == "FAILED" and call.error_code == "llm_provider_failed"
            fault_count += 1
            if capture_fault == "hang":
                await asyncio.Event().wait()
            raise RuntimeError("synthetic_capture_write_failed")
        await original_capture(session, item, prepared)

    monkeypatch.setattr(probe.writer, "_persist_item", capture)
    monkeypatch.setattr(runtime_module, "AGENT_RESOURCE_CLEANUP_TIMEOUT_SECONDS", 0.02)
    await probe.runtime.start_agent(
        match_id=probe.actor.state.match_id,
        action_key="1:1",
        agent_profile_id=probe.agent_id,
        duration_ms=30_000,
        side="AFFIRMATIVE",
    )
    probe.run = probe.runtime._runs[probe.actor.state.match_id]  # pyright: ignore[reportPrivateUsage]
    task = probe.run.task
    assert task is not None
    outcome = (await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3.0))[0]
    await probe.writer.close()
    async with probe.factory() as session:
        match = await session.get(Match, probe.actor.state.match_id)
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
                    .where(ExternalCall.match_id == probe.actor.state.match_id)
                    .order_by(ExternalCall.attempt_no)
                )
            ).all()
        )
        events = list(
            (
                await session.scalars(
                    select(MatchEvent)
                    .where(MatchEvent.match_id == probe.actor.state.match_id)
                    .order_by(MatchEvent.sequence)
                )
            ).all()
        )
        speeches = list(
            (
                await session.scalars(
                    select(Speech).where(Speech.match_id == probe.actor.state.match_id)
                )
            ).all()
        )
        participants = list(
            (
                await session.scalars(
                    select(MatchParticipant).where(
                        MatchParticipant.match_id == probe.actor.state.match_id
                    )
                )
            ).all()
        )
    assert match is not None
    evidence = {
        "capture_faults": fault_count,
        "provider_calls": probe.provider_calls,
        "provider_error_observed": any(
            getattr(record, "error_code", None) == "llm_provider_failed"
            for record in caplog.records
        ),
        "task_done": task.done(),
        "task_exception_type": type(outcome).__name__ if outcome else None,
        "actor_status": probe.actor.state.status,
        "actor_action_state": probe.actor.state.action_state,
        "actor_sequence": probe.actor.state.sequence,
        "database_status": match.status,
        "database_sequence": match.sequence,
        "database_action_state": match.runtime_snapshot["action_state"],
        "generation_statuses": [item.status for item in generations],
        "generation_error_codes": [item.error_code for item in generations],
        "call_statuses": [item.status for item in calls],
        "speech_count": len(speeches),
        "event_types": [event.event_type for event in events],
        "broadcast_count": probe.queue.qsize(),
        "participant_composition": {
            side: {
                kind: sum(item.side == side and item.kind == kind for item in participants)
                for kind in ("HUMAN", "AGENT")
            }
            for side in ("AFFIRMATIVE", "NEGATIVE")
        },
        "run_registered": probe.actor.state.match_id in probe.runtime._runs,  # pyright: ignore[reportPrivateUsage]
    }
    print(json.dumps(evidence, sort_keys=True))
    assert fault_count == int(capture_fault != "normal"), evidence
    assert evidence["provider_error_observed"], evidence
    assert evidence["participant_composition"] == {
        "AFFIRMATIVE": {"HUMAN": 2, "AGENT": 2},
        "NEGATIVE": {"HUMAN": 2, "AGENT": 2},
    }, evidence
    leases = await probe.runtime.capacity_limiter.acquire(
        str(probe.model_id), 1, timeout_seconds=0.1
    )
    probe.runtime.capacity_limiter.release(leases)
    # Product convergence is mandatory even if pure diagnostic capture fails.
    assert probe.actor.state.status == "ERROR", evidence
    assert probe.actor.state.action_state == "RECOVERY_REQUIRED", evidence
    assert match.status == "ERROR" and match.sequence == probe.actor.state.sequence, evidence
    assert match.runtime_snapshot["action_state"] == "RECOVERY_REQUIRED", evidence
    assert any(event.event_type == "match.error" for event in events), evidence
    assert any(event.payload.get("error_code") == "llm_provider_failed" for event in events), (
        evidence
    )
    assert probe.provider_calls == 2, evidence
    assert len(generations) == len(calls) == 2, evidence
    assert all(
        item.status == "FAILED" and item.error_code == "llm_provider_failed" for item in generations
    ), evidence
    assert all(
        item.status == "FAILED" and item.error_code == "llm_provider_failed" for item in calls
    ), evidence
    assert all(item.capture_storage_mode == "ASYNC_V1" for item in calls)
    async with probe.factory() as session:
        captures = list(
            (
                await session.scalars(
                    select(CaptureRow).where(
                        CaptureRow.external_call_id.in_([call.id for call in calls])
                    )
                )
            ).all()
        )
    if capture_fault == "hang":
        assert not captures
        assert probe.writer.stats()["quarantined"]
    else:
        assert len(captures) == 2
        assert all(
            row.request_blob_id is not None and row.response_blob_id is not None for row in captures
        )
        if capture_fault == "raise":
            assert probe.writer.stats().get("retry") == 1
    # The manager fences/cancels the failed Agent task after committing ERROR.
    assert outcome is None or isinstance(outcome, asyncio.CancelledError), evidence


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "error_code"),
    [
        ("first_token", "llm_first_token_timeout"),
        ("truncated", "llm_stream_interrupted"),
    ],
)
async def test_actual_provider_stream_failure_retries_and_commits_recovery_state(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    error_code: str,
) -> None:
    probe = agent_fault_probe

    class FaultStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            if failure == "first_token":
                await asyncio.Event().wait()
            else:
                body = {"choices": [{"delta": {"content": "合成截断响应。"}}]}
                yield f"data: {json.dumps(body)}\n\n".encode()

    async def provider(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "synthetic.invalid"
        probe.provider_calls += 1
        return httpx.Response(200, stream=FaultStream())

    def provider_client(**arguments: Any) -> OpenAIStreamingClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        probe.http_clients.append(client)
        return OpenAIStreamingClient(**arguments, client=client, first_token_timeout_seconds=0.02)

    async def synthesize(chunks: Any, **_: Any) -> None:
        async for _chunk in chunks:
            pass
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", provider_client)
    # A truncated response may already have started TTS. Keep the entire
    # media boundary synthetic and cancellation cooperative, with no API use.
    connection = SimpleNamespace(synthesize=synthesize, close=AsyncMock())
    monkeypatch.setattr(runtime_module, "QwenTtsConnection", Mock(return_value=connection))
    await probe.runtime.start_agent(
        match_id=probe.actor.state.match_id,
        action_key="1:1",
        agent_profile_id=probe.agent_id,
        duration_ms=30_000,
        side="AFFIRMATIVE",
    )
    probe.run = probe.runtime._runs[probe.actor.state.match_id]
    assert probe.run.task is not None
    outcomes = await asyncio.wait_for(asyncio.gather(probe.run.task, return_exceptions=True), 3.0)
    assert outcomes[0] is None or isinstance(outcomes[0], asyncio.CancelledError)
    assert probe.provider_calls == 2
    assert probe.actor.state.status == "ERROR"
    assert probe.actor.state.action_state == "RECOVERY_REQUIRED"
    async with probe.factory() as session:
        match = await session.get(Match, probe.actor.state.match_id)
        assert match is not None and match.status == "ERROR"
        assert match.sequence == probe.actor.state.sequence
        generations = list(
            (
                await session.scalars(
                    select(AgentGeneration).where(
                        AgentGeneration.match_id == probe.actor.state.match_id
                    )
                )
            ).all()
        )
        assert len(generations) == 2
        assert all(
            item.status == "FAILED" and item.error_code == error_code for item in generations
        )
        calls = list(
            (
                await session.scalars(
                    select(ExternalCall).where(
                        ExternalCall.match_id == probe.actor.state.match_id,
                        ExternalCall.call_kind == "LLM_SPEECH",
                    )
                )
            ).all()
        )
        assert len(calls) == 2
        assert all(item.status == "FAILED" and item.error_code == error_code for item in calls)
        errors = list(
            (
                await session.scalars(
                    select(MatchEvent).where(
                        MatchEvent.match_id == probe.actor.state.match_id,
                        MatchEvent.event_type == "match.error",
                    )
                )
            ).all()
        )
        assert len(errors) == 1 and errors[0].payload["error_code"] == error_code
        assert not (
            await session.scalars(
                select(Speech).where(Speech.match_id == probe.actor.state.match_id)
            )
        ).all()
    held = await probe.runtime.capacity_limiter.acquire(str(probe.model_id), 1, timeout_seconds=0.1)
    probe.runtime.capacity_limiter.release(held)


async def create_generation_call(probe: AgentFaultProbe, attempt_no: int = 1) -> tuple[UUID, UUID]:
    run = AgentRun(
        match_id=probe.actor.state.match_id,
        action_key="1:1",
        agent_profile_id=probe.agent_id,
        duration_ms=30_000,
        side="AFFIRMATIVE",
    )
    config = await probe.runtime._load_config(run)
    generation_id = await probe.runtime._create_generation(config, attempt_no)
    call_id = await probe.runtime._start_generation_call(
        generation_id, config, logical_call_id=uuid4()
    )
    return generation_id, call_id


def hold_writer_capture(
    probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    entered: asyncio.Event,
    release: asyncio.Event,
    cancelled: asyncio.Event | None = None,
) -> None:
    original = probe.writer._persist_item

    async def capture(
        session: AsyncSession,
        item: CaptureSubmission,
        prepared: tuple[tuple[bytes, str], tuple[bytes, str]],
    ) -> None:
        entered.set()
        try:
            await release.wait()
        finally:
            if cancelled is not None:
                cancelled.set()
        await original(session, item, prepared)

    monkeypatch.setattr(probe.writer, "_persist_item", capture)


@pytest.mark.asyncio
async def test_capture_cancel_propagates_after_authoritative_failure_commit(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = agent_fault_probe
    generation_id, call_id = await create_generation_call(probe)
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    hold_writer_capture(probe, monkeypatch, entered, release, cancelled)
    task = asyncio.create_task(
        probe.runtime._mark_generation_failed(
            generation_id, "llm_provider_failed", llm_capture=ProviderCallCapture()
        )
    )
    await asyncio.wait_for(entered.wait(), 0.5)
    async with probe.factory() as session:
        generation = await session.get(AgentGeneration, generation_id)
        call = await session.get(ExternalCall, call_id)
        assert generation is not None and generation.status == "FAILED"
        assert call is not None and call.status == "FAILED"
    assert task.done() and task.cancel() is False
    await task
    release.set()
    await probe.writer.close()
    await asyncio.wait_for(cancelled.wait(), 0.5)
    async with probe.factory() as session:
        generation = await session.get(AgentGeneration, generation_id)
        call = await session.get(ExternalCall, call_id)
        assert generation is not None and generation.error_code == "llm_provider_failed"
        assert call is not None and call.error_code == "llm_provider_failed"
        assert call.request_blob_id is None and call.response_blob_id is None


@pytest.mark.asyncio
async def test_pending_capture_does_not_lock_authoritative_generation_update(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = agent_fault_probe
    generation_id, call_id = await create_generation_call(probe)
    await probe.runtime._mark_generation_failed(generation_id, "llm_provider_failed")
    entered, release = asyncio.Event(), asyncio.Event()
    hold_writer_capture(probe, monkeypatch, entered, release)
    task = asyncio.create_task(
        probe.runtime._capture_failed_generation(
            generation_id, "llm_provider_failed", {call_id: ProviderCallCapture()}
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        # A separate authoritative transaction must commit while the raw
        # capture remains pending, rather than waiting for its cleanup budget.
        async with probe.factory() as session:
            async with session.begin():
                await session.execute(text("SET LOCAL lock_timeout = '150ms'"))
                await session.execute(
                    update(AgentGeneration)
                    .where(AgentGeneration.id == generation_id)
                    .values(error_code="agent_cancelled")
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
        assert call is not None and call.status == "FAILED"
        assert call.error_code == "llm_provider_failed"


@pytest.mark.asyncio
async def test_authoritative_failure_transaction_error_is_not_swallowed(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = agent_fault_probe
    generation_id, call_id = await create_generation_call(probe)
    authority_error = RuntimeError("synthetic_authority_commit_failed")
    injected = False
    capture = Mock()
    monkeypatch.setattr(runtime_module, "submit_capture", capture)

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

    event.listen(probe.engine.sync_engine, "before_cursor_execute", fail_generation_write)
    try:
        with pytest.raises(RuntimeError) as caught:
            await probe.runtime._mark_generation_failed(
                generation_id, "llm_provider_failed", llm_capture=ProviderCallCapture()
            )
        assert caught.value is authority_error
    finally:
        event.remove(probe.engine.sync_engine, "before_cursor_execute", fail_generation_write)
    assert injected
    capture.assert_not_called()
    async with probe.factory() as session:
        generation = await session.get(AgentGeneration, generation_id)
        call = await session.get(ExternalCall, call_id)
        match = await session.get(Match, probe.actor.state.match_id)
        assert generation is not None and generation.status == "STARTED"
        assert call is not None and call.status == "STARTED"
        assert match is not None and match.status == "RUNNING" and match.sequence == 0
    assert probe.actor.state.sequence == 0
    assert probe.queue.empty()


@pytest.mark.asyncio
async def test_match_event_commit_failure_does_not_advance_actor_or_broadcast(
    agent_fault_probe: AgentFaultProbe,
) -> None:
    probe = agent_fault_probe
    original_state = probe.actor.state
    commit_error = RuntimeError("synthetic_match_event_commit_failed")
    injected = False

    def fail_event_insert(
        _: Connection,
        __: Any,
        statement: str,
        ___: Any,
        ____: Any,
        _____: bool,
    ) -> None:
        nonlocal injected
        if not injected and statement.lstrip().startswith("INSERT INTO match_events"):
            injected = True
            raise commit_error

    command = MatchCommand(
        type="system.error",
        message_id=f"fault-commit:{original_state.match_id}",
        payload={"error_code": "llm_provider_failed"},
    )
    event.listen(probe.engine.sync_engine, "before_cursor_execute", fail_event_insert)
    try:
        with pytest.raises(RuntimeError) as caught:
            await probe.actor.submit(command)
        assert caught.value is commit_error
    finally:
        event.remove(probe.engine.sync_engine, "before_cursor_execute", fail_event_insert)
    assert injected
    assert probe.actor.state == original_state
    assert probe.queue.empty()
    async with probe.factory() as session:
        match = await session.get(Match, original_state.match_id)
        assert match is not None
        room = await session.get(Room, match.room_id)
        assert room is not None and room.status == "RUNNING"
        assert match.status == "RUNNING" and match.sequence == original_state.sequence
        assert match.runtime_snapshot["action_state"] == "AGENT_PREPARING"
        assert not (
            await session.scalars(
                select(MatchEvent).where(MatchEvent.match_id == original_state.match_id)
            )
        ).all()
        assert not (
            await session.scalars(select(Speech).where(Speech.match_id == original_state.match_id))
        ).all()
    # A failed transaction must not consume command idempotency. The exact
    # same command succeeds after the injected database failure is removed.
    result = await probe.actor.submit(command)
    assert result.state.status == "ERROR"
    assert result.state.action_state == "RECOVERY_REQUIRED"
    assert result.state.sequence > original_state.sequence
    async with probe.factory() as session:
        match = await session.get(Match, original_state.match_id)
        assert match is not None and match.status == "ERROR"
        assert match.sequence == result.state.sequence
        rows = list(
            (
                await session.scalars(
                    select(MatchEvent)
                    .where(MatchEvent.match_id == original_state.match_id)
                    .order_by(MatchEvent.sequence)
                )
            ).all()
        )
        assert [row.sequence for row in rows] == [item.sequence for item in result.events]
        assert [row.event_type for row in rows] == [item.type for item in result.events]
    assert probe.queue.qsize() == len(result.events)
    before_duplicate = probe.queue.qsize()
    duplicate = await probe.actor.submit(command)
    assert duplicate.duplicate
    assert duplicate.state == result.state
    assert duplicate.events == result.events
    assert probe.queue.qsize() == before_duplicate


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "guard",
    [
        "generation_status",
        "generation_error",
        "call_status",
        "call_error",
        "call_generation",
    ],
)
async def test_delayed_failure_capture_cannot_mutate_retired_or_unrelated_state(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    guard: str,
) -> None:
    probe = agent_fault_probe
    generation_id, call_id = await create_generation_call(probe)
    await probe.runtime._mark_generation_failed(generation_id, "llm_provider_failed")
    if guard == "call_generation":
        other_generation_id, call_id = await create_generation_call(probe, attempt_no=2)
        await probe.runtime._mark_generation_failed(other_generation_id, "llm_provider_failed")
    else:
        model_type = AgentGeneration if guard.startswith("generation") else ExternalCall
        row_id = generation_id if model_type is AgentGeneration else call_id
        field_name = "status" if guard.endswith("status") else "error_code"
        new_value = (
            ("FINALIZED" if model_type is AgentGeneration else "SUCCEEDED")
            if field_name == "status"
            else "other_failure"
        )
        async with probe.factory() as session:
            async with session.begin():
                await session.execute(
                    update(model_type)
                    .where(model_type.id == row_id)
                    .values(**{field_name: new_value})
                )
    capture = Mock()
    monkeypatch.setattr(runtime_module, "submit_capture", capture)
    await probe.runtime._capture_failed_generation(
        generation_id, "llm_provider_failed", {call_id: ProviderCallCapture()}
    )
    capture.assert_not_called()
    async with probe.factory() as session:
        call = await session.get(ExternalCall, call_id)
        assert call is not None
        assert call.request_blob_id is None and call.response_blob_id is None
        if guard == "call_status":
            assert call.status == "SUCCEEDED"
        if guard == "call_error":
            assert call.error_code == "other_failure"
    assert probe.actor.state.status == "RUNNING" and probe.actor.state.sequence == 0

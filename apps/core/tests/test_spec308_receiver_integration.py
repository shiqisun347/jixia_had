"""Real PG/source controls for the QA receiver; no provider or native RTC calls.

The rows and media are owned public fixtures.  Only the snapshot transport and
received RTC PCM are supplied by the fixture; prove() uses its real SQL, codec,
source-file access and unchanged NCC matcher.  These are receiver controls, not
evidence of a complete match or of real participant audio.
"""

from __future__ import annotations

import io
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import av
import numpy as np
import pytest
import pytest_asyncio
from scripts.qa.probes.server_spec308_receiver import (
    PassiveReceiver,
    ReceivedTrack,
)
from scripts.qa.probes.spec308_tail_matcher import compare_tail, encoded_source
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from jx_core.agent.audio import decode_ogg_opus_pcm
from jx_core.matches.domain import MatchAction, MatchRuntimeState, compile_linear_actions
from jx_core.matches.service import state_snapshot
from jx_core.models import (
    AgentAudioAsset,
    AgentGeneration,
    AgentProfile,
    Match,
    MatchEvent,
    MatchFile,
    MatchParticipant,
    ModelProfile,
    Room,
    Rule,
    Speech,
    User,
    VoiceProfile,
)
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import terminalize_owned_fault_fixture

pytestmark = pytest.mark.integration

RATE = 48_000
SOURCE_IDENTITY_ERROR = "qa_receiver_source_identity_not_proved"


@pytest.fixture(scope="module")
def valid_ogg_and_pcm() -> tuple[bytes, bytes]:
    """Encode public varying PCM as real Ogg/Opus, then use the actual decoder."""
    raw, _ = encoded_source("spec308-public-real-pg-source-control")
    samples = np.frombuffer(raw, dtype="<i2")
    target = io.BytesIO()
    with av.open(target, mode="w", format="ogg") as container:
        stream = container.add_stream("libopus", rate=RATE)
        stream.layout = "mono"
        stream.bit_rate = 64_000
        for offset in range(0, len(samples), 960):
            frame = av.AudioFrame.from_ndarray(
                samples[offset : offset + 960].reshape(1, -1), format="s16", layout="mono"
            )
            frame.sample_rate = RATE
            frame.time_base = Fraction(1, RATE)
            frame.pts = offset
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    encoded = target.getvalue()
    decoded = decode_ogg_opus_pcm(encoded, sample_rate=RATE)
    assert encoded.startswith(b"OggS") and len(decoded) > RATE * 2
    assert compare_tail(decoded, decoded, synthetic=False)["passed"]
    assert not compare_tail(decoded, decoded[: -RATE * 2 * 160 // 1000], synthetic=False)["passed"]
    return encoded, decoded


@dataclass
class SourceCase:
    factory: async_sessionmaker[AsyncSession]
    receiver: PassiveReceiver
    record: ReceivedTrack
    match_id: UUID
    other_match_id: UUID
    speech_id: UUID
    generation_id: UUID
    asset_id: UUID
    file_id: UUID
    finalizing_event_id: UUID
    agent_ids: tuple[UUID, ...]
    source_path: Path
    source_pcm: bytes
    generation_created_at: datetime
    speech_started_at: datetime
    speech_ended_at: datetime


@pytest_asyncio.fixture
async def source_case(
    agent_fault_database_url: str,
    tmp_path: Path,
    valid_ogg_and_pcm: tuple[bytes, bytes],
) -> AsyncIterator[SourceCase]:
    """Commit unique owned rows; never truncate or alter another fixture's rows."""
    engine = create_async_engine(agent_fault_database_url, pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    encoded, decoded = valid_ogg_and_pcm
    key = uuid4().hex
    now = datetime.now(UTC)
    generation_created = now - timedelta(seconds=4)
    speech_started = now - timedelta(seconds=2)
    source_root = tmp_path / "owned-source"
    source_root.mkdir()
    generation_id, speech_id = uuid4(), uuid4()
    source_path = source_root / f"{generation_id}.ogg"
    source_path.write_bytes(encoded)
    owned_matches: list[tuple[UUID, UUID]] = []
    try:
        async with factory() as session, session.begin():
            users = [
                User(
                    username=f"rx308-{key[:15]}-{index}",
                    username_normalized=f"rx308-{key[:15]}-{index}",
                    real_name="公开接收控件",
                    password_hash="public-fixture-not-used-for-login",
                    role="USER",
                )
                for index in range(4)
            ]
            model = ModelProfile(name=f"rx308-{key}", config_ref="public-unused-model")
            voices = [
                VoiceProfile(
                    name=f"公开控件{index}",
                    kind="AGENT",
                    provider_voice=f"public-unused-{key}-{index}",
                    avatar_key=f"agent-{index + 1:02d}",
                )
                for index in range(4)
            ]
            session.add_all([*users, model, *voices])
            await session.flush()
            rule = Rule(
                rule_key=f"rx308-{key}",
                version=1,
                name="公开四对四接收控件",
                side_size=4,
                estimated_seconds=120,
                created_by=users[0].id,
            )
            session.add(rule)
            await session.flush()
            agents = [
                AgentProfile(
                    name=f"公开控件{index}",
                    rule_id=rule.id,
                    model_profile_id=model.id,
                    voice_profile_id=voice.id,
                )
                for index, voice in enumerate(voices)
            ]
            # Allocate unused six-character room codes without modifying existing rooms.
            used_codes = set((await session.scalars(select(Room.code))).all())
            code = int(key[:12], 16) % 1_000_000
            codes: list[str] = []
            while len(codes) < 2:
                candidate = f"{code:06d}"
                if candidate not in used_codes:
                    codes.append(candidate)
                    used_codes.add(candidate)
                code = (code + 1) % 1_000_000
            rooms = [
                Room(
                    code=code,
                    title="公开接收控件",
                    label="QA",
                    topic_snapshot={"title": "公开合成材料"},
                    rule_id=rule.id,
                    rule_snapshot={"side_size": 4, "stages": []},
                    organizer_user_id=users[0].id,
                    status="FINISHED",
                )
                for code in codes
            ]
            session.add_all([*agents, *rooms])
            await session.flush()
            matches = [
                Match(room_id=room.id, status="RUNNING", runtime_snapshot={}) for room in rooms
            ]
            session.add_all(matches)
            await session.flush()
            match_id, other_match_id = matches[0].id, matches[1].id
            agent_ids = tuple(agent.id for agent in agents)
            for index, agent in enumerate(agents):
                side = "AFFIRMATIVE" if index < 2 else "NEGATIVE"
                session.add_all(
                    [
                        MatchParticipant(
                            match_id=match_id,
                            kind="AGENT",
                            agent_profile_id=agent.id,
                            display_name=agent.name,
                            side=side,
                            seat_no=index % 2 + 3,
                        ),
                        MatchParticipant(
                            match_id=match_id,
                            kind="HUMAN",
                            user_id=users[index].id,
                            display_name=users[index].real_name,
                            side=side,
                            seat_no=index % 2 + 1,
                        ),
                    ]
                )
            session.add(
                AgentGeneration(
                    id=generation_id,
                    match_id=match_id,
                    agent_profile_id=agents[0].id,
                    action_key="1:1",
                    context_version=7,
                    status="PLAYING",
                    created_at=generation_created,
                )
            )
            await session.flush()
            session.add(
                Speech(
                    id=speech_id,
                    match_id=match_id,
                    agent_profile_id=agents[0].id,
                    generation_id=generation_id,
                    action_key="1:1",
                    speaker_kind="AGENT",
                    side="AFFIRMATIVE",
                    seat_no=3,
                    status="STARTED",
                    started_at=speech_started,
                    playback_started_at=speech_started,
                )
            )
            action = MatchAction(
                stage_position=1,
                action_position=1,
                action_kind="AGENT_SPEECH",
                duration_seconds=90,
                speaker_kind="AGENT",
                agent_profile_id=agents[0].id,
                side="AFFIRMATIVE",
                seat_no=3,
            )
            snapshot = state_snapshot(
                MatchRuntimeState(
                    match_id=match_id,
                    status="RUNNING",
                    action_state="AGENT_SPEAKING",
                    actions=(action,),
                    current_speech_id=speech_id,
                    current_agent_profile_id=agents[0].id,
                    sequence=11,
                    speech_start_sequence=10,
                )
            )
            # Context version belongs to the persisted Match, not Actor state.
            snapshot["context_version"] = 7
            matches[0].runtime_snapshot = snapshot
            matches[0].sequence = 11
            matches[0].context_version = 7
            session.add(
                MatchEvent(
                    match_id=match_id,
                    sequence=10,
                    event_type="agent.playback_started",
                    payload={
                        "speech_id": str(speech_id),
                        "generation_id": str(generation_id),
                        "agent_profile_id": str(agents[0].id),
                        "context_version": 7,
                        "attempt_no": 1,
                    },
                    created_at=speech_started,
                )
            )

        owned_matches.extend(
            (match.id, room.id) for match, room in zip(matches, rooms, strict=True)
        )

        async def persisted_snapshot() -> dict[str, object]:
            # This transport reads the actual committed snapshot, not mocked SQL rows.
            async with factory() as session:
                match = await session.get(Match, match_id)
                assert match is not None
                return dict(match.runtime_snapshot)

        clients = SimpleNamespace(
            match_id=match_id,
            settings=SimpleNamespace(
                app_env="test",
                database_url_value=agent_fault_database_url,
                agent_audio_storage_dir=str(source_root),
            ),
            people=[SimpleNamespace(publisher=SimpleNamespace(room=None))],
            snapshot=persisted_snapshot,
        )
        receiver = PassiveReceiver(clients, tmp_path / "received")
        received = b"\x00\x00" * 24_000 + decoded
        record = ReceivedTrack(
            sid="TR_real_pg_control",
            pcm_path=receiver.root / "TR_real_pg_control.pcm",
            stream=None,
            first_frame_utc=(speech_started + timedelta(seconds=0.25)).timestamp(),
            samples=len(received) // 2,
            unsubscribed=True,
            cutoff_samples=len(received) // 2,
            consumer_stopped=True,
            stream_closed=True,
            observation_closed_by_unsubscribe=True,
        )
        record.pcm_path.write_bytes(received)
        receiver.tracks[record.sid] = record
        await receiver.bind(record)
        assert record.error_code is None
        assert record.speech_id == str(speech_id) and record.agent_id == str(agent_ids[0])
        ended = datetime.now(UTC)
        async with factory() as session, session.begin():
            speech = await session.get(Speech, speech_id)
            generation = await session.get(AgentGeneration, generation_id)
            assert speech is not None and generation is not None
            speech.status = generation.status = "FINALIZED"
            speech.llm_draft_text = speech.display_text = "公开合成控件具有完整句末。"
            speech.ended_at = speech.finalized_at = ended
            speech.audio_storage_path = str(source_path)
            speech.audio_duration_ms = len(decoded) * 1000 // (RATE * 2)
            asset = AgentAudioAsset(
                generation_id=generation_id,
                task_id="public-fixture-tts-not-called",
                voice=voices[0].provider_voice,
                rate=1.0,
                status="FINALIZED",
                storage_path=str(source_path),
                byte_count=len(encoded),
                pcm_sample_count=len(decoded) // 2,
                duration_ms=speech.audio_duration_ms,
                finalized_at=ended,
            )
            file = MatchFile(
                match_id=match_id,
                speech_id=speech_id,
                file_key=f"agent-{speech_id}",
                file_kind="AGENT_RAW",
                status="READY",
                storage_path=str(source_path),
                codec="ogg_opus",
                byte_count=len(encoded),
                duration_ms=speech.audio_duration_ms,
            )
            finalizing = MatchEvent(
                match_id=match_id,
                sequence=12,
                event_type="agent.finalizing",
                payload={
                    "speech_id": str(speech_id),
                    "reason": "COMPLETED",
                },
                created_at=ended,
            )
            finalized = MatchEvent(
                match_id=match_id,
                sequence=13,
                event_type="agent.finalized",
                payload={
                    "speech_id": str(speech_id),
                    "generation_id": str(generation_id),
                    "reason": "COMPLETED",
                },
                created_at=ended,
            )
            session.add_all([asset, file, finalizing, finalized])
            await session.flush()
        yield SourceCase(
            factory=factory,
            receiver=receiver,
            record=record,
            match_id=match_id,
            other_match_id=other_match_id,
            speech_id=speech_id,
            generation_id=generation_id,
            asset_id=asset.id,
            file_id=file.id,
            finalizing_event_id=finalizing.id,
            agent_ids=agent_ids,
            source_path=source_path,
            source_pcm=decoded,
            generation_created_at=generation_created,
            speech_started_at=speech_started,
            speech_ended_at=ended,
        )
    finally:
        try:
            for owned_match_id, owned_room_id in owned_matches:
                await terminalize_owned_fault_fixture(
                    factory, match_id=owned_match_id, room_id=owned_room_id
                )
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_passive_proof_reads_real_schema_and_never_qualifies_an_empty_match(tmp_path: Path):
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("requires explicitly enabled independent PostgreSQL")
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url or database_url == os.environ.get("DATABASE_URL"):
        pytest.fail("TEST_DATABASE_URL must be independent")
    clients = SimpleNamespace(
        match_id=uuid4(),
        settings=SimpleNamespace(
            app_env="test",
            database_url_value=database_url,
            agent_audio_storage_dir=str(tmp_path / "source"),
        ),
        people=[SimpleNamespace(publisher=SimpleNamespace(room=None))],
    )
    receiver = PassiveReceiver(clients, tmp_path / "received")
    proof = await receiver.prove()
    assert proof["status"] == "failed"
    assert proof["expected_agent_count"] == proof["agents_tail_proved"] == 0
    assert proof["tracks"] == []
    assert (receiver.root / "RECEIVER_PROOF.json").is_file()


@pytest.mark.asyncio
async def test_real_pg_final_source_chain_and_actual_ogg_tail_are_proved(source_case: SourceCase):
    proof = await source_case.receiver.prove()
    assert proof["status"] == "passed"
    assert proof["expected_agent_count"] == 4
    assert proof["agents_tail_proved"] == 1
    check = proof["tracks"][0]
    assert check["passed"] and check["source_identity_verified"]
    assert check["generation_id"] == str(source_case.generation_id)
    assert check["agent_profile_id"] == str(source_case.agent_ids[0])
    assert check["natural_eof"] is False and check["observation_closed_by_unsubscribe"]
    assert check["tail"]["tail_correlation"] >= 0.60
    assert check["tail"]["minimum_suffix_correlation"] >= 0.45
    assert check["tail"]["thresholds"] == {"tail_ncc": 0.60, "every_suffix_frame_ncc": 0.45}
    assert not check["error_code"] and not check["error_codes"]


@pytest.mark.asyncio
async def test_real_pg_preparing_silence_before_speech_start_is_a_valid_source(
    source_case: SourceCase,
):
    """Regression: RTC publication precedes the first official speech PCM/start.

    This assertion deliberately fails against the old first-frame >= speech-start
    guard.  It must become green only after the real App diagnoses that lifecycle.
    No expected-failure marker or fake source rows hide the old receiver failure.
    """
    case = source_case
    case.record.first_frame_utc = (case.generation_created_at + timedelta(seconds=1)).timestamp()
    assert (
        case.generation_created_at.timestamp()
        <= case.record.first_frame_utc
        < case.speech_started_at.timestamp()
    )
    proof = await case.receiver.prove()
    assert proof["status"] == "passed", proof["tracks"][0].get("proof_error_code")
    check = proof["tracks"][0]
    assert check["passed"] and check["source_identity_verified"]
    assert check["tail"]["passed"]
    assert proof["agent_profile_ids_tail_proved"] == [str(case.agent_ids[0])]


@pytest.mark.asyncio
async def test_real_pg_free_snapshot_binds_current_agent_without_fixed_action_agent(
    source_case: SourceCase,
):
    case = source_case
    free_action = compile_linear_actions(
        {"stages": [{"position": 7, "stage_kind": "FREE_DEBATE", "duration_seconds": 120}]},
        {},
        {
            ("AFFIRMATIVE", 4): case.agent_ids[1],
            ("NEGATIVE", 3): case.agent_ids[2],
        },
    )[0]
    free_snapshot = state_snapshot(
        MatchRuntimeState(
            match_id=case.match_id,
            status="RUNNING",
            action_state="AGENT_SPEAKING",
            actions=(free_action,),
            current_speech_id=case.speech_id,
            current_agent_profile_id=case.agent_ids[1],
            sequence=11,
        )
    )
    async with case.factory() as session, session.begin():
        match = await session.get(Match, case.match_id)
        speech = await session.get(Speech, case.speech_id)
        generation = await session.get(AgentGeneration, case.generation_id)
        asset = await session.get(AgentAudioAsset, case.asset_id)
        profile = await session.get(AgentProfile, case.agent_ids[1])
        assert match is not None and speech is not None and generation is not None
        assert asset is not None and profile is not None
        voice = await session.get(VoiceProfile, profile.voice_profile_id)
        assert voice is not None
        # Free debate allocates the actual Agent on the state, while the base
        # action retains HUMAN/default metadata and no fixed profile.
        speech.agent_profile_id = generation.agent_profile_id = case.agent_ids[1]
        speech.action_key = generation.action_key = free_action.action_key
        speech.seat_no = 4
        asset.voice = voice.provider_voice
        for event in await session.scalars(
            select(MatchEvent).where(MatchEvent.match_id == case.match_id)
        ):
            if "agent_profile_id" in event.payload:
                event.payload = {**event.payload, "agent_profile_id": str(case.agent_ids[1])}
        match.runtime_snapshot = {**free_snapshot, "context_version": 7}
    await case.receiver.bind(case.record)
    assert case.record.error_code is None
    assert case.record.agent_id == str(case.agent_ids[1])
    assert case.record.binding_action_key == "7:0"
    proof = await case.receiver.prove()
    assert proof["status"] == "passed"
    assert proof["agent_profile_ids_tail_proved"] == [str(case.agent_ids[1])]
    assert proof["tracks"][0]["source_identity_verified"]


@pytest.mark.asyncio
@pytest.mark.parametrize("completion", ("finalized", "free_late", "free_release_wrong_speech"))
async def test_real_pg_finalizing_binding_uses_speech_event_window_after_body_end(
    source_case: SourceCase, completion: str
):
    case = source_case
    async with case.factory() as session, session.begin():
        match = await session.get(Match, case.match_id)
        speech = await session.get(Speech, case.speech_id)
        generation = await session.get(AgentGeneration, case.generation_id)
        assert match is not None and speech is not None and generation is not None
        # The synthetic HTTP response is observed after the body ended; the
        # authenticated snapshot represents this speech's real FINALIZING seq.
        speech.started_at -= timedelta(seconds=2)
        assert speech.ended_at is not None
        speech.ended_at -= timedelta(seconds=2)
        generation.created_at -= timedelta(seconds=2)
        assert case.record.first_frame_utc is not None
        case.record.first_frame_utc -= 2
        snapshot = {**match.runtime_snapshot, "action_state": "AGENT_FINALIZING", "sequence": 12}
        if completion != "finalized":
            action = compile_linear_actions(
                {"stages": [{"position": 7, "stage_kind": "FREE_DEBATE", "duration_seconds": 120}]},
                {},
                {("AFFIRMATIVE", 3): case.agent_ids[0], ("NEGATIVE", 3): case.agent_ids[2]},
            )[0]
            snapshot["current_action"] = state_snapshot(
                MatchRuntimeState(
                    match_id=case.match_id,
                    status="RUNNING",
                    action_state="AGENT_FINALIZING",
                    actions=(action,),
                )
            )["current_action"]
            speech.action_key = generation.action_key = action.action_key
            finalized = await session.scalar(
                select(MatchEvent).where(
                    MatchEvent.match_id == case.match_id,
                    MatchEvent.event_type == "agent.finalized",
                )
            )
            assert finalized is not None
            # Exact real HANDOFF payload has a speech ID and no generation ID.
            finalized.event_type = "free.turn_released"
            finalized.payload = {
                "speech_id": str(
                    uuid4() if completion == "free_release_wrong_speech" else case.speech_id
                ),
                "reason": "HANDOFF",
            }
        match.runtime_snapshot = snapshot
    await case.receiver.bind(case.record)
    assert case.record.error_code is None and case.record.binding_observed_utc is not None
    assert case.record.binding_observed_utc > case.speech_ended_at.timestamp() - 2 + 0.5
    proof = await case.receiver.prove()
    check = proof["tracks"][0]
    if completion == "free_release_wrong_speech":
        assert proof["status"] == "failed" and not check["passed"]
        assert check["error_code"] == SOURCE_IDENTITY_ERROR
        assert "binding_terminal_event_present" in check["failed_source_checks"]
    else:
        assert proof["status"] == "passed" and check["source_identity_verified"]
        assert check["tail"]["passed"]
        assert check["source_time_deltas_ms"]["speech_ended_minus_binding_observed"] < -500
        if completion == "free_late":
            assert check["source_event_sequences"]["finalized"] is None
            assert check["source_event_sequences"]["free_turn_released"] == 13


SOURCE_DEFECTS = (
    "bound_agent",
    "generation_missing",
    "generation_agent",
    "generation_match",
    "speech_action",
    "generation_action",
    "generation_status",
    "asset_status",
    "file_status",
    "speech_path",
    "asset_path",
    "file_path",
    "file_speech",
    "file_match",
    "file_kind",
    "before_generation",
    "after_speech_end",
    "binding_before_playback_sequence",
    "binding_at_finalized_sequence",
    "binding_wrong_phase",
    "playback_generation",
    "finalized_generation",
    "unclosed_sentence",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", SOURCE_DEFECTS)
async def test_real_pg_source_guard_rejects_each_inconsistent_identity_fact(
    source_case: SourceCase, defect: str
):
    case = source_case
    async with case.factory() as session, session.begin():
        speech = await session.get(Speech, case.speech_id)
        generation = await session.get(AgentGeneration, case.generation_id)
        asset = await session.get(AgentAudioAsset, case.asset_id)
        file = await session.get(MatchFile, case.file_id)
        assert speech is not None and generation is not None and asset is not None
        assert file is not None
        if defect == "bound_agent":
            case.record.agent_id = str(case.agent_ids[1])
        elif defect == "generation_missing":
            # Speech.generation_id is intentionally not a FK in the real schema.
            speech.generation_id = uuid4()
        elif defect == "generation_agent":
            generation.agent_profile_id = case.agent_ids[1]
        elif defect == "generation_match":
            generation.match_id = case.other_match_id
        elif defect == "speech_action":
            speech.action_key = "9:9"
        elif defect == "generation_action":
            generation.action_key = "9:9"
        elif defect == "generation_status":
            generation.status = "FAILED"
        elif defect == "asset_status":
            asset.status = "FAILED"
        elif defect == "file_status":
            file.status = "PROCESSING"
        elif defect == "speech_path":
            speech.audio_storage_path = str(case.source_path.with_name("different.ogg"))
        elif defect == "asset_path":
            asset.storage_path = str(case.source_path.with_name("different.ogg"))
        elif defect == "file_path":
            file.storage_path = str(case.source_path.with_name("different.ogg"))
        elif defect == "file_speech":
            file.speech_id = None
        elif defect == "file_match":
            file.match_id = case.other_match_id
        elif defect == "file_kind":
            file.file_kind = "HUMAN_RAW"
        elif defect == "before_generation":
            assert case.record.first_frame_utc is not None
            assert case.record.first_frame_utc >= case.speech_started_at.timestamp()
            # Keep firstAny in the old speech window and change only generation
            # creation, so this independently exercises the new lifetime bound.
            generation.created_at = datetime.fromtimestamp(case.record.first_frame_utc + 0.001, UTC)
        elif defect == "after_speech_end":
            case.record.first_frame_utc = case.speech_ended_at.timestamp() + 0.501
        elif defect == "binding_before_playback_sequence":
            case.record.binding_snapshot_sequence = 9
        elif defect == "binding_at_finalized_sequence":
            case.record.binding_snapshot_sequence = 13
            case.record.binding_snapshot_state = "AGENT_FINALIZING"
        elif defect == "binding_wrong_phase":
            case.record.binding_snapshot_state = "AGENT_FINALIZING"
        elif defect in {"playback_generation", "finalized_generation"}:
            event = await session.scalar(
                select(MatchEvent).where(
                    MatchEvent.match_id == case.match_id,
                    MatchEvent.event_type
                    == (
                        "agent.playback_started"
                        if defect == "playback_generation"
                        else "agent.finalized"
                    ),
                )
            )
            assert event is not None
            event.payload = {**event.payload, "generation_id": str(uuid4())}
        elif defect == "unclosed_sentence":
            speech.llm_draft_text = "没有闭合句末的公开控件"
        else:
            raise AssertionError("unhandled public fixture defect")
    proof = await case.receiver.prove()
    check = proof["tracks"][0]
    assert not check["passed"], defect
    assert proof["agent_profile_ids_tail_proved"] == [], defect
    assert check["status"] == "failed_safe", defect
    assert check["proof_error_code"] == SOURCE_IDENTITY_ERROR, defect
    assert check["error_code"] == SOURCE_IDENTITY_ERROR, defect
    assert SOURCE_IDENTITY_ERROR in check["error_codes"], defect
    assert "tail" not in check, defect


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ("missing", "outside_owned_root"))
async def test_real_pg_source_chain_cannot_authorize_a_missing_or_unowned_file(
    source_case: SourceCase, tmp_path: Path, defect: str
):
    case = source_case
    if defect == "missing":
        case.source_path.unlink()
    else:
        outside = tmp_path / "outside-source.ogg"
        outside.write_bytes(case.source_path.read_bytes())
        async with case.factory() as session, session.begin():
            speech = await session.get(Speech, case.speech_id)
            asset = await session.get(AgentAudioAsset, case.asset_id)
            file = await session.get(MatchFile, case.file_id)
            assert speech is not None and asset is not None and file is not None
            speech.audio_storage_path = asset.storage_path = file.storage_path = str(outside)
    proof = await case.receiver.prove()
    check = proof["tracks"][0]
    assert proof["status"] == "failed" and not check["passed"]
    assert check["error_code"] == "qa_receiver_source_not_owned"
    assert check["proof_error_code"] == "qa_receiver_source_not_owned"
    assert "tail" not in check


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("defect", "expected_status"),
    (
        ("unknown_speech", "retired_or_unbound_not_qualified"),
        ("other_match_speech", "retired_or_unbound_not_qualified"),
        ("speech_not_final", "retired_or_unbound_not_qualified"),
        ("placeholder", "retired_or_unbound_not_qualified"),
        ("truncated", "truncated_not_qualified"),
        ("not_completed", "truncated_not_qualified"),
    ),
)
async def test_real_pg_retired_or_incomplete_speech_never_counts_as_tail_coverage(
    source_case: SourceCase, defect: str, expected_status: str
):
    case = source_case
    async with case.factory() as session, session.begin():
        speech = await session.get(Speech, case.speech_id)
        finalizing = await session.get(MatchEvent, case.finalizing_event_id)
        assert speech is not None and finalizing is not None
        if defect == "unknown_speech":
            case.record.speech_id = str(uuid4())
        elif defect == "other_match_speech":
            speech.match_id = case.other_match_id
        elif defect == "speech_not_final":
            speech.status = "FAILED"
        elif defect == "placeholder":
            speech.origin = "ADMIN_PLACEHOLDER"
        elif defect == "truncated":
            speech.audio_truncated = True
        elif defect == "not_completed":
            finalizing.payload = {**finalizing.payload, "reason": "DEADLINE"}
        else:
            raise AssertionError("unhandled public fixture defect")
    proof = await case.receiver.prove()
    assert proof["agents_tail_proved"] == 0
    assert proof["agent_profile_ids_tail_proved"] == []
    assert proof["tracks"][0]["status"] == expected_status
    assert proof["tracks"][0]["passed"] is False
    assert "tail" not in proof["tracks"][0]


@pytest.mark.asyncio
async def test_real_pg_source_identity_does_not_hide_a_missing_160ms_audio_tail(
    source_case: SourceCase,
):
    case = source_case
    received = b"\x00\x00" * 24_000 + case.source_pcm[: -RATE * 2 * 160 // 1000]
    case.record.pcm_path.write_bytes(received)
    case.record.samples = case.record.cutoff_samples = len(received) // 2
    proof = await case.receiver.prove()
    check = proof["tracks"][0]
    assert proof["status"] == "failed"
    assert check["source_identity_verified"]
    assert check["status"] == "compared" and not check["passed"]
    assert check["error_code"] == "qa_receiver_tail_mismatch"
    assert check["tail"]["passed"] is False
    assert check["tail"]["thresholds"] == {"tail_ncc": 0.60, "every_suffix_frame_ncc": 0.45}
    assert proof["agent_profile_ids_tail_proved"] == []


@pytest.mark.asyncio
async def test_real_pg_waveform_failure_is_safe_observation_but_never_delivery_coverage(
    source_case: SourceCase,
):
    from scripts.qa.probes.server_spec308_receiver import evaluate_receiver_proof

    case = source_case
    received = b"\x00\x00" * 24_000 + case.source_pcm[: -RATE * 2 * 160 // 1000]
    case.record.pcm_path.write_bytes(received)
    case.record.samples = case.record.cutoff_samples = len(received) // 2
    proof = await case.receiver.prove()
    assert proof["status"] == "failed"
    assert proof["tracks"][0]["error_code"] == "qa_receiver_tail_mismatch"
    evaluation = evaluate_receiver_proof(
        proof, str(case.match_id), [str(a) for a in case.agent_ids]
    )
    assert evaluation["status"] == "passed"
    assert evaluation["alltracks_status"] == "failed"
    assert evaluation["alltracks_failed_track_count"] == 1
    assert evaluation["agent_profile_ids_tail_proved"] == []
    assert proof["tracks"][0]["passed"] is False


@pytest.mark.asyncio
async def test_real_pg_reset_observation_has_exact_retired_identity_without_final_source(
    source_case: SourceCase,
):
    from scripts.qa.probes.server_spec308_receiver import evaluate_receiver_proof

    case = source_case
    async with case.factory() as session, session.begin():
        speech = await session.get(Speech, case.speech_id)
        generation = await session.get(AgentGeneration, case.generation_id)
        assert speech is not None and generation is not None
        speech.status = "RESET"
        speech.llm_draft_text = ""
        generation.status = "CANCELLED"
    proof = await case.receiver.prove()
    track = proof["tracks"][0]
    assert track["status"] == "retired_or_unbound_not_qualified"
    assert "tail" not in track
    assert track["exclusion_identity"]["generation_id"] == str(case.generation_id)
    evaluation = evaluate_receiver_proof(
        proof, str(case.match_id), [str(a) for a in case.agent_ids]
    )
    assert evaluation["status"] == "passed"
    assert evaluation["agent_profile_ids_tail_proved"] == []
    assert evaluation["not_qualified_track_count"] == 1

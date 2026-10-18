"""Passive receiver controls; no network, providers or native RTC connections."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import numpy as np
import pytest
from livekit import rtc
from scripts.qa.probes.server_spec308_receiver import (
    ClientFailure,
    PassiveReceiver,
    ReceivedTrack,
    aggregate_receiver_proofs,
    observation_complete,
    source_identity_evidence,
)
from scripts.qa.probes.spec308_tail_matcher import compare_tail

from jx_core.matches.domain import MatchRuntimeState, compile_linear_actions
from jx_core.matches.schemas import MatchActionResponse
from jx_core.matches.service import state_snapshot


def clients():
    match_id = uuid4()
    return SimpleNamespace(
        match_id=match_id,
        people=[SimpleNamespace(publisher=SimpleNamespace(room=SimpleNamespace()))],
        snapshot=AsyncMock(
            return_value={
                "match_id": str(match_id),
                "action_state": "AGENT_SPEAKING",
                "sequence": 11,
                "context_version": 7,
                "current_action_index": 0,
                "current_action": {
                    "stage_position": 1,
                    "action_position": 1,
                    "action_kind": "AGENT_SPEECH",
                },
                "current_speech_id": str(uuid4()),
                "current_agent_profile_id": str(uuid4()),
            }
        ),
    )


def test_receiver_subscribes_only_to_the_current_match_agent_participant(tmp_path: Path) -> None:
    client = clients()
    receiver = PassiveReceiver(client, tmp_path)
    publication = SimpleNamespace(kind=rtc.TrackKind.KIND_AUDIO, set_subscribed=lambda _: None)
    assert receiver.accepts(
        publication, SimpleNamespace(identity=f"jx-agent-{client.match_id}-abc")
    )
    for identity in (f"jx-host-{client.match_id}-abc", f"jx-agent-{uuid4()}-abc", "public-human"):
        assert not receiver.accepts(publication, SimpleNamespace(identity=identity))
    publication.kind = rtc.TrackKind.KIND_VIDEO
    assert not receiver.accepts(
        publication, SimpleNamespace(identity=f"jx-agent-{client.match_id}-abc")
    )


@pytest.mark.asyncio
async def test_unsubscribe_synchronously_cuts_off_late_pcm_without_claiming_natural_eof(
    tmp_path: Path,
):
    client = clients()
    speech, agent = str(uuid4()), str(uuid4())
    base_snapshot = client.snapshot.return_value
    client.snapshot.side_effect = [
        {**base_snapshot, "action_state": "AGENT_PREPARING", "current_speech_id": None},
        {
            **base_snapshot,
            "action_state": "AGENT_SPEAKING",
            "current_speech_id": speech,
            "current_agent_profile_id": agent,
        },
    ]
    receiver = PassiveReceiver(client, tmp_path)
    body = b"\x01\x00" * 960

    class Stream:
        closed = False

        async def __aiter__(self):
            yield SimpleNamespace(
                frame=SimpleNamespace(data=body, sample_rate=48000, num_channels=1)
            )
            receiver.unsubscribed(None, SimpleNamespace(sid="TR_fixture"), None)
            yield SimpleNamespace(
                frame=SimpleNamespace(data=body, sample_rate=48000, num_channels=1)
            )

        async def aclose(self):
            self.closed = True

    record = ReceivedTrack("TR_fixture", receiver.root / "TR_fixture.pcm", Stream())
    receiver.tracks[record.sid] = record
    await receiver.consume(record)
    assert not record.eof and record.unsubscribed and record.stream.closed
    assert record.observation_closed_by_unsubscribe and observation_complete(record)
    assert record.error_code is None
    assert record.pcm_path.read_bytes() == body
    assert record.samples == record.cutoff_samples == 960
    assert record.speech_id == speech and record.agent_id == agent
    assert record.binding_snapshot_match_id == str(client.match_id)
    assert record.binding_action_key == "1:1" and record.binding_snapshot_sequence == 11
    assert record.binding_observed_utc is not None
    assert not record.pcm


@pytest.mark.asyncio
async def test_nonagent_snapshot_never_gets_assigned_to_a_received_agent_track(tmp_path: Path):
    client = clients()
    client.snapshot.return_value = {
        "action_state": "HUMAN_SPEAKING",
        "current_speech_id": str(uuid4()),
        "current_agent_profile_id": None,
    }
    receiver = PassiveReceiver(client, tmp_path)
    record = ReceivedTrack("TR_fixture", receiver.root / "TR_fixture.pcm", None)
    await receiver.bind(record)
    assert record.speech_id is None and record.agent_id is None
    assert record.error_code == "qa_receiver_binding_not_agent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    (
        "match",
        "sequence",
        "action",
        "free_nonzero_position",
        "fixed_zero_position",
        "non_agent_kind",
    ),
)
async def test_binding_rejects_incomplete_or_cross_match_authoritative_snapshot(
    tmp_path: Path, defect: str
):
    client = clients()
    snapshot = dict(client.snapshot.return_value)
    if defect == "match":
        snapshot["match_id"] = str(uuid4())
    elif defect == "sequence":
        snapshot.pop("sequence")
    elif defect == "action":
        snapshot["current_action"] = None
    elif defect == "free_nonzero_position":
        snapshot["current_action"] = {**snapshot["current_action"], "action_kind": "FREE_DEBATE"}
    elif defect == "fixed_zero_position":
        snapshot["current_action"] = {**snapshot["current_action"], "action_position": 0}
    else:
        snapshot["current_action"] = {**snapshot["current_action"], "action_kind": "HUMAN_SPEECH"}
    client.snapshot.return_value = snapshot
    receiver = PassiveReceiver(client, tmp_path)
    record = ReceivedTrack("TR_bad_snapshot", receiver.root / "bad.pcm", None)
    await receiver.bind(record)
    assert record.error_code == "qa_receiver_binding_snapshot_invalid"
    assert record.speech_id is record.agent_id is record.binding_observed_utc is None


@pytest.mark.asyncio
async def test_preparing_context_can_change_before_the_same_authoritative_speech_binding(
    tmp_path: Path,
):
    client = clients()
    snapshot = dict(client.snapshot.return_value)
    client.snapshot.side_effect = [
        {
            **snapshot,
            "action_state": "AGENT_PREPARING",
            "current_speech_id": None,
            "context_version": 6,
        },
        snapshot,
    ]
    receiver = PassiveReceiver(client, tmp_path)
    record = ReceivedTrack("TR_context", receiver.root / "context.pcm", None)
    await receiver.bind(record)
    assert record.error_code is None and record.binding_context_version == 7


@pytest.mark.asyncio
async def test_free_action_binds_each_actual_current_agent_without_a_fixed_action_agent(
    tmp_path: Path,
):
    client = clients()
    base = dict(client.snapshot.return_value)
    receiver = PassiveReceiver(client, tmp_path)
    selected_agents = [str(uuid4()), str(uuid4())]
    free_action = compile_linear_actions(
        {"stages": [{"position": 7, "stage_kind": "FREE_DEBATE", "duration_seconds": 120}]},
        {},
        {
            ("AFFIRMATIVE", 3): UUID(selected_agents[0]),
            ("NEGATIVE", 3): UUID(selected_agents[1]),
        },
    )[0]
    public_action = state_snapshot(
        MatchRuntimeState(
            match_id=client.match_id,
            status="RUNNING",
            action_state="AGENT_SPEAKING",
            actions=(free_action,),
        )
    )["current_action"]
    public_action = MatchActionResponse.model_validate(public_action).model_dump(
        mode="json", exclude_none=True
    )
    assert "agent_profile_id" not in public_action and public_action["speaker_kind"] == "HUMAN"
    for index, agent_id in enumerate(selected_agents):
        client.snapshot.return_value = {
            **base,
            "current_agent_profile_id": agent_id,
            "current_speech_id": str(uuid4()),
            "current_action": public_action,
        }
        record = ReceivedTrack(f"TR_free_{index}", receiver.root / f"free-{index}.pcm", None)
        await receiver.bind(record)
        assert record.error_code is None
        assert record.agent_id == agent_id and record.binding_action_key == "7:0"


def source_fixture(tmp_path: Path):
    match_id, agent_id = uuid4(), uuid4()
    start = datetime(2026, 10, 17, tzinfo=UTC)
    record = ReceivedTrack(
        "TR_source",
        tmp_path / "source.pcm",
        None,
        speech_id=str(uuid4()),
        agent_id=str(agent_id),
        first_frame_utc=(start + timedelta(seconds=0.25)).timestamp(),
        binding_observed_utc=(start + timedelta(seconds=0.08)).timestamp(),
        binding_snapshot_match_id=str(match_id),
        binding_snapshot_sequence=11,
        binding_snapshot_state="AGENT_SPEAKING",
        binding_action_index=0,
        binding_action_key="1:1",
        binding_context_version=7,
    )
    row = {
        "agent_profile_id": agent_id,
        "action_key": "1:1",
        "generation_action_key": "1:1",
        "generation_id": uuid4(),
        "generation_status": "FINALIZED",
        "generation_record_status": "FINALIZED",
        "generation_created_at": start - timedelta(seconds=1),
        "generation_context_version": 7,
        "playback_started_sequence": 10,
        "finalizing_sequence": 12,
        "finalized_sequence": 13,
        "free_turn_released_sequence": None,
        "file_status": "READY",
        "audio_storage_path": "/owned/public-source.ogg",
        "generation_path": "/owned/public-source.ogg",
        "file_path": "/owned/public-source.ogg",
        "sentence_closed": True,
        "started_at": start,
        "ended_at": start + timedelta(seconds=3),
        "status": "FINALIZED",
        "origin": "ORIGINAL",
        "reason": "COMPLETED",
    }
    return match_id, record, row


def test_source_timeline_separates_preparing_pcm_and_speech_binding_without_paths(tmp_path: Path):
    match_id, record, row = source_fixture(tmp_path)
    record.first_frame_utc = row["started_at"].timestamp() - 0.5
    evidence = source_identity_evidence(record, row, match_id)
    assert all(evidence["source_checks"].values())
    assert evidence["failed_source_checks"] == []
    assert evidence["source_time_deltas_ms"]["first_any_minus_speech_started"] == -500
    assert evidence["source_time_deltas_ms"]["binding_observed_minus_speech_started"] == 80
    assert "/owned/public-source.ogg" not in json.dumps(evidence)


@pytest.mark.parametrize(
    ("defect", "failed_check"),
    (
        ("generation_late", "first_frame_not_before_generation_created"),
        ("first_frame_late", "first_frame_not_after_speech_ended"),
        ("binding_early", "binding_sequence_not_before_playback"),
        ("binding_late", "binding_sequence_before_terminal"),
        ("binding_wrong_phase", "binding_phase_matches_event_interval"),
        ("binding_match", "binding_match_matches_target"),
        ("speech_action", "speech_action_matches_binding"),
        ("generation_action", "generation_action_matches_speech"),
    ),
)
def test_source_evidence_isolates_each_lifecycle_and_action_failure(
    tmp_path: Path, defect: str, failed_check: str
):
    match_id, record, row = source_fixture(tmp_path)
    if defect == "generation_late":
        row["generation_created_at"] = row["started_at"] + timedelta(seconds=0.251)
    elif defect == "first_frame_late":
        record.first_frame_utc = row["ended_at"].timestamp() + 0.501
    elif defect == "binding_early":
        record.binding_snapshot_sequence = 9
    elif defect == "binding_late":
        record.binding_snapshot_state = "AGENT_FINALIZING"
        record.binding_snapshot_sequence = 13
    elif defect == "binding_wrong_phase":
        record.binding_snapshot_state = "AGENT_FINALIZING"
    elif defect == "binding_match":
        record.binding_snapshot_match_id = str(uuid4())
    elif defect == "speech_action":
        record.binding_action_key = "9:9"
    elif defect == "generation_action":
        row["generation_action_key"] = "9:9"
    else:
        raise AssertionError("unhandled public fixture defect")
    evidence = source_identity_evidence(record, row, match_id)
    assert evidence["failed_source_checks"] == [failed_check]


def test_finalizing_binding_uses_authoritative_sequence_after_body_ended_not_http_time(
    tmp_path: Path,
):
    match_id, record, row = source_fixture(tmp_path)
    record.binding_snapshot_state = "AGENT_FINALIZING"
    record.binding_snapshot_sequence = 12
    record.binding_observed_utc = row["ended_at"].timestamp() + 5
    evidence = source_identity_evidence(record, row, match_id)
    assert all(evidence["source_checks"].values())
    assert evidence["source_time_deltas_ms"]["speech_ended_minus_binding_observed"] == -5000


def test_late_free_finalization_uses_exact_speech_release_event_without_finalized_event(
    tmp_path: Path,
):
    match_id, record, row = source_fixture(tmp_path)
    record.binding_action_key = row["action_key"] = row["generation_action_key"] = "7:0"
    row["finalized_sequence"] = None
    row["free_turn_released_sequence"] = 13
    evidence = source_identity_evidence(record, row, match_id)
    assert all(evidence["source_checks"].values())
    assert evidence["source_event_sequences"]["terminal"] == 13
    record.binding_snapshot_state = "AGENT_FINALIZING"
    record.binding_snapshot_sequence = 13
    evidence = source_identity_evidence(record, row, match_id)
    assert evidence["failed_source_checks"] == ["binding_sequence_before_terminal"]


def test_exact_spec305_matcher_and_thresholds_reject_missing_real_audio_suffix() -> None:
    root = Path(__file__).resolve().parents[3]
    original = root / "profile_output/spec305/voice-variant-v2/tail_marker.py"
    copied = root / "scripts/qa/probes/spec308_tail_matcher.py"
    if original.is_file():
        assert (
            hashlib.sha256(original.read_bytes()).digest()
            == hashlib.sha256(copied.read_bytes()).digest()
        )
    source = np.random.default_rng(308).normal(0, 5000, 96000).astype("<i2").tobytes()
    full = compare_tail(source, b"\x00\x00" * 960 + source, synthetic=False)
    cut = compare_tail(source, source[:-15360], synthetic=False)
    assert full["passed"]
    assert full["thresholds"] == {"tail_ncc": 0.60, "every_suffix_frame_ncc": 0.45}
    assert not cut["passed"]


def receiver_cases(root: Path, counts: tuple[int, int]):
    agents = [str(uuid4()) for _ in range(4)]
    matches = [str(uuid4()), str(uuid4())]
    offset = 0
    for match, count in zip(matches, counts, strict=True):
        covered = agents[offset : offset + count]
        offset += count
        proof = {
            "status": "passed",
            "match_id": match,
            "passive_only": True,
            "synthetic_marker_used": False,
            "expected_agent_profile_ids": agents,
            "agent_profile_ids_tail_proved": covered,
            "tracks": [
                {
                    "status": "compared",
                    "passed": True,
                    "agent_profile_id": agent,
                    "speech_id": str(uuid4()),
                    "generation_id": str(uuid4()),
                    "natural_eof": True,
                    "observation_closed_by_unsubscribe": False,
                    "unsubscribed_observed": False,
                    "cutoff_samples": None,
                    "received_samples": 96000,
                    "consumer_stopped": True,
                    "stream_closed": True,
                    "source_identity_verified": True,
                    "sentence_closed": True,
                    "error_code": None,
                    "error_codes": [],
                    "tail": {
                        "passed": True,
                        "tail_correlation": 0.8,
                        "minimum_suffix_correlation": 0.7,
                        "thresholds": {"tail_ncc": 0.60, "every_suffix_frame_ncc": 0.45},
                    },
                }
                for agent in covered
            ],
        }
        proof.update(
            source="actual_complete_agent_ogg",
            expected_agent_count=4,
            agents_tail_proved=len(covered),
            error_code=None,
            thresholds={"tail_ncc": 0.60, "every_suffix_frame_ncc": 0.45},
        )
        for index, track in enumerate(proof["tracks"]):
            _, record, row = source_fixture(root)
            record.binding_snapshot_match_id = match
            record.speech_id = track["speech_id"]
            record.agent_id = track["agent_profile_id"]
            row["agent_profile_id"] = UUID(track["agent_profile_id"])
            evidence = source_identity_evidence(record, row, UUID(match))
            track.update(
                evidence,
                track_sid=f"TR_case_{index}",
                observed_queue_high_water=0,
                queued_audio_frames_at_eof=0,
                queued_audio_frames_at_cutoff=None,
                exact_sdk_drop_counter_available=False,
                source_file_checks={"is_file": True, "under_owned_source_root": True},
                source_ogg_sha256="a" * 64,
                source_pcm_sha256="b" * 64,
                received_pcm_sha256="c" * 64,
            )
            track["tail"].update(
                last_six_frame_correlations=[0.7] * 6,
                matched_tail_samples=48000,
                reference_audible_end_sample=48000,
                source_samples=48000,
                source_silent_tail_ignored_samples=0,
                received_match_start_sample=1000,
                received_match_end_sample=49000,
                received_samples=96000,
            )
        path = root / "passive-receiver" / match / "RECEIVER_PROOF.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(proof))
    return matches, agents


def test_two_case_union_proves_four_agents_without_requiring_each_case_to_cover_four(
    tmp_path: Path,
):
    matches, agents = receiver_cases(tmp_path, (2, 2))
    proof = aggregate_receiver_proofs(tmp_path, matches, agents)
    assert proof["status"] == "passed"
    assert set(proof["agent_profile_ids_tail_proved"]) == set(agents)


def test_two_case_union_rejects_one_missing_designated_agent(tmp_path: Path):
    matches, agents = receiver_cases(tmp_path, (2, 1))
    with pytest.raises(ClientFailure, match="qa_receiver_aggregate_coverage_incomplete"):
        aggregate_receiver_proofs(tmp_path, matches, agents)
    result = json.loads((tmp_path / "RECEIVER_AGGREGATE.json").read_text())
    assert result["status"] == "failed"


@pytest.mark.parametrize("defect", ["missing_eof", "bad_suffix", "wrong_summary"])
def test_aggregate_rejects_claimed_coverage_without_valid_source_eof_and_tail_proof(
    tmp_path, defect
):
    matches, agents = receiver_cases(tmp_path, (2, 2))
    path = tmp_path / "passive-receiver" / matches[0] / "RECEIVER_PROOF.json"
    proof = json.loads(path.read_text())
    if defect == "missing_eof":
        proof["tracks"][0]["natural_eof"] = False
    elif defect == "bad_suffix":
        proof["tracks"][0]["tail"]["minimum_suffix_correlation"] = 0.44
    else:
        proof["agent_profile_ids_tail_proved"] = []
    path.write_text(json.dumps(proof))
    with pytest.raises(ClientFailure):
        aggregate_receiver_proofs(tmp_path, matches, agents)


class NeverEndingStream:
    """SDK shape: unsubscribe never emits EOS and queued silence can continue forever."""

    def __init__(self):
        self.frames = asyncio.Queue()
        self.closed = False
        self.close_calls = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.frames.get()

    async def aclose(self):
        self.close_calls += 1
        self.closed = True


def frame(body=b"\x01\x00" * 960):
    return SimpleNamespace(frame=SimpleNamespace(data=body, sample_rate=48000, num_channels=1))


async def running_capture(tmp_path):
    receiver = PassiveReceiver(clients(), tmp_path)
    stream = NeverEndingStream()
    record = ReceivedTrack("TR_fixture", receiver.root / "TR_fixture.pcm", stream)
    receiver.tracks[record.sid] = record
    record.task = asyncio.create_task(receiver.consume(record))
    await stream.frames.put(frame())
    async with asyncio.timeout(0.5):
        while record.samples != 960:
            await asyncio.sleep(0)
    return receiver, record, stream


@pytest.mark.asyncio
async def test_no_eof_unsubscribe_closes_promptly_and_excludes_queued_and_late_frames(tmp_path):
    receiver, record, stream = await running_capture(tmp_path)
    stream.frames.put_nowait(frame())  # Already queued, but not consumed before the callback.
    receiver.unsubscribed(None, SimpleNamespace(sid=record.sid), None)
    frozen_samples = record.cutoff_samples
    stream.frames.put_nowait(frame())
    receiver.unsubscribed(None, SimpleNamespace(sid=record.sid), None)
    await asyncio.wait_for(record.task, 0.5)
    assert record.samples == frozen_samples == 960
    assert record.pcm_path.read_bytes() == b"\x01\x00" * 960
    assert stream.close_calls == 1 and stream.closed
    assert record.consumer_stopped and record.observation_closed_by_unsubscribe
    assert observation_complete(record) and not record.eof and not record.error_codes


@pytest.mark.asyncio
async def test_natural_eof_remains_distinct_from_unsubscribe_closure(tmp_path):
    receiver = PassiveReceiver(clients(), tmp_path)

    class Stream:
        async def __aiter__(self):
            yield frame()

        async def aclose(self):
            return None

    record = ReceivedTrack("TR_eof", receiver.root / "TR_eof.pcm", Stream())
    await receiver.consume(record)
    assert record.eof and observation_complete(record)
    assert not record.observation_closed_by_unsubscribe and record.cutoff_samples is None


@pytest.mark.asyncio
async def test_unexpected_cancel_is_failed_even_when_stream_closes(tmp_path):
    receiver, record, _ = await running_capture(tmp_path)
    record.task.cancel()
    await asyncio.wait_for(record.task, 0.5)
    assert record.error_code == "qa_receiver_capture_cancelled"
    assert record.stream_closed and record.consumer_stopped
    assert not observation_complete(record)


@pytest.mark.asyncio
async def test_same_tick_cutoff_during_frame_access_is_checked_again_before_append(tmp_path):
    receiver = PassiveReceiver(clients(), tmp_path)

    class LateFrame:
        sample_rate = 48000
        num_channels = 1

        @property
        def data(self):
            receiver.unsubscribed(None, SimpleNamespace(sid="TR_late"), None)
            return b"\x02\x00" * 960

    class Stream:
        async def __aiter__(self):
            yield frame()
            yield SimpleNamespace(frame=LateFrame())

        async def aclose(self):
            return None

    record = ReceivedTrack("TR_late", receiver.root / "TR_late.pcm", Stream())
    receiver.tracks[record.sid] = record
    await receiver.consume(record)
    assert record.samples == record.cutoff_samples == 960
    assert record.pcm_path.read_bytes() == b"\x01\x00" * 960
    assert record.observation_closed_by_unsubscribe and not record.eof


@pytest.mark.asyncio
async def test_stream_root_code_survives_cleanup_failure(tmp_path):
    receiver = PassiveReceiver(clients(), tmp_path)

    class Stream:
        async def __aiter__(self):
            yield frame(b"odd")

        async def aclose(self):
            raise RuntimeError("not-exported")

    record = ReceivedTrack("TR_bad", receiver.root / "TR_bad.pcm", Stream())
    await receiver.consume(record)
    assert record.error_code == "qa_receiver_pcm_format"
    assert record.error_codes == ["qa_receiver_pcm_format", "qa_receiver_stream_cleanup_failed"]
    assert not observation_complete(record)


@pytest.mark.asyncio
async def test_queue_possible_drop_is_never_cleared_by_unsubscribe(tmp_path):
    receiver, record, stream = await running_capture(tmp_path)
    stream._queue = SimpleNamespace(_queue=deque([object()] * 199))
    await stream.frames.put(frame())
    async with asyncio.timeout(0.5):
        while record.samples != 1920:
            await asyncio.sleep(0)
    receiver.unsubscribed(None, SimpleNamespace(sid=record.sid), None)
    await asyncio.wait_for(record.task, 0.5)
    assert record.error_code == "qa_receiver_queue_overflow_possible"
    assert record.queue_high_water == 199
    assert not record.observation_closed_by_unsubscribe


@pytest.mark.asyncio
async def test_hard_pcm_budget_remains_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.qa.probes.server_spec308_receiver.MAX_TOTAL_BYTES", 1920)
    receiver, record, stream = await running_capture(tmp_path)
    await stream.frames.put(frame())
    await asyncio.wait_for(record.task, 0.5)
    assert record.samples == 960 and receiver.total_bytes == 1920
    assert record.error_code == "qa_receiver_pcm_budget"
    assert not record.observation_closed_by_unsubscribe


@pytest.mark.asyncio
async def test_no_unsubscribe_or_eof_is_a_capture_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.qa.probes.server_spec308_receiver.CAPTURE_TIMEOUT_SECONDS", 0.01)
    receiver = PassiveReceiver(clients(), tmp_path)
    record = ReceivedTrack("TR_hung", receiver.root / "TR_hung.pcm", NeverEndingStream())
    await asyncio.wait_for(receiver.consume(record), 0.5)
    assert record.error_code == "qa_receiver_stream_timeout"
    assert record.stream_closed and not observation_complete(record)


@pytest.mark.asyncio
async def test_finish_keeps_unsubscribe_listener_until_observation_closes(tmp_path):
    receiver, record, _ = await running_capture(tmp_path)
    receiver.room.off = Mock()
    receiver.prove = AsyncMock(return_value={"status": "passed"})
    task = asyncio.create_task(receiver.finish())
    await asyncio.sleep(0)
    assert not any(
        call.args[0] == "track_unsubscribed" for call in receiver.room.off.call_args_list
    )
    receiver.unsubscribed(None, SimpleNamespace(sid=record.sid), None)
    await asyncio.wait_for(task, 0.5)
    assert record.observation_closed_by_unsubscribe
    assert receiver.room.off.call_args_list[-1].args[0] == "track_unsubscribed"


@pytest.mark.asyncio
async def test_finish_timeout_preserves_unexpected_cancellation_reason(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.qa.probes.server_spec308_receiver.FINISH_TIMEOUT_SECONDS", 0.01)
    receiver, record, _ = await running_capture(tmp_path)
    receiver.room.off = Mock()
    receiver.prove = AsyncMock(return_value={"status": "failed"})
    await asyncio.wait_for(receiver.finish(), 0.5)
    assert receiver.error_code == "qa_receiver_finish_timeout"
    assert record.error_code == "qa_receiver_capture_cancelled"


@pytest.mark.parametrize("defect", [None, "bad_cutoff", "not_stopped", "not_closed", "drop_error"])
def test_aggregate_accepts_only_verified_unsubscribe_closure_and_unchanged_tail_gate(
    tmp_path, defect
):
    matches, agents = receiver_cases(tmp_path, (2, 2))
    for match in matches:
        path = tmp_path / "passive-receiver" / match / "RECEIVER_PROOF.json"
        proof = json.loads(path.read_text())
        for track in proof["tracks"]:
            track.update(
                natural_eof=False,
                observation_closed_by_unsubscribe=True,
                unsubscribed_observed=True,
                cutoff_samples=track["received_samples"],
            )
        if match == matches[0]:
            track = proof["tracks"][0]
            if defect == "bad_cutoff":
                track["cutoff_samples"] -= 1
            if defect == "not_stopped":
                track["consumer_stopped"] = False
            if defect == "not_closed":
                track["stream_closed"] = False
            if defect == "drop_error":
                track["error_codes"] = ["qa_receiver_queue_overflow_possible"]
        path.write_text(json.dumps(proof))
    if defect is None:
        assert aggregate_receiver_proofs(tmp_path, matches, agents)["status"] == "passed"
    else:
        with pytest.raises(ClientFailure, match="qa_receiver_aggregate_track_not_proved"):
            aggregate_receiver_proofs(tmp_path, matches, agents)


@pytest.mark.asyncio
async def test_unsubscribe_before_consumer_starts_still_closes_once_and_stays_non_eof(tmp_path):
    receiver = PassiveReceiver(clients(), tmp_path)
    stream = NeverEndingStream()
    record = ReceivedTrack("TR_early", receiver.root / "TR_early.pcm", stream)
    receiver.tracks[record.sid] = record
    record.task = asyncio.create_task(receiver.consume(record))
    receiver.unsubscribed(None, SimpleNamespace(sid=record.sid), None)
    await asyncio.wait_for(record.task, 0.5)
    assert record.samples == record.cutoff_samples == 0
    assert stream.close_calls == 1 and record.observation_closed_by_unsubscribe
    assert record.consumer_stopped and not record.eof


@pytest.mark.asyncio
async def test_stream_close_timeout_is_specific_and_never_qualifies_eof(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "scripts.qa.probes.server_spec308_receiver.STREAM_CLOSE_TIMEOUT_SECONDS", 0.01
    )
    receiver = PassiveReceiver(clients(), tmp_path)

    class Stream:
        async def __aiter__(self):
            yield frame()

        async def aclose(self):
            await asyncio.Event().wait()

    record = ReceivedTrack("TR_close", receiver.root / "TR_close.pcm", Stream())
    await asyncio.wait_for(receiver.consume(record), 0.5)
    assert record.eof and not record.stream_closed
    assert record.error_code == "qa_receiver_stream_close_timeout"
    assert not observation_complete(record)


def add_waveform_failure(root, match):
    from copy import deepcopy

    path = root / "passive-receiver" / match / "RECEIVER_PROOF.json"
    proof = json.loads(path.read_text())
    track = deepcopy(proof["tracks"][0])
    track["track_sid"] += "_extra"
    track["speech_id"] = str(uuid4())
    track["generation_id"] = str(uuid4())
    track["binding_snapshot"]["speech_id"] = track["speech_id"]
    track["passed"] = track["tail"]["passed"] = False
    track["tail"]["last_six_frame_correlations"][-1] = 0.2
    track["tail"]["minimum_suffix_correlation"] = 0.2
    track["error_code"] = "qa_receiver_tail_mismatch"
    track["error_codes"] = ["qa_receiver_tail_mismatch"]
    proof["tracks"].append(track)
    proof["status"] = "failed"
    path.write_text(json.dumps(proof))
    return path, proof


def test_spec_coverage_retains_extra_waveform_failure_without_changing_any_original_proof(tmp_path):
    matches, agents = receiver_cases(tmp_path, (2, 2))
    path, _ = add_waveform_failure(tmp_path, matches[0])
    original = path.read_bytes()
    result = aggregate_receiver_proofs(tmp_path, matches, agents)
    assert result["status"] == "passed"
    assert result["alltracks_failed_track_count"] == 1
    assert result["cases"][0]["alltracks_status"] == "failed"
    assert result["cases"][0]["tail_mismatch_tracks"][0]["passed"] is False
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "defect",
    [
        "empty_source",
        "missing_source",
        "source_false",
        "binding_agent",
        "capture_extra_error",
        "unknown_error",
        "not_closed",
        "forged_pass",
        "unbound_retirement",
        "unknown_status",
    ],
)
def test_extra_failed_track_never_hides_capture_source_or_unknown_evidence(tmp_path, defect):
    matches, agents = receiver_cases(tmp_path, (2, 2))
    path, proof = add_waveform_failure(tmp_path, matches[0])
    track = proof["tracks"][-1]
    if defect == "empty_source":
        track["source_checks"] = {}
    elif defect == "missing_source":
        del track["source_checks"]["generation_present"]
    elif defect == "source_false":
        track["source_checks"]["generation_present"] = False
    elif defect == "binding_agent":
        track["binding_snapshot"]["agent_profile_id"] = str(uuid4())
    elif defect == "capture_extra_error":
        track["error_codes"].append("qa_receiver_pcm_budget")
    elif defect == "unknown_error":
        track["error_code"] = "unknown"
    elif defect == "not_closed":
        track["stream_closed"] = False
    elif defect == "forged_pass":
        track["passed"] = track["tail"]["passed"] = True
    elif defect == "unbound_retirement":
        track.update(status="retired_or_unbound_not_qualified", error_code=None, error_codes=[])
    elif defect == "unknown_status":
        track["status"] = "unknown"
    path.write_text(json.dumps(proof))
    with pytest.raises(ClientFailure):
        aggregate_receiver_proofs(tmp_path, matches, agents)


@pytest.mark.asyncio
async def test_client_close_preserves_original_failed_proof_but_continues_for_pure_waveform_only(
    tmp_path, monkeypatch
):
    import scripts.qa.probes.server_spec308_receiver as module

    matches, _ = receiver_cases(tmp_path, (2, 2))
    path, proof = add_waveform_failure(tmp_path, matches[0])
    client = module.ConferenceClients.__new__(module.ConferenceClients)
    client.match_id = UUID(matches[0])
    client._passive_receiver = SimpleNamespace(
        finish=AsyncMock(return_value=proof), root=path.parent
    )
    base_close = AsyncMock()
    monkeypatch.setattr(module.BaseConferenceClients, "close", base_close)
    await client.close()
    base_close.assert_awaited_once()
    assert json.loads(path.read_text())["status"] == "failed"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_coverage_rejects_nonfinite_or_boolean_score_numbers(tmp_path, value):
    matches, agents = receiver_cases(tmp_path, (2, 2))
    path = tmp_path / "passive-receiver" / matches[0] / "RECEIVER_PROOF.json"
    proof = json.loads(path.read_text())
    proof["tracks"][0]["tail"]["tail_correlation"] = value
    path.write_text(json.dumps(proof))
    with pytest.raises(ClientFailure):
        aggregate_receiver_proofs(tmp_path, matches, agents)


@pytest.mark.asyncio
async def test_owner_close_failure_still_blocks_after_safe_waveform_diagnostic(
    tmp_path, monkeypatch
):
    import scripts.qa.probes.server_spec308_receiver as module

    matches, _ = receiver_cases(tmp_path, (2, 2))
    path, proof = add_waveform_failure(tmp_path, matches[0])
    client = module.ConferenceClients.__new__(module.ConferenceClients)
    client.match_id = UUID(matches[0])
    client._passive_receiver = SimpleNamespace(
        finish=AsyncMock(return_value=proof), root=path.parent
    )
    monkeypatch.setattr(
        module.BaseConferenceClients, "close", AsyncMock(side_effect=RuntimeError("owned_close"))
    )
    with pytest.raises(RuntimeError, match="owned_close"):
        await client.close()
    assert json.loads(path.read_text())["status"] == "failed"


@pytest.mark.parametrize("conflicting_identity", [False, True])
def test_republished_same_speech_is_independently_checked_without_extra_coverage(
    tmp_path, conflicting_identity
):
    from copy import deepcopy

    matches, agents = receiver_cases(tmp_path, (2, 2))
    path = tmp_path / "passive-receiver" / matches[0] / "RECEIVER_PROOF.json"
    proof = json.loads(path.read_text())
    repeat = deepcopy(proof["tracks"][0])
    repeat["track_sid"] += "_republished"
    if conflicting_identity:
        repeat["generation_id"] = str(uuid4())
    proof["tracks"].append(repeat)
    path.write_text(json.dumps(proof))
    if conflicting_identity:
        with pytest.raises(ClientFailure):
            aggregate_receiver_proofs(tmp_path, matches, agents)
    else:
        result = aggregate_receiver_proofs(tmp_path, matches, agents)
        assert result["status"] == "passed"
        assert len(result["agent_profile_ids_tail_proved"]) == 4


@pytest.mark.parametrize("field", ["is_file", "under_owned_source_root"])
def test_source_file_boolean_fields_do_not_accept_integer_one(tmp_path, field):
    matches, agents = receiver_cases(tmp_path, (2, 2))
    path = tmp_path / "passive-receiver" / matches[0] / "RECEIVER_PROOF.json"
    proof = json.loads(path.read_text())
    proof["tracks"][0]["source_file_checks"][field] = 1
    path.write_text(json.dumps(proof))
    with pytest.raises(ClientFailure):
        aggregate_receiver_proofs(tmp_path, matches, agents)

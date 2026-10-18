"""Passive, bounded receiving proof for public synthetic Spec308 matches only."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from livekit import rtc
from scripts.qa.probes.server_spec307_clients import (
    ClientFailure,
)
from scripts.qa.probes.server_spec307_clients import (
    ConferenceClients as BaseConferenceClients,
)
from scripts.qa.probes.spec308_tail_matcher import compare_tail
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jx_core.agent.audio import decode_ogg_opus_pcm

RATE = 48_000
MAX_TRACKS = 96
MAX_TRACK_BYTES = RATE * 2 * 190
MAX_TOTAL_BYTES = 192 * 1024 * 1024
CAPTURE_TIMEOUT_SECONDS = 180
FINISH_TIMEOUT_SECONDS = 10
STREAM_CLOSE_TIMEOUT_SECONDS = 5


def require(condition: object, code: str) -> None:
    if not condition:
        raise ClientFailure(code)


def receiver_output_root(arguments: list[str]) -> Path:
    require(arguments.count("--out") == 1, "qa_receiver_output_missing")
    position = arguments.index("--out")
    require(position + 1 < len(arguments), "qa_receiver_output_missing")
    raw = Path(arguments[position + 1])
    require(not raw.is_symlink(), "qa_receiver_output_not_owned")
    root = raw.resolve()
    require(
        root.parent == Path("/tmp").resolve()
        and re.fullmatch(r"jx-spec307-qa-[0-9a-f]{6,64}", root.name)
        and root.is_dir(),
        "qa_receiver_output_not_owned",
    )
    return root / "passive-receiver"


@dataclass
class ReceivedTrack:
    sid: str
    pcm_path: Path
    stream: Any
    pcm: bytearray = field(default_factory=bytearray)
    task: asyncio.Task[None] | None = None
    binding_task: asyncio.Task[None] | None = None
    speech_id: str | None = None
    agent_id: str | None = None
    first_frame_utc: float | None = None
    binding_observed_utc: float | None = None
    binding_snapshot_match_id: str | None = None
    binding_snapshot_sequence: int | None = None
    binding_snapshot_state: str | None = None
    binding_action_index: int | None = None
    binding_action_key: str | None = None
    binding_context_version: int | None = None
    samples: int = 0
    eof: bool = False
    unsubscribed: bool = False
    error_code: str | None = None
    queue_high_water: int | None = None
    queued_audio_frames_at_eof: int | None = None
    cutoff_samples: int | None = None
    queued_audio_frames_at_cutoff: int | None = None
    consume_started: bool = False
    capture_active: bool = False
    consumer_stopped: bool = False
    stream_closed: bool = False
    unsubscribe_cancel_requested: bool = False
    observation_closed_by_unsubscribe: bool = False
    close_task: asyncio.Task[None] | None = None
    error_codes: list[str] = field(default_factory=lambda: list[str]())


def record_error(record: ReceivedTrack, code: str) -> None:
    if record.error_code is None:
        record.error_code = code
    if code not in record.error_codes:
        record.error_codes.append(code)


def failure_code(error: Exception, fallback: str) -> str:
    if isinstance(error, ClientFailure) and re.fullmatch(
        r"qa_receiver_[a-z0-9_]{1,100}", error.code
    ):
        return error.code
    return fallback


def observation_complete(record: ReceivedTrack) -> bool:
    return (
        record.consumer_stopped
        and record.stream_closed
        and (
            record.eof
            or (
                record.observation_closed_by_unsubscribe
                and record.unsubscribed
                and record.cutoff_samples is not None
                and record.samples == record.cutoff_samples
            )
        )
    )


def stream_queue_state(stream: Any) -> tuple[int | None, int | None]:
    queue = getattr(getattr(stream, "_queue", None), "_queue", None)
    if queue is None:
        return None, None
    return len(queue), sum(item is not None for item in queue)


def source_identity_evidence(
    record: ReceivedTrack, row: Mapping[str, Any], match_id: UUID
) -> dict[str, Any]:
    """Separate publication/preparing PCM from the authoritative speech binding.

    A published native track can yield silence before the first body PCM creates
    Speech. Generation creation bounds that track's lifetime. The authenticated
    snapshot sequence binds the later speech within its real playback/finalized
    events, including valid FINALIZING states after body end. Energy and HTTP
    response wall time never supply identity. Diagnostics omit paths and text.
    """

    def timestamp(name: str) -> float | None:
        value = row.get(name)
        return value.timestamp() if isinstance(value, datetime) else None

    def delta_ms(later: float | None, earlier: float | None) -> float | None:
        return (
            round((later - earlier) * 1000, 3)
            if later is not None and earlier is not None
            else None
        )

    created = timestamp("generation_created_at")
    started = timestamp("started_at")
    ended = timestamp("ended_at")
    first = record.first_frame_utc
    bound = record.binding_observed_utc
    sequence = record.binding_snapshot_sequence
    playback_sequence = row["playback_started_sequence"]
    finalizing_sequence = row["finalizing_sequence"]
    finalized_sequence = row["finalized_sequence"]
    released_sequence = row["free_turn_released_sequence"]
    terminal_sequences = [
        value for value in (finalized_sequence, released_sequence) if type(value) is int
    ]
    terminal_sequence = min(terminal_sequences) if terminal_sequences else None
    paths = (row["audio_storage_path"], row["generation_path"], row["file_path"])
    checks = {
        "binding_match_matches_target": record.binding_snapshot_match_id == str(match_id),
        "speech_agent_matches_binding": str(row["agent_profile_id"]) == record.agent_id,
        "speech_action_matches_binding": row["action_key"] == record.binding_action_key,
        "generation_present": row["generation_id"] is not None,
        "generation_action_matches_speech": row["generation_action_key"] == row["action_key"],
        "asset_status_finalized": row["generation_status"] == "FINALIZED",
        "generation_status_finalized": row["generation_record_status"] == "FINALIZED",
        "file_status_ready": row["file_status"] == "READY",
        "three_paths_present": all(isinstance(value, str) and bool(value) for value in paths),
        "three_paths_equal": paths[0] == paths[1] == paths[2],
        "sentence_closed": bool(row["sentence_closed"]),
        "binding_playback_event_present": type(playback_sequence) is int,
        "binding_terminal_event_present": terminal_sequence is not None,
        "binding_sequence_not_before_playback": (
            sequence is not None
            and type(playback_sequence) is int
            and playback_sequence <= sequence
        ),
        "binding_sequence_before_terminal": (
            sequence is not None and terminal_sequence is not None and sequence < terminal_sequence
        ),
        "binding_phase_matches_event_interval": (
            sequence is not None
            and type(finalizing_sequence) is int
            and (
                (
                    record.binding_snapshot_state == "AGENT_SPEAKING"
                    and sequence < finalizing_sequence
                )
                or (
                    record.binding_snapshot_state == "AGENT_FINALIZING"
                    and sequence >= finalizing_sequence
                )
            )
        ),
        "first_frame_not_before_generation_created": (
            first is not None and created is not None and created <= first
        ),
        "first_frame_not_after_speech_ended": (
            first is not None and ended is not None and first <= ended + 0.5
        ),
    }
    return {
        "source_checks": checks,
        "failed_source_checks": [key for key, passed in checks.items() if not passed],
        "source_statuses": {
            "speech": row["status"],
            "origin": row["origin"],
            "generation": row["generation_record_status"],
            "asset": row["generation_status"],
            "file": row["file_status"],
            "finalizing_reason": row["reason"],
        },
        "source_time_deltas_ms": {
            "first_any_minus_generation_created": delta_ms(first, created),
            "first_any_minus_speech_started": delta_ms(first, started),
            "binding_observed_minus_speech_started": delta_ms(bound, started),
            "speech_ended_minus_first_any": delta_ms(ended, first),
            "speech_ended_minus_binding_observed": delta_ms(ended, bound),
        },
        "binding_snapshot": {
            "match_id": record.binding_snapshot_match_id,
            "speech_id": record.speech_id,
            "agent_profile_id": record.agent_id,
            "sequence": record.binding_snapshot_sequence,
            "action_state": record.binding_snapshot_state,
            "action_index": record.binding_action_index,
            "action_key": record.binding_action_key,
            "context_version": record.binding_context_version,
        },
        "first_any_pcm_utc": first,
        "binding_snapshot_observed_utc": bound,
        "generation_context_version": row["generation_context_version"],
        "source_event_sequences": {
            "playback_started": playback_sequence,
            "finalizing": finalizing_sequence,
            "finalized": finalized_sequence,
            "free_turn_released": released_sequence,
            "terminal": terminal_sequence,
        },
    }


class PassiveReceiver:
    def __init__(self, clients: Any, root: Path) -> None:
        self.clients = clients
        self.root = root / str(clients.match_id)
        self.root.mkdir(parents=True, exist_ok=False)
        self.room = clients.people[0].publisher.room
        self.tracks: dict[str, ReceivedTrack] = {}
        self.total_bytes = 0
        self.error_code: str | None = None
        self.finishing = False

    def accepts(self, publication: Any, participant: Any) -> bool:
        return (
            participant.identity.startswith(f"jx-agent-{self.clients.match_id}-")
            and publication.kind == rtc.TrackKind.KIND_AUDIO
        )

    def published(self, publication: Any, participant: Any) -> None:
        if not self.finishing and self.accepts(publication, participant):
            publication.set_subscribed(True)

    def subscribed(self, track: Any, publication: Any, participant: Any) -> None:
        if (
            self.finishing
            or not self.accepts(publication, participant)
            or publication.sid in self.tracks
        ):
            return
        if len(self.tracks) >= MAX_TRACKS or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", publication.sid
        ):
            self.error_code = "qa_receiver_track_budget_or_identity"
            return
        stream = rtc.AudioStream(track, sample_rate=RATE, num_channels=1, capacity=200)
        record = ReceivedTrack(publication.sid, self.root / f"{publication.sid}.pcm", stream)
        self.tracks[record.sid] = record
        record.task = asyncio.create_task(self.consume(record), name=f"qa308-rx-{record.sid}")

    def unsubscribed(self, _track: Any, publication: Any, _participant: Any) -> None:
        record = self.tracks.get(publication.sid)
        if record is None or record.unsubscribed:
            return
        # The observer boundary is synchronous: queued/late SDK PCM is not admissible.
        record.unsubscribed = True
        record.cutoff_samples = record.samples
        _, record.queued_audio_frames_at_cutoff = stream_queue_state(record.stream)
        if record.task is not None and record.capture_active and not record.task.done():
            record.unsubscribe_cancel_requested = record.task.cancel()
        self.close_stream_once(record)

    def close_stream_once(self, record: ReceivedTrack) -> asyncio.Task[None]:
        # Register before yielding; callback and consumer finally share this one owner.
        if record.close_task is None:
            record.close_task = asyncio.create_task(
                self.close_stream(record), name=f"qa308-close-{record.sid}"
            )
        return record.close_task

    async def close_stream(self, record: ReceivedTrack) -> None:
        try:
            await asyncio.wait_for(record.stream.aclose(), STREAM_CLOSE_TIMEOUT_SECONDS)
            record.stream_closed = True
        except TimeoutError:
            record_error(record, "qa_receiver_stream_close_timeout")
        except Exception as error:
            record_error(record, failure_code(error, "qa_receiver_stream_cleanup_failed"))

    def attach(self) -> None:
        self.room.on("track_published", self.published)
        self.room.on("track_subscribed", self.subscribed)
        self.room.on("track_unsubscribed", self.unsubscribed)
        for participant in self.room.remote_participants.values():
            for publication in participant.track_publications.values():
                self.published(publication, participant)

    async def bind(self, record: ReceivedTrack) -> None:
        try:
            async with asyncio.timeout(8):
                while True:
                    snapshot = await asyncio.wait_for(self.clients.snapshot(), 1.5)
                    observed = time.time()
                    speech = snapshot.get("current_speech_id")
                    agent = snapshot.get("current_agent_profile_id")
                    if (
                        speech
                        and agent
                        and snapshot.get("action_state") in {"AGENT_SPEAKING", "AGENT_FINALIZING"}
                    ):
                        raw_action = snapshot.get("current_action")
                        require(
                            isinstance(raw_action, dict), "qa_receiver_binding_snapshot_invalid"
                        )
                        action = cast(dict[str, Any], raw_action)
                        require(
                            snapshot.get("match_id") == str(self.clients.match_id)
                            and type(snapshot.get("sequence")) is int
                            and snapshot["sequence"] >= 0
                            and type(snapshot.get("current_action_index")) is int
                            and snapshot["current_action_index"] >= 0
                            and type(action.get("stage_position")) is int
                            and action["stage_position"] >= 1
                            and type(action.get("action_position")) is int
                            and (
                                (
                                    action.get("action_kind") == "FREE_DEBATE"
                                    and action["action_position"] == 0
                                )
                                or (
                                    action.get("action_kind") == "AGENT_SPEECH"
                                    and action["action_position"] >= 1
                                )
                            ),
                            "qa_receiver_binding_snapshot_invalid",
                        )
                        record.speech_id, record.agent_id = str(UUID(speech)), str(UUID(agent))
                        record.binding_observed_utc = observed
                        record.binding_snapshot_match_id = snapshot["match_id"]
                        record.binding_snapshot_sequence = snapshot["sequence"]
                        record.binding_snapshot_state = snapshot["action_state"]
                        record.binding_action_index = snapshot["current_action_index"]
                        record.binding_action_key = (
                            f"{action['stage_position']}:{action['action_position']}"
                        )
                        context = snapshot.get("context_version")
                        record.binding_context_version = context if type(context) is int else None
                        return
                    if snapshot.get("action_state") != "AGENT_PREPARING":
                        raise ClientFailure("qa_receiver_binding_not_agent")
                    await asyncio.sleep(0.05)
        except TimeoutError:
            record_error(record, "qa_receiver_binding_timeout")
        except Exception as error:
            record_error(record, failure_code(error, "qa_receiver_snapshot_binding_failed"))

    async def consume(self, record: ReceivedTrack) -> None:
        record.consume_started = True
        record.capture_active = True
        try:
            if record.cutoff_samples is not None:
                return
            async with asyncio.timeout(CAPTURE_TIMEOUT_SECONDS):
                async for event in record.stream:
                    if record.cutoff_samples is not None:
                        break
                    body = bytes(event.frame.data)
                    if not body:
                        continue
                    require(
                        event.frame.sample_rate == RATE
                        and event.frame.num_channels == 1
                        and len(body) % 2 == 0,
                        "qa_receiver_pcm_format",
                    )
                    depth, _ = stream_queue_state(record.stream)
                    if depth is not None:
                        record.queue_high_water = max(record.queue_high_water or 0, depth)
                        if depth >= 199:
                            record_error(record, "qa_receiver_queue_overflow_possible")
                    if record.first_frame_utc is None:
                        record.first_frame_utc = time.time()
                        record.binding_task = asyncio.create_task(
                            self.bind(record), name=f"qa308-bind-{record.sid}"
                        )
                    require(
                        len(record.pcm) + len(body) <= MAX_TRACK_BYTES
                        and self.total_bytes + len(body) <= MAX_TOTAL_BYTES,
                        "qa_receiver_pcm_budget",
                    )
                    if record.cutoff_samples is not None:
                        break
                    record.pcm.extend(body)
                    record.samples += len(body) // 2
                    self.total_bytes += len(body)
                else:
                    # An explicit close can stop the iterator; it is not native EOF.
                    record.eof = record.cutoff_samples is None and record.close_task is None
                    if record.eof:
                        _, record.queued_audio_frames_at_eof = stream_queue_state(record.stream)
        except asyncio.CancelledError:
            if not (record.unsubscribe_cancel_requested and record.cutoff_samples is not None):
                record_error(record, "qa_receiver_capture_cancelled")
        except TimeoutError:
            record_error(record, "qa_receiver_stream_timeout")
        except Exception as error:
            record_error(record, failure_code(error, "qa_receiver_stream_incomplete"))
        finally:
            record.capture_active = False
            # Shield the separately owned close from the expected unsubscribe cancellation.
            close_task = self.close_stream_once(record)
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                record_error(record, "qa_receiver_capture_cancelled")
                await asyncio.shield(close_task)
            if record.binding_task is not None:
                await asyncio.gather(record.binding_task, return_exceptions=True)
            try:
                await asyncio.to_thread(record.pcm_path.write_bytes, bytes(record.pcm))
            except Exception:
                record_error(record, "qa_receiver_storage_failed")
            record.pcm.clear()
            record.consumer_stopped = True
            record.observation_closed_by_unsubscribe = (
                record.unsubscribed
                and not record.eof
                and record.cutoff_samples == record.samples
                and record.stream_closed
                and not record.error_code
            )

    async def finish(self) -> dict[str, Any]:
        self.finishing = True
        self.room.off("track_published", self.published)
        self.room.off("track_subscribed", self.subscribed)
        tasks = [record.task for record in self.tracks.values() if record.task is not None]
        try:
            if tasks:
                done, pending = await asyncio.wait(tasks, timeout=FINISH_TIMEOUT_SECONDS)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*done, *pending, return_exceptions=True)
                if pending:
                    self.error_code = "qa_receiver_finish_timeout"
        finally:
            # The last unsubscribe may arrive after the authoritative match-finished event.
            self.room.off("track_unsubscribed", self.unsubscribed)
        return await self.prove()

    async def prove(self) -> dict[str, Any]:
        settings = self.clients.settings
        target = make_url(settings.database_url_value)
        require(
            settings.app_env != "production"
            and target.host == "127.0.0.1"
            and target.database == "jx_debate_qa_agent_faults",
            "qa_receiver_database_not_owned",
        )
        engine = create_async_engine(settings.database_url_value, pool_size=1, max_overflow=0)
        try:
            async with async_sessionmaker(engine)() as session:
                await session.execute(text("SET TRANSACTION READ ONLY"))
                await session.execute(text("SET LOCAL statement_timeout='5s'"))
                rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT s.id,s.action_key,s.agent_profile_id,s.generation_id,"
                                "s.status,s.origin,"
                                "s.audio_truncated,s.started_at,s.ended_at,s.audio_storage_path,"
                                "s.llm_draft_text ~ :ending AS sentence_closed,"
                                "(SELECT e.payload->>'reason' FROM match_events e "
                                "WHERE e.match_id=s.match_id AND e.event_type='agent.finalizing' "
                                "AND e.payload->>'speech_id'=s.id::text "
                                "ORDER BY e.sequence DESC LIMIT 1) AS reason,"
                                "a.storage_path AS generation_path,a.status AS generation_status,"
                                "g.status AS generation_record_status,"
                                "g.action_key AS generation_action_key,"
                                "g.created_at AS generation_created_at,"
                                "g.context_version AS generation_context_version,"
                                "(SELECT MIN(e.sequence) FROM match_events e "
                                "WHERE e.match_id=s.match_id "
                                "AND e.event_type='agent.playback_started' "
                                "AND e.payload->>'speech_id'=s.id::text "
                                "AND e.payload->>'generation_id'=s.generation_id::text "
                                "AND e.payload->>'agent_profile_id'=s.agent_profile_id::text) "
                                "AS playback_started_sequence,"
                                "(SELECT MIN(e.sequence) FROM match_events e "
                                "WHERE e.match_id=s.match_id AND e.event_type='agent.finalizing' "
                                "AND e.payload->>'speech_id'=s.id::text) AS finalizing_sequence,"
                                "(SELECT MIN(e.sequence) FROM match_events e "
                                "WHERE e.match_id=s.match_id AND e.event_type='agent.finalized' "
                                "AND e.payload->>'speech_id'=s.id::text "
                                "AND e.payload->>'generation_id'=s.generation_id::text) "
                                "AS finalized_sequence,"
                                "(SELECT MIN(e.sequence) FROM match_events e "
                                "WHERE e.match_id=s.match_id AND e.event_type='free.turn_released' "
                                "AND e.payload->>'speech_id'=s.id::text "
                                "AND e.payload->>'reason'='HANDOFF') "
                                "AS free_turn_released_sequence,"
                                "f.storage_path AS file_path,f.status AS file_status "
                                "FROM speeches s LEFT JOIN agent_generations g "
                                "ON g.id=s.generation_id AND g.match_id=s.match_id "
                                "AND g.agent_profile_id=s.agent_profile_id "
                                "LEFT JOIN agent_audio_assets a "
                                "ON a.generation_id=s.generation_id "
                                "LEFT JOIN match_files f ON f.speech_id=s.id "
                                "AND f.match_id=s.match_id "
                                "AND f.file_kind='AGENT_RAW' "
                                "AND f.file_key='agent-'||s.id::text "
                                "WHERE s.match_id=:match AND s.speaker_kind='AGENT'"
                            ),
                            {"match": self.clients.match_id, "ending": r"[。！？!?][”’」』]?\s*$"},
                        )
                    )
                    .mappings()
                    .all()
                )
                participants = set(
                    (
                        await session.scalars(
                            text(
                                "SELECT agent_profile_id FROM match_participants "
                                "WHERE match_id=:match "
                                "AND kind='AGENT'"
                            ),
                            {"match": self.clients.match_id},
                        )
                    ).all()
                )
        finally:
            await engine.dispose()
        by_speech = {str(row["id"]): row for row in rows}
        source_root = Path(settings.agent_audio_storage_dir).resolve()
        checks: list[dict[str, Any]] = []
        passed_agents: set[UUID] = set()
        for record in self.tracks.values():
            check: dict[str, Any] = {
                "track_sid": record.sid,
                "speech_id": record.speech_id,
                "agent_profile_id": record.agent_id,
                "received_samples": record.samples,
                "natural_eof": record.eof,
                "unsubscribed_observed": record.unsubscribed,
                "observation_closed_by_unsubscribe": record.observation_closed_by_unsubscribe,
                "cutoff_samples": record.cutoff_samples,
                "consumer_stopped": record.consumer_stopped,
                "stream_closed": record.stream_closed,
                "observed_queue_high_water": record.queue_high_water,
                "queued_audio_frames_at_eof": record.queued_audio_frames_at_eof,
                "queued_audio_frames_at_cutoff": record.queued_audio_frames_at_cutoff,
                "exact_sdk_drop_counter_available": False,
                "error_code": record.error_code,
                "error_codes": list(record.error_codes),
                "passed": False,
            }
            row = by_speech.get(record.speech_id or "")
            if row is None or row["status"] != "FINALIZED" or row["origin"] != "ORIGINAL":
                check["exclusion_identity"] = excluded_identity_evidence(
                    record, cast(Mapping[str, Any] | None, row)
                )
                check["status"] = "retired_or_unbound_not_qualified"
                checks.append(check)
                continue
            if row["audio_truncated"] or row["reason"] != "COMPLETED":
                check["exclusion_identity"] = excluded_identity_evidence(
                    record, cast(Mapping[str, Any] | None, row)
                )
                check["status"] = "truncated_not_qualified"
                checks.append(check)
                continue
            try:
                require(observation_complete(record), "qa_receiver_observation_not_closed")
                require(not record.error_code, record.error_code or "qa_receiver_capture_failed")
                check.update(
                    source_identity_evidence(
                        record, cast(Mapping[str, Any], row), self.clients.match_id
                    )
                )
                require(
                    all(check["source_checks"].values()),
                    "qa_receiver_source_identity_not_proved",
                )
                path = Path(row["generation_path"]).resolve()
                check["source_file_checks"] = {
                    "is_file": path.is_file(),
                    "under_owned_source_root": path.is_relative_to(source_root),
                }
                require(
                    all(check["source_file_checks"].values()),
                    "qa_receiver_source_not_owned",
                )
                encoded = await asyncio.to_thread(path.read_bytes)
                source = await asyncio.to_thread(decode_ogg_opus_pcm, encoded, sample_rate=RATE)
                received = await asyncio.to_thread(record.pcm_path.read_bytes)
                comparison = await asyncio.to_thread(
                    compare_tail, source, received, synthetic=False
                )
                stem = str(row["id"])
                await asyncio.to_thread((self.root / f"source-{stem}.ogg").write_bytes, encoded)
                await asyncio.to_thread((self.root / f"source-{stem}.pcm").write_bytes, source)
                check.update(
                    status="compared",
                    passed=comparison["passed"],
                    source_identity_verified=True,
                    sentence_closed=True,
                    generation_id=str(row["generation_id"]),
                    source_ogg_sha256=hashlib.sha256(encoded).hexdigest(),
                    source_pcm_sha256=hashlib.sha256(source).hexdigest(),
                    received_pcm_sha256=hashlib.sha256(received).hexdigest(),
                    tail=comparison,
                )
                if comparison["passed"]:
                    passed_agents.add(row["agent_profile_id"])
                else:
                    check["error_code"] = "qa_receiver_tail_mismatch"
                    check["error_codes"].append("qa_receiver_tail_mismatch")
            except Exception as error:
                code = failure_code(error, "qa_receiver_tail_proof_failed")
                check.update(status="failed_safe", proof_error_code=code)
                if not check["error_code"]:
                    check["error_code"] = code
                if code not in check["error_codes"]:
                    check["error_codes"].append(code)
            checks.append(check)
        proof = {
            "status": "passed"
            if len(participants) == 4
            and bool(checks)
            and not self.error_code
            and not any(record.error_code for record in self.tracks.values())
            and not any(
                check["status"] == "failed_safe"
                or (check["status"] == "compared" and not check["passed"])
                for check in checks
            )
            else "failed",
            "match_id": str(self.clients.match_id),
            "passive_only": True,
            "source": "actual_complete_agent_ogg",
            "synthetic_marker_used": False,
            "expected_agent_count": len(participants),
            "agents_tail_proved": len(passed_agents),
            "expected_agent_profile_ids": sorted(str(value) for value in participants),
            "agent_profile_ids_tail_proved": sorted(str(value) for value in passed_agents),
            "tracks": checks,
            "error_code": self.error_code,
            "thresholds": {"tail_ncc": 0.60, "every_suffix_frame_ncc": 0.45},
        }
        await asyncio.to_thread(
            (self.root / "RECEIVER_PROOF.json").write_text, json.dumps(proof, indent=2) + "\n"
        )
        return proof


SOURCE_CHECK_KEYS = frozenset(
    {
        "binding_match_matches_target",
        "speech_agent_matches_binding",
        "speech_action_matches_binding",
        "generation_present",
        "generation_action_matches_speech",
        "asset_status_finalized",
        "generation_status_finalized",
        "file_status_ready",
        "three_paths_present",
        "three_paths_equal",
        "sentence_closed",
        "binding_playback_event_present",
        "binding_terminal_event_present",
        "binding_sequence_not_before_playback",
        "binding_sequence_before_terminal",
        "binding_phase_matches_event_interval",
        "first_frame_not_before_generation_created",
        "first_frame_not_after_speech_ended",
    }
)
TAIL_THRESHOLDS = {"tail_ncc": 0.60, "every_suffix_frame_ncc": 0.45}


def excluded_identity_evidence(
    record: ReceivedTrack, row: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Known noneligible rows need identity, not a fictitious completed source chain."""
    return {
        "binding_match_id": record.binding_snapshot_match_id,
        "binding_speech_id": record.speech_id,
        "binding_agent_profile_id": record.agent_id,
        "binding_action_key": record.binding_action_key,
        "speech_id": str(row["id"]) if row is not None else None,
        "agent_profile_id": str(row["agent_profile_id"]) if row is not None else None,
        "generation_id": str(row["generation_id"])
        if row is not None and row["generation_id"]
        else None,
        "speech_action_key": row["action_key"] if row is not None else None,
        "generation_action_key": row["generation_action_key"] if row is not None else None,
        "generation_status": row["generation_record_status"] if row is not None else None,
        "speech_status": row["status"] if row is not None else None,
        "origin": row["origin"] if row is not None else None,
        "audio_truncated": row["audio_truncated"] if row is not None else None,
        "finalizing_reason": row["reason"] if row is not None else None,
    }


def evaluate_receiver_proof(
    proof: Mapping[str, Any], match_id: str, expected_agent_ids: list[str]
) -> dict[str, Any]:
    """Keep all-tracks diagnostics; admit only safe observation to Spec308 coverage.

    This does not turn a failed tail into delivery. Every track is inspected;
    missing identity/capture/source evidence is never a tail-only exception.
    """
    try:
        return _evaluate_receiver_proof(proof, match_id, expected_agent_ids)
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError) as error:
        raise ClientFailure("qa_receiver_aggregate_track_not_proved") from error


def _evaluate_receiver_proof(
    proof: Mapping[str, Any], match_id: str, expected_agent_ids: list[str]
) -> dict[str, Any]:
    import math

    code = "qa_receiver_aggregate_track_not_proved"
    expected = {str(UUID(value)) for value in expected_agent_ids}
    require(len(expected) == len(expected_agent_ids) == 4, code)
    require(
        proof["match_id"] == str(UUID(match_id))
        and proof["passive_only"] is True
        and proof["synthetic_marker_used"] is False
        and proof["error_code"] is None
        and proof["source"] == "actual_complete_agent_ogg"
        and proof["thresholds"] == TAIL_THRESHOLDS
        and proof["expected_agent_count"] == 4
        and len(proof["expected_agent_profile_ids"]) == 4
        and set(proof["expected_agent_profile_ids"]) == expected,
        code,
    )
    raw_tracks = proof["tracks"]
    require(isinstance(raw_tracks, list), code)
    tracks = cast(list[Any], raw_tracks)
    require(0 < len(tracks) <= MAX_TRACKS, code)
    verified: set[str] = set()
    seen_tracks: set[str] = set()
    speech_identities: dict[str, tuple[str, str, str]] = {}
    mismatches: list[dict[str, Any]] = []
    excluded = 0
    total_bytes = 0
    for raw_track in tracks:
        require(isinstance(raw_track, dict), code)
        track = cast(dict[str, Any], raw_track)
        sid = track["track_sid"]
        speech = str(UUID(track["speech_id"]))
        agent = str(UUID(track["agent_profile_id"]))
        require(
            isinstance(sid, str)
            and re.fullmatch(r"TR_[A-Za-z0-9_-]{1,125}", sid)
            and sid not in seen_tracks
            and agent in expected,
            code,
        )
        seen_tracks.add(sid)
        samples = track["received_samples"]
        require(type(samples) is int and 0 < samples <= MAX_TRACK_BYTES // 2, code)
        total_bytes += samples * 2
        require(total_bytes <= MAX_TOTAL_BYTES, code)
        require(track["consumer_stopped"] is True and track["stream_closed"] is True, code)
        require(
            type(track["natural_eof"]) is bool
            and type(track["unsubscribed_observed"]) is bool
            and type(track["observation_closed_by_unsubscribe"]) is bool,
            code,
        )
        require(
            track["natural_eof"] is True
            or (
                track["observation_closed_by_unsubscribe"] is True
                and track["unsubscribed_observed"] is True
                and type(track["cutoff_samples"]) is int
                and track["cutoff_samples"] == samples
            ),
            code,
        )
        depth = track["observed_queue_high_water"]
        require(depth is None or (type(depth) is int and 0 <= depth < 199), code)
        for name in ("queued_audio_frames_at_eof", "queued_audio_frames_at_cutoff"):
            value = track[name]
            require(value is None or (type(value) is int and value >= 0), code)
        require(track["exact_sdk_drop_counter_available"] is False, code)
        status = track["status"]
        if status in {"retired_or_unbound_not_qualified", "truncated_not_qualified"}:
            require(
                track["passed"] is False
                and track["error_code"] is None
                and track["error_codes"] == []
                and "tail" not in track,
                code,
            )
            identity = track["exclusion_identity"]
            require(
                identity["binding_match_id"] == match_id
                and identity["speech_id"] == identity["binding_speech_id"] == speech
                and identity["agent_profile_id"] == identity["binding_agent_profile_id"] == agent
                and identity["speech_action_key"]
                == identity["binding_action_key"]
                == identity["generation_action_key"]
                and isinstance(identity["speech_action_key"], str)
                and re.fullmatch(r"[1-9][0-9]*:[0-9]+", identity["speech_action_key"])
                and identity["generation_status"]
                in {"STARTED", "LLM_READY", "PLAYING", "FINALIZED", "FAILED", "CANCELLED"},
                code,
            )
            UUID(identity["generation_id"])
            key = (agent, identity["generation_id"], identity["speech_action_key"])
            require(speech not in speech_identities or speech_identities[speech] == key, code)
            speech_identities[speech] = key
            if status == "retired_or_unbound_not_qualified":
                require(
                    identity["origin"] == "ORIGINAL"
                    and identity["speech_status"] in {"RESET", "FAILED"},
                    code,
                )
            else:
                require(
                    identity["speech_status"] == "FINALIZED"
                    and identity["origin"] == "ORIGINAL"
                    and type(identity["audio_truncated"]) is bool
                    and identity["finalizing_reason"] in {"COMPLETED", "TIME_LIMIT"}
                    and (
                        identity["audio_truncated"] or identity["finalizing_reason"] == "TIME_LIMIT"
                    ),
                    code,
                )
            excluded += 1
            continue
        require(
            status == "compared"
            and track["source_identity_verified"] is True
            and track["sentence_closed"] is True,
            code,
        )
        checks = cast(dict[str, Any], track["source_checks"])
        require(
            isinstance(cast(object, checks), dict)
            and frozenset(checks) == SOURCE_CHECK_KEYS
            and all(value is True for value in checks.values())
            and track["failed_source_checks"] == [],
            code,
        )
        require(
            track["source_statuses"]
            == {
                "speech": "FINALIZED",
                "origin": "ORIGINAL",
                "generation": "FINALIZED",
                "asset": "FINALIZED",
                "file": "READY",
                "finalizing_reason": "COMPLETED",
            },
            code,
        )
        require(
            set(track["source_file_checks"]) == {"is_file", "under_owned_source_root"}
            and all(value is True for value in track["source_file_checks"].values()),
            code,
        )
        UUID(track["generation_id"])
        binding = track["binding_snapshot"]
        require(
            binding["match_id"] == match_id
            and binding["speech_id"] == speech
            and binding["agent_profile_id"] == agent
            and type(binding["action_index"]) is int
            and binding["action_index"] >= 0
            and re.fullmatch(r"[1-9][0-9]*:[0-9]+", binding["action_key"]),
            code,
        )
        key = (agent, track["generation_id"], binding["action_key"])
        require(speech not in speech_identities or speech_identities[speech] == key, code)
        speech_identities[speech] = key
        sequence = binding["sequence"]
        events = track["source_event_sequences"]
        terminals = [
            events[name] for name in ("finalized", "free_turn_released") if events[name] is not None
        ]
        require(
            type(sequence) is int
            and type(events["playback_started"]) is int
            and type(events["finalizing"]) is int
            and terminals
            and all(type(value) is int for value in terminals)
            and events["terminal"] == min(terminals)
            and events["playback_started"] <= sequence < events["terminal"]
            and (
                (binding["action_state"] == "AGENT_SPEAKING" and sequence < events["finalizing"])
                or (
                    binding["action_state"] == "AGENT_FINALIZING"
                    and sequence >= events["finalizing"]
                )
            ),
            code,
        )
        for name in ("source_ogg_sha256", "source_pcm_sha256", "received_pcm_sha256"):
            require(
                isinstance(track[name], str) and re.fullmatch(r"[0-9a-f]{64}", track[name]), code
            )
        for name in ("first_any_pcm_utc", "binding_snapshot_observed_utc"):
            require(type(track[name]) in (int, float) and math.isfinite(track[name]), code)
        require(
            "generation_context_version" in track
            and (
                track["generation_context_version"] is None
                or type(track["generation_context_version"]) is int
            ),
            code,
        )
        require(
            "context_version" in binding
            and (binding["context_version"] is None or type(binding["context_version"]) is int),
            code,
        )
        delta = track["source_time_deltas_ms"]
        require(
            set(delta)
            == {
                "first_any_minus_generation_created",
                "first_any_minus_speech_started",
                "binding_observed_minus_speech_started",
                "speech_ended_minus_first_any",
                "speech_ended_minus_binding_observed",
            },
            code,
        )
        require(
            all(type(value) in (int, float) and math.isfinite(value) for value in delta.values())
            and delta["first_any_minus_generation_created"] >= 0
            and delta["speech_ended_minus_first_any"] >= -500,
            code,
        )
        tail = track["tail"]
        require(tail["thresholds"] == TAIL_THRESHOLDS and tail["received_samples"] == samples, code)
        corr = tail["tail_correlation"]
        suffixes = cast(list[Any], tail["last_six_frame_correlations"])
        require(
            isinstance(cast(object, suffixes), list)
            and suffixes
            and all(
                type(value) in (int, float) and math.isfinite(value) and -1 <= value <= 1
                for value in [corr, *suffixes]
            )
            and type(tail["minimum_suffix_correlation"]) in (int, float)
            and math.isfinite(tail["minimum_suffix_correlation"])
            and tail["minimum_suffix_correlation"] == min(suffixes),
            code,
        )
        for name in (
            "matched_tail_samples",
            "reference_audible_end_sample",
            "source_samples",
            "source_silent_tail_ignored_samples",
            "received_match_start_sample",
            "received_match_end_sample",
        ):
            require(type(tail[name]) is int, code)
        length = tail["matched_tail_samples"]
        endpoint = tail["reference_audible_end_sample"]
        offset = tail["received_match_start_sample"]
        require(
            0 < endpoint <= tail["source_samples"]
            and length == min(endpoint, RATE)
            and len(suffixes) == len(range(max(0, length - 6 * 960), length, 960))
            and tail["source_silent_tail_ignored_samples"] == tail["source_samples"] - endpoint
            and tail["received_match_end_sample"] == offset + length,
            code,
        )
        passed = (
            offset >= 0 and offset + length <= samples and corr >= 0.60 and min(suffixes) >= 0.45
        )
        require(tail["passed"] is passed and track["passed"] is passed, code)
        if passed:
            require(track["error_code"] is None and track["error_codes"] == [], code)
            verified.add(agent)
        else:
            require(
                track["error_code"] == "qa_receiver_tail_mismatch"
                and track["error_codes"] == ["qa_receiver_tail_mismatch"],
                code,
            )
            mismatches.append(track)
    original_status = "failed" if mismatches else "passed"
    require(proof["status"] == original_status, code)
    require(
        type(proof["agents_tail_proved"]) is int
        and proof["agents_tail_proved"] == len(verified)
        and len(proof["agent_profile_ids_tail_proved"]) == len(verified)
        and set(proof["agent_profile_ids_tail_proved"]) == verified,
        "qa_receiver_aggregate_summary_mismatch",
    )
    return {
        "status": "passed",
        "match_id": match_id,
        "alltracks_status": original_status,
        "alltracks_failed_track_count": len(mismatches),
        "tail_mismatch_tracks": mismatches,
        "not_qualified_track_count": excluded,
        "agent_profile_ids_tail_proved": sorted(verified),
    }


def aggregate_receiver_proofs(
    output: Path, match_ids: list[str], expected_agent_ids: list[str]
) -> dict[str, Any]:
    expected = {str(UUID(value)) for value in expected_agent_ids}
    require(len(expected) == len(expected_agent_ids) == 4, "qa_receiver_aggregate_identity")
    require(len(match_ids) == len(set(match_ids)) == 2, "qa_receiver_aggregate_cases")
    coverage: set[str] = set()
    cases: list[dict[str, Any]] = []
    for match_id in match_ids:
        match_id = str(UUID(match_id))
        root = output / "passive-receiver" / match_id
        path = root / "RECEIVER_PROOF.json"
        require(
            path.is_file() and not path.is_symlink() and path.stat().st_size <= 512 * 1024,
            "qa_receiver_aggregate_proof_missing",
        )
        proof = json.loads(path.read_bytes())
        evaluation = evaluate_receiver_proof(proof, match_id, expected_agent_ids)
        coverage.update(evaluation["agent_profile_ids_tail_proved"])
        cases.append(evaluation)
    result: dict[str, Any] = {
        "status": "passed" if coverage == expected else "failed",
        "expected_agent_profile_ids": sorted(expected),
        "agent_profile_ids_tail_proved": sorted(coverage),
        "cases": cases,
        "alltracks_failed_track_count": sum(case["alltracks_failed_track_count"] for case in cases),
        "thresholds": {"tail_ncc": 0.60, "every_suffix_frame_ncc": 0.45},
    }
    (output / "RECEIVER_AGGREGATE.json").write_text(json.dumps(result, indent=2) + "\n")
    require(coverage == expected, "qa_receiver_aggregate_coverage_incomplete")
    return result


class ConferenceClients(BaseConferenceClients):
    async def prepare_and_connect(self) -> dict[str, Any]:
        snapshot = await super().prepare_and_connect()
        self._passive_receiver = PassiveReceiver(self, receiver_output_root(sys.argv))
        self._passive_receiver.attach()
        return snapshot

    async def close(self) -> None:
        error = None
        receiver = getattr(self, "_passive_receiver", None)
        if receiver is not None:
            self._passive_receiver = None
            try:
                proof = await receiver.finish()
                evaluation = evaluate_receiver_proof(
                    proof, str(self.match_id), proof["expected_agent_profile_ids"]
                )
                await asyncio.to_thread(
                    (receiver.root / "OBSERVATION_SAFETY.json").write_text,
                    json.dumps(evaluation, indent=2) + "\n",
                )
            except Exception as failure:
                code = failure_code(failure, "qa_receiver_proof_failed")
                error = ClientFailure(code)
                try:
                    failed = {
                        "status": "failed_safe",
                        "match_id": str(self.match_id),
                        "error_code": code,
                        "passive_only": True,
                    }
                    await asyncio.to_thread(
                        (receiver.root / "OBSERVATION_FAILURE.json").write_text,
                        json.dumps(failed, indent=2) + "\n",
                    )
                except Exception:
                    pass
        try:
            await super().close()
        finally:
            if error is not None:
                raise error

"""One real mixed 4v4 match, only in a new owned server PostgreSQL instance.

Uses deployed business code and current provider configuration. Synthetic users,
short rule text/timing and synthetic microphones are explicit QA inputs. All
state changes, provider callbacks, media playback and timers remain real. Only
aggregate receipts leave the server; owned database/audio/logs are removed.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import logging
import math
import os
import pwd
import re
import resource
import socket
import subprocess
import sys
import tempfile
from array import array
from collections import Counter, deque
from dataclasses import dataclass, replace
from datetime import timedelta
from functools import partial
from pathlib import Path
from time import monotonic, time
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4
from weakref import WeakKeyDictionary, ref

import psycopg
from livekit import api, rtc
from pydantic import SecretStr
from server_postmatch_archive_gate import verify_owned_archive
from server_provider_media_probe import connect_human, error_receipt
from sqlalchemy import func, select, text
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ops"))
import provider_chain_probe as qa_provider_probe  # noqa: E402
from provider_chain_probe import load_material, probe_tts  # noqa: E402

from jx_core.agent.audio import IncrementalOpusDecoder  # noqa: E402
from jx_core.agent.llm import LlmCapacityLimiter  # noqa: E402
from jx_core.agent.runtime import (  # noqa: E402
    AgentRuntime,
    _consume_detached_cleanup,
    _parse_experiment_decision,
    _retired_publisher_connection_finished,
    _schedule_publisher_startup_cleanup,
)
from jx_core.asr.runtime import AsrRuntime  # noqa: E402
from jx_core.auth.errors import APIError  # noqa: E402
from jx_core.auth.session import AuthContext  # noqa: E402
from jx_core.config import Settings, load_settings  # noqa: E402
from jx_core.data_capture.content import load_content_blob  # noqa: E402
from jx_core.data_capture.provider_projection import capture_projection  # noqa: E402
from jx_core.data_capture.provider_writer import ProviderCaptureWriter  # noqa: E402
from jx_core.database import Database  # noqa: E402
from jx_core.legal.terms import get_current_human_participation_terms  # noqa: E402
from jx_core.matches.domain import MatchCommand  # noqa: E402
from jx_core.matches.routes import submit_match_command  # noqa: E402
from jx_core.matches.schemas import MatchCommandRequest  # noqa: E402
from jx_core.matches.service import MatchRuntimeManager  # noqa: E402
from jx_core.models import (  # noqa: E402
    AgentGeneration,
    BackgroundTask,
    ExternalCall,
    HostAudioAsset,
    JudgeResult,
    Match,
    MatchParticipant,
    ModelProfile,
    Rule,
    RuleJudgeConfig,
    Speech,
    User,
    VoiceProfile,
)
from jx_core.models import MatchEvent as StoredEvent  # noqa: E402
from jx_core.postmatch import PostmatchService  # noqa: E402
from jx_core.postmatch_showcase_routes import (  # noqa: E402
    AudienceVoteRequest,
    JudgeEntryRequest,
    ShowcasePhaseRequest,
    get_showcase,
    save_judge_entry,
    submit_audience_vote,
    update_showcase_phase,
)
from jx_core.room_connections import RoomConnectionService  # noqa: E402
from jx_core.rooms.schemas import (  # noqa: E402
    DeviceCheckRequest,
    RoomCreateRequest,
    RoomJoinRequest,
    SeatSelectRequest,
)
from jx_core.rooms.service import RoomService  # noqa: E402
from jx_core.rules.audio import AudioConfiguration  # noqa: E402
from jx_core.rules.formal_4v4 import build_formal_4v4_draft  # noqa: E402
from jx_core.rules.prompts import (  # noqa: E402
    DEFAULT_FIXED_SPEECH_PROMPT,
    DEFAULT_FREE_DECISION_PROMPT,
    DEFAULT_FREE_SPEECH_PROMPT,
)
from jx_core.rules.schemas import (  # noqa: E402
    ModelProfileCreate,
    RuleCreate,
    RuleJudgeUpdate,
    VoiceProfileCreate,
)
from jx_core.rules.service import CatalogService, RuleService  # noqa: E402
from jx_jobs.host_tts_worker import process_one_host_tts  # noqa: E402
from jx_jobs.tts import DashScopeTTSClient  # noqa: E402


class OwnedPostgres:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="jx-full-match-qa-")
        self.root = Path(self.temporary.name)
        self.data = self.root / "postgres"
        self.sockets = self.root / "socket"
        self.started = False
        self.port = 0

    def command(self, *arguments: str) -> None:
        result = subprocess.run(
            ["runuser", "-u", "postgres", "--", *arguments],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=40,
        )
        if result.returncode:
            raise RuntimeError("qa_postgres_command_failed")

    def start(self) -> str:
        if os.geteuid() != 0:
            raise RuntimeError("qa_owned_postgres_requires_server_root")
        self.root.chmod(0o755)
        account = pwd.getpwnam("postgres")
        for path in (self.data, self.sockets):
            path.mkdir(mode=0o700)
            os.chown(path, account.pw_uid, account.pw_gid)
        self.command(
            "/usr/lib/postgresql/16/bin/initdb",
            "-D",
            str(self.data),
            "--auth=trust",
            "--username=jx_qa",
            "--no-locale",
            "--encoding=UTF8",
        )
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            self.port = available.getsockname()[1]
        self.command(
            "/usr/lib/postgresql/16/bin/pg_ctl",
            "-D",
            str(self.data),
            "-l",
            str(self.data / "qa-server.log"),
            "-w",
            "-t",
            "20",
            "-o",
            f"-p {self.port} -h 127.0.0.1 -k {self.sockets} "
            "-c shared_buffers=16MB -c work_mem=1MB -c maintenance_work_mem=16MB "
            "-c max_connections=12 -c max_wal_senders=0 -c wal_level=minimal",
            "start",
        )
        self.started = True
        with psycopg.connect(
            host="127.0.0.1",
            port=self.port,
            user="jx_qa",
            dbname="postgres",
            autocommit=True,
        ) as connection:
            connection.execute("CREATE DATABASE jx_debate_qa_agent_faults")
        return f"postgresql+psycopg://jx_qa@127.0.0.1:{self.port}/jx_debate_qa_agent_faults"

    def migrate(self, url: str, *, repo_root: Path | None = None) -> None:
        candidate_root = (repo_root or Path.cwd()).resolve()
        if not (candidate_root / "alembic.ini").is_file():
            raise RuntimeError("qa_candidate_alembic_missing")
        if url != f"postgresql+psycopg://jx_qa@127.0.0.1:{self.port}/jx_debate_qa_agent_faults":
            raise RuntimeError("qa_migration_database_not_owned")
        environment = dict(os.environ)
        environment["DATABASE_URL"] = url
        # Child settings cannot discover the deployed .env after this cwd change.
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(candidate_root / "alembic.ini"),
                "upgrade",
                "head",
            ],
            cwd=self.root,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=90,
        )
        if result.returncode:
            raise RuntimeError("qa_owned_migration_failed")

    def close(self) -> bool:
        if self.started or (self.data / "postmaster.pid").exists():
            # pg_ctl is scoped to the exact owned data directory and PID file.
            self.command(
                "/usr/lib/postgresql/16/bin/pg_ctl",
                "-D",
                str(self.data),
                "-m",
                "fast",
                "-w",
                "-t",
                "20",
                "stop",
            )
            self.started = False
        self.temporary.cleanup()
        return not self.root.exists()


@dataclass(slots=True)
class AudienceTrackOwner:
    room_owner: object
    identity: str
    publication_sid: str
    track: Any
    publication: Any
    participant: Any
    task: asyncio.Task[Any] | None = None
    retiring: bool = False


class AudienceAudio:
    def __init__(self) -> None:
        self.room: Any = None
        self.tasks: set[asyncio.Task[Any]] = set()
        self.frames: Counter[str] = Counter()
        self.samples: Counter[str] = Counter()
        self.tracks: Counter[str] = Counter()
        self.audible_samples: Counter[str] = Counter()
        self.squared_samples: Counter[str] = Counter()
        self._room_owner: object | None = None
        self._consumers: dict[tuple[object, str, str], AudienceTrackOwner] = {}

    @staticmethod
    def _retire(owner: AudienceTrackOwner) -> None:
        if owner.retiring:
            return
        owner.retiring = True
        if owner.task is not None and not owner.task.done():
            owner.task.cancel()

    async def connect(self, settings: Settings, match_id: UUID) -> None:
        assert settings.livekit_api_key is not None and settings.livekit_api_secret is not None
        token = (
            api.AccessToken(
                settings.livekit_api_key.get_secret_value(),
                settings.livekit_api_secret.get_secret_value(),
            )
            .with_identity(f"jx-qa-audience-{uuid4().hex}")
            .with_ttl(timedelta(minutes=15))
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=f"jx-match-{match_id}",
                    can_publish=False,
                    can_subscribe=True,
                    can_publish_data=False,
                )
            )
            .to_jwt()
        )
        room = rtc.Room()
        room_owner = object()
        self._room_owner = room_owner

        def prune_inventory(*_: Any) -> None:
            if self._room_owner is not room_owner:
                return
            for owner in tuple(self._consumers.values()):
                if owner.room_owner is not room_owner:
                    continue
                participant = room.remote_participants.get(owner.identity)
                publication = (
                    participant.track_publications.get(owner.publication_sid)
                    if participant is owner.participant
                    else None
                )
                if publication is not owner.publication or publication.track is not owner.track:
                    self._retire(owner)

        def subscribed(track: Any, publication: Any, participant: Any) -> None:
            if self._room_owner is not room_owner:
                return
            prune_inventory()
            current_participant = room.remote_participants.get(participant.identity)
            if (
                current_participant is not participant
                or current_participant.track_publications.get(publication.sid) is not publication
                or publication.track is not track
            ):
                return
            key = (room_owner, participant.identity, publication.sid)
            current = self._consumers.get(key)
            if current is not None:
                if (
                    current.track is track
                    and current.publication is publication
                    and current.participant is participant
                    and not current.retiring
                ):
                    return
                self._retire(current)
            kind = (
                "host"
                if participant.identity.startswith("jx-host-")
                else "agent"
                if participant.identity.startswith("jx-agent-")
                else "human"
                if participant.identity.startswith("jx-human-")
                else "other"
            )
            self.tracks[kind] += 1
            owner = AudienceTrackOwner(
                room_owner, participant.identity, publication.sid, track, publication, participant
            )
            self._consumers[key] = owner
            task = asyncio.create_task(self.consume(track, kind), name=f"qa-audience-{kind}")
            owner.task = task
            self.tasks.add(task)

            def finished(completed: asyncio.Task[Any]) -> None:
                self.tasks.discard(completed)
                if self._consumers.get(key) is owner:
                    self._consumers.pop(key, None)

            task.add_done_callback(finished)
            task.add_done_callback(_consume_detached_cleanup)

        def unsubscribed(track: Any, publication: Any, participant: Any) -> None:
            if self._room_owner is not room_owner:
                return
            owner = self._consumers.get((room_owner, participant.identity, publication.sid))
            if (
                owner is not None
                and owner.track is track
                and owner.publication is publication
                and owner.participant is participant
            ):
                self._retire(owner)

        def unpublished(publication: Any, participant: Any) -> None:
            if self._room_owner is not room_owner:
                return
            owner = self._consumers.get((room_owner, participant.identity, publication.sid))
            if (
                owner is not None
                and owner.publication is publication
                and owner.participant is participant
            ):
                self._retire(owner)

        def participant_disconnected(participant: Any) -> None:
            if self._room_owner is not room_owner:
                return
            for owner in tuple(self._consumers.values()):
                if owner.room_owner is room_owner and owner.participant is participant:
                    self._retire(owner)

        def disconnected(*_: Any) -> None:
            if self._room_owner is not room_owner:
                return
            self._room_owner = None
            for owner in tuple(self._consumers.values()):
                if owner.room_owner is room_owner:
                    self._retire(owner)

        room.on("track_subscribed", subscribed)
        room.on("track_unsubscribed", unsubscribed)
        room.on("track_unpublished", unpublished)
        room.on("participant_disconnected", participant_disconnected)
        room.on("disconnected", disconnected)
        room.on("participant_connected", prune_inventory)
        room.on("reconnected", prune_inventory)
        connection = asyncio.create_task(
            room.connect(settings.livekit_url, token, rtc.RoomOptions(auto_subscribe=True)),
            name="qa-audience-connect",
        )
        connection.add_done_callback(_consume_detached_cleanup)
        try:
            await asyncio.wait_for(asyncio.shield(connection), timeout=15)
            if self._room_owner is not room_owner:
                raise asyncio.CancelledError
            self.room = room
        except BaseException:
            if self._room_owner is room_owner:
                self._room_owner = None
            for owner in tuple(self._consumers.values()):
                if owner.room_owner is room_owner:
                    self._retire(owner)
            if not connection.done():
                connection.add_done_callback(
                    lambda done: _retired_publisher_connection_finished(done, room=room)
                )
            else:
                await asyncio.wait(_schedule_publisher_startup_cleanup(room, None))
            raise

    async def consume(self, track: Any, kind: str) -> None:
        stream = rtc.AudioStream(track, capacity=10, sample_rate=16000, num_channels=1)
        try:
            async for event in stream:
                self.frames[kind] += 1
                self.samples[kind] += event.frame.samples_per_channel
                pcm = array("h", bytes(event.frame.data))
                self.audible_samples[kind] += sum(abs(value) >= 64 for value in pcm)
                self.squared_samples[kind] += sum(value * value for value in pcm)
        finally:
            await stream.aclose()

    async def close(self) -> None:
        self._room_owner = None
        room, self.room = self.room, None
        for owner in tuple(self._consumers.values()):
            self._retire(owner)
        tasks = tuple(self.tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if room is not None:
            await room.disconnect()


class StableWarnings(logging.Handler):
    EXCEPTION_NAMES = frozenset(
        {
            "TimeoutError",
            "CancelledError",
            "ConnectionError",
            "ConnectionResetError",
            "ConnectionAbortedError",
            "BrokenPipeError",
            "OSError",
            "RuntimeError",
            "ValueError",
            "TypeError",
            "AttributeError",
            "KeyError",
            "AssertionError",
            "TtsProviderError",
            "LlmProviderError",
            "MatchDomainError",
            "IntegrityError",
            "OperationalError",
            "HTTPStatusError",
            "RemoteProtocolError",
            "ConnectTimeout",
            "ReadTimeout",
            "WriteTimeout",
            "PoolTimeout",
        }
    )
    RESOURCE_NAMES = frozenset(
        {
            "llm_client",
            "publisher_startup_source",
            "publisher_startup_room",
            "publisher_startup_queue",
            "host_decoder",
            "host_audio_queue",
            "host_audio_source",
            "host_room",
            "host_spool",
            "agent_decoder_input",
            "agent_decoder",
            "agent_audio_queue",
            "agent_audio_source",
            "agent_room",
            "agent_spool",
        }
    )

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        code = getattr(record, "error_code", None)
        if (
            isinstance(code, str)
            and re.fullmatch(r"[a-zA-Z0-9_]{1,100}", code)
            and len(self.rows) < 100
        ):
            resource = getattr(record, "resource", None)
            row: dict[str, Any] = {
                "code": code,
                "attempt_no": getattr(record, "attempt_no", None),
                "resource": resource if resource in self.RESOURCE_NAMES else None,
            }
            # Host retries use warning() inside their exception handler without
            # attaching exc_info. This synchronous QA handler can inspect that
            # active exception without changing the runtime logger or payload.
            error_info = record.exc_info or sys.exc_info()
            if error_info[0] is not None:
                error_type, _, tb = error_info
                name = error_type.__name__ if error_type is not None else None
                row["exception_class"] = name if name in self.EXCEPTION_NAMES else None
                frames: deque[dict[str, Any]] = deque(maxlen=8)
                while tb is not None:
                    function = tb.tb_frame.f_code.co_name
                    frames.append(
                        {
                            "function": function
                            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", function)
                            else None,
                            "line": tb.tb_lineno,
                        }
                    )
                    tb = tb.tb_next
                row["exception_frames"] = list(frames)
            self.rows.append(row)


def runtime_resource_counts() -> dict[str, int | None]:
    from livekit.rtc._ffi_client import FfiClient

    queue = FfiClient.instance.queue
    with queue._lock:
        sizes = [subscriber.qsize() for subscriber, _, _ in queue._subscribers]
    row: dict[str, int | None] = {
        "ffi_subscribers": len(sizes),
        "ffi_queued_events": sum(sizes),
        "ffi_largest_queue": max(sizes, default=0),
        "rss_kib": None,
        "threads": None,
        "fds": None,
    }
    status = Path("/proc/self/status")
    if status.is_file():
        for line in status.read_text().splitlines():
            if line.startswith("VmRSS:"):
                row["rss_kib"] = int(line.split()[1])
            elif line.startswith("Threads:"):
                row["threads"] = int(line.split()[1])
    fds = Path("/proc/self/fd")
    if fds.is_dir():
        row["fds"] = sum(1 for _ in fds.iterdir())
    return row


class RuntimeTelemetry:
    """One bounded record per state, refreshed by a single one-second sampler."""

    def __init__(self, state: Any, *, counts: Any = runtime_resource_counts, period: float = 1):
        self.state, self.counts, self.period = state, counts, period
        self.rows: list[dict[str, Any]] = []
        self.dropped = 0
        self.max_lag_ms = 0.0
        self.task: asyncio.Task[Any] | None = None

    def record(self) -> None:
        state = self.state()
        marker = (
            getattr(state, "status", None),
            getattr(state, "action_state", None),
            getattr(state, "current_action_index", None),
        )
        counts = self.counts()
        if not self.rows or self.rows[-1]["marker"] != marker:
            if len(self.rows) == 128:
                self.rows.pop(0)
                self.dropped += 1
            self.rows.append({"marker": marker, "samples": 0, "first_utc_ms": round(time() * 1000)})
        row = self.rows[-1]
        row["samples"] += 1
        row["last_utc_ms"] = round(time() * 1000)
        row["at_mono"] = monotonic()
        row["ffi_subscribers_peak"] = max(
            row.get("ffi_subscribers_peak", 0), counts["ffi_subscribers"]
        )
        row["ffi_queued_events_peak"] = max(
            row.get("ffi_queued_events_peak", 0), counts["ffi_queued_events"]
        )
        row["counts"] = counts

    def start(self) -> None:
        async def sample() -> None:
            while True:
                expected = monotonic() + self.period
                await asyncio.sleep(self.period)
                self.max_lag_ms = max(self.max_lag_ms, (monotonic() - expected) * 1000)
                self.record()

        self.task = asyncio.create_task(sample(), name="qa-runtime-resource-telemetry")

    async def close(self) -> dict[str, Any]:
        self.record()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        return {
            "rows": list(self.rows),
            "state_row_limit": 128,
            "dropped_state_rows": self.dropped,
            "sampling_period_ms": round(self.period * 1000),
            "max_loop_lag_ms": self.max_lag_ms,
            "observer_ffi_subscriptions_created": 0,
        }


class NativeDisconnectLedger:
    """Observe original SDK disconnect calls in this owned QA process only."""

    def __init__(self) -> None:
        self.original = rtc.Room.disconnect
        self.rows: list[dict[str, Any]] = []
        self.room_numbers: WeakKeyDictionary[Any, int] = WeakKeyDictionary()
        self.room_resources: WeakKeyDictionary[Any, str] = WeakKeyDictionary()
        self.task_numbers: WeakKeyDictionary[Any, int] = WeakKeyDictionary()
        self.next_room_number = self.next_task_number = 1

    def room_number(self, room: rtc.Room, resource: str | None = None) -> int:
        if room not in self.room_numbers:
            self.room_numbers[room] = self.next_room_number
            self.next_room_number += 1
        if resource is not None:
            self.room_resources[room] = resource
        return self.room_numbers[room]

    def task_number(self, task: asyncio.Task[Any]) -> int:
        if task not in self.task_numbers:
            self.task_numbers[task] = self.next_task_number
            self.next_task_number += 1
        return self.task_numbers[task]

    async def disconnect(self, room: rtc.Room, *args: Any, **kwargs: Any) -> None:
        operation = self.original(room, *args, **kwargs)
        identity = getattr(getattr(room, "_local_participant", None), "identity", "")
        role = next(
            (kind for kind in ("host", "agent", "human") if identity.startswith(f"jx-{kind}-")),
            "receiver_or_audience",
        )
        row: dict[str, Any] = {
            "resource": self.room_resources.get(room, f"{role}_room"),
            "owned_room_no": self.room_number(room),
        }
        started = monotonic()

        async def observe() -> None:
            while True:
                chain = []
                awaited: Any = operation
                for _ in range(10):
                    code = getattr(awaited, "cr_code", None)
                    if code is None:
                        break
                    frame = getattr(awaited, "cr_frame", None)
                    chain.append(
                        {"function": code.co_name, "line": frame.f_lineno if frame else None}
                    )
                    awaited = getattr(awaited, "cr_await", None)
                row["last_await_frames"] = chain
                listener = getattr(room, "_task", None)
                if isinstance(listener, asyncio.Task):
                    row["sdk_listener_done"] = listener.done()
                    row["sdk_listener_cancelled"] = listener.cancelled()
                await asyncio.sleep(0.1)

        monitor = asyncio.create_task(observe(), name="qa-sdk-disconnect-observer")
        try:
            await operation
            row["status"] = "returned"
        except BaseException as error:
            row["exception_class"] = type(error).__name__
            frames = []
            tb = error.__traceback__
            while tb is not None:
                frames.append({"function": tb.tb_frame.f_code.co_name, "line": tb.tb_lineno})
                tb = tb.tb_next
            row["exception_frames"] = frames[-8:]
            raise
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            listener = getattr(room, "_task", None)
            if isinstance(listener, asyncio.Task):
                row["sdk_listener_done_at_exit"] = listener.done()
                row["sdk_listener_cancelled_at_exit"] = listener.cancelled()
            row["elapsed_ms"] = round((monotonic() - started) * 1000)
            if len(self.rows) < 200:
                self.rows.append(row)

    def install(self) -> None:
        ledger = self

        async def transparent_disconnect(room: rtc.Room, *args: Any, **kwargs: Any) -> None:
            await ledger.disconnect(room, *args, **kwargs)

        rtc.Room.disconnect = transparent_disconnect

    def restore(self) -> None:
        rtc.Room.disconnect = self.original


async def load_resources(database: Database) -> tuple[Any, list[dict[str, Any]]]:
    material = await load_material(database)
    async with database.session_factory() as session:
        judge = await session.scalar(
            select(RuleJudgeConfig)
            .join(Rule, Rule.id == RuleJudgeConfig.rule_id)
            .join(ModelProfile, ModelProfile.id == RuleJudgeConfig.model_profile_id)
            .where(
                Rule.status == "ENABLED",
                RuleJudgeConfig.enabled.is_(True),
                ModelProfile.model_id == material.model_id,
            )
            .order_by(Rule.updated_at.desc())
            .limit(1)
        )
        if judge is None or not judge.judge_prompt.strip():
            raise RuntimeError("qa_enabled_rule_judge_missing")
        # Match runtime freezes RuleJudgeConfig, not the legacy global JudgeProfile
        # used by the short provider probe. Keep this production rule prompt exact.
        material = replace(material, judge_prompt=judge.judge_prompt)
        host = await session.scalar(
            select(VoiceProfile)
            .where(VoiceProfile.status == "ENABLED", VoiceProfile.kind == "HOST")
            .order_by(VoiceProfile.name)
            .limit(1)
        )
        agents = list(
            (
                await session.scalars(
                    select(VoiceProfile)
                    .where(VoiceProfile.status == "ENABLED", VoiceProfile.kind == "AGENT")
                    .order_by(VoiceProfile.name)
                    .limit(8)
                )
            ).all()
        )
        if host is None or len(agents) != 8:
            raise RuntimeError("qa_current_voice_resources_incomplete")
        voices = [
            {
                field: getattr(voice, field)
                for field in (
                    "name",
                    "kind",
                    "provider_voice",
                    "rate",
                    "chars_per_second",
                    "playback_gain",
                    "avatar_key",
                )
            }
            for voice in [host, *agents]
        ]
    return material, voices


def short_draft(
    tag: str,
    handoff_slice: bool = False,
    lifecycle_slice: bool = False,
    free_closing_slice: bool = False,
) -> dict[str, Any]:
    draft: Any = build_formal_4v4_draft()
    draft["name"] = f"真实短场QA-{tag}"
    for index, stage in enumerate(draft["stages"], 1):
        if stage["start_host_text"]:
            stage["start_host_text"] = f"测试第{index}阶段，请开始。"
        if stage["end_host_text"]:
            stage["end_host_text"] = "测试比赛结束，谢谢。"
        if stage["stage_kind"] == "FIXED_SPEECH":
            for action in stage["actions"]:
                action["duration_seconds"] = 12
            stage["speech_prompt"] = (
                DEFAULT_FIXED_SPEECH_PROMPT + "\n这场合成QA仅说一句20到25汉字的完整论点。"
            )
        if stage["stage_kind"] == "FREE_DEBATE":
            stage["duration_seconds"] = 24
            stage["parameters"]["max_speech_seconds"] = 8
            stage["speech_prompt"] = (
                DEFAULT_FREE_SPEECH_PROMPT + "\n这场合成QA仅说一句20到25汉字的完整回应。"
            )
            stage["decision_prompt"] = (
                DEFAULT_FREE_DECISION_PROMPT
                + "\n合成QA希望验证真实Agent正式发言，请争取发言，理由仅2个汉字，只输出紧凑JSON。"
            )
    if free_closing_slice:
        draft["stages"] = [draft["stages"][index] for index in (6, 7, 8, 9)]
    elif lifecycle_slice:
        draft["stages"] = [draft["stages"][index] for index in (4, 5, 7, 8, 9)]
    elif handoff_slice:
        draft["stages"] = [draft["stages"][8], draft["stages"][-1]]
    return draft


async def prepare_room(
    factory: Any,
    settings: Settings,
    material: Any,
    voices: list[dict[str, Any]],
    handoff_slice: bool = False,
    lifecycle_slice: bool = False,
    free_closing_slice: bool = False,
) -> dict[str, Any]:
    catalog = CatalogService()
    rules = RuleService(
        AudioConfiguration(
            model=settings.tts_model,
            workspace_id=settings.tts_workspace_id or "",
            websocket_url=settings.tts_ws_url,
            storage_root=settings.host_audio_storage_dir,
        )
    )
    rooms = RoomService()
    tag = uuid4().hex[:8]
    humans = {(side, seat): uuid4() for side in ("AFFIRMATIVE", "NEGATIVE") for seat in (1, 2)}
    spectator_id = uuid4()
    async with factory() as session:
        session.add_all(
            [
                User(
                    id=user_id,
                    username=f"qa-{tag}-{index}",
                    username_normalized=f"qa-{tag}-{index}",
                    real_name=f"合成辩手{index + 1}",
                    password_hash="unusable-synthetic-hash",
                )
                for index, user_id in enumerate([*humans.values(), spectator_id])
            ]
        )
        await session.commit()
        owner = next(iter(humans.values()))
        model = await catalog.create_model(
            session,
            master_key=(settings.llm_key_encryption_key.get_secret_value()),
            payload=ModelProfileCreate(
                name=f"qa-current-model-{tag}",
                config_ref="server-owned-current-provider",
                base_url=material.base_url,
                model_id=material.model_id,
                generation_params=material.model_params,
                api_key=SecretStr(material.api_key),
            ),
        )
        host_id = None
        for values in voices:
            voice = await catalog.create_voice(session, payload=VoiceProfileCreate(**values))
            if values["kind"] == "HOST":
                host_id = voice.id
        rule = await rules.create_rule(
            session,
            creator_user_id=owner,
            payload=RuleCreate(
                host_voice_profile_id=host_id,
                default_agent_model_profile_id=model.id,
                draft=short_draft(tag, handoff_slice, lifecycle_slice, free_closing_slice),
            ),
        )
        await rules.update_judge(
            session,
            rule_id=rule.id,
            actor_user_id=owner,
            payload=RuleJudgeUpdate(
                enabled=not (handoff_slice or lifecycle_slice or free_closing_slice),
                model_profile_id=model.id,
                judge_prompt=material.judge_prompt,
            ),
        )
    client = DashScopeTTSClient(
        websocket_url=settings.tts_ws_url,
        api_key=settings.tts_api_key.get_secret_value(),
        workspace=settings.tts_workspace_id or "",
        model=settings.tts_model,
    )
    writer = ProviderCaptureWriter(settings.database_url_value, service="jx-qa-host")
    await writer.start()
    try:
        expected_assets = (
            4 if free_closing_slice else 5 if lifecycle_slice else 2 if handoff_slice else 10
        )
        deadline = monotonic() + 240
        for _ in range(30):
            worked = await process_one_host_tts(
                factory,
                client=client,
                storage_root=Path(settings.host_audio_storage_dir),
                capture_writer=writer,
            )
            async with factory() as session:
                ready = await session.scalar(
                    select(func.count())
                    .select_from(HostAudioAsset)
                    .where(HostAudioAsset.rule_id == rule.id, HostAudioAsset.status == "READY")
                )
            if ready == expected_assets:
                break
            if monotonic() >= deadline:
                raise RuntimeError("qa_host_asset_generation_timeout")
            if not worked:
                await asyncio.sleep(1)
        if ready != expected_assets:
            raise RuntimeError("qa_host_asset_generation_failed")
    finally:
        await client.close()
        await writer.close()
    async with factory() as session:
        await rules.review_audio(session, rule_id=rule.id)
        await rules.enable_rule(session, rule_id=rule.id)
        terms = get_current_human_participation_terms().version
        room = await rooms.create_room(
            session,
            organizer_user_id=owner,
            organizer_role="USER",
            payload=RoomCreateRequest(
                title=f"真实短场QA-{tag}",
                label="服务器隔离合成QA",
                rule_id=rule.id,
                custom_topic_title="过程还是结果更体现奋斗价值",
                affirmative_text="过程更体现奋斗价值",
                negative_text="结果更体现奋斗价值",
                human_participation_terms_version=terms,
            ),
        )
        for index, ((side, seat), user_id) in enumerate(humans.items()):
            if index:
                await rooms.join_room(
                    session,
                    user_id=user_id,
                    room_id=room.id,
                    payload=RoomJoinRequest(
                        member_role="DEBATER", human_participation_terms_version=terms
                    ),
                )
            await rooms.select_seat(
                session,
                user_id=user_id,
                room_id=room.id,
                payload=SeatSelectRequest(
                    side=side, seat_no=seat, human_participation_terms_version=terms
                ),
            )
            check = await rooms.save_device_check(
                session,
                user_id=user_id,
                room_id=room.id,
                payload=DeviceCheckRequest(status="PASS", details={"synthetic_rtc_qa": True}),
            )
            await rooms.ready(
                session, user_id=user_id, room_id=room.id, check_version=check.check_version
            )
        await rooms.join_room(
            session,
            user_id=spectator_id,
            room_id=room.id,
            payload=RoomJoinRequest(member_role="SPECTATOR"),
        )
        await rooms.start_room(session, room_id=room.id, actor_user_id=owner, actor_role="USER")
        leases = {}
        for user_id in humans.values():
            leases[user_id] = await RoomConnectionService().acquire(
                session,
                user_id=user_id,
                room_id=room.id,
            )
    return {
        "room_id": room.id,
        "owner": owner,
        "spectator": spectator_id,
        "humans": humans,
        "host_assets": ready,
        "leases": leases,
        "handoff_slice": handoff_slice,
        "lifecycle_slice": lifecycle_slice,
        "free_closing_slice": free_closing_slice,
    }


async def route_command(
    manager: Any, factory: Any, values: Any, actor: Any, command_type: str
) -> Any:
    owner = values["owner"]
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(
                state=SimpleNamespace(match_runtime_manager=manager),
            ),
        }
    )
    async with factory() as session:
        return await submit_match_command(
            actor.state.match_id,
            MatchCommandRequest(
                type=command_type,
                message_id=f"qa-route-{uuid4().hex}",
                expected_sequence=actor.state.sequence,
                connection_epoch=values["leases"][owner].connection_epoch,
            ),
            request,
            AuthContext(owner, "USER", uuid4(), False),
            session,
        )


async def pause_and_resume(
    manager: Any, factory: Any, values: Any, actor: Any, checkpoint: dict[str, Any], kind: str
) -> None:
    before_speech = actor.state.current_speech_id
    before_action = actor.state.current_action_index
    await route_command(manager, factory, values, actor, "match.pause")
    assert actor.state.status == "PAUSED"
    if kind == "HUMAN":
        receiver = manager._speech_runtime._receivers[actor.state.match_id]
        deadline = monotonic() + 8
        while receiver._speech is not None and monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert receiver._speech is None and receiver._paused_speech is not None
        checkpoint["human_checkpoint_audio_ms"] = receiver._paused_speech.audio_duration_ms
    started = monotonic()
    await route_command(manager, factory, values, actor, "match.resume")
    assert actor.state.action_state == "RESUME_COUNTDOWN"
    while actor.state.status == "PAUSED" and monotonic() - started < 10:
        await asyncio.sleep(0.05)
    assert actor.state.status == "RUNNING" and actor.state.current_action_index == before_action
    assert monotonic() - started >= 2.9
    if kind == "HUMAN":
        assert actor.state.current_speech_id == before_speech
        assert actor.state.action_state == "HUMAN_READY_TO_START"
    else:
        assert actor.state.current_speech_id != before_speech
        assert actor.state.action_state in {"AGENT_PREPARING", "AGENT_SPEAKING"}
    checkpoint[f"{kind.lower()}_natural_resume_ms"] = round((monotonic() - started) * 1000)
    checkpoint[f"{kind.lower()}_pause_resume"] = "passed"


async def speak(
    manager: Any,
    actor: Any,
    source: Any,
    pcm: bytes,
    interruption: Any = None,
    *,
    finish_when_done: bool = True,
) -> None:
    user_id = actor.state.current_speaker_user_id
    action_key = actor.state.current_action.action_key
    message = uuid4().hex
    await manager.submit(
        actor.state.match_id,
        MatchCommand(
            type="speech.start",
            message_id=f"qa-start-{message}",
            actor_user_id=user_id,
        ),
    )
    speech_id = actor.state.current_speech_id
    samples = array("h", pcm)
    expanded = array("h", (value for sample in samples for value in (sample,) * 3))
    if not expanded:
        raise RuntimeError("qa_microphone_pcm_empty")
    speech_watchdog = monotonic() + (actor.view().speech_remaining_ms or 0) / 1000 + 20
    offset = 0
    while offset < len(expanded) or not finish_when_done:
        if not finish_when_done and monotonic() >= speech_watchdog:
            raise RuntimeError("qa_human_natural_deadline_timeout")
        if offset >= len(expanded):
            offset = 0
        if (
            actor.state.current_speech_id != speech_id
            or actor.state.action_state != "HUMAN_SPEAKING"
        ):
            break
        frame = expanded[offset : offset + 4800]
        await source.capture_frame(
            rtc.AudioFrame(
                data=frame.tobytes(),
                sample_rate=48000,
                num_channels=1,
                samples_per_channel=len(frame),
            )
        )
        if interruption is not None and offset >= 76800:
            source.clear_queue()
            await interruption()
            return
        offset += 4800
    if not finish_when_done:
        source.clear_queue()
    for _ in range(6):
        if not finish_when_done and actor.state.current_speech_id != speech_id:
            break
        await source.capture_frame(
            rtc.AudioFrame(
                data=bytes(9600),
                sample_rate=48000,
                num_channels=1,
                samples_per_channel=4800,
            )
        )
    await source.wait_for_playout()
    await asyncio.sleep(0.1)
    if (
        finish_when_done
        and actor.state.current_speech_id == speech_id
        and actor.state.action_state == "HUMAN_SPEAKING"
    ):
        await manager.submit(
            actor.state.match_id,
            MatchCommand(
                type="speech.finish",
                message_id=f"qa-finish-{message}",
                actor_user_id=user_id,
            ),
        )
    deadline = monotonic() + 20
    while actor.state.current_speech_id == speech_id and monotonic() < deadline:
        if actor.state.status != "RUNNING":
            raise RuntimeError("qa_human_speech_not_running")
        if actor.state.current_action.action_key != action_key:
            break
        await asyncio.sleep(0.05)


async def postmatch_awards(factory: Any, values: dict[str, Any], match_id: UUID) -> dict[str, Any]:
    async with factory() as session:
        participants = list(
            (
                await session.scalars(
                    select(MatchParticipant)
                    .where(MatchParticipant.match_id == match_id)
                    .order_by(MatchParticipant.side, MatchParticipant.seat_no)
                )
            ).all()
        )
        owner = AuthContext(values["owner"], "USER", uuid4(), False)
        spectator = AuthContext(values["spectator"], "USER", uuid4(), False)
        initial = await get_showcase(match_id, owner, session)
        assert initial.can_manage and len(initial.participants) == 8
        await save_judge_entry(
            match_id,
            JudgeEntryRequest(
                comments="合成现场评委点评",
                winner_side="AFFIRMATIVE",
                best_participant_id=participants[0].id,
                published=True,
            ),
            owner,
            session,
        )
        await update_showcase_phase(
            match_id, ShowcasePhaseRequest(phase="AUDIENCE_VOTE"), owner, session
        )
        vote = AudienceVoteRequest(winner_side="AFFIRMATIVE", ranking=[p.id for p in participants])
        await submit_audience_vote(match_id, vote, spectator, session)
        duplicate_rejected = False
        try:
            await submit_audience_vote(match_id, vote, spectator, session)
        except APIError:
            duplicate_rejected = True
            await session.rollback()
        awards = await update_showcase_phase(
            match_id, ShowcasePhaseRequest(phase="AWARDS"), owner, session
        )
        assert duplicate_rejected and awards.audience["total_votes"] == 1
        assert len(awards.audience["ranking"]) == 8
        return {
            "phase": awards.phase,
            "votes": 1,
            "ranked_participants": 8,
            "duplicate_vote_rejected": duplicate_rejected,
        }


async def failure_shapes(factory: Any, match_id: UUID) -> list[dict[str, Any]]:
    """Derive bounded response shape in server memory, without copying payloads."""
    from server_judge_shape_probe import shape

    summaries = []
    async with factory() as session:
        participants = list(
            (
                await session.scalars(
                    select(MatchParticipant)
                    .where(MatchParticipant.match_id == match_id)
                    .order_by(MatchParticipant.side, MatchParticipant.seat_no)
                )
            ).all()
        )
        participant_records = [
            {
                "participant_id": str(p.id),
                "name": p.display_name,
                "side": p.side,
                "seat_no": p.seat_no,
            }
            for p in participants
        ]
        calls = list(
            (
                await session.scalars(
                    select(ExternalCall)
                    .where(
                        ExternalCall.match_id == match_id,
                        ExternalCall.status == "FAILED",
                        ExternalCall.call_kind.in_(["LLM_DECISION", "JUDGE"]),
                    )
                    .order_by(ExternalCall.started_at)
                    .limit(16)
                )
            ).all()
        )
        for call in calls:
            info: dict[str, Any] = {
                "kind": call.call_kind,
                "attempt_no": call.attempt_no,
                "error_code": call.error_code,
            }
            projection = await capture_projection(session, call)
            raw_id = projection.get("response_blob_id")
            if raw_id is None:
                info["capture"] = "unavailable"
                summaries.append(info)
                continue
            raw = await load_content_blob(session, raw_id)
            body = raw.get("body", {}) if isinstance(raw, dict) else {}
            if not isinstance(body, dict):
                info["capture"] = "body_unavailable"
                summaries.append(info)
                continue
            response = body.get("text", "") if isinstance(body, dict) else ""
            info["completion_tokens"] = body.get("completion_tokens")
            info["max_tokens"] = 32 if call.call_kind == "LLM_DECISION" else 1600
            info["finish_reasons"] = sorted(
                {
                    choice["finish_reason"]
                    for event in raw.get("events", [])
                    if isinstance(event, dict)
                    for choice in event.get("choices", [])
                    if isinstance(choice, dict)
                    and choice.get("finish_reason")
                    in {"stop", "length", "content_filter", "tool_calls"}
                }
            )
            info["finish_reason_available"] = bool(info["finish_reasons"])
            if call.call_kind == "JUDGE":
                info["shape"] = shape(response, participant_records)
            else:
                info["response_chars"] = len(response)
                try:
                    parsed = json.loads(response)
                except json.JSONDecodeError as error:
                    info.update(
                        json_valid=False,
                        json_error_line=error.lineno,
                        json_error_column=error.colno,
                    )
                else:
                    info["json_valid"] = True
                    info["expected_keys_exact"] = isinstance(parsed, dict) and set(parsed) == {
                        "should_speak",
                        "decision_reason",
                    }
                    if isinstance(parsed, dict):
                        known = {"should_speak", "willingness", "decision_reason"}
                        info["known_keys"] = sorted(known & parsed.keys())
                        info["other_key_count"] = len(parsed.keys() - known)
                    info["reason_chars"] = (
                        len(parsed.get("decision_reason", "")) if isinstance(parsed, dict) else None
                    )
                try:
                    _parse_experiment_decision(response)
                except Exception as error:
                    tb = error.__traceback__
                    while tb is not None:
                        if tb.tb_frame.f_code.co_name == "_parse_experiment_decision":
                            info["parser_failure_line"] = tb.tb_lineno
                        tb = tb.tb_next
            summaries.append(info)
    return summaries


async def run_match(
    factory: Any,
    settings: Settings,
    values: dict[str, Any],
    pcm: Any,
    pause_resume: bool = False,
) -> dict[str, Any]:
    baseline = set(asyncio.all_tasks())
    writer = ProviderCaptureWriter(settings.database_url_value, service="jx-qa-core")
    await writer.start()
    manager = MatchRuntimeManager(factory, capture_writer=writer)
    limiter = LlmCapacityLimiter(4)
    agents = AgentRuntime(
        settings=settings,
        session_factory=factory,
        callbacks=manager,
        limiter=limiter,
        capture_writer=writer,
    )
    asr = AsrRuntime(settings=settings, callbacks=manager, capture_writer=writer)
    postmatch = PostmatchService(
        settings=settings, session_factory=factory, limiter=limiter, capture_writer=writer
    )
    manager.set_speech_runtime(asr)
    manager.set_agent_runtime(agents)
    manager.set_postmatch_runtime(postmatch)
    qa_driver = values.get("qa_driver")
    publishers: dict[UUID, tuple[Any, Any]] = {}
    audience = AudienceAudio()
    match_id = None
    result: dict[str, Any] = {"status": "failed"}
    verification: dict[str, Any] = {}
    interruption_receipts: dict[str, Any] = {}
    host_callback_receipts: list[dict[str, Any]] = []
    host_operation_receipts: list[dict[str, Any]] = []
    original_host_finished = manager.host_audio_finished
    original_play_host = agents._play_host_audio

    async def observed_play_host(run: Any) -> None:
        operation = original_play_host(run)
        observations: dict[str, Any] = {"attempt_no": run.attempt_no}
        started = monotonic()

        async def observe() -> None:
            while True:
                chain = []
                awaited: Any = operation
                for _ in range(10):
                    code = getattr(awaited, "cr_code", None)
                    if code is None:
                        break
                    chain.append(code.co_name)
                    if code.co_name == "_connect_publisher":
                        frame = getattr(awaited, "cr_frame", None)
                        joining = frame.f_locals.get("connection_task") if frame else None
                        if isinstance(joining, asyncio.Task):
                            observations["sdk_join_done"] = joining.done()
                            observations["sdk_join_cancelled"] = joining.cancelled()
                            sdk_frames = []
                            child: Any = joining.get_coro()
                            for _ in range(10):
                                child_code = getattr(child, "cr_code", None)
                                if child_code is None:
                                    break
                                child_frame = getattr(child, "cr_frame", None)
                                sdk_frames.append(
                                    {
                                        "function": child_code.co_name,
                                        "line": child_frame.f_lineno if child_frame else None,
                                    }
                                )
                                child = getattr(child, "cr_await", None)
                            observations["sdk_join_await_frames"] = sdk_frames
                    awaited = getattr(awaited, "cr_await", None)
                observations["last_await_functions"] = chain
                await asyncio.sleep(0.1)

        monitor = asyncio.create_task(observe(), name="qa-host-operation-observer")
        try:
            await operation
            observations["status"] = "returned"
        except BaseException as error:
            observations["exception_class"] = type(error).__name__
            frames = []
            tb = error.__traceback__
            while tb is not None:
                frames.append({"function": tb.tb_frame.f_code.co_name, "line": tb.tb_lineno})
                tb = tb.tb_next
            observations["exception_frames"] = frames[-8:]
            raise
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            observations["elapsed_ms"] = round((monotonic() - started) * 1000)
            host_operation_receipts.append(observations)

    agents._play_host_audio = observed_play_host

    async def observed_host_finished(
        *, envelope: Any, action_key: str, runtime: Any = agents, authority: Any = manager
    ) -> None:
        actor = authority._actors.get(envelope.match_id)
        observed = {
            "attempt_no": envelope.attempt_no,
            "action_index": actor.state.current_action_index if actor else None,
            "runtime_owner_current": runtime.host_callback_is_current(
                envelope=envelope, action_key=action_key
            ),
            "envelope_context_version": envelope.context_version,
        }
        host_callback_receipts.append(observed)
        try:
            await original_host_finished(envelope=envelope, action_key=action_key)
        except BaseException as error:
            observed["exception_class"] = type(error).__name__
            frames = []
            tb = error.__traceback__
            while tb:
                frames.append({"function": tb.tb_frame.f_code.co_name, "line": tb.tb_lineno})
                tb = tb.tb_next
            observed["exception_frames"] = frames[-8:]
            raise
        else:
            observed["status"] = "returned"

    manager.host_audio_finished = observed_host_finished
    warnings = StableWarnings()
    agent_logger = logging.getLogger("jx-core.agent")
    old_level, old_propagate = agent_logger.level, agent_logger.propagate
    agent_logger.setLevel(logging.WARNING)
    agent_logger.propagate = False
    agent_logger.addHandler(warnings)
    disconnects = NativeDisconnectLedger()
    disconnects.install()
    publisher_joins: list[dict[str, Any]] = []
    agent_cancellations: list[dict[str, Any]] = []
    original_connect = agents._connect_publisher
    original_cancel_run = agents._cancel_run
    original_finalize = agents.finalize_agent

    def await_frames(awaited: Any) -> list[dict[str, Any]]:
        frames = []
        for _ in range(12):
            code = getattr(awaited, "cr_code", None)
            if code is None:
                break
            frame = getattr(awaited, "cr_frame", None)
            frames.append({"function": code.co_name, "line": frame.f_lineno if frame else None})
            awaited = getattr(awaited, "cr_await", None)
        return frames

    async def observed_connect(
        owner_match_id: UUID, *, authority: Any = manager, **kwargs: Any
    ) -> Any:
        operation = original_connect(owner_match_id, **kwargs)
        resource_name = "host_room" if kwargs.get("identity_prefix") == "jx-host" else "agent_room"
        actor = authority._actors.get(owner_match_id)
        row: dict[str, Any] = {
            "resource": resource_name,
            "action_index": actor.state.current_action_index if actor else None,
            "action_state": actor.state.action_state if actor else None,
        }
        joining_ref: Any = None
        started = monotonic()

        async def observe() -> None:
            nonlocal joining_ref
            while True:
                frame = getattr(operation, "cr_frame", None)
                if frame is not None:
                    room = frame.f_locals.get("room")
                    if isinstance(room, rtc.Room):
                        row["owned_room_no"] = disconnects.room_number(room, resource_name)
                    joining = frame.f_locals.get("connection_task")
                    if isinstance(joining, asyncio.Task):
                        joining_ref = ref(joining)
                        row["sdk_connection_task_no"] = disconnects.task_number(joining)
                        row["sdk_connection_done"] = joining.done()
                        row["sdk_connection_cancelled"] = joining.cancelled()
                        row["sdk_connection_await_frames"] = await_frames(joining.get_coro())
                row["publisher_await_frames"] = await_frames(operation)
                await asyncio.sleep(0.05)

        monitor = asyncio.create_task(observe(), name="qa-publisher-join-observer")
        try:
            result_value = await operation
            row["owned_room_no"] = disconnects.room_number(result_value[0], resource_name)
            row["status"] = "returned"
            return result_value
        except BaseException as error:
            row["exception_class"] = type(error).__name__
            frames = []
            tb = error.__traceback__
            while tb is not None:
                frames.append({"function": tb.tb_frame.f_code.co_name, "line": tb.tb_lineno})
                tb = tb.tb_next
            row["exception_frames"] = frames[-8:]
            raise
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            joining = joining_ref() if joining_ref is not None else None
            if joining is not None:
                row["sdk_connection_done_at_exit"] = joining.done()
                row["sdk_connection_cancelled_at_exit"] = joining.cancelled()
            row["elapsed_ms"] = round((monotonic() - started) * 1000)
            if len(publisher_joins) < 200:
                publisher_joins.append(row)

    async def observed_agent_lifecycle(
        operation: Any, run: Any, kind: str, reason: Any, authority: Any = manager
    ) -> None:
        actor = authority._actors.get(run.match_id) if run else None
        task = run.task if run else None
        row: dict[str, Any] = {
            "kind": kind,
            "reason": reason
            if isinstance(reason, str) and re.fullmatch(r"[A-Z_]{1,40}", reason)
            else None,
            "action_index": actor.state.current_action_index if actor else None,
            "action_state": actor.state.action_state if actor else None,
            "duration_ms": run.duration_ms if run else None,
            "played_ms_at_entry": run.played_samples * 1000 // 48000 if run else None,
            "natural_complete_at_entry": run.natural_complete if run else None,
            "media_fenced_at_entry": run.media_fenced if run else None,
            "owned_room_no": disconnects.room_number(run.room) if run and run.room else None,
            "agent_task_done_at_entry": task.done() if task else None,
            "agent_task_cancelled_at_entry": task.cancelled() if task else None,
            "agent_task_await_frames": await_frames(task.get_coro()) if task else [],
        }
        started = monotonic()
        try:
            await operation
            row["status"] = "returned"
        except BaseException as error:
            row["exception_class"] = type(error).__name__
            raise
        finally:
            row["elapsed_ms"] = round((monotonic() - started) * 1000)
            row["agent_task_done_at_exit"] = task.done() if task else None
            row["agent_task_cancelled_at_exit"] = task.cancelled() if task else None
            if len(agent_cancellations) < 100:
                agent_cancellations.append(row)

    async def observed_cancel_run(run: Any) -> None:
        await observed_agent_lifecycle(original_cancel_run(run), run, "cancel_run", None)

    async def observed_finalize(
        owner_match_id: UUID, speech_id: UUID, reason: str, runtime: Any = agents
    ) -> None:
        run = runtime._runs.get(owner_match_id)
        await observed_agent_lifecycle(
            original_finalize(owner_match_id, speech_id, reason), run, "finalize_agent", reason
        )

    agents._connect_publisher = observed_connect
    agents._cancel_run = observed_cancel_run
    agents.finalize_agent = observed_finalize
    telemetry = RuntimeTelemetry(
        lambda authority=manager: (
            authority._actors[match_id].state if match_id in authority._actors else None
        )
    )
    telemetry.start()
    try:
        if qa_driver is not None:
            await qa_driver.bind(manager=manager, agents=agents, limiter=limiter, writer=writer)
        async with factory() as session:
            pending = await manager.start_room_match(
                session,
                room_id=values["room_id"],
                actor_user_id=values["owner"],
                actor_role="USER",
                launch_runtime=False,
            )
        assert pending.status == "START_PENDING_RUNTIME"
        match_id = pending.match_id
        actor = await manager.get_actor(match_id)
        await asr.ensure_match(match_id)
        await audience.connect(settings, match_id)
        for user_id in values["humans"].values():
            publishers[user_id] = await asyncio.wait_for(
                connect_human(settings, match_id, user_id), 15
            )
        await asyncio.sleep(0.2)
        assert actor.state.status == "START_PENDING_RUNTIME" and actor.state.sequence == 0
        async with factory() as session:
            await manager.start_existing_match_runtime(
                session, room_id=values["room_id"], actor_user_id=values["owner"], actor_role="USER"
            )
        handled_opportunities: set[Any] = set()
        free_turns = 0
        last_marker = None
        deadline = monotonic() + 480
        while monotonic() < deadline:
            state = actor.state
            marker = (state.status, state.action_state, state.current_action_index)
            if marker != last_marker:
                telemetry.record()
                print(
                    json.dumps(
                        {
                            "progress": "owned_match",
                            "status": state.status,
                            "action_state": state.action_state,
                            "action_index": state.current_action_index,
                        }
                    ),
                    flush=True,
                )
                last_marker = marker
            if state.status == "FINISHED":
                break
            if state.status in {"PAUSED", "ERROR", "SYSTEM_RECOVERY", "TERMINATED"}:
                raise RuntimeError("qa_match_provider_or_runtime_failure")
            if qa_driver is not None and await qa_driver.tick(actor, publishers, pcm):
                continue
            if (
                pause_resume
                and state.action_state == "AGENT_SPEAKING"
                and "agent_pause_resume" not in interruption_receipts
            ):
                await pause_and_resume(
                    manager, factory, values, actor, interruption_receipts, "AGENT"
                )
                continue
            if (
                state.action_state == "FREE_SELECTING"
                and state.opportunity_id not in handled_opportunities
            ):
                handled_opportunities.add(state.opportunity_id)
                if free_turns % 4 < 2:
                    user_id = values["humans"][(state.free_holder_side, 1)]
                    await manager.submit(
                        match_id,
                        MatchCommand(
                            type="hand.raise",
                            message_id=f"qa-hand-{uuid4().hex}",
                            actor_user_id=user_id,
                        ),
                    )
                free_turns += 1
            elif state.action_state == "HUMAN_READY_TO_START":
                interruption = None
                if pause_resume and "human_pause_resume" not in interruption_receipts:
                    interruption = partial(
                        pause_and_resume,
                        manager,
                        factory,
                        values,
                        actor,
                        interruption_receipts,
                        "HUMAN",
                    )

                await speak(
                    manager,
                    actor,
                    publishers[state.current_speaker_user_id][1],
                    pcm[state.current_speaker_side] if isinstance(pcm, dict) else pcm,
                    interruption,
                )
            else:
                await asyncio.sleep(0.05)
        if actor.state.status != "FINISHED":
            raise RuntimeError("qa_full_match_timeout")
        lifecycle_slice = values["lifecycle_slice"]
        free_closing_slice = values["free_closing_slice"]
        handoff_slice = values["handoff_slice"] or lifecycle_slice or free_closing_slice
        judge_deadline = monotonic() + 90
        judge = None
        while not handoff_slice and monotonic() < judge_deadline:
            async with factory() as session:
                judge = await session.scalar(
                    select(JudgeResult)
                    .where(JudgeResult.match_id == match_id)
                    .order_by(JudgeResult.created_at.desc())
                    .limit(1)
                )
            if judge is not None and judge.status in {"SUCCEEDED", "FAILED"}:
                break
            await asyncio.sleep(0.2)
        verification["ai_judge"] = judge.status if judge else "not_tested"
        verification["judge_attempt_no"] = judge.attempt_no if judge else None
        if not handoff_slice and (judge is None or judge.status not in {"SUCCEEDED", "FAILED"}):
            raise RuntimeError("qa_full_match_judge_timeout")
        awards = (
            await postmatch_awards(factory, values, match_id)
            if not handoff_slice
            else {"phase": "not_tested"}
        )
        verification["postmatch"] = awards
        async with factory() as session:
            all_speeches = list(
                (
                    await session.scalars(
                        select(Speech)
                        .where(Speech.match_id == match_id)
                        .order_by(Speech.created_at)
                    )
                ).all()
            )
            events = list(
                (
                    await session.scalars(
                        select(StoredEvent)
                        .where(StoredEvent.match_id == match_id)
                        .order_by(StoredEvent.sequence)
                    )
                ).all()
            )
            match = await session.get(Match, match_id)
            calls = (
                await session.execute(
                    select(ExternalCall.call_kind, ExternalCall.status, func.count()).group_by(
                        ExternalCall.call_kind, ExternalCall.status
                    )
                )
            ).all()
            replay = await session.scalar(
                select(func.count())
                .select_from(BackgroundTask)
                .where(
                    BackgroundTask.task_type == "POSTMATCH_AUDIO",
                    func.jsonb_extract_path_text(BackgroundTask.payload, "match_id")
                    == str(match_id),
                )
            )
            generation_states = (
                await session.execute(
                    select(Speech.status, AgentGeneration.status, func.count())
                    .outerjoin(AgentGeneration, AgentGeneration.id == Speech.generation_id)
                    .where(Speech.match_id == match_id, Speech.speaker_kind == "AGENT")
                    .group_by(Speech.status, AgentGeneration.status)
                )
            ).all()
        verification["stored_speech_status_counts"] = dict(Counter(s.status for s in all_speeches))
        verification["free_side_kind_counts"] = dict(
            Counter(
                f"{speech.side}:{speech.speaker_kind}"
                for speech in all_speeches
                if speech.status == "FINALIZED" and speech.opportunity_id is not None
            )
        )
        verification["agent_speech_generation_status_counts"] = [
            {"speech_status": speech_status, "generation_status": generation_status, "count": count}
            for speech_status, generation_status, count in generation_states
        ]
        verification["provider_calls"] = [
            {"kind": kind, "status": status, "count": count} for kind, status, count in calls
        ]
        verification["postmatch_audio_tasks_queued"] = replay
        verification["authoritative_sequence"] = match.sequence
        verification["host_play_events"] = sum(event.event_type == "host.play" for event in events)
        verification["finished_events"] = sum(
            event.event_type == "match.finished" for event in events
        )
        verification["postmatch_audio_archive"] = await verify_owned_archive(
            factory,
            settings,
            match_id,
            owned_root=Path(settings.match_audio_storage_dir).parent,
        )
        assert verification["postmatch_audio_archive"]["status"] == "passed"
        speeches = [speech for speech in all_speeches if speech.status == "FINALIZED"]
        placeholders = [speech for speech in speeches if speech.origin == "ADMIN_PLACEHOLDER"]
        assert len(placeholders) == values.get("expected_admin_placeholders", 0)
        assert all(speech.display_text and not speech.audio_storage_path for speech in placeholders)
        assert all(s.status in {"FINALIZED", "RESET"} for s in all_speeches)
        fixed = [speech for speech in speeches if speech.opportunity_id is None]
        free = [speech for speech in speeches if speech.opportunity_id is not None]
        assert len(fixed) == (
            2 if free_closing_slice else 4 if lifecycle_slice else 1 if handoff_slice else 8
        )
        expected_fixed = [
            ("AFFIRMATIVE", 1),
            ("NEGATIVE", 1),
            ("AFFIRMATIVE", 2),
            ("NEGATIVE", 2),
            ("AFFIRMATIVE", 3),
            ("NEGATIVE", 3),
            ("NEGATIVE", 4),
            ("AFFIRMATIVE", 4),
        ]
        assert [(s.side, s.seat_no) for s in fixed] == (
            expected_fixed[-2:]
            if free_closing_slice
            else expected_fixed[4:]
            if lifecycle_slice
            else [("AFFIRMATIVE", 4)]
            if handoff_slice
            else expected_fixed
        )
        if not handoff_slice or free_closing_slice:
            assert {s.speaker_kind for s in free} == {"HUMAN", "AGENT"}
        if not handoff_slice and not lifecycle_slice and not free_closing_slice:
            assert set(verification["free_side_kind_counts"]) == {
                f"{side}:{kind}"
                for side in ("AFFIRMATIVE", "NEGATIVE")
                for kind in ("HUMAN", "AGENT")
            }
        assert all(
            s.status == "FINALIZED" and s.display_text and s.audio_storage_path
            for s in speeches
            if s.origin != "ADMIN_PLACEHOLDER"
        )
        assert [event.sequence for event in events] == list(range(1, match.sequence + 1))
        types = Counter(event.event_type for event in events)
        expected_kinds = (
            ("host", "agent")
            if handoff_slice and not free_closing_slice
            else ("host", "agent", "human")
        )
        assert types["match.finished"] == 1
        assert types["host.play"] == values["host_assets"] + values.get("expected_host_replays", 0)
        assert replay == 1 and all(audience.frames[kind] > 0 for kind in expected_kinds)
        assert all(audience.audible_samples[kind] > 1000 for kind in expected_kinds)
        result = {
            "status": "passed" if handoff_slice or judge.status == "SUCCEEDED" else "failed",
            "match_status": match.status,
            "speeches": len(speeches),
            "fixed_speeches": len(fixed),
            "free_speeches": len(free),
            "free_speaker_kinds": sorted({s.speaker_kind for s in free}),
            "free_sides": [s.side for s in free],
            "host_play_events": types["host.play"],
            "finished_events": types["match.finished"],
            "authoritative_sequence": match.sequence,
            "ai_judge": judge.status if judge else "not_tested",
            "judge_attempt_no": judge.attempt_no if judge else None,
            "interruption_receipts": interruption_receipts,
            "stored_speech_status_counts": dict(Counter(s.status for s in all_speeches)),
            "postmatch": awards,
            "postmatch_audio_tasks": replay,
            "provider_calls": [
                {"kind": kind, "status": status, "count": count} for kind, status, count in calls
            ],
            "audience_audio_frames": dict(audience.frames),
            "audience_audible_samples": dict(audience.audible_samples),
            "audience_pcm_rms": {
                kind: round(math.sqrt(audience.squared_samples[kind] / count), 1)
                for kind, count in audience.samples.items()
                if count
            },
            "audience_audio_duration_ms": {
                kind: count // 16 for kind, count in audience.samples.items()
            },
        }
        if not handoff_slice and judge.status != "SUCCEEDED":
            result["error_code"] = "qa_full_match_judge_failed"
        if qa_driver is not None:
            result["spec304_scenario"] = await qa_driver.verify(actor, all_speeches, events)
    except Exception as error:
        result = error_receipt(error)
        frames = []
        tb = error.__traceback__
        while tb is not None:
            frames.append({"function": tb.tb_frame.f_code.co_name, "line": tb.tb_lineno})
            tb = tb.tb_next
        result["qa_exception_frames"] = frames[-8:]
        if match_id is not None:
            actor = manager._actors.get(match_id)
            if actor is not None:
                result.update(
                    match_status=actor.state.status,
                    action_state=actor.state.action_state,
                    action_index=actor.state.current_action_index,
                )
            async with factory() as session:
                errors = (
                    await session.execute(
                        select(ExternalCall.call_kind, ExternalCall.error_code, func.count())
                        .where(ExternalCall.status == "FAILED")
                        .group_by(ExternalCall.call_kind, ExternalCall.error_code)
                    )
                ).all()
                result["provider_errors"] = [
                    {"kind": kind, "code": code, "count": count} for kind, code, count in errors
                ]
    finally:
        result["runtime_telemetry"] = await telemetry.close()
        # Preserve already observed terminal records even if a later gate fails.
        result.update(verification)
        if match_id is not None:
            await asyncio.sleep(1)
            try:
                async with asyncio.timeout(5), factory() as session:
                    speech_counts = (
                        await session.execute(
                            select(Speech.status, func.count())
                            .where(Speech.match_id == match_id)
                            .group_by(Speech.status)
                        )
                    ).all()
                    generation_counts = (
                        await session.execute(
                            select(Speech.status, AgentGeneration.status, func.count())
                            .outerjoin(AgentGeneration, AgentGeneration.id == Speech.generation_id)
                            .where(Speech.match_id == match_id, Speech.speaker_kind == "AGENT")
                            .group_by(Speech.status, AgentGeneration.status)
                        )
                    ).all()
                    last_judge = await session.scalar(
                        select(JudgeResult)
                        .where(JudgeResult.match_id == match_id)
                        .order_by(JudgeResult.created_at.desc())
                        .limit(1)
                    )
                result["stored_speech_status_counts"] = dict(speech_counts)
                result["agent_speech_generation_status_counts"] = [
                    {
                        "speech_status": status,
                        "generation_status": generation_status,
                        "count": count,
                    }
                    for status, generation_status, count in generation_counts
                ]
                result["ai_judge"] = last_judge.status if last_judge else "not_started"
                result["judge_attempt_no"] = last_judge.attempt_no if last_judge else None
            except Exception as error:
                result["terminal_record_diagnostic_error"] = error_receipt(error)
            try:
                result["provider_failure_shapes"] = await failure_shapes(factory, match_id)
            except Exception as error:
                result["shape_diagnostic_error"] = error_receipt(error)
                if isinstance(error, ModuleNotFoundError) and isinstance(error.name, str):
                    if re.fullmatch(r"[A-Za-z0-9_.]{1,160}", error.name):
                        result["shape_diagnostic_error"]["missing_module"] = error.name
            actor = manager._actors.get(match_id)
            if actor is not None:
                result["authoritative_error_code"] = actor.state.error_code
                host_run = agents._host_runs.get(match_id)
                result["host_resource_at_completion"] = {
                    "registered": host_run is not None,
                    "attempt_no": host_run.attempt_no if host_run else None,
                    "room_attached": bool(host_run and host_run.room is not None),
                    "source_attached": bool(host_run and host_run.source is not None),
                }
            async with factory() as session:
                error_events = list(
                    (
                        await session.scalars(
                            select(StoredEvent)
                            .where(
                                StoredEvent.match_id == match_id,
                                StoredEvent.event_type.in_(
                                    ["match.error", "host.failed", "match.paused"]
                                ),
                            )
                            .order_by(StoredEvent.sequence)
                            .limit(20)
                        )
                    ).all()
                )
            result["authoritative_error_events"] = [
                {
                    "type": event.event_type,
                    "error_code": event.payload.get("error_code"),
                    "reason": event.payload.get("reason"),
                }
                for event in error_events
            ]
        result["host_finish_callbacks"] = host_callback_receipts
        result["host_operations"] = host_operation_receipts
        result["interruption_receipts"] = interruption_receipts
        result["runtime_stable_warnings"] = warnings.rows
        result["native_room_disconnects"] = disconnects.rows
        result["publisher_sdk_joins"] = publisher_joins
        result["agent_lifecycle_operations"] = agent_cancellations
        # Failed runs still retain aggregate PCM evidence before owned readers close.
        result["audience_audio_frames"] = dict(audience.frames)
        result["audience_audible_samples"] = dict(audience.audible_samples)
        result["audience_pcm_rms"] = {
            kind: round(math.sqrt(audience.squared_samples[kind] / count), 1)
            for kind, count in audience.samples.items()
            if count
        }
        result["audience_audio_duration_ms"] = {
            kind: count // 16 for kind, count in audience.samples.items()
        }
        if qa_driver is not None:
            diagnostics = getattr(qa_driver, "diagnostics", None)
            if diagnostics is not None:
                result["qa_scenario_observations"] = diagnostics()
            await qa_driver.close()
        await postmatch.close()
        await manager.close()
        await audience.close()
        for room, source in publishers.values():
            await source.aclose()
            await room.disconnect()
        publishers.clear()
        await writer.close()
        if qa_driver is not None:
            after_close = getattr(qa_driver, "after_close", None)
            if after_close is not None:
                result["qa_scenario_observations"] = after_close()
                media = result["qa_scenario_observations"].get("media_observation")
                if media is not None and media.get("status") != "passed":
                    result["status"] = "failed"
                    result.setdefault("error_code", "qa_media_after_retirement")
        if match_id is not None:
            client = api.LiveKitAPI(
                settings.livekit_url,
                settings.livekit_api_key.get_secret_value(),
                settings.livekit_api_secret.get_secret_value(),
            )
            try:
                room_name = f"jx-match-{match_id}"
                await client.room.delete_room(api.DeleteRoomRequest(room=room_name))
                rooms = await client.room.list_rooms(api.ListRoomsRequest(names=[room_name]))
                result["owned_rtc_room_deleted"] = not rooms.rooms
            finally:
                await client.aclose()
        result["manager_actors_after_close"] = len(manager._actors)
        result["asr_receivers_after_close"] = len(asr._receivers)
        result["agent_runs_after_close"] = len(agents._runs)
        result["host_runs_after_close"] = len(agents._host_runs)
        pending_media = getattr(agents, "_pending_media_starts", None)
        result["pending_media_starts_after_close"] = (
            len(pending_media) if pending_media is not None else None
        )
        result["postmatch_tasks_after_close"] = len(postmatch._tasks)
        del audience, asr, agents, postmatch, manager, writer
        gc.collect()
        await asyncio.sleep(0.5)
        pending_tasks = tuple(asyncio.all_tasks() - baseline)
        result["new_pending_tasks_after_cleanup"] = len(pending_tasks)
        pending_diagnostics = []
        for task in pending_tasks[:32]:
            frames = []
            awaited: Any = task.get_coro()
            for _ in range(12):
                code = getattr(awaited, "cr_code", None)
                if code is None:
                    break
                frame = getattr(awaited, "cr_frame", None)
                frames.append(
                    {"function": code.co_qualname, "line": frame.f_lineno if frame else None}
                )
                awaited = getattr(awaited, "cr_await", None)
            pending_diagnostics.append(
                {"done": task.done(), "cancelled": task.cancelled(), "await_frames": frames}
            )
        result["new_pending_task_diagnostics"] = pending_diagnostics
        owners = (
            "manager_actors_after_close",
            "asr_receivers_after_close",
            "agent_runs_after_close",
            "host_runs_after_close",
            "postmatch_tasks_after_close",
            "new_pending_tasks_after_cleanup",
        )
        if not result.get("owned_rtc_room_deleted") or any(result[key] != 0 for key in owners):
            result["status"] = "failed"
            result["cleanup_error_code"] = "qa_runtime_cleanup_incomplete"
        agent_logger.removeHandler(warnings)
        agent_logger.setLevel(old_level)
        agent_logger.propagate = old_propagate
        disconnects.restore()
    return result


async def run(
    settings: Settings,
    owned: OwnedPostgres,
    url: str,
    pause_resume: bool = False,
    handoff_slice: bool = False,
    topical_microphones: bool = False,
    host_decode_only: bool = False,
    lifecycle_slice: bool = False,
    free_closing_slice: bool = False,
) -> dict[str, Any]:
    production = Database(settings.database_url_value)
    try:
        material, voices = await load_resources(production)
        # This clip is synthetic and held only in server runtime memory.
        if topical_microphones:
            pcm = {}
            original_probe_text = qa_provider_probe.PROBE_TEXT
            try:
                for side, text_value in (
                    ("AFFIRMATIVE", "奋斗过程塑造能力与人格，比结果更能体现成长的价值。"),
                    ("NEGATIVE", "奋斗结果检验实践效果，比过程更能体现实际创造的价值。"),
                ):
                    # Only this separate QA process's synthetic probe text changes.
                    qa_provider_probe.PROBE_TEXT = text_value
                    _, pcm[side] = await qa_provider_probe.probe_tts(material, production)
            finally:
                qa_provider_probe.PROBE_TEXT = original_probe_text
        else:
            _, pcm = await probe_tts(material, production)
    finally:
        await production.dispose()
    qa_settings = settings.model_copy(
        update={
            "database_url": SecretStr(url),
            "llm_global_concurrency": 4,
            "host_audio_storage_dir": str(owned.root / "host-audio"),
            "agent_audio_storage_dir": str(owned.root / "agent-audio"),
            "match_audio_storage_dir": str(owned.root / "match-audio"),
            "export_storage_dir": str(owned.root / "exports"),
        }
    )
    # Use the application's exact bounded 5-connection/10-second DB adapter.
    database = Database(url)
    factory = database.session_factory
    try:
        async with factory() as session:
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        values = await prepare_room(
            factory,
            qa_settings,
            material,
            voices,
            handoff_slice,
            lifecycle_slice,
            free_closing_slice,
        )
        if host_decode_only:
            async with factory() as session:
                assets = list(
                    (
                        await session.scalars(
                            select(HostAudioAsset)
                            .where(HostAudioAsset.status == "READY")
                            .order_by(HostAudioAsset.segment_key)
                        )
                    ).all()
                )
            decoded = []
            for asset in assets:
                decoder = IncrementalOpusDecoder()
                path = Path(qa_settings.host_audio_storage_dir) / asset.storage_path
                record: dict[str, Any] = {
                    "segment_key": asset.segment_key,
                    "jobs_duration_ms": asset.duration_ms,
                    "byte_count": asset.byte_count,
                }
                pcm_bytes = 0
                try:
                    await decoder.start(path)
                    decoder.notify_written(path.stat().st_size)
                    decoder.finish_input()
                    async with asyncio.timeout(10):
                        async for chunk in decoder.frames():
                            pcm_bytes += len(chunk)
                except Exception as error:
                    record.update(error_receipt(error))
                    frames = []
                    tb = error.__traceback__
                    while tb is not None:
                        frames.append(
                            {"function": tb.tb_frame.f_code.co_name, "line": tb.tb_lineno}
                        )
                        tb = tb.tb_next
                    record["exception_frames"] = frames[-8:]
                else:
                    record.update(status="passed", decoded_duration_ms=pcm_bytes // 96)
                finally:
                    await asyncio.wait_for(decoder.close(), timeout=3)
                decoded.append(record)
            receipt = {
                "status": "passed"
                if all(item["status"] == "passed" for item in decoded)
                else "failed",
                "host_decoder_assets": decoded,
            }
        else:
            receipt = await run_match(factory, qa_settings, values, pcm, pause_resume)
        receipt.update(
            probe="server_real_host_incremental_decode_only"
            if host_decode_only
            else "server_real_agent_closing_host_handoff_slice"
            if handoff_slice
            else "server_real_host_agent_lifecycle_slice"
            if lifecycle_slice
            else "server_real_free_debate_closing_subset"
            if free_closing_slice
            else "server_real_mixed_4v4_complete_match",
            migration=revision,
            production_matches_created=0,
            production_users_created=0,
            human_seats=4,
            agent_seats=4,
            host_assets_generated=values["host_assets"],
            physical_microphone="not_tested",
            browser_flow="not_tested",
            five_match_pressure="not_tested",
            voice_blind_test="not_tested",
            timing="real_authoritative_wall_clock",
            microphone_content="topical_side_arguments"
            if topical_microphones
            else "generic_health_clip",
            fixed_limit_seconds=12,
            free_side_seconds=24,
            free_turn_limit_seconds=8,
            configuration={
                "llm_model": material.model_id,
                "tts_model": settings.tts_model,
                "asr_model": settings.asr_model,
            },
        )
        return receipt
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pause-resume", action="store_true")
    parser.add_argument("--handoff-slice", action="store_true")
    parser.add_argument("--topical-microphones", action="store_true")
    parser.add_argument("--host-decode-only", action="store_true")
    parser.add_argument("--agent-lifecycle-slice", action="store_true")
    parser.add_argument("--free-closing-slice", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.CRITICAL)
    settings = load_settings()
    owned = OwnedPostgres()
    receipt: dict[str, Any] = {"status": "failed"}
    started = monotonic()
    try:
        url = owned.start()
        owned.migrate(url)
        receipt = asyncio.run(
            run(
                settings,
                owned,
                url,
                args.pause_resume,
                args.handoff_slice,
                args.topical_microphones,
                args.host_decode_only,
                args.agent_lifecycle_slice,
                args.free_closing_slice,
            )
        )
    except Exception as error:
        receipt = error_receipt(error)
    finally:
        try:
            receipt["owned_postgres_and_audio_removed"] = owned.close()
            if not receipt["owned_postgres_and_audio_removed"]:
                receipt["status"] = "failed"
                receipt["cleanup_error_code"] = "qa_owned_postgres_cleanup_incomplete"
        except Exception as error:
            receipt["cleanup_error"] = error_receipt(error)
            receipt["status"] = "failed"
        receipt["elapsed_ms"] = round((monotonic() - started) * 1000)
        receipt["qa_python_peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(json.dumps({"receipt": receipt}, ensure_ascii=False, sort_keys=True), flush=True)
    raise SystemExit(0 if receipt["status"] == "passed" else 1)


if __name__ == "__main__":
    main()

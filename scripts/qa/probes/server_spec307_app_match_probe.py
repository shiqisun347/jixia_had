"""Three serial conferences through one actual isolated Core App.

The only private fault access is one exact owned ASR socket transport abort.
All match changes go through normal HTTP/WS. Importing this file performs no IO.
Stdout is a private control pipe; the caller must never log witness_start frames.
"""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import argparse
import asyncio
import contextlib
import gc
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import random
import re
import sys
from pathlib import Path
from time import monotonic
from typing import Any, cast
from uuid import UUID, uuid4

PROTOCOL = "spec307-qa-control-v1"
SCENARIOS = ("normal", "weaknet", "recovery")
CONTROL_STDOUT = sys.stdout
_control_reader: asyncio.StreamReader | None = None
_control_transport: asyncio.ReadTransport | None = None
_unraisable_classes: list[str] = []
_unraisable_count = 0


class ProbeError(RuntimeError):
    pass


def require(condition: object, code: str) -> None:
    if not condition:
        raise ProbeError(code)


def emit(event: str, **fields: Any) -> None:
    CONTROL_STDOUT.write(json.dumps({"protocol": PROTOCOL, "event": event, **fields}) + "\n")
    CONTROL_STDOUT.flush()


def safe_failure(error: BaseException) -> dict[str, str]:
    code = "qa_external_failure"
    if isinstance(error, ProbeError):
        candidate = str(error)
    elif type(error).__name__ == "ClientFailure":
        candidate = getattr(error, "code", "")
    else:
        candidate = ""
    if isinstance(candidate, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", candidate):
        code = candidate
    return {"error_code": code, "exception_class": type(error).__name__}


def diagnostic_value(value: Any) -> Any:
    if value is None or type(value) in {bool, int, float}:
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        return value
    return None


def diagnostic_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: diagnostic_value(value[key])
        for key in (
            "status",
            "sequence",
            "action_state",
            "current_action_index",
            "stage_generation",
            "context_version",
            "current_speech_id",
            "asr_failed_speech_id",
            "current_speaker_user_id",
            "current_speaker_side",
            "current_speaker_seat_no",
            "speech_remaining_ms",
            "error_code",
        )
        if key in value
    }


def diagnostic_task(task: asyncio.Task[Any] | None) -> dict[str, Any]:
    if task is None:
        return {"present": False}
    result: dict[str, Any] = {"present": True, "done": task.done(), "cancelled": task.cancelled()}
    if task.done() and not task.cancelled():
        error = task.exception()
        result["exception_class"] = type(error).__name__ if error is not None else None
    return result


def asr_failure_projection(core: Any, match_id: UUID, turn: Any) -> dict[str, Any]:
    runtime = core.app.state.match_runtime_manager._speech_runtime
    receiver = runtime._receivers.get(match_id) if runtime is not None else None
    result: dict[str, Any] = {"receiver_present": receiver is not None}
    if turn is not None:
        started = getattr(turn, "started", 0)
        result["human_turn"] = {
            "speech_id": diagnostic_value(getattr(turn, "speech_id", None)),
            "sent_samples": diagnostic_value(getattr(turn, "sent_samples", None)),
            "elapsed_ms": round((monotonic() - started) * 1000) if started else None,
            "initial_budget_ms": diagnostic_value(getattr(turn, "initial_budget_ms", None)),
            "task": diagnostic_task(getattr(turn, "task", None)),
        }
    if receiver is None:
        return result
    envelope = getattr(receiver, "_speech_envelope", None)
    result["envelope"] = {
        key: diagnostic_value(getattr(envelope, key, None))
        for key in (
            "match_id",
            "speech_id",
            "attempt_no",
            "generation_id",
            "connection_epoch",
            "context_version",
        )
    }
    result.update(
        closed=receiver._closed,
        close_requested=receiver._close_requested,
        track_tasks=len(receiver._track_tasks),
        track_owners=len(receiver._subscribed_tracks),
        speech_task=diagnostic_task(receiver._speech_task),
        recording_speech_id=diagnostic_value(receiver._recording_speech_id),
        recording_error=diagnostic_value(receiver._recording_error),
    )
    recording = receiver._recording_file
    try:
        result["recording_bytes"] = recording.tell() if recording is not None else None
    except (OSError, ValueError):
        result["recording_bytes"] = None
    connection = receiver._connection
    result["connection"] = {
        "model": diagnostic_value(getattr(connection, "_model", None)),
        "retirement_epoch": diagnostic_value(getattr(connection, "_retirement_epoch", None)),
    }
    sessions: list[dict[str, Any]] = []
    retained = getattr(receiver, "_retained_speech", None)
    for owner, session in (
        ("current", receiver._speech),
        ("retained", getattr(retained, "session", None)),
        ("retrying", receiver._retrying_session),
        ("fenced", receiver._callback_fenced_session),
    ):
        if session is None:
            continue
        deadline = session._finalization_deadline
        sessions.append(
            {
                "owner": owner,
                "speech_id": str(session.speech_id),
                "history_bytes": len(session._history),
                "input_samples": session._sample_count,
                "initial_samples": session._initial_sample_count,
                "confirmed_local_samples": session._confirmed_local_samples,
                "max_audio_duration_ms": session._max_audio_duration_ms,
                "max_bytes": session._max_bytes,
                "automatic_retries_used": session._automatic_retries_used,
                "segment_no": session._current_segment_no,
                "closed": session._closed,
                "final_deadline_remaining_ms": round((deadline - monotonic()) * 1000)
                if deadline is not None
                else None,
                "provider_tasks": len(session._provider_tasks),
                "pending_provider_tasks": sum(not task.done() for task in session._provider_tasks),
            }
        )
    result["sessions"] = sessions
    return result


async def capture_failure_diagnostics(
    core: Any,
    clients: Any,
    turn: Any,
    error: BaseException,
    out: Path,
    scenario: str,
    extra_safe_numeric: dict[str, Any] | None = None,
    *,
    last_snapshot: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Snapshot owned state before any diagnostic-driven cancel/terminate/close."""
    original = safe_failure(error)
    result: dict[str, Any] = {
        "scenario": scenario,
        "match_id": str(clients.match_id),
        "original_error": original,
        "last_snapshot": diagnostic_snapshot(last_snapshot or {}),
        "captured_before_cleanup": True,
        "counters": {
            key: value
            for key, value in (extra_safe_numeric or {}).items()
            if re.fullmatch(r"[a-z_]{1,64}", key)
            and type(value) in {bool, int, float}
            and math.isfinite(value)
        },
    }
    try:
        result["runtime"] = asr_failure_projection(core, clients.match_id, turn)
    except BaseException as runtime_error:
        result["runtime_error_class"] = type(runtime_error).__name__

    async def capture_http() -> None:
        try:
            result["snapshot"] = diagnostic_snapshot(await clients.snapshot())
        except BaseException as http_error:
            result["http_error_class"] = type(http_error).__name__

    async def capture_database() -> None:
        try:
            from sqlalchemy import text

            async with core.factory() as session:
                await session.execute(text("SET TRANSACTION READ ONLY"))
                await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
                require(
                    await session.scalar(text("SHOW transaction_read_only")) == "on",
                    "qa_diagnostic_not_readonly",
                )
                queries = {
                    "match": (
                        "SELECT status,sequence,context_version,current_speech_id,"
                        "runtime_snapshot->>'action_state' AS action_state,"
                        "runtime_snapshot->>'stage_generation' AS stage_generation,"
                        "runtime_snapshot->>'error_code' AS error_code FROM matches WHERE id=:id"
                    ),
                    "events": (
                        "SELECT sequence,event_type,payload->>'speech_id' AS speech_id,"
                        "payload->>'reason' AS reason,payload->>'code' AS code,"
                        "payload->>'error_code' AS error_code FROM match_events "
                        "WHERE match_id=:id ORDER BY sequence DESC LIMIT 64"
                    ),
                    "asr_calls": (
                        "SELECT id,speech_id,asr_segment_id,model,status,error_code,attempt_no,"
                        "generation_id,connection_epoch,context_version,audio_bytes,"
                        "audio_duration_ms,first_result_latency_ms,completed_latency_ms "
                        "FROM external_calls WHERE match_id=:id AND call_kind='ASR' "
                        "ORDER BY started_at DESC,id DESC LIMIT 64"
                    ),
                    "speeches": (
                        "SELECT id,status,speaker_kind,side,seat_no,attempt_no,finish_reason,"
                        "asr_error_code,audio_duration_ms,display_text FROM speeches "
                        "WHERE match_id=:id ORDER BY created_at DESC,id DESC LIMIT 64"
                    ),
                }
                for label, query in queries.items():
                    rows = (
                        (await session.execute(text(query), {"id": clients.match_id}))
                        .mappings()
                        .all()
                    )
                    projected: list[dict[str, Any]] = []
                    for row in rows:
                        clean = {
                            key: diagnostic_value(value)
                            for key, value in row.items()
                            if key != "display_text"
                        }
                        if "display_text" in row:
                            value = row["display_text"] or ""
                            clean.update(
                                text_sha256=hashlib.sha256(value.encode()).hexdigest(),
                                text_chars=len(value),
                            )
                        projected.append(clean)
                    result[label] = projected
        except BaseException as db_error:
            result["db_error_class"] = type(db_error).__name__

    try:
        async with asyncio.timeout(3), asyncio.TaskGroup() as group:
            group.create_task(capture_http(), name="qa-failure-http-snapshot")
            group.create_task(capture_database(), name="qa-failure-readonly-database")
    except BaseException as diagnostic_error:
        result["collection_error_class"] = type(diagnostic_error).__name__
    try:
        require(
            bool(re.fullmatch(r"[a-z0-9_-]{1,48}", scenario))
            and out.is_dir()
            and not out.is_symlink(),
            "qa_failure_output_invalid",
        )
        data = (json.dumps(result, indent=2) + "\n").encode()
        descriptor = os.open(
            out / f"{scenario}-failure.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        return {**original, "diagnostic_sha256": hashlib.sha256(data).hexdigest()}
    except BaseException as write_error:
        return {**original, "diagnostic_write_error_class": type(write_error).__name__}


def prefix_cer(reference: str, recognized: str) -> dict[str, Any]:
    expected = "".join(char for char in reference if char.isalnum())
    actual = "".join(char for char in recognized if char.isalnum())[: len(expected)]
    previous = list(range(len(actual) + 1))
    for index, char in enumerate(expected, start=1):
        current = [index]
        for target_index, target in enumerate(actual, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[target_index] + 1,
                    previous[target_index - 1] + int(char != target),
                )
            )
        previous = current
    return {
        "completed_reference_chars": len(expected),
        "recognized_prefix_chars": len(actual),
        "edit_distance": previous[-1],
        "prefix_cer_diagnostic": previous[-1] / len(expected) if expected else None,
        "scope": "completed_blocks_only_normalized_prefix_diagnostic",
    }


def anchor_distance(anchor: str, recognized: str) -> int:
    expected = "".join(char for char in anchor.casefold() if char.isalnum())
    actual = "".join(char for char in recognized.casefold() if char.isalnum())
    previous = [0] * (len(actual) + 1)
    for index, char in enumerate(expected, start=1):
        current = [index]
        for offset, target in enumerate(actual, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[offset] + 1,
                    previous[offset - 1] + int(char != target),
                )
            )
        previous = current
    return min(previous)


def short_free_reference_eligible(
    material: Any, sent_samples: int, recognized: str, context: dict[str, Any] | None
) -> bool:
    if not context or not material.blocks or not any(char.isalnum() for char in recognized):
        return False
    if (
        context.get("action_kind") != "FREE_DEBATE"
        or context.get("finish_reason") != "TIME_LIMIT"
        or not isinstance(context.get("action_key"), str)
        or re.fullmatch(r"\d+:\d+", context["action_key"]) is None
        or context.get("manually_finished") is not False
        or context.get("media_interrupted") is not False
    ):
        return False
    if not all(
        context.get(key) is True
        for key in (
            "owner_verified",
            "asr_succeeded",
            "recording_verified",
            "event_chain_verified",
            "natural_finish_verified",
        )
    ):
        return False
    if not all(
        type(context.get(key)) is int
        for key in (
            "initial_budget_ms",
            "free_side_remaining_ms",
            "wall_ms",
            "recorded_duration_ms",
        )
    ):
        return False
    budget, bank = context["initial_budget_ms"], context["free_side_remaining_ms"]
    first_end = int(material.blocks[0]["end_sample"])
    sent_ms = sent_samples / 48
    return bool(
        0 < sent_samples < first_end
        and 0 < budget < first_end / 48
        and 0 < bank < first_end / 48
        and abs(bank - budget) <= 1000
        and budget - 1000 <= sent_ms <= budget + 1000
        and context["wall_ms"] >= budget - 1000
        and abs(context["recorded_duration_ms"] - budget) <= 1000
        and abs(context["recorded_duration_ms"] - sent_ms) <= 1000
    )


def quality_coverage(
    material: Any,
    sent_samples: int,
    recognized: str,
    arguments: Any,
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = prefix_cer(material.completed_text(sent_samples), recognized)
    blocks: list[dict[str, Any]] = []
    for block in material.blocks:
        if block["end_sample"] > sent_samples:
            continue
        principle, example = arguments[block["index"]]
        distances = [anchor_distance(anchor, recognized) for anchor in (principle, example)]
        limits = [
            max(2, len("".join(c for c in anchor if c.isalnum())) // 5)
            for anchor in (principle, example)
        ]
        blocks.append(
            {
                "index": block["index"],
                "principle_distance": distances[0],
                "example_distance": distances[1],
                "limits": limits,
                "passed": all(
                    error <= limit for error, limit in zip(distances, limits, strict=True)
                ),
            }
        )
    result.update(
        blocks=blocks,
        missing_blocks=[row["index"] for row in blocks if not row["passed"]],
        passed=bool(blocks)
        and all(row["passed"] for row in blocks)
        and result["prefix_cer_diagnostic"] is not None
        and result["prefix_cer_diagnostic"] <= 0.20,
        qualification_scope="synthetic_public_material_not_human_cer_acceptance",
    )
    result["status"] = "passed" if result["passed"] else "failed"
    result["qualification_accepted"] = result["passed"]
    if not blocks and short_free_reference_eligible(material, sent_samples, recognized, context):
        result.update(
            status="not_evaluable",
            reason="partial_reference_no_complete_sent_block",
            passed=None,
            edit_distance=None,
            qualification_accepted=True,
            quality_success_claimed=False,
        )
    return result


async def acknowledge(match_id: str, revision: int) -> None:
    global _control_reader, _control_transport
    if _control_reader is None:
        _control_reader = asyncio.StreamReader(limit=16_384)
        _control_transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(cast(asyncio.StreamReader, _control_reader)),
            sys.stdin,
        )
    raw = await asyncio.wait_for(_control_reader.readline(), 45)
    require(0 < len(raw) <= 16_384, "qa_witness_ack_missing")
    value = json.loads(raw)
    require(
        value
        == {
            "protocol": PROTOCOL,
            "event": "witness_ack",
            "match_id": match_id,
            "revision": revision,
        },
        "qa_witness_ack_mismatch",
    )


def audit_sources(root: Path) -> tuple[str, dict[str, str]]:
    manifest_path = root / "candidate-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    require(isinstance(manifest["members"], dict), "qa_manifest_incomplete")
    members = cast(dict[str, str], manifest["members"])
    require(len(members) >= 100, "qa_manifest_incomplete")
    actual: dict[str, str] = {}
    for relative, expected in members.items():
        require(
            not Path(relative).is_absolute()
            and ".." not in Path(relative).parts
            and ".env" not in relative
            and Path(relative).name != "api.md"
            and (
                relative.startswith(("apps/", "scripts/", "packages/", "migrations/"))
                or relative in {"alembic.ini", "pyproject.toml", "uv.lock"}
            ),
            "qa_manifest_path_invalid",
        )
        path = root / relative
        require(path.is_file() and not path.is_symlink(), "qa_source_not_regular")
        require(path.resolve().is_relative_to(root), "qa_source_outside_candidate")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        require(digest == expected, "qa_candidate_source_changed")
        actual[relative] = digest
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest(), actual


def browser_case(clients: Any) -> dict[str, Any]:
    require(clients.match_id is not None, "qa_match_not_prepared")
    users: list[dict[str, Any]] = []
    for role, person in (("admin", clients.admin), ("audience", clients.audience)):
        require(person is not None, "qa_witness_identity_missing")
        cookies = [
            {
                "name": item.name,
                "value": item.value,
                "domain": "127.0.0.1",
                "path": item.path or "/",
                "httpOnly": "HttpOnly" in item._rest,
                "secure": item.secure,
                "sameSite": "Lax",
                **({"expires": float(item.expires)} if item.expires is not None else {}),
            }
            for item in person.http.cookies.jar
        ]
        require(cookies, "qa_witness_cookie_missing")
        users.append({"role": role, "user_id": str(person.user_id), "cookies": cookies})
    return {
        "protocol": "spec307-dual-witness-v1",
        "web_origin": "http://127.0.0.1:3100",
        "core_origin": clients.base_url,
        "match_id": str(clients.match_id),
        "users": users,
        "revision": 0,
        "max_run_ms": 3_600_000,
        "expected": {"speeches": [], "finished": False},
    }


async def expected_projection(clients: Any, *, finished: bool = False) -> dict[str, Any]:
    body = await clients.request(
        clients.admin, "GET", f"/api/matches/{clients.match_id}/transcript"
    )
    return {
        "speeches": [
            {
                "speech_id": speech["id"],
                "text_sha256": hashlib.sha256((speech["display_text"] or "").encode()).hexdigest(),
            }
            for speech in body["speeches"]
        ],
        "finished": finished,
    }


class Witness:
    def __init__(self, clients: Any) -> None:
        self.clients = clients
        self.revision = 0
        self.expected: dict[str, Any] = {"speeches": [], "finished": False}

    async def start(self) -> None:
        emit("witness_start", case=browser_case(self.clients))
        await acknowledge(str(self.clients.match_id), 0)

    async def sync(self, *, force: bool = False, finished: bool = False) -> None:
        expected = await expected_projection(self.clients, finished=finished)
        if force or expected != self.expected:
            self.revision += 1
            emit(
                "witness_revision",
                match_id=str(self.clients.match_id),
                revision=self.revision,
                expected=expected,
            )
            await acknowledge(str(self.clients.match_id), self.revision)
            self.expected = expected


async def reconnect_business_ws(clients: Any, person: Any) -> None:
    """Replace only Core WS; leave the original RTC publisher running."""
    from websockets.asyncio.client import connect

    if person.socket is not None:
        await person.socket.close()
    if person.receiver is not None:
        await asyncio.gather(person.receiver, return_exceptions=True)
    person.socket, person.receiver = None, None
    await asyncio.sleep(2)
    cookie = "; ".join(f"{item.name}={item.value}" for item in person.http.cookies.jar)
    person.socket = await connect(
        f"ws://127.0.0.1:8027/api/matches/{clients.match_id}/events",
        origin=clients.origin,
        additional_headers={"Cookie": cookie},
        proxy=None,
        max_size=2**20,
    )
    first = json.loads(await asyncio.wait_for(person.socket.recv(), 10))
    require(first.get("type") == "match.snapshot", "qa_reconnect_snapshot_missing")
    require(type(first.get("connection_epoch")) is int, "qa_reconnect_epoch_missing")
    person.epoch = first["connection_epoch"]
    person.receiver = asyncio.create_task(
        clients._receive(person), name=f"qa-corews-{person.label}-reconnected"
    )


class HumanTurn:
    def __init__(self, clients: Any, person: Any, material: Any) -> None:
        self.clients, self.person, self.material = clients, person, material
        self.media_available = asyncio.Event()
        self.media_available.set()
        self.started = 0.0
        self.speech_id: str | None = None
        self.sent_samples = 0
        self.initial_budget_ms: int | None = None
        self.task: asyncio.Task[dict[str, Any]] | None = None
        self.allow_continuation = False
        self.media_interrupted = False

    async def run(self) -> dict[str, Any]:
        from livekit import rtc

        snapshot = await self.clients.command(self.person, "speech.start")
        self.speech_id = snapshot.get("current_speech_id")
        require(self.speech_id, "qa_human_start_missing_id")
        self.started = monotonic()
        self.initial_budget_ms = snapshot.get("speech_remaining_ms")
        action = cast(dict[str, Any], snapshot.get("current_action") or {})
        require(action.get("action_kind"), "qa_human_action_metadata_missing")
        action_key = f"{action['stage_position']}:{action['action_position']}"
        side = self.person.side
        initial_owner_verified = (
            str(snapshot.get("current_speaker_user_id")) == str(self.person.user_id)
            and snapshot.get("current_speaker_side") == side
            and snapshot.get("current_speaker_seat_no") == self.person.seat
        )
        require(initial_owner_verified, "qa_human_start_owner_mismatch")
        side_bank = snapshot.get(
            "free_affirmative_remaining_ms"
            if side == "AFFIRMATIVE"
            else "free_negative_remaining_ms"
        )
        next_frame, next_snapshot = self.started, self.started
        pcm = self.material.pcm
        for offset in range(0, len(pcm), 1920):
            await self.media_available.wait()
            if monotonic() >= next_snapshot:
                snapshot = await self.clients.snapshot(self.person)
                next_snapshot = monotonic() + 0.2
            if (
                self.allow_continuation
                and snapshot.get("current_speech_id") == self.speech_id
                and snapshot.get("action_state") == "HUMAN_READY_TO_START"
            ):
                snapshot = await self.clients.command(self.person, "speech.start")
                require(snapshot["current_speech_id"] == self.speech_id, "qa_continuation_new_id")
                self.allow_continuation = False
            if (
                snapshot.get("current_speech_id") != self.speech_id
                or snapshot.get("action_state") != "HUMAN_SPEAKING"
            ):
                break
            require(snapshot.get("status") == "RUNNING", "qa_human_unexpected_pause")
            require(monotonic() - self.started < 270, "qa_human_deadline_stuck")
            await asyncio.sleep(max(0, next_frame - monotonic()))
            frame = pcm[offset : offset + 1920]
            require(self.person.publisher is not None, "qa_human_publisher_missing")
            await self.person.publisher.source.capture_frame(
                rtc.AudioFrame(
                    data=frame,
                    sample_rate=48000,
                    num_channels=1,
                    samples_per_channel=len(frame) // 2,
                )
            )
            self.sent_samples += len(frame) // 2
            # Never burst accumulated audio after a deliberate RTC outage.
            next_frame = max(next_frame + len(frame) / 96000, monotonic())
        require(self.sent_samples * 2 < len(pcm), "qa_long_material_exhausted")
        if self.person.publisher is not None:
            await self.person.publisher.source.wait_for_playout()
        reference = self.material.completed_text(self.sent_samples)
        return {
            "speech_id": self.speech_id,
            "sent_samples": self.sent_samples,
            "source_pcm_sha256": hashlib.sha256(pcm).hexdigest(),
            "completed_reference_sha256": hashlib.sha256(reference.encode()).hexdigest(),
            "completed_reference_chars": len(reference),
            "wall_ms": round((monotonic() - self.started) * 1000),
            "initial_budget_ms": self.initial_budget_ms,
            "action_kind": action["action_kind"],
            "action_key": action_key,
            "speaker_side": side,
            "speaker_seat_no": self.person.seat,
            "start_owner_verified": initial_owner_verified,
            "free_side_remaining_ms": side_bank,
            "media_interrupted": self.media_interrupted,
            "manually_finished": False,
        }


async def rtc_handoff(clients: Any, person: Any, turn: HumanTurn) -> dict[str, Any]:
    from scripts.qa.probes.server_spec307_clients import HumanPublisher

    before = await clients.snapshot(person)
    turn.media_available.clear()
    turn.media_interrupted = True
    started = monotonic()
    old = person.publisher
    require(old is not None, "qa_old_rtc_owner_missing")
    try:
        await old.close()
        token = await clients.request(
            person,
            "POST",
            f"/api/matches/{clients.match_id}/livekit-token",
            {"connection_epoch": person.epoch},
        )
        new = HumanPublisher()
        person.publisher = new
        await new.open(token, clients.match_id)
        elapsed = (monotonic() - started) * 1000
        require(elapsed < 3000, "qa_rtc_handoff_exceeded_grace")
        after = await clients.snapshot(person)
        require(after["current_speech_id"] == before["current_speech_id"], "qa_rtc_changed_speech")
        require(
            int(after.get("speech_remaining_ms") or 0)
            <= int(before.get("speech_remaining_ms") or 0),
            "qa_rtc_refunded_budget",
        )
        return {"kind": "rtc_republish", "elapsed_ms": round(elapsed), "same_speech": True}
    finally:
        turn.allow_continuation = True
        turn.media_available.set()


async def postmatch_display(clients: Any) -> dict[str, Any]:
    path = f"/api/matches/{clients.match_id}/showcase"
    current = await clients.request(clients.admin, "GET", path)
    participants = current["participants"]
    require(current["can_manage"] and len(participants) == 8, "qa_showcase_roster_invalid")
    await clients.request(
        clients.admin,
        "POST",
        path + "/judge",
        {
            "comments": "公开合成现场评委点评",
            "winner_side": "AFFIRMATIVE",
            "best_participant_id": participants[0]["id"],
            "published": True,
        },
    )
    await clients.request(clients.admin, "POST", path + "/phase", {"phase": "AUDIENCE_VOTE"})
    payload = {"winner_side": "AFFIRMATIVE", "ranking": [p["id"] for p in participants]}
    await clients.request(clients.audience, "POST", path + "/vote", payload)
    response = await clients.audience.http.post(path + "/vote", json=payload)
    require(response.status_code == 409, "qa_duplicate_vote_not_rejected")
    awards = await clients.request(clients.admin, "POST", path + "/phase", {"phase": "AWARDS"})
    require(awards["phase"] == "AWARDS", "qa_awards_not_published")
    require(len(awards["audience"]["ranking"]) == 8, "qa_awards_ranking_missing")
    return {"phase": "AWARDS", "ranked_participants": 8, "duplicate_vote_rejected": True}


async def case_resource_counts(core: Any, clients: Any) -> dict[str, int]:
    from sqlalchemy import text

    manager = core.app.state.match_runtime_manager
    asr, agent = manager._speech_runtime, manager._agent_runtime
    match_id = clients.match_id
    counts = {
        "asr_receivers": int(asr is not None and match_id in asr._receivers),
        "agent_runs": int(agent is not None and match_id in agent._runs),
        "host_runs": int(agent is not None and match_id in agent._host_runs),
        "pending_media_starts": int(agent is not None and match_id in agent._pending_media_starts),
        "asr_retry_tasks": int(match_id in manager._asr_retry_tasks),
    }
    async with core.factory() as session:
        counts["leases"] = int(
            await session.scalar(
                text("SELECT count(*) FROM room_connection_leases WHERE room_id=:id"),
                {"id": clients.room_id},
            )
            or 0
        )
    return counts


async def retire_failed_owned_case(clients: Any) -> bool:
    from sqlalchemy.engine import make_url

    target = make_url(clients.settings.database_url_value)
    require(
        clients.settings.app_env != "production"
        and target.host == "127.0.0.1"
        and target.database == "jx_debate_qa_agent_faults"
        and clients.base_url == "http://127.0.0.1:8027",
        "qa_failure_cleanup_not_owned",
    )
    if clients.match_id is None:
        return False
    snapshot = await clients.snapshot()
    if snapshot["status"] in {"FINISHED", "TERMINATED"}:
        return False
    await clients.control("terminate")
    return True


async def close_owned(core: Any, livekit: Any, postgres: Any) -> tuple[dict[str, bool], list[str]]:
    cleanup = {"core_closed": False, "postgres_closed": False, "livekit_closed": False}
    errors: list[str] = []
    try:
        if core is not None:
            await core.close()
        cleanup["core_closed"] = True
    except BaseException as error:
        errors.append(type(error).__name__)
    try:
        await asyncio.to_thread(livekit.close)
        cleanup["livekit_closed"] = True
    except BaseException as error:
        errors.append(type(error).__name__)
    try:
        cleanup["postgres_closed"] = await asyncio.to_thread(postgres.close)
    except BaseException as error:
        errors.append(type(error).__name__)
    return cleanup, errors


def pending_cleanup_snapshot(baseline: set[asyncio.Task[Any]], core: Any) -> dict[str, Any]:
    pending = [task for task in asyncio.all_tasks() - baseline if not task.done()]
    close_names = {
        "Connection.close",
        "FunAsrConnection.close",
        "_close_retired_asr_provider",
        "_bounded_cleanup_call",
        "_bounded_cleanup",
        "_wait_for_retired_asr_tasks",
    }
    names = [str(getattr(task.get_coro(), "__qualname__", "unknown"))[:128] for task in pending]
    closing = bool(set(names) & close_names)
    rows: list[dict[str, Any]] = []
    for task, name in zip(pending, names, strict=True):
        row: dict[str, Any] = {"coroutine": name, "cancelling": task.cancelling()}
        frame = getattr(task.get_coro(), "cr_frame", None)
        if frame is not None:
            # Read only these known owners' booleans, never repr/serialize locals.
            for value in (frame.f_locals.get("self"), frame.f_locals.get("session")):
                if type(value).__name__ == "AsrSpeechSession":
                    row["input_closed"] = value._closed
                elif type(value).__name__ == "MatchAudioReceiver":
                    row["receiver_closed"] = value._closed
        row["retired"] = (
            bool(
                task.cancelling()
                or name in close_names
                or (closing and name == "Connection.keepalive")
            )
            and row.get("input_closed") is not False
            and row.get("receiver_closed") is not False
        )
        rows.append(row)
    owners: dict[str, int] = {}
    if core is not None:
        manager = core.app.state.match_runtime_manager
        asr, agent = manager._speech_runtime, manager._agent_runtime
        owners = {
            "actors": len(manager._actors),
            "asr_retry_tasks": len(manager._asr_retry_tasks),
            "asr_receivers": len(asr._receivers) if asr is not None else 0,
            "agent_runs": len(agent._runs) if agent is not None else 0,
            "host_runs": len(agent._host_runs) if agent is not None else 0,
            "pending_media_starts": len(agent._pending_media_starts) if agent is not None else 0,
        }
    return {"pending": rows, "owners": owners}


async def observe_retired_cleanup(
    baseline: set[asyncio.Task[Any]],
    core: Any,
    *,
    snapshot: Any = pending_cleanup_snapshot,
    clock: Any = monotonic,
    sleep: Any = asyncio.sleep,
) -> dict[str, Any]:
    """Observe existing close owners; never cancel tasks or alter a timeout."""
    started = clock()
    observations: list[dict[str, Any]] = []
    unsafe = False
    for elapsed in (0, 0.1, 1, 2.1, 5):
        await sleep(max(0.0, started + elapsed - clock()))
        value = snapshot(baseline, core)
        rows, owners = value["pending"], value["owners"]
        unsafe |= any(owners.values()) or any(not row["retired"] for row in rows)
        observations.append({"elapsed_ms": round((clock() - started) * 1000), **value})
        if not rows:
            break
    first, final = observations[0], observations[-1]
    return {
        "status": "passed"
        if not unsafe and not final["pending"] and not any(final["owners"].values())
        else "failed",
        "initial_pending_tasks": len(first["pending"]),
        "pending_tasks": len(final["pending"]),
        "initial_pending_coroutines": sorted(row["coroutine"] for row in first["pending"]),
        "pending_coroutines": sorted(row["coroutine"] for row in final["pending"]),
        "observations": observations,
        "maximum_wait_ms": 5000,
        "tasks_cancelled_by_observer": 0,
    }


def abort_owned_asr_socket(app: Any, match_id: UUID, speech_id: str) -> dict[str, Any]:
    manager = app.state.match_runtime_manager
    runtime = manager._speech_runtime
    require(runtime is not None, "qa_asr_runtime_missing")
    receiver = runtime._receivers.get(match_id)
    require(receiver is not None and receiver.match_id == match_id, "qa_asr_owner_missing")
    assert receiver is not None
    require(
        receiver._speech is not None and str(receiver._speech.speech_id) == speech_id,
        "qa_asr_speech_owner_changed",
    )
    connection = receiver._connection
    socket = getattr(connection, "_socket", None)
    transport = getattr(socket, "transport", None)
    require(transport is not None and not transport.is_closing(), "qa_asr_socket_not_active")
    assert transport is not None
    transport.abort()
    return {"kind": "asr_transport_abort", "same_speech": True, "protocol_close_called": False}


async def archive_match(
    factory: Any, settings: Any, match_id: UUID, owned_root: Path
) -> dict[str, Any]:
    from server_postmatch_archive_gate import decoded_samples, task_state, verify_lengths
    from sqlalchemy import text

    from jx_jobs.audio_worker import (  # pyright: ignore[reportMissingTypeStubs]
        AudioSource,
        _load_sources,
        process_one_postmatch_audio,
    )

    root = owned_root.resolve()
    require(root.name.startswith("jx-full-match-qa-"), "qa_archive_root_not_owned")
    host, speech, output = (
        Path(settings.host_audio_storage_dir),
        Path(settings.agent_audio_storage_dir),
        Path(settings.match_audio_storage_dir),
    )
    require(
        all(path.resolve().is_relative_to(root) for path in (host, speech, output)), "qa_storage"
    )
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT status, attempts FROM background_tasks "
                    "WHERE task_type='POSTMATCH_AUDIO' "
                    "AND payload->>'match_id'=:match_id"
                ),
                {"match_id": str(match_id)},
            )
        ).all()
        require(len(rows) == 1 and rows[0] == ("PENDING", 0), "qa_archive_not_unique_pending")
        frozen = await session.scalar(
            text("SELECT runtime_snapshot FROM matches WHERE id=:id AND status='FINISHED'"),
            {"id": match_id},
        )
        require(frozen, "qa_archive_match_not_finished")
        stored_speeches = int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM speeches WHERE match_id=:id AND status='FINALIZED' "
                    "AND audio_storage_path IS NOT NULL"
                ),
                {"id": match_id},
            )
            or 0
        )
        host_plays = (
            await session.execute(
                text(
                    "SELECT payload->>'action_key',count(*) FROM match_events "
                    "WHERE match_id=:id AND event_type='host.play' "
                    "GROUP BY payload->>'action_key'"
                ),
                {"id": match_id},
            )
        ).all()
        expected_hosts = {
            f"{item['stage_position']}:{item['action_position']}"
            for item in frozen["actions"]
            if item.get("host_audio_path")
        }
        require(
            {row[0] for row in host_plays} == expected_hosts
            and all(row[1] == 1 for row in host_plays),
            "qa_host_play_incomplete_or_repeated",
        )
    sources = await _load_sources(
        factory, match_id=match_id, host_storage_root=host, speech_storage_root=speech
    )
    require(sources, "qa_archive_sources_empty")
    require(
        len(sources)
        == stored_speeches
        + sum(bool(action.get("host_audio_path")) for action in frozen["actions"]),
        "qa_archive_source_count_mismatch",
    )
    worked = False
    state: dict[str, Any] = {}
    for _ in range(2):
        worked = await process_one_postmatch_audio(
            factory, storage_root=output, host_storage_root=host, speech_storage_root=speech
        )
        state = await task_state(factory, match_id)
        if not worked or state["tasks"][0]["status"] != "PENDING":
            break
    require(worked and state["tasks"][0]["status"] == "SUCCEEDED", "qa_archive_failed")
    path = output / "matches" / str(match_id) / "replay" / "match.opus"
    require(
        state["file_status"] == "READY" and state["tasks"][0]["lease_released"],
        "qa_archive_not_published",
    )
    source_count = sum(await asyncio.to_thread(lambda: [decoded_samples(item) for item in sources]))
    replay_count = await asyncio.to_thread(decoded_samples, AudioSource(path, "ogg_opus"))
    difference = verify_lengths(source_count, replay_count, len(sources))
    return {
        "source_count": len(sources),
        "source_samples": source_count,
        "replay_samples": replay_count,
        "difference_samples": difference,
        "status": "passed",
        "host_play_count": len(host_plays),
        "final_host_played": bool(expected_hosts),
    }


async def run_scenario(
    scenario: str,
    core: Any,
    settings: Any,
    rule_id: UUID,
    production_url: str,
    materials: dict[tuple[str, int], Any],
    existing_admin: Any,
    seed: int,
    diagnostic_out: Path,
) -> tuple[dict[str, Any], Any]:
    from scripts.qa.probes.server_spec307_clients import ClientFailure, ConferenceClients
    from scripts.qa.probes.server_spec307_speech_material import (
        ARGUMENTS,
        calibrate_conference_voices,
    )
    from sqlalchemy import text

    voice_receipts: list[dict[str, Any]] = []

    async def calibrate(clients: ConferenceClients) -> None:
        voice_receipts.extend(await calibrate_conference_voices(clients))

    clients = await ConferenceClients.create(
        core.base_url,
        settings,
        core.factory,
        uuid4().hex,
        rule_id,
        owned_database_url=settings.database_url_value,
        production_database_url=production_url,
        existing_admin=existing_admin,
        before_room=calibrate if existing_admin is None else None,
    )
    turn: HumanTurn | None = None
    turns: list[dict[str, Any]] = []
    faults: list[dict[str, Any]] = []
    rng = random.Random(seed)
    raised: set[str] = set()
    hand_schedule: dict[str, tuple[float, Any]] = {}
    phase = 0
    changed_at = monotonic()
    signature: tuple[Any, ...] = ()
    fault_done: set[str] = set()
    receipt: dict[str, Any] = {}
    snapshot: dict[str, Any] = {}
    scenario_error: BaseException | None = None
    try:
        prepared = await clients.prepare_and_connect()
        assert clients.match_id is not None
        require(prepared["status"] == "START_PENDING_RUNTIME", "qa_not_waiting_for_explicit_start")
        witness = Witness(clients)
        await witness.start()
        await clients.start_match()
        deadline = monotonic() + 3600
        while monotonic() < deadline:
            snapshot = await clients.snapshot()
            current = (
                snapshot["status"],
                snapshot["action_state"],
                snapshot["current_action_index"],
                snapshot.get("current_speech_id"),
            )
            if current != signature:
                signature, changed_at = current, monotonic()
                emit(
                    "progress",
                    scenario=scenario,
                    match_id=str(clients.match_id),
                    sequence=snapshot["sequence"],
                    status=snapshot["status"],
                    stage=snapshot["action_state"],
                )
            await witness.sync()
            require(
                snapshot["status"] not in {"ERROR", "SYSTEM_RECOVERY", "TERMINATED"},
                "qa_match_unexpected_failure",
            )
            if turn is not None and turn.task is not None and turn.task.done():
                turns.append(await turn.task)
                turn = None
            if snapshot["status"] == "FINISHED":
                await witness.sync(finished=True)
                break
            if snapshot["action_state"] == "HUMAN_READY_TO_START" and turn is None:
                pending = snapshot.get("pending_source")
                if not pending or pending["status"] not in {"STARTED", "FINALIZING"}:
                    person = next(
                        p
                        for p in clients.people
                        if str(p.user_id) == snapshot["current_speaker_user_id"]
                    )
                    assert person.side is not None and person.seat is not None
                    turn = HumanTurn(clients, person, materials[(person.side, person.seat)])
                    turn.task = asyncio.create_task(turn.run(), name=f"qa-human-{person.label}")
            if snapshot["action_state"] == "FREE_SELECTING" and snapshot["hand_window_open"]:
                team = cast(dict[str, Any], snapshot.get("experiment_team_state") or {})
                opportunity = str(team.get("opportunity_id") or snapshot["sequence"])
                side = snapshot.get("free_holder_side")
                if opportunity not in hand_schedule:
                    candidates = [p for p in clients.people if p.side == side]
                    require(candidates, "qa_free_candidate_missing")
                    hand_schedule[opportunity] = (
                        monotonic() + rng.uniform(0.2, 7),
                        rng.choice(candidates),
                    )
                at, person = hand_schedule[opportunity]
                if opportunity not in raised and monotonic() >= at:
                    try:
                        await clients.command(person, "hand.raise")
                    except ClientFailure as error:
                        if error.code != "hand_window_closed":
                            raise
                    raised.add(opportunity)
            if scenario == "weaknet" and turn is not None and turn.started and turn.speech_id:
                elapsed = monotonic() - turn.started
                if elapsed >= 12 and "ws" not in fault_done:
                    await witness.sync(force=True)
                    publisher = turn.person.publisher
                    await reconnect_business_ws(clients, turn.person)
                    require(turn.person.publisher is publisher, "qa_ws_replaced_rtc")
                    after = await clients.snapshot(turn.person)
                    require(after["current_speech_id"] == turn.speech_id, "qa_ws_changed_speech")
                    faults.append({"kind": "business_ws_2s", "same_speech": True, "rtc_kept": True})
                    fault_done.add("ws")
                if elapsed >= 30 and "rtc" not in fault_done:
                    await witness.sync(force=True)
                    faults.append(await rtc_handoff(clients, turn.person, turn))
                    fault_done.add("rtc")
                if elapsed >= 45 and "asr" not in fault_done:
                    await witness.sync(force=True)
                    faults.append(
                        abort_owned_asr_socket(core.app, clients.match_id, turn.speech_id)
                    )
                    fault_done.add("asr")
            if (
                scenario == "recovery"
                and snapshot["action_state"] == "AGENT_SPEAKING"
                and cast(dict[str, Any], snapshot.get("current_action") or {}).get("seat_no") == 2
                and monotonic() - changed_at >= 1
                and phase < 3
            ):
                await witness.sync(force=True)
                action = ("pause", "reset_speech", "skip_speech")[phase]
                target_id = snapshot.get("current_speech_id")
                await clients.control(action)
                if action != "skip_speech":
                    await clients.control("resume")
                faults.append({"kind": action, "target_speech_id": target_id})
                phase += 1
            await asyncio.sleep(0.15)
        else:
            raise ProbeError("qa_full_conference_timeout")
        if scenario == "weaknet":
            require(fault_done == {"ws", "rtc", "asr"}, "qa_weaknet_coverage_missing")
        if scenario == "recovery":
            require(phase == 3, "qa_recovery_coverage_missing")
        async with core.factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT sequence,event_type,payload->>'speech_id',payload->>'reason' "
                        "FROM match_events "
                        "WHERE match_id=:id ORDER BY sequence"
                    ),
                    {"id": clients.match_id},
                )
            ).all()
            require([row[0] for row in rows] == list(range(1, len(rows) + 1)), "qa_sequence_gap")
            require(sum(row[1] == "match.finished" for row in rows) == 1, "qa_finished_not_unique")
            speeches = (
                (
                    await session.execute(
                        text(
                            "SELECT id,status,speaker_kind,side,seat_no,origin,"
                            "audio_storage_path,audio_duration_ms,user_id,display_text,"
                            "action_key,finish_reason "
                            "FROM speeches WHERE match_id=:id"
                        ),
                        {"id": clients.match_id},
                    )
                )
                .mappings()
                .all()
            )
            calls = (
                (
                    await session.execute(
                        text(
                            "SELECT call_kind,status,speech_id FROM external_calls "
                            "WHERE match_id=:id"
                        ),
                        {"id": clients.match_id},
                    )
                )
                .mappings()
                .all()
            )
            roster = (
                await session.execute(
                    text(
                        "SELECT side,kind,count(*) FROM match_participants "
                        "WHERE match_id=:id GROUP BY side,kind"
                    ),
                    {"id": clients.match_id},
                )
            ).all()
            decisions = (
                await session.execute(
                    text(
                        "SELECT status,should_speak,effective_status,count(*) "
                        "FROM agent_free_debate_decisions WHERE match_id=:id "
                        "GROUP BY status,should_speak,effective_status"
                    ),
                    {"id": clients.match_id},
                )
            ).all()
            allocations = (
                await session.execute(
                    text(
                        "SELECT speaker_kind,count(*) FROM speaker_allocations "
                        "WHERE effective=true AND opportunity_id IN "
                        "(SELECT id FROM free_debate_opportunities WHERE match_id=:id) "
                        "GROUP BY speaker_kind"
                    ),
                    {"id": clients.match_id},
                )
            ).all()
            judge_policy = await session.scalar(
                text(
                    "SELECT r.format_snapshot FROM rooms r JOIN matches m ON m.room_id=r.id "
                    "WHERE m.id=:id"
                ),
                {"id": clients.match_id},
            )
        from jx_core.postmatch import _snapshot_judge

        judge_config = _snapshot_judge(judge_policy)
        judge_status = "disabled_in_frozen_rule"
        if judge_config is not None:
            async with asyncio.timeout(90):
                while True:
                    async with core.factory() as session:
                        judge_status = await session.scalar(
                            text(
                                "SELECT status FROM judge_results WHERE match_id=:id "
                                "ORDER BY created_at DESC LIMIT 1"
                            ),
                            {"id": clients.match_id},
                        )
                    if judge_status in {"SUCCEEDED", "FAILED"}:
                        break
                    await asyncio.sleep(0.2)
            require(judge_status == "SUCCEEDED", "qa_enabled_judge_not_succeeded")
        require(
            len(roster) == 4 and all(row[2] == 2 for row in roster), "qa_roster_not_2h2a_per_side"
        )
        finalized = [row for row in speeches if row["status"] == "FINALIZED"]
        placeholders = [row for row in finalized if row["origin"] == "ADMIN_PLACEHOLDER"]
        require(len(placeholders) == int(scenario == "recovery"), "qa_placeholder_count_mismatch")
        require(
            all(row["seat_no"] == 2 and not row["audio_storage_path"] for row in placeholders),
            "qa_placeholder_audio_or_wrong_seat",
        )
        require(
            not any(
                call["call_kind"] == "TTS" and call["speech_id"] == row["id"]
                for call in calls
                for row in placeholders
            ),
            "qa_placeholder_tts_created",
        )
        reset_ids = {str(row["id"]) for row in speeches if row["status"] == "RESET"}
        intended_resets = {
            item["target_speech_id"]
            for item in faults
            if item["kind"] in {"pause", "reset_speech", "skip_speech"}
        }
        require(reset_ids == intended_resets, "qa_unexpected_speech_reset")
        require(
            all(row["status"] in {"FINALIZED", "RESET"} for row in speeches), "qa_unsettled_speech"
        )
        require(
            all(
                row["display_text"] and any(c.isalnum() for c in row["display_text"])
                for row in finalized
            ),
            "qa_final_text_missing",
        )
        for turn_receipt in turns:
            stored = next(row for row in finalized if str(row["id"]) == turn_receipt["speech_id"])
            person = next(p for p in clients.people if p.user_id == stored["user_id"])
            assert person.side is not None and person.seat is not None
            source = materials[(person.side, person.seat)]
            owner_verified = (
                turn_receipt["start_owner_verified"]
                and stored["action_key"] == turn_receipt["action_key"]
                and stored["side"] == turn_receipt["speaker_side"]
                and stored["seat_no"] == turn_receipt["speaker_seat_no"]
                and stored["speaker_kind"] == "HUMAN"
                and stored["origin"] == "ORIGINAL"
            )
            require(owner_verified, "qa_human_final_owner_mismatch")
            audio_path = Path(stored["audio_storage_path"] or "")
            require(
                audio_path.is_file()
                and audio_path.resolve().is_relative_to(
                    Path(settings.agent_audio_storage_dir).resolve()
                ),
                "qa_human_recording_missing",
            )
            require(
                audio_path.stat().st_size > 0 and audio_path.stat().st_size % 2 == 0,
                "qa_human_recording_invalid",
            )
            require(
                abs(audio_path.stat().st_size // 32 - int(stored["audio_duration_ms"] or 0)) <= 1,
                "qa_human_recording_duration_mismatch",
            )
            require(
                any(
                    str(call["speech_id"]) == turn_receipt["speech_id"]
                    and call["call_kind"] == "ASR"
                    and call["status"] == "SUCCEEDED"
                    for call in calls
                ),
                "qa_human_successful_asr_call_missing",
            )
            turn_receipt["recorded_duration_ms"] = stored["audio_duration_ms"]
            turn_receipt["finish_reason"] = stored["finish_reason"]
            natural_finish = (
                sum(
                    row[1] == "speech.finalizing"
                    and row[2] == turn_receipt["speech_id"]
                    and row[3] == "TIME_LIMIT"
                    for row in rows
                )
                == 1
            )
            turn_receipt["asr_text_quality"] = quality_coverage(
                source,
                turn_receipt["sent_samples"],
                stored["display_text"],
                ARGUMENTS,
                context={
                    **turn_receipt,
                    "owner_verified": owner_verified,
                    "asr_succeeded": True,
                    "recording_verified": True,
                    "event_chain_verified": True,
                    "natural_finish_verified": natural_finish,
                },
            )
        require(
            {
                (turn["speaker_side"], turn["speaker_seat_no"])
                for turn in turns
                if turn["action_kind"] == "HUMAN_SPEECH"
                and turn["asr_text_quality"]["passed"] is True
            }
            == {(side, seat) for side in ("AFFIRMATIVE", "NEGATIVE") for seat in (1, 3)},
            "qa_fixed_human_quality_coverage_missing",
        )
        require(
            turns
            and int(turns[0]["initial_budget_ms"] or 0) >= 119_000
            and turns[0]["wall_ms"] >= int(turns[0]["initial_budget_ms"] or 0) - 1_000,
            "qa_first_human_not_full_original_budget",
        )
        require(
            all(
                any(row["side"] == side and row["seat_no"] == 4 for row in finalized)
                for side in ("AFFIRMATIVE", "NEGATIVE")
            ),
            "qa_fourth_speaker_missing",
        )
        if scenario == "weaknet":
            injected = next(item for item in turns if item["wall_ms"] >= 45_000)
            target_calls = [
                row
                for row in calls
                if str(row["speech_id"]) == injected["speech_id"] and row["call_kind"] == "ASR"
            ]
            require(
                any(row["status"] == "FAILED" for row in target_calls)
                and any(row["status"] == "SUCCEEDED" for row in target_calls),
                "qa_real_asr_replay_missing",
            )
        archive = await archive_match(
            core.factory, settings, clients.match_id, Path(settings.match_audio_storage_dir).parent
        )
        receipt = {
            "scenario": scenario,
            "match_id": str(clients.match_id),
            "status": "passed"
            if all(turn["asr_text_quality"]["qualification_accepted"] for turn in turns)
            else "failed",
            "structure_finished": True,
            "sequence": snapshot["sequence"],
            "speech_count": len(speeches),
            "asr_call_count": sum(row["call_kind"] == "ASR" for row in calls),
            "archive_source_count": archive["source_count"],
            "archive_sample_difference": archive["difference_samples"],
            "human_turns": turns,
            "quality_evaluated_passed": sum(
                t["asr_text_quality"]["status"] == "passed" for t in turns
            ),
            "quality_not_evaluable": sum(
                t["asr_text_quality"]["status"] == "not_evaluable" for t in turns
            ),
            "timing_scope": "unchanged_production_yxh_120s_first_human_90s_third",
            "independent_180s_input_qualification": False,
            "faults": faults,
            "voice_calibrations": voice_receipts,
            "agent_decisions": [
                {
                    "status": row[0],
                    "should_speak": row[1],
                    "effective_status": row[2],
                    "count": row[3],
                }
                for row in decisions
            ],
            "speaker_allocations": {row[0]: row[1] for row in allocations},
            "postmatch": await postmatch_display(clients),
            "ai_judge": judge_status,
            "resource_counts": {},
        }
        return receipt, clients.admin
    except BaseException as error:
        scenario_error = error
        await capture_failure_diagnostics(
            core,
            clients,
            turn,
            error,
            diagnostic_out,
            scenario,
            {"completed_human_turns": len(turns), "injected_faults": len(faults)},
            last_snapshot=snapshot,
        )
        raise
    finally:
        try:
            if turn is not None and turn.task is not None:
                turn.media_available.set()
                if not turn.task.done():
                    turn.task.cancel()
                await asyncio.gather(turn.task, return_exceptions=True)
            try:
                if not receipt:
                    await retire_failed_owned_case(clients)
            finally:
                await clients.close()
            for person in clients.people:
                person.publisher = None
                person.socket = None
                person.receiver = None
            gc.collect()
            async with asyncio.timeout(5):
                while True:
                    counts = await case_resource_counts(core, clients)
                    if not any(counts.values()):
                        break
                    await asyncio.sleep(0.05)
            receipt["resource_counts"] = counts
        except BaseException as cleanup_error:
            if scenario_error is None:
                raise
            emit("progress", phase="scenario_cleanup_failed", **safe_failure(cleanup_error))


async def run(args: argparse.Namespace) -> int:
    root = Path(args.candidate_root).resolve()
    out = Path(args.out).resolve()
    require(
        bool(re.fullmatch(r"jx-spec307-candidate-[0-9a-f]{6,64}", root.name)), "qa_candidate_root"
    )
    require(root.parent == Path("/tmp") and root.is_dir(), "qa_candidate_root")
    require(
        out.parent == Path("/tmp")
        and bool(re.fullmatch(r"jx-spec307-qa-[0-9a-f]{6,64}", out.name)),
        "qa_output_root",
    )
    require(not out.exists(), "qa_output_already_exists")
    out.mkdir(mode=0o700)
    manifest_sha, before = audit_sources(root)
    require(importlib.metadata.version("livekit") == "1.1.20", "qa_livekit_sdk_version_mismatch")
    for relative in ("apps/core/src", "apps/jobs/src", "scripts/qa/probes", ""):
        sys.path.insert(0, str(root / relative))
    config = importlib.import_module("jx_core.config")
    require(
        Path(cast(str, config.__file__)).resolve().is_relative_to(root), "qa_imported_non_candidate"
    )
    os.chdir("/opt/jixia-debate")
    normal = config.load_settings()
    production_url = normal.database_url_value
    material_module = importlib.import_module("server_spec307_material")
    material = await material_module.read_material(
        normal, source_match_id=UUID(args.source_match_id)
    )
    avatar_root = str(Path(normal.avatar_storage_dir).resolve())
    postgres = importlib.import_module("server_full_match_probe").OwnedPostgres()
    livekit = importlib.import_module("server_spec307_infra").OwnedLiveKit(core_port=8027)
    core: Any = None
    results: list[dict[str, Any]] = []
    original_error: dict[str, str] | None = None
    baseline = set(asyncio.all_tasks())
    try:
        database_url = await asyncio.to_thread(postgres.start)
        await asyncio.to_thread(postgres.migrate, database_url, repo_root=root)
        livekit_settings = await asyncio.to_thread(livekit.start)
        settings = normal.model_copy(
            update={
                **livekit_settings,
                "app_env": "development",
                "database_url": database_url,
                "core_host": "127.0.0.1",
                "core_port": 8027,
                "cors_origins": "http://127.0.0.1:3100",
                "avatar_storage_dir": avatar_root,
                "host_audio_storage_dir": str(postgres.root / "host"),
                "agent_audio_storage_dir": str(postgres.root / "speech"),
                "match_audio_storage_dir": str(postgres.root / "match"),
                "export_storage_dir": str(postgres.root / "exports"),
            }
        )
        from pydantic import SecretStr
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        settings = settings.model_copy(update={"database_url": SecretStr(database_url)})
        engine = create_async_engine(database_url, pool_size=2, max_overflow=0)
        try:
            seeded = await material_module.seed_material(
                async_sessionmaker(engine, expire_on_commit=False),
                material,
                Path(settings.host_audio_storage_dir),
            )
        finally:
            await engine.dispose()
        del material
        os.chdir(postgres.root)
        core = importlib.import_module("server_spec307_app").OwnedCoreApp(settings)
        await core.start()
        material_builder = importlib.import_module("server_spec307_speech_material")
        semaphore = asyncio.Semaphore(2)

        async def build(side: str, seat: int, voice: str) -> tuple[tuple[str, int], Any]:
            async with semaphore:
                value = await material_builder.build_human_material(
                    settings, core.factory, side=side, seat=seat, voice=voice, target_seconds=205
                )
            return (side, seat), value

        async with asyncio.TaskGroup() as group:
            material_tasks = [
                group.create_task(build(side, seat, voice))
                for side, seat, voice in (
                    ("AFFIRMATIVE", 1, "longhua_v3.1"),
                    ("AFFIRMATIVE", 3, "anmingyuan_v3.1"),
                    ("NEGATIVE", 1, "huozhuoshi_v3.1"),
                    ("NEGATIVE", 3, "xiaxiaochen_v3.1"),
                )
            ]
        material_pairs = [task.result() for task in material_tasks]
        materials = dict(material_pairs)
        # These are owned public synthetic sources. Preserve exact PCM and
        # block references so a failed long ASR can be checked against the
        # identical short source after temporary match storage is retired.
        source_root = out / "public-materials"
        source_root.mkdir(mode=0o700)
        for (side, seat), value in materials.items():
            stem = f"{side.lower()}-{seat}"
            (source_root / f"{stem}.pcm").write_bytes(value.pcm)
            (source_root / f"{stem}.json").write_text(
                json.dumps({"receipt": value.receipt(), "blocks": value.blocks}, indent=2) + "\n"
            )
        previous_admin: Any = None
        for index, scenario in enumerate(SCENARIOS):
            receipt, previous_admin = await run_scenario(
                scenario,
                core,
                settings,
                UUID(seeded["rule_id"]),
                production_url,
                materials,
                previous_admin,
                seed=307 + index,
                diagnostic_out=out,
            )
            results.append(receipt)
            (out / f"{scenario}.json").write_text(json.dumps(receipt, indent=2) + "\n")
            require(receipt["status"] == "passed", "qa_asr_quality_not_qualified")
        return 0
    except BaseException as error:
        original_error = safe_failure(error)
        for scenario in SCENARIOS:
            failure_path = out / f"{scenario}-failure.json"
            if failure_path.is_file() and not failure_path.is_symlink():
                original_error["diagnostic_sha256"] = hashlib.sha256(
                    failure_path.read_bytes()
                ).hexdigest()
        raise
    finally:
        cleanup, cleanup_errors = await close_owned(core, livekit, postgres)
        if _control_transport is not None:
            _control_transport.close()
        after_sha, after = audit_sources(root)
        gc.collect()
        await asyncio.sleep(0)
        pending_observation = await observe_retired_cleanup(baseline, core)
        proof = {
            "manifest_sha256": manifest_sha,
            "before": before,
            "after": after,
            "verified": before == after and manifest_sha == after_sha,
            "count": len(before),
        }
        receipt = {
            "status": "passed"
            if len(results) == 3
            and all(item["status"] == "passed" for item in results)
            and all(cleanup.values())
            and not cleanup_errors
            and pending_observation["status"] == "passed"
            and not _unraisable_count
            and proof["verified"]
            else "failed",
            "scenarios": results,
            "original_error": original_error,
            "failure_artifacts": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(out.glob("*-failure.json"))
                if path.is_file() and not path.is_symlink()
            },
            "source_proof": proof,
            "cleanup": {
                **cleanup,
                "pending_tasks": pending_observation["pending_tasks"],
                "initial_pending_tasks": pending_observation["initial_pending_tasks"],
                "pending_coroutines": pending_observation["pending_coroutines"],
                "pending_observation": pending_observation,
                "errors": cleanup_errors,
                "sdk_unraisable_count": _unraisable_count,
            },
        }
        (out / "RESULT.json").write_text(json.dumps(receipt, indent=2) + "\n")
        emit("result", **receipt)
        if original_error is None:
            require(receipt["status"] == "passed", "qa_overall_not_qualified")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--source-match-id", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    require(
        args.execute and bool(UUID(args.nonce)) and bool(UUID(args.source_match_id)),
        "qa_explicit_execution_required",
    )
    original_unraisable = sys.unraisablehook
    try:

        def record_unraisable(value: Any) -> None:
            global _unraisable_count
            _unraisable_count += 1
            if len(_unraisable_classes) < 16:
                _unraisable_classes.append(value.exc_type.__name__)
            original_unraisable(value)

        sys.unraisablehook = record_unraisable
        with contextlib.redirect_stdout(sys.stderr):
            return asyncio.run(run(args))
    except BaseException as error:
        emit("error", **safe_failure(error))
        return 1
    finally:
        sys.unraisablehook = original_unraisable


if __name__ == "__main__":
    raise SystemExit(main())

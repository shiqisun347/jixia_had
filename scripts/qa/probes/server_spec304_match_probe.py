"""Real Spec304 match scenarios, exclusively in the inherited owned QA database.

Run from the deployed settings directory with candidate modules on PYTHONPATH.
Only user actions and QA fixture inputs are extended; provider callbacks, timers,
PCM, generation, judging and Jobs are the normal application implementations.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import resource
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import server_full_match_probe as full
from server_repeat_mixed_4v4_probe import cleanup_complete, completed_flow
from sqlalchemy import select
from sqlalchemy.engine import make_url
from starlette.requests import Request

from jx_core.admin_routes import MatchControlRequest, control_match
from jx_core.agent.tts import QwenTtsConnection
from jx_core.auth.session import AuthContext
from jx_core.config import load_settings
from jx_core.data_capture.content import load_content_blob
from jx_core.data_capture.provider_projection import capture_projection
from jx_core.matches.emergency import MatchEmergencyCoordinator
from jx_core.matches.routes import get_match_transcript
from jx_core.models import ExternalCall, JudgeResult, Match, Speech, User
from jx_jobs.audio_worker import _load_sources

# This QA driver intentionally inspects runtime ownership and the Jobs source
# selector; these internal interfaces are assertions, never callback substitutes.
# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false


def nested_contexts(value: Any, *, depth: int = 0) -> list[dict[str, Any]]:
    """Inspect bounded captured requests in memory; never emit their bodies."""
    if depth > 12:
        return []
    if isinstance(value, str):
        if len(value) > 256_000 or not value.lstrip().startswith(("{", "[")):
            return []
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        if isinstance(mapping.get("history"), list):
            result.append(mapping)
        for child in mapping.values():
            result.extend(nested_contexts(child, depth=depth + 1))
    elif isinstance(value, list):
        for child in cast(list[Any], value):
            result.extend(nested_contexts(child, depth=depth + 1))
    return result


def history_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], item)
        for item in cast(list[Any], context["history"])
        if isinstance(item, dict)
    ]


def reset_history_valid(
    contexts: list[dict[str, Any]], started_at: datetime, resets: list[dict[str, Any]]
) -> bool:
    """Only a reset already committed when this request started can retire input."""
    if not resets or any(
        not isinstance(row.get("committed_at"), datetime)
        or not row.get("speech_id")
        for row in resets
    ):
        return False
    retired = {str(row["speech_id"]) for row in resets if row["committed_at"] <= started_at}
    return all(
        not retired.intersection(str(item.get("speech_id")) for item in history_items(context))
        for context in contexts
    )


def reset_windows_complete(rows: Any) -> bool:
    if not isinstance(rows, list):
        return False
    windows = cast(list[dict[str, Any]], rows)
    if len(windows) != 2:
        return False
    return (
        all(isinstance(row, dict) for row in cast(list[Any], windows))
        and [row.get("action") for row in windows] == ["reset_speech", "reset_stage"]
        and all(
            isinstance(row.get("committed_at"), str)
            and row["committed_at"]
            and isinstance(row.get("speech_id"), str)
            and row["speech_id"]
            and isinstance(row.get("event_sequence"), int)
            and row["event_sequence"] > 0
            and isinstance(row.get("successful_calls_checked"), int)
            and row["successful_calls_checked"] > 0
            for row in windows
        )
    )


async def owned_admin(factory: Any, owned: Any, url: str) -> tuple[UUID, str]:
    """Reuse the migrated single admin, only in this probe's exact database."""
    parsed = make_url(url)
    binding = factory.kw.get("bind")
    if (
        parsed.host != "127.0.0.1"
        or parsed.port != owned.port
        or parsed.database != "jx_debate_qa_agent_faults"
        or getattr(binding, "url", None) != parsed
    ):
        raise RuntimeError("qa_admin_database_not_owned")
    async with factory() as session:
        admin = await session.scalar(select(User.id).where(User.role == "ADMIN"))
        if admin is not None:
            return cast(UUID, admin), "existing_admin"
        admin = uuid4()
        session.add(
            User(
                id=admin,
                username=f"qa-admin-{admin.hex[:8]}",
                username_normalized=f"qa-admin-{admin.hex[:8]}",
                real_name="合成应急管理员",
                role="ADMIN",
                password_hash="unusable-synthetic-hash",
            )
        )
        await session.commit()
        return admin, "created_synthetic_admin"


def explicit_presend_retirement(
    call: Any, projection: dict[str, Any], raw: Any, retirements: list[dict[str, Any]]
) -> bool:
    """An observed pre-send retirement must still have its explicit raw receipt."""
    if not isinstance(raw, dict):
        return False
    envelope = cast(dict[str, Any], raw)
    capture = envelope.get("capture")
    if not isinstance(capture, dict):
        return False
    capture = cast(dict[str, Any], capture)
    return (
        call.call_kind == "LLM_SPEECH"
        and call.operation == "chat.completions.stream"
        and call.status == "FAILED"
        and call.error_code == "agent_cancelled"
        and call.completed_at is not None
        and call.first_result_at is None
        and call.first_result_latency_ms is None
        and call.generation_id is not None
        and call.agent_generation_id == call.generation_id
        and projection.get("request_blob_id") is not None
        and projection.get("request_capture_status") == "UNAVAILABLE"
        and capture.get("status") == "UNAVAILABLE"
        and capture.get("error_code") == "provider_request_unavailable"
        and envelope.get("body") is None
        and envelope.get("url") is None
        and envelope.get("transport") is None
        and any(
            row.get("generation_id") == str(call.generation_id)
            and row.get("match_id") == str(call.match_id)
            and row.get("capture_present") is True
            and row.get("request_captured") is False
            and row.get("speech_id") is None
            and row.get("played_samples") == 0
            and row.get("source") == "actual_cancel_run_entry"
            and row.get("operation_id")
            for row in retirements
        )
    )


def strict_scenario(receipt: dict[str, Any], scenario: str) -> bool:
    proof = receipt.get("spec304_scenario", {})
    host = receipt.get("conference_host_fixture", {})
    return (
        scenario in {"normal", "timeout", "recovery"}
        and completed_flow(receipt)
        and proof.get("scenario") == scenario
        and proof.get("status") == "passed"
        and proof.get("media_observation", {}).get("status") == "passed"
        and proof.get("media_observation", {}).get("violations") == []
        and proof.get("media_observation", {}).get("overflow_count") == 0
        and proof.get("media_observation", {}).get("synthesize_entries", 0) > 0
        and proof.get("media_observation", {}).get("actual_pcm_samples", 0) > 0
        and receipt.get("pending_media_starts_after_close") == 0
        and host.get("status") == "passed"
        and host.get("provider_voice") == "longanwen_v3.1"
        and host.get("host_name") == "龙风月瑶"
        and host.get("all_assets_frozen") is True
        and host.get("host_reserved_absent_from_agents") is True
        and host.get("normal_agent_voices") == 8
        and host.get("fixture_version") == "spec304-conference-host-fixture-v2"
        and host.get("original_first_host_prompt_preserved") is True
        and host.get("original_first_host_prompt_present") is True
        and isinstance(host.get("welcome_whole_asset_decoded_ms"), int)
        and host["welcome_whole_asset_decoded_ms"] > 0
        and all(
            isinstance(host.get(key), int) and 5000 <= host[key] <= 8000
            for key in ("welcome_preface_ms", "closing_decoded_ms")
        )
        and host.get("welcome_preface_measurement", {}).get("actual_provider") is True
        and host.get("welcome_preface_measurement", {}).get("temporary_audio_removed") is True
        and (
            scenario != "timeout"
            or (
                proof.get("natural_time_limit_count") == 1
                and proof.get("continuous_pcm_until_authoritative_deadline") is True
                and isinstance(proof.get("speech_id"), str)
                and bool(proof["speech_id"])
            )
        )
        and (
            scenario != "recovery"
            or (
                proof.get("speech_reset_count") == 1
                and proof.get("stage_reset_count") == 1
                and proof.get("placeholder_count") == 1
                and proof.get("placeholder_provider_succeeded") is True
                and proof.get("placeholder_tts_calls") == 0
                and proof.get("tts_window", {}).get("exact_placeholder_tts_calls") == 0
                and proof.get("tts_window", {}).get("late_metadata_explained") is True
                and isinstance(
                    proof.get("tts_window", {}).get("global_count_delta_after_ack"), int
                )
                and proof.get("reset_excluded_from_transcript") is True
                and proof.get("reset_excluded_from_agent_history") is True
                and proof.get("reset_excluded_from_replay") is True
                and proof.get("placeholder_excluded_from_judge_performance") is True
                and operation_evidence(proof.get("operations", []))
                and reset_windows_complete(proof.get("reset_context_windows"))
                and proof.get("placeholder_labelled_in_agent_history") is True
                and set(proof.get("fourth_speakers_spoken", {})) == {"AFFIRMATIVE", "NEGATIVE"}
                and all(
                    isinstance(value, int) and value > 0
                    for value in proof.get("fourth_speakers_spoken", {}).values()
                )
            )
        )
    )


def operation_evidence(rows: Any) -> bool:
    if not isinstance(rows, list):
        return False
    entries = cast(list[dict[str, Any]], rows)
    return (
        len(entries) == 3
        and all(isinstance(row, dict) for row in cast(list[Any], entries))
        and [row.get("action") for row in entries] == ["reset_speech", "reset_stage", "skip_speech"]
        and all(
            isinstance(row.get("operation_id"), str)
            and row["operation_id"]
            and isinstance(row.get("before_sequence"), int)
            and isinstance(row.get("after_sequence"), int)
            and row["after_sequence"] > row["before_sequence"]
            for row in entries
        )
        and len({row["operation_id"] for row in entries}) == 3
        and entries[2].get("target_seat_no") == 3
        and all(
            isinstance(row.get("natural_resume_ms"), int) and row["natural_resume_ms"] >= 2900
            for row in entries
        )
    )


class MediaObservation:
    """Forward original media methods while keeping bounded, body-free evidence."""

    def __init__(self, manager: Any, agents: Any, acknowledgements: dict[str, float]):
        self.manager, self.agents, self.acknowledgements = manager, agents, acknowledgements
        self.runs: dict[str, Any] = {}
        self.task_runs: dict[Any, Any] = {}
        self.entries: list[dict[str, Any]] = []
        self.violations: list[dict[str, Any]] = []
        self.audio: dict[str, dict[str, int]] = {}
        self.sources: list[tuple[Any, Any, Any]] = []
        self.overflow_count = 0
        self.original_execute = agents._execute
        self.original_host = agents._play_host_audio
        self.original_connect = agents._connect_publisher
        self.original_synthesize = QwenTtsConnection.synthesize
        self.execute_wrapper: Any = None
        self.host_wrapper: Any = None
        self.connect_wrapper: Any = None
        self.synthesize_wrapper: Any = None

    def snapshot(self, run: Any, kind: str) -> dict[str, Any]:
        actor = self.manager._actors.get(run.match_id) if run is not None else None
        state = actor.state if actor is not None else None
        operation = cast(dict[str, Any], state.emergency_operation or {}) if state else {}
        return {
            "kind": kind,
            "at_mono": monotonic(),
            "match_id": str(run.match_id) if run is not None else None,
            "generation_id": str(run.generation_id) if run and run.generation_id else None,
            "action_key": run.action_key if run is not None else None,
            "actor_status": state.status if state else None,
            "actor_action_state": state.action_state if state else None,
            "operation_id": operation.get("id"),
            "operation_acknowledged": operation.get("id") in self.acknowledgements,
            "media_fenced": bool(run and run.media_fenced),
            "played_samples": getattr(run, "played_samples", 0),
        }

    def record(self, row: dict[str, Any], *, violation: bool = False) -> None:
        if len(self.entries) >= 256:
            self.overflow_count += 1
            raise RuntimeError("qa_media_observation_capacity_exhausted")
        self.entries.append(row)
        if violation:
            self.violations.append(row)

    def counter(self, run: Any) -> dict[str, int]:
        key = str(run.generation_id) if run is not None else "unknown"
        if key not in self.audio and len(self.audio) >= 128:
            self.overflow_count += 1
            raise RuntimeError("qa_media_generation_capacity_exhausted")
        return self.audio.setdefault(
            key,
            {
                "raw_chunks": 0,
                "raw_bytes": 0,
                "raw_chunks_while_paused": 0,
                "rejected_chunks": 0,
                "accepted_chunks": 0,
                "accepted_bytes": 0,
                "pcm_frames": 0,
                "pcm_samples": 0,
                "pcm_frames_during_cleanup": 0,
            },
        )

    def install(self) -> None:
        async def execute(run: Any, config: Any, generation_id: Any) -> None:
            if str(generation_id) not in self.runs and len(self.runs) >= 128:
                self.overflow_count += 1
                raise RuntimeError("qa_media_generation_capacity_exhausted")
            task = asyncio.current_task()
            self.runs[str(generation_id)] = run
            self.task_runs[task] = run
            row = self.snapshot(run, "agent_execute")
            self.record(row, violation=row["actor_status"] != "RUNNING")
            try:
                await self.original_execute(run, config, generation_id)
            finally:
                self.task_runs.pop(task, None)

        async def host(run: Any) -> None:
            task = asyncio.current_task()
            self.runs[str(run.generation_id)] = run
            self.task_runs[task] = run
            row = self.snapshot(run, "host_play")
            self.record(row, violation=row["actor_status"] != "RUNNING")
            try:
                await self.original_host(run)
            finally:
                self.task_runs.pop(task, None)

        async def connect(*args: Any, **kwargs: Any) -> Any:
            run = self.task_runs.get(asyncio.current_task())
            room, source = await self.original_connect(*args, **kwargs)
            if run is not None:
                original = source.capture_frame

                async def frame(value: Any) -> None:
                    row = self.snapshot(run, "capture_frame")
                    after_ack = bool(row["operation_acknowledged"])
                    forbidden = after_ack and (
                        row["actor_status"] != "RUNNING" or row["media_fenced"]
                    )
                    # An in-flight queue operation before the cleanup ACK is
                    # recorded separately; the original runtime clears it.
                    if forbidden:
                        self.record(row, violation=True)
                    await original(value)
                    count = self.counter(run)
                    count["pcm_frames"] += 1
                    count["pcm_samples"] += int(value.samples_per_channel)
                    if row["actor_status"] != "RUNNING" and not after_ack:
                        count["pcm_frames_during_cleanup"] += 1
                    completed = self.snapshot(run, "capture_frame_completed")
                    if not forbidden and completed["operation_acknowledged"] and (
                        completed["actor_status"] != "RUNNING" or completed["media_fenced"]
                    ):
                        self.record(completed, violation=True)

                source.capture_frame = frame
                self.sources.append((source, original, frame))
            else:
                self.record(self.snapshot(None, "unknown_publisher_owner"), violation=True)
            return room, source

        async def synthesize(connection: Any, chunks: Any, **kwargs: Any) -> Any:
            task = asyncio.current_task()
            name = task.get_name() if task is not None else ""
            generation_id = name.removeprefix("agent-tts-") if name.startswith("agent-tts-") else ""
            run = self.runs.get(generation_id)
            row = self.snapshot(run, "synthesize")
            self.record(row, violation=run is None or row["actor_status"] != "RUNNING")
            original_audio = kwargs["on_audio"]

            async def audio(chunk: bytes) -> None:
                count = self.counter(run)
                count["raw_chunks"] += 1
                count["raw_bytes"] += len(chunk)
                arrival = self.snapshot(run, "raw_audio")
                if arrival["actor_status"] != "RUNNING":
                    count["raw_chunks_while_paused"] += 1
                    if count["raw_chunks_while_paused"] == 1:
                        self.record(arrival)
                try:
                    await original_audio(chunk)
                except BaseException:
                    count["rejected_chunks"] += 1
                    raise
                count["accepted_chunks"] += 1
                count["accepted_bytes"] += len(chunk)
                if arrival["operation_acknowledged"] and (
                    arrival["actor_status"] != "RUNNING" or arrival["media_fenced"]
                ):
                    self.record(self.snapshot(run, "accepted_audio_after_ack"), violation=True)

            kwargs["on_audio"] = audio
            return await self.original_synthesize(connection, chunks, **kwargs)

        self.execute_wrapper, self.host_wrapper = execute, host
        self.connect_wrapper, self.synthesize_wrapper = connect, synthesize
        self.agents._execute, self.agents._play_host_audio = execute, host
        self.agents._connect_publisher = connect
        QwenTtsConnection.synthesize = cast(Any, synthesize)

    def restore(self) -> None:
        if self.agents._execute is self.execute_wrapper:
            self.agents._execute = self.original_execute
        if self.agents._play_host_audio is self.host_wrapper:
            self.agents._play_host_audio = self.original_host
        if self.agents._connect_publisher is self.connect_wrapper:
            self.agents._connect_publisher = self.original_connect
        if QwenTtsConnection.synthesize is self.synthesize_wrapper:
            QwenTtsConnection.synthesize = self.original_synthesize
        for source, original, wrapper in self.sources:
            if source.capture_frame is wrapper:
                source.capture_frame = original
        self.sources.clear()
        self.task_runs.clear()
        self.runs.clear()

    def receipt(self) -> dict[str, Any]:
        return {
            "status": "failed" if self.violations or self.overflow_count else "passed",
            "overflow_count": self.overflow_count,
            "event_limit": 256,
            "generation_limit": 128,
            "observed_event_count": len(self.entries),
            "entries": list(self.entries),
            "violations": list(self.violations),
            "audio_by_generation": deepcopy(self.audio),
            "synthesize_entries": sum(row["kind"] == "synthesize" for row in self.entries),
            "actual_pcm_samples": sum(row["pcm_samples"] for row in self.audio.values()),
        }


def late_tts_metadata_only(
    rows: list[dict[str, Any]],
    retirements: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    acknowledged_at: datetime,
    acknowledged_mono: float,
) -> bool:
    known = {row.get("generation_id") for row in retirements if row.get("generation_id")}
    for row in rows:
        generation = row.get("generation_id")
        if generation not in known or not isinstance(row.get("started_at"), str):
            return False
        try:
            started = datetime.fromisoformat(row["started_at"])
        except ValueError:
            return False
        if started > acknowledged_at:
            return False
        if any(
            event.get("kind") == "synthesize"
            and event.get("generation_id") == generation
            and (
                event.get("at_mono", acknowledged_mono) >= acknowledged_mono
                or event.get("actor_status") != "RUNNING"
            )
            for event in entries
        ):
            return False
    return True


class ScenarioDriver:
    def __init__(self, scenario: str, factory: Any, settings: Any, values: dict[str, Any]):
        self.scenario, self.factory, self.settings, self.values = (
            scenario,
            factory,
            settings,
            values,
        )
        self.phase = 0
        self.reset_action_index = -1
        self.reset_ids: list[UUID] = []
        self.reset_commits: list[dict[str, Any]] = []
        self.retirements: list[dict[str, Any]] = []
        self.active_operation_id: str | None = None
        self.prior_cancel_run: Any = None
        self.cancel_observer: Any = None
        self.operations: list[dict[str, Any]] = []
        self.operation_acks: dict[str, float] = {}
        self.media: MediaObservation | None = None
        self.tts_window: dict[str, Any] = {}
        self.manager: Any = None
        self.agents: Any = None
        self.writer: Any = None
        self.coordinator: Any = None
        self.timeout_id: UUID | None = None
        self.timeout_used = False
        self.placeholder_tts_calls = -1

    async def bind(self, *, manager: Any, agents: Any, limiter: Any, writer: Any) -> None:
        self.manager, self.agents, self.writer = manager, agents, writer
        self.coordinator = MatchEmergencyCoordinator(
            settings=self.settings,
            session_factory=self.factory,
            manager=manager,
            limiter=limiter,
            capture_writer=writer,
        )
        manager.set_terminal_callback(self.coordinator.cancel)
        self.media = MediaObservation(manager, agents, self.operation_acks)
        self.media.install()
        self.prior_cancel_run = agents._cancel_run

        async def observe_cancel(run: Any) -> None:
            actor = manager._actors.get(run.match_id)
            candidate = actor.state.emergency_operation if actor is not None else None
            operation = cast(dict[str, Any], candidate) if isinstance(candidate, dict) else {}
            if (
                operation.get("id") == self.active_operation_id
                and operation.get("kind") in {"speech.reset", "stage.reset", "speech.skip"}
                and operation.get("action_key") == run.action_key
                and operation.get("agent_profile_id") == str(run.agent_profile_id)
            ):
                capture = run.llm_capture
                self.retirements.append(
                    {
                        "source": "actual_cancel_run_entry",
                        "operation_id": self.active_operation_id,
                        "match_id": str(run.match_id),
                        "generation_id": str(run.generation_id) if run.generation_id else None,
                        "speech_id": str(run.speech_id) if run.speech_id else None,
                        "played_samples": run.played_samples,
                        "capture_present": capture is not None,
                        "request_captured": capture.request is not None
                        if capture is not None
                        else None,
                    }
                )
            await self.prior_cancel_run(run)

        self.cancel_observer = observe_cancel
        agents._cancel_run = observe_cancel

    async def admin(
        self, actor: Any, action: Literal["reset_speech", "reset_stage", "skip_speech"]
    ) -> dict[str, Any]:
        state = actor.state
        async with self.factory() as session:
            match = await session.get(Match, state.match_id)
            context = match.context_version
        operation_id = uuid4()
        payload = MatchControlRequest(
            action=action,
            operation_id=operation_id,
            expected_sequence=state.sequence,
            expected_action_key=state.current_action.action_key,
            expected_stage_generation=state.stage_generation,
            expected_speech_id=state.current_speech_id,
            expected_context_version=context,
        )
        request = Request(
            {
                "type": "http",
                "app": SimpleNamespace(
                    state=SimpleNamespace(
                        match_runtime_manager=self.manager,
                        match_emergency_coordinator=self.coordinator,
                    )
                ),
            }
        )
        started = monotonic()
        self.active_operation_id = str(operation_id)
        try:
            async with self.factory() as session:
                result = await control_match(
                    state.match_id,
                    payload,
                    request,
                    AuthContext(self.values["qa_admin"], "ADMIN", uuid4(), False),
                    session,
                )
            self.operation_acks[str(operation_id)] = monotonic()
        finally:
            self.active_operation_id = None
        row = {
            "action": action,
            "operation_id": str(operation_id),
            "target_speech_id": str(state.current_speech_id) if state.current_speech_id else None,
            "target_action_key": state.current_action.action_key,
            "target_seat_no": state.current_speaker_seat_no or state.current_action.seat_no,
            "target_side": state.current_speaker_side or state.current_action.side,
            "before_sequence": state.sequence,
            "after_sequence": actor.state.sequence,
            "ack_elapsed_ms": round((monotonic() - started) * 1000),
            "response_status": result["status"],
            "acknowledged_at": datetime.now(UTC).isoformat(),
            "acknowledged_mono": self.operation_acks[str(operation_id)],
        }
        if action in {"reset_speech", "reset_stage"}:
            async with self.factory() as session:
                event = await session.scalar(
                    select(full.StoredEvent)
                    .where(
                        full.StoredEvent.match_id == state.match_id,
                        full.StoredEvent.sequence > state.sequence,
                        full.StoredEvent.sequence <= actor.state.sequence,
                        full.StoredEvent.event_type
                        == ("speech.reset" if action == "reset_speech" else "stage.reset"),
                    )
                    .order_by(full.StoredEvent.sequence.desc())
                    .limit(1)
                )
                assert event is not None
                assert event.payload.get("operation_id", event.payload.get("id")) == str(
                    operation_id
                )
                target = await session.get(Speech, state.current_speech_id)
                assert target is not None and target.status == "RESET"
                self.reset_commits.append(
                    {
                        "action": action,
                        "operation_id": str(operation_id),
                        "speech_id": str(target.id),
                        "generation_id": (
                            str(target.generation_id) if target.generation_id else None
                        ),
                        "committed_at": event.created_at,
                        "event_sequence": event.sequence,
                    }
                )
                row["reset_event_at"] = event.created_at.isoformat()
                row["reset_event_sequence"] = event.sequence
        self.operations.append(row)
        return row

    async def resume(self, actor: Any) -> None:
        started = monotonic()
        await full.route_command(self.manager, self.factory, self.values, actor, "match.resume")
        async with asyncio.timeout(12):
            while actor.state.status != "RUNNING":
                if actor.state.status != "PAUSED":
                    raise RuntimeError("qa_recovery_resume_failed")
                await asyncio.sleep(0.05)
        elapsed = round((monotonic() - started) * 1000)
        assert elapsed >= 2900
        self.operations[-1]["natural_resume_ms"] = elapsed

    async def tick(self, actor: Any, publishers: Any, pcm: Any) -> bool:
        if self.scenario != "recovery":
            return False
        state, action = actor.state, actor.state.current_action
        if action is None or action.action_kind != "AGENT_SPEECH":
            return False
        if self.phase in (0, 1):
            if state.action_state != "AGENT_SPEAKING":
                return False
            if self.phase == 1 and state.current_action_index < self.reset_action_index:
                return False
            speech_id = state.current_speech_id
            assert speech_id is not None
            if speech_id in self.reset_ids:
                return False
            # Actual provider text and first PCM have already arrived. Do not
            # fabricate an old result or call its callback from the probe.
            await asyncio.sleep(0.15)
            if (
                actor.state.current_speech_id != speech_id
                or actor.state.action_state != "AGENT_SPEAKING"
            ):
                return False
            control: Literal["reset_speech", "reset_stage"] = (
                "reset_speech" if self.phase == 0 else "reset_stage"
            )
            self.reset_ids.append(speech_id)
            self.reset_action_index = state.current_action_index
            await self.admin(actor, control)
            async with self.factory() as session:
                old = await session.get(Speech, speech_id)
                assert old.status == "RESET"
                assert old.display_text or old.llm_draft_text
            assert actor.state.current_speech_id != speech_id
            if control == "reset_stage" or str(actor.state.action_state) == "AGENT_PREPARING":
                assert actor.state.interim_text == ""
            self.phase += 1
            assert actor.state.status == "PAUSED"
            await self.resume(actor)
            return True
        if (
            self.phase == 2
            and state.current_action_index > self.reset_action_index
            and state.action_state in {"AGENT_PREPARING", "AGENT_SPEAKING"}
        ):
            assert action.seat_no == 3, "qa_skip_must_preserve_both_fourth_speakers"
            before_operation = await self.tts_rows(state.match_id)
            row = await self.admin(actor, "skip_speech")
            assert actor.state.status == "PAUSED"
            at_ack = await self.tts_rows(state.match_id)
            async with asyncio.timeout(55):
                while (
                    cast(dict[str, Any], actor.state.emergency_operation or {}).get("status")
                    == "PENDING"
                ):
                    await asyncio.sleep(0.05)
            operation = cast(dict[str, Any], actor.state.emergency_operation or {})
            assert (
                operation.get("id") == row["operation_id"]
                and operation.get("status") == "SUCCEEDED"
            )
            after = await self.tts_rows(state.match_id)
            old_ids, ack_ids = {item["id"] for item in before_operation}, {
                item["id"] for item in at_ack
            }
            assert old_ids <= ack_ids <= {item["id"] for item in after}
            added_after_ack = [item for item in after if item["id"] not in ack_ids]
            self.placeholder_tts_calls = sum(
                item["operation"] == "admin.speech.placeholder"
                or str(row["operation_id"])
                in {item["generation_id"], item["logical_call_id"], item["source_resource_id"]}
                for item in after
            )
            self.tts_window = {
                "operation_id": row["operation_id"],
                "global_count_before_operation": len(before_operation),
                "global_count_at_ack": len(at_ack),
                "global_count_after_placeholder": len(after),
                "global_count_delta_after_ack": len(after) - len(at_ack),
                "new_rows_during_operation": [item for item in after if item["id"] not in old_ids],
                "new_rows_after_ack": added_after_ack,
                "exact_placeholder_tts_calls": self.placeholder_tts_calls,
            }
            assert self.media is not None
            explained = late_tts_metadata_only(
                added_after_ack,
                self.retirements,
                self.media.entries,
                datetime.fromisoformat(row["acknowledged_at"]),
                float(row["acknowledged_mono"]),
            )
            self.tts_window["late_metadata_explained"] = explained
            assert self.placeholder_tts_calls == 0
            assert explained and self.media.receipt()["status"] == "passed"
            self.phase = 3
            await self.resume(actor)
            return True
        return False

    async def tts_rows(self, match_id: UUID) -> list[dict[str, Any]]:
        async with self.factory() as session:
            calls = list(
                (
                    await session.scalars(
                        select(ExternalCall)
                        .where(ExternalCall.match_id == match_id, ExternalCall.call_kind == "TTS")
                        .order_by(ExternalCall.started_at)
                        .limit(256)
                    )
                ).all()
            )
            assert len(calls) < 256
            return [
                {
                    "id": str(call.id),
                    "generation_id": str(call.generation_id) if call.generation_id else None,
                    "speech_id": str(call.speech_id) if call.speech_id else None,
                    "logical_call_id": str(call.logical_call_id) if call.logical_call_id else None,
                    "source_resource_id": call.source_resource_id,
                    "operation": call.operation,
                    "status": call.status,
                    "error_code": call.error_code,
                    "started_at": call.started_at.isoformat(),
                    "completed_at": call.completed_at.isoformat() if call.completed_at else None,
                }
                for call in calls
            ]

    async def speak(
        self,
        original: Any,
        manager: Any,
        actor: Any,
        source: Any,
        pcm: bytes,
        interruption: Any = None,
    ) -> None:
        if self.scenario != "timeout" or self.timeout_used:
            return await original(manager, actor, source, pcm, interruption)
        self.timeout_used = True
        before_sequence = actor.state.sequence
        await original(manager, actor, source, pcm, interruption, finish_when_done=False)
        async with self.factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(full.StoredEvent)
                        .where(
                            full.StoredEvent.match_id == actor.state.match_id,
                            full.StoredEvent.sequence > before_sequence,
                            full.StoredEvent.event_type == "speech.finalizing",
                        )
                        .order_by(full.StoredEvent.sequence)
                    )
                ).all()
            )
        assert len(rows) == 1 and rows[0].payload.get("reason") == "TIME_LIMIT"
        self.timeout_id = UUID(rows[0].payload["speech_id"])

    async def verify(self, actor: Any, speeches: list[Any], events: list[Any]) -> dict[str, Any]:
        assert self.media is not None and self.media.receipt()["status"] == "passed"
        receipt: dict[str, Any] = {
            "scenario": self.scenario,
            "status": "passed",
            "operations": self.operations,
            "media_observation": self.media.receipt(),
            "tts_window": self.tts_window,
        }
        if self.scenario == "normal":
            return receipt
        if self.scenario == "timeout":
            assert self.timeout_used and self.timeout_id is not None
            timed = [
                event
                for event in events
                if event.event_type == "speech.finalizing"
                and event.payload.get("speech_id") == str(self.timeout_id)
                and event.payload.get("reason") == "TIME_LIMIT"
            ]
            assert len(timed) == 1
            receipt.update(
                natural_time_limit_count=1,
                speech_id=str(self.timeout_id),
                continuous_pcm_until_authoritative_deadline=True,
            )
            return receipt
        assert self.phase == 3 and len(self.reset_ids) == 2
        assert len(self.reset_commits) == 2
        fourth = [
            speech
            for speech in speeches
            if speech.seat_no == 4
            and speech.opportunity_id is None
            and speech.status == "FINALIZED"
            and speech.origin == "ORIGINAL"
        ]
        assert len(fourth) == 2 and {speech.side for speech in fourth} == {
            "AFFIRMATIVE", "NEGATIVE"
        }
        assert all(speech.audio_storage_path and speech.audio_duration_ms > 0 for speech in fourth)
        reset_ids = {str(value) for value in self.reset_ids}
        assert all(
            next(s for s in speeches if s.id == value).status == "RESET" for value in self.reset_ids
        )
        placeholders = [s for s in speeches if s.origin == "ADMIN_PLACEHOLDER"]
        assert (
            len(placeholders) == 1
            and placeholders[0].status == "FINALIZED"
            and not placeholders[0].audio_storage_path
        )
        skip_id = self.operations[-1]["operation_id"]
        assert not any(
            row["speech_id"] == str(placeholders[0].id)
            or row["operation"] == "admin.speech.placeholder"
            or skip_id
            in {row["generation_id"], row["logical_call_id"], row["source_resource_id"]}
            for row in await self.tts_rows(actor.state.match_id)
        )
        async with asyncio.timeout(8):
            while self.writer.stats()["items"]:
                await asyncio.sleep(0.05)
        async with self.factory() as session:
            transcript = await get_match_transcript(
                actor.state.match_id,
                AuthContext(self.values["qa_admin"], "ADMIN", uuid4(), False),
                session,
            )
            assert not reset_ids.intersection(str(item.id) for item in transcript.speeches)
            calls = list(
                (
                    await session.scalars(
                        select(ExternalCall).where(
                            ExternalCall.match_id == actor.state.match_id,
                            ExternalCall.call_kind == "LLM_SPEECH",
                            ExternalCall.started_at >= self.reset_commits[0]["committed_at"],
                        )
                    )
                ).all()
            )
            contexts: list[dict[str, Any]] = []
            succeeded_placeholder = False
            window_counts = [0, 0]
            presend_exceptions: list[dict[str, Any]] = []
            for call in calls:
                if call.operation == "admin.speech.placeholder":
                    succeeded_placeholder |= call.status == "SUCCEEDED"
                projection = await capture_projection(session, call)
                assert projection.get("request_blob_id") is not None
                raw = await load_content_blob(session, projection["request_blob_id"])
                call_contexts = nested_contexts(raw)
                if not call_contexts:
                    assert explicit_presend_retirement(call, projection, raw, self.retirements)
                    presend_exceptions.append(
                        {"call_id": str(call.id), "generation_id": str(call.generation_id)}
                    )
                else:
                    assert projection.get("request_capture_status") == "COMPLETE"
                assert reset_history_valid(call_contexts, call.started_at, self.reset_commits)
                if call.status == "SUCCEEDED":
                    assert call_contexts
                    window_index = max(
                        index
                        for index, reset in enumerate(self.reset_commits)
                        if reset["committed_at"] <= call.started_at
                    )
                    window_counts[window_index] += 1
                contexts.extend(call_contexts)
            assert contexts and succeeded_placeholder
            assert all(window_counts)
            assert any(
                item.get("speech_id") == str(placeholders[0].id)
                and item.get("origin") == "ADMIN_PLACEHOLDER"
                and item.get("delivery") == "NOT_SPOKEN_ADMIN_PLACEHOLDER"
                for ctx in contexts
                for item in history_items(ctx)
            )
            judge = await session.scalar(
                select(JudgeResult)
                .where(JudgeResult.match_id == actor.state.match_id)
                .order_by(JudgeResult.created_at.desc())
                .limit(1)
            )
            assert judge and judge.status == "SUCCEEDED"
            incidents = judge.input_snapshot.get("administrative_incidents", [])
            assert (
                len(incidents) == 1
                and incidents[0].get("speech_id") == str(placeholders[0].id)
                and incidents[0].get("spoken") is False
            )
            assert placeholders[0].display_text not in json.dumps(
                judge.input_snapshot.get("history", []), ensure_ascii=False
            )
        sources = await _load_sources(
            self.factory,
            match_id=actor.state.match_id,
            host_storage_root=Path(self.settings.host_audio_storage_dir),
            speech_storage_root=Path(self.settings.agent_audio_storage_dir),
        )
        paths = {str(source.path) for source in sources}
        for speech in speeches:
            if speech.status == "RESET" and speech.audio_storage_path:
                assert (
                    str(Path(self.settings.agent_audio_storage_dir) / speech.audio_storage_path)
                    not in paths
                )
        receipt.update(
            speech_reset_count=1,
            stage_reset_count=1,
            placeholder_count=1,
            placeholder_provider_succeeded=succeeded_placeholder,
            placeholder_tts_calls=self.placeholder_tts_calls,
            reset_excluded_from_transcript=True,
            reset_excluded_from_agent_history=True,
            reset_excluded_from_replay=True,
            placeholder_excluded_from_judge_performance=True,
            placeholder_labelled_in_agent_history=True,
            actual_request_contexts_checked=len(contexts),
            fourth_speakers_spoken={speech.side: speech.audio_duration_ms for speech in fourth},
            observed_retirements=self.retirements,
            explicit_presend_capture_exceptions=presend_exceptions,
            reset_context_windows=[
                {
                    **row,
                    "committed_at": row["committed_at"].isoformat(),
                    "successful_calls_checked": window_counts[index],
                }
                for index, row in enumerate(self.reset_commits)
            ],
        )
        return receipt

    async def close(self) -> None:
        if self.coordinator is not None:
            await self.coordinator.close()
        if self.agents is not None and self.agents._cancel_run is self.cancel_observer:
            self.agents._cancel_run = self.prior_cancel_run

    def diagnostics(self) -> dict[str, Any]:
        return {
            "operations": self.operations,
            "tts_window": self.tts_window,
            "media_observation": self.media.receipt() if self.media is not None else None,
        }

    def after_close(self) -> dict[str, Any]:
        if self.media is not None:
            self.media.restore()
        return self.diagnostics()


async def run_scenario(settings: Any, owned: Any, url: str, scenario: str) -> dict[str, Any]:
    import server_spec304_host_fixture as host_fixture

    original_load, original_draft = full.load_resources, full.short_draft
    original_prepare, original_speak, original_run_match = (
        full.prepare_room,
        full.speak,
        full.run_match,
    )
    driver: ScenarioDriver | None = None
    host_boundary_inputs: dict[str, Any] = {}

    async def load(database: Any) -> Any:
        return await host_fixture.load_resources(database, original=original_load)

    async def prepare(factory: Any, qa_settings: Any, *args: Any) -> Any:
        nonlocal driver
        values = await original_prepare(factory, qa_settings, *args)
        values["conference_host_boundary_inputs"] = deepcopy(host_boundary_inputs)
        await host_fixture.verify_prepared(factory, qa_settings, values)
        if scenario == "recovery":
            admin, source = await owned_admin(factory, owned, url)
            values.update(
                qa_admin=admin,
                qa_admin_fixture=source,
                expected_admin_placeholders=1,
                expected_host_replays=1,
            )
        driver = ScenarioDriver(scenario, factory, qa_settings, values)
        values["qa_driver"] = driver
        return values

    async def speak(*args: Any, **kwargs: Any) -> None:
        assert driver is not None
        await driver.speak(original_speak, *args, **kwargs)

    async def match(*args: Any, **kwargs: Any) -> Any:
        receipt = await original_run_match(*args, **kwargs)
        receipt["conference_host_fixture"] = args[2]["conference_host_fixture"]
        return receipt

    full.load_resources, full.prepare_room, full.speak, full.run_match = load, prepare, speak, match

    def draft(tag: str, *flags: Any) -> Any:
        return host_fixture.short_draft(
            original_draft, tag, *flags, boundary_receipt=host_boundary_inputs
        )

    full.short_draft = draft
    try:
        return await full.run(settings, owned, url, topical_microphones=True)
    finally:
        full.load_resources, full.short_draft = original_load, original_draft
        full.prepare_room, full.speak, full.run_match = (
            original_prepare,
            original_speak,
            original_run_match,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("normal", "timeout", "recovery"), required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    args = parser.parse_args()
    candidate = args.candidate_root.resolve()
    import jx_core.matches.service as candidate_service

    if not Path(candidate_service.__file__).resolve().is_relative_to(candidate):
        raise RuntimeError("qa_business_code_not_from_candidate")
    logging.basicConfig(level=logging.CRITICAL)
    settings = load_settings()
    owned = full.OwnedPostgres()
    receipt: dict[str, Any] = {"status": "failed"}
    started = monotonic()
    source = Path(candidate_service.__file__)
    before_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    try:
        url = owned.start()
        owned.migrate(url, repo_root=candidate)
        receipt = asyncio.run(run_scenario(settings, owned, url, args.scenario))
    except Exception as error:
        receipt = full.error_receipt(error)
    finally:
        try:
            receipt["owned_postgres_and_audio_removed"] = owned.close()
        except Exception as error:
            receipt["cleanup_error"] = full.error_receipt(error)
            receipt["owned_postgres_and_audio_removed"] = False
        receipt.update(
            scenario=args.scenario,
            elapsed_ms=round((monotonic() - started) * 1000),
            candidate_service_sha256=before_sha,
            candidate_source_unchanged=hashlib.sha256(source.read_bytes()).hexdigest()
            == before_sha,
            qa_python_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        )
    if (
        not strict_scenario(receipt, args.scenario)
        or not cleanup_complete(receipt)
        or not receipt["candidate_source_unchanged"]
    ):
        receipt["status"] = "failed"
        receipt.setdefault("error_code", "qa_spec304_strict_gate_failed")
    print(json.dumps({"receipt": receipt}, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

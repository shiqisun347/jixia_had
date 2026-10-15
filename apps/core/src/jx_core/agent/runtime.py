"""Fixed-stage Agent LLM -> TTS -> PyAV -> LiveKit runtime."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from collections import OrderedDict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from uuid import UUID, uuid4

from livekit import api, rtc
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings
from ..data_capture.content import CAPTURE_VERSION, store_content_blob
from ..data_capture.provider import PROVIDER_CAPTURE_VERSION, ProviderCallCapture
from ..data_capture.provider_writer import (
    CaptureSubmission,
    ProviderCaptureWriter,
    new_provider_capture,
    prepare_capture,
    submit_capture,
)
from ..experiments.prompts import (
    EXPERIMENT_PROMPT_VERSION,
    render_experiment_decision_prompt,
    render_experiment_speech_prompt,
)
from ..host_audio_timing import (
    HOST_AUDIO_ATTEMPT_CLEANUP_TIMEOUT_SECONDS,
    HOST_AUDIO_ATTEMPTS,
    HOST_AUDIO_RETRY_BACKOFF_SECONDS,
    host_audio_attempt_timeout_seconds,
)
from ..models import (
    AgentAudioAsset,
    AgentFreeDebateDecision,
    AgentGeneration,
    AgentProfile,
    ExperimentMatchAttempt,
    ExternalCall,
    FreeDebateOpportunity,
    Match,
    MatchParticipant,
    ModelProfile,
    Room,
    Speech,
    VoiceProfile,
)
from ..runtime_identity import CallbackEnvelope
from ..security.crypto import decrypt_secret
from .audio import IncrementalOpusDecoder, apply_pcm16_gain
from .llm import (
    LLM_FAST_DECISION_TIMEOUT_SECONDS,
    LLM_MAX_COMPLETION_TOKENS,
    LlmCapacityLimiter,
    LlmProviderError,
    OpenAIStreamingClient,
    iter_text_queue,
)
from .prompt_context import (
    AGENT_CONTEXT_POLICY_VERSION,
    HISTORY_REFERENCE,
    assemble_agent_messages,
)
from .tts import QwenTtsConnection, TtsProviderError, TtsStreamResult
from .voice_compatibility import effective_tts_voice

logger = logging.getLogger("jx-core.agent")
AGENT_TASK_CANCEL_TIMEOUT_SECONDS = 3.0
AGENT_TTS_QUEUE_SIZE = 32
AGENT_PROVIDER_RETRY_BACKOFF_SECONDS = 0.5
AGENT_RESOURCE_CLEANUP_TIMEOUT_SECONDS = 0.5
CLOSED_MATCH_TOMBSTONE_LIMIT = 4096
AGENT_HISTORY_CHAR_BUDGET = 24_000
AGENT_HISTORY_ENTRY_OVERHEAD_CHARS = 256
AGENT_HISTORY_RECORD_LIMIT = 128
_PROMPT_VARIABLE_RE = re.compile(r"{{\s*([A-Z][A-Z0-9_]*)\s*}}")


def _snapshot_agent(
    snapshot: dict[str, Any] | None, agent_profile_id: UUID
) -> dict[str, Any] | None:
    if not snapshot:
        return None
    for item in cast(list[object], snapshot.get("agents", [])):
        if isinstance(item, dict):
            mapping = cast(dict[str, Any], item)
            if str(mapping.get("id")) == str(agent_profile_id):
                return mapping
    return None


def _snapshot_prompt(
    snapshot: dict[str, Any] | None,
    *,
    agent_profile_id: UUID,
    stage_key: str,
    purpose: str,
) -> dict[str, Any] | None:
    if not snapshot:
        return None
    candidates: list[dict[str, Any]] = []
    for item in cast(list[object], snapshot.get("prompts", [])):
        if not isinstance(item, dict):
            continue
        mapping = cast(dict[str, Any], item)
        if mapping.get("stage_key") == stage_key and mapping.get("purpose") == purpose:
            candidates.append(mapping)
    return next(
        (item for item in candidates if str(item.get("agent_profile_id")) == str(agent_profile_id)),
        next((item for item in candidates if item.get("agent_profile_id") is None), None),
    )


def _render_snapshot_prompt(template: str, variables: dict[str, str]) -> str:
    missing = sorted(set(_PROMPT_VARIABLE_RE.findall(template)) - set(variables))
    if missing:
        raise LlmProviderError("format_prompt_variable_missing")
    return _PROMPT_VARIABLE_RE.sub(lambda match: variables[match.group(1)], template)


def _bound_history(
    history: list[dict[str, Any]], *, priority_speech_id: str | None = None
) -> tuple[list[dict[str, Any]], bool]:
    """Keep recent complete speeches within a prompt memory budget."""

    if not history:
        return [], False

    def entry_cost(item: dict[str, Any]) -> int:
        if item.get("speech_id"):
            return len(json.dumps(item, ensure_ascii=False)) + 2
        return len(str(item.get("content") or "")) + AGENT_HISTORY_ENTRY_OVERHEAD_CHARS

    if priority_speech_id is not None:
        priority = next(
            (item for item in history if item.get("speech_id") == priority_speech_id), None
        )
        if priority is not None:
            selected_ids = {id(priority)}
            remaining = AGENT_HISTORY_CHAR_BUDGET - entry_cost(priority)
            for item in reversed(history):
                if item is priority:
                    continue
                cost = entry_cost(item)
                if cost > remaining:
                    break
                selected_ids.add(id(item))
                remaining -= cost
            selected = [item for item in history if id(item) in selected_ids]
            truncated = len(selected) < len(history)
            return selected, truncated
    remaining = AGENT_HISTORY_CHAR_BUDGET
    selected: list[dict[str, Any]] = []
    truncated = False
    for item in reversed(history):
        cost = entry_cost(item)
        # Keep the newest speech even when it alone is larger than the budget;
        # stage limits bound a single entry and preserving it is more useful
        # than dropping the current opponent response entirely.
        if selected and cost > remaining:
            truncated = True
            break
        selected.append(item)
        remaining -= cost
    if len(selected) < len(history):
        truncated = True
    selected.reverse()
    if truncated:
        selected.insert(
            0,
            {
                "stage": "context",
                "speaker": "SYSTEM",
                "side": None,
                "seat_no": None,
                "speaker_kind": "SYSTEM",
                "agent_profile_id": None,
                "content": "（更早的发言已省略，仅保留最近完整记录）",
            },
        )
    return selected, truncated


async def _ignore_delta(_: str) -> None:
    return


async def _close_llm_client(client: OpenAIStreamingClient) -> None:
    await _bounded_agent_cleanup(client.close(), resource="llm_client")


def _consume_detached_cleanup(task: asyncio.Task[Any]) -> None:
    """Consume the result of a cleanup task detached after its deadline."""

    try:
        task.exception()
    except BaseException:
        # Cancelled tasks and cleanup failures are already represented by the
        # bounded-cleanup diagnostic; never emit an unhandled-task warning.
        pass


def _warn_agent_cleanup_failure(error: BaseException, *, resource: str) -> None:
    logger.warning(
        "agent media resource cleanup failed",
        extra={
            "error_code": "agent_resource_cleanup_failed",
            "resource": resource,
            "exception_type": type(error).__name__,
        },
    )


def _try_agent_cleanup(action: Callable[[], object], *, resource: str) -> None:
    """Isolate synchronous native/file cleanup errors from the remaining resources."""

    try:
        action()
    except Exception as error:
        _warn_agent_cleanup_failure(error, resource=resource)


async def _bounded_agent_cleanup_call(
    close: Callable[[], Coroutine[Any, Any, Any]], *, resource: str
) -> None:
    try:
        awaitable = close()
    except Exception as error:
        _warn_agent_cleanup_failure(error, resource=resource)
        return
    await _bounded_agent_cleanup(awaitable, resource=resource)


async def _bounded_agent_cleanup(awaitable: Coroutine[Any, Any, Any], *, resource: str) -> None:
    """Best-effort close a media resource without blocking the actor.

    LiveKit room disconnect and decoder shutdown can wait on native/FFI work.
    A stuck close must not keep the match actor or retry path occupied.  On a
    deadline we cancel the child and consume its eventual result via a
    callback, allowing the caller to release its resource reference and move
    on to the remaining cleanup steps.
    """

    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait({task}, timeout=AGENT_RESOURCE_CLEANUP_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        task.cancel()
        task.add_done_callback(_consume_detached_cleanup)
        raise
    if not done:
        task.cancel()
        task.add_done_callback(_consume_detached_cleanup)
        logger.warning(
            "agent media resource cleanup timed out",
            extra={
                "error_code": "agent_resource_cleanup_timeout",
                "resource": resource,
            },
        )
        return
    try:
        task.result()
    except BaseException as error:
        _warn_agent_cleanup_failure(error, resource=resource)


def _schedule_publisher_startup_cleanup(
    room: rtc.Room, source: rtc.AudioSource | None
) -> list[asyncio.Task[None]]:
    """Start every owned close before a caller can be cancelled again."""

    cleanup: list[asyncio.Task[None]] = []
    if source is not None:
        cleanup.append(
            asyncio.create_task(
                _bounded_agent_cleanup_call(source.aclose, resource="publisher_startup_source")
            )
        )
    cleanup.append(
        asyncio.create_task(
            _bounded_agent_cleanup_call(room.disconnect, resource="publisher_startup_room")
        )
    )
    for task in cleanup:
        task.add_done_callback(_consume_detached_cleanup)
    if source is not None:
        try:
            source.clear_queue()
        except BaseException as error:
            # Only this synchronous rollback action is isolated. It must not
            # replace a forward failure or prevent the already scheduled closes.
            _warn_agent_cleanup_failure(error, resource="publisher_startup_queue")
    return cleanup


def _retired_publisher_connection_finished(task: asyncio.Task[None], *, room: rtc.Room) -> None:
    _consume_detached_cleanup(task)
    # The SDK must finish its callback/ready handshake before a retired join
    # can disconnect. This callback owns only its old Room, never a replacement
    # run, Actor, model/session, AudioSource or publication.
    _schedule_publisher_startup_cleanup(room, None)


async def _bounded_decision_capture(
    awaitable: Coroutine[Any, Any, UUID | None], *, phase: str
) -> UUID | None:
    return await _bounded_provider_capture(awaitable, call_kind="decision", phase=phase)


async def _bounded_provider_capture(
    awaitable: Coroutine[Any, Any, UUID | None],
    *,
    call_kind: Literal["decision", "llm", "tts", "generation"],
    phase: str,
    match_id: UUID | None = None,
    generation_id: UUID | None = None,
) -> UUID | None:
    """Bound diagnostic persistence independently of the provider lifecycle."""

    label = {
        "decision": "decision",
        "llm": "Agent LLM",
        "tts": "TTS",
        "generation": "Agent generation",
    }[call_kind]
    association = {
        key: str(value)
        for key, value in {"match_id": match_id, "generation_id": generation_id}.items()
        if value is not None
    }
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait({task}, timeout=AGENT_RESOURCE_CLEANUP_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        task.cancel()
        task.add_done_callback(_consume_detached_cleanup)
        raise
    if not done:
        task.cancel()
        task.add_done_callback(_consume_detached_cleanup)
        logger.warning(
            "%s call capture timed out",
            label,
            extra={"error_code": f"{call_kind}_capture_timeout", "phase": phase, **association},
        )
        return None
    try:
        return task.result()
    except BaseException as error:
        logger.warning(
            "%s call capture failed",
            label,
            extra={
                "error_code": f"{call_kind}_capture_failed",
                "phase": phase,
                "exception_type": type(error).__name__,
                **association,
            },
        )
        return None


def _parse_decision(text: str) -> tuple[bool, float]:
    candidate = text.strip().removeprefix("```").removeprefix("json").removesuffix("```").strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise LlmProviderError("agent_decision_invalid") from error
    value = cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}
    if not isinstance(value.get("should_speak"), bool):
        raise LlmProviderError("agent_decision_invalid")
    willingness_value = value.get("willingness")
    if not isinstance(willingness_value, (int, float)):
        raise LlmProviderError("agent_decision_invalid")
    should_speak = cast(bool, value["should_speak"])
    return should_speak, max(0.0, min(1.0, float(willingness_value)))


def _parse_experiment_decision(text: str) -> tuple[bool, str]:
    try:
        parsed = json.loads(text.strip())
    except json.JSONDecodeError as error:
        raise LlmProviderError("agent_decision_invalid") from error
    value = cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}
    reason = value.get("decision_reason")
    if set(value) != {"should_speak", "decision_reason"} or not isinstance(
        value.get("should_speak"), bool
    ):
        raise LlmProviderError("agent_decision_invalid")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 20:
        raise LlmProviderError("agent_decision_invalid")
    return cast(bool, value["should_speak"]), reason.strip()


class AgentRuntimeCallbacks(Protocol):
    async def report_free_decision(
        self,
        *,
        envelope: CallbackEnvelope,
        action_key: str,
        agent_profile_id: UUID,
        side: str,
        decision_round_id: UUID,
        should_speak: bool | None,
        willingness: float | None,
        failed: bool,
        attempt_no: int,
        duration_ms: int,
        error_code: str | None,
        decision_reason: str | None = None,
    ) -> object: ...

    async def publish_agent_text_delta(self, *, envelope: CallbackEnvelope, text: str) -> None: ...

    async def agent_text_generated(self, *, envelope: CallbackEnvelope, text: str) -> None: ...

    async def publish_agent_subtitle(
        self, *, envelope: CallbackEnvelope, text: str, played_ms: int
    ) -> None: ...

    async def publish_agent_retry(self, *, envelope: CallbackEnvelope, error_code: str) -> None: ...

    async def start_agent_playback(
        self,
        *,
        envelope: CallbackEnvelope,
        agent_profile_id: UUID,
        audio_storage_path: str,
    ) -> object: ...

    async def finish_agent_playback(self, *, envelope: CallbackEnvelope) -> object: ...

    async def finalize_agent_speech(
        self,
        *,
        envelope: CallbackEnvelope,
        final_text: str,
        llm_draft_text: str,
        audio_storage_path: str,
        audio_duration_ms: int,
        audio_truncated: bool,
    ) -> object: ...

    async def handle_agent_failure(
        self, *, envelope: CallbackEnvelope, error_code: str, logical_call_id: UUID | None = None
    ) -> None: ...

    async def host_audio_finished(self, *, envelope: CallbackEnvelope, action_key: str) -> None: ...

    async def host_audio_failed(
        self, *, envelope: CallbackEnvelope, action_key: str, error_code: str
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class AgentConfig:
    match_id: UUID
    action_key: str
    agent_profile_id: UUID
    context_version: int
    match_seed: int
    model_key: str
    base_url: str
    model_id: str
    api_key: str
    model_limit: int
    generation_params: dict[str, Any]
    max_tokens: int
    messages: list[dict[str, str]]
    voice: str
    rate: float
    chars_per_second: float
    playback_gain: float
    experiment_mode: bool = False
    formal_4v4: bool = False
    decision_messages: list[dict[str, str]] | None = None
    prompt_version: str | None = None
    opportunity_id: UUID | None = None
    opportunity_generation: int | None = None
    context_policy_version: str | None = None
    history_truncated: bool = False


@dataclass(slots=True)
class AgentRun:
    match_id: UUID
    action_key: str
    agent_profile_id: UUID
    duration_ms: int
    side: str | None = None
    task: asyncio.Task[None] | None = None
    generation_id: UUID | None = None
    speech_id: UUID | None = None
    draft_parts: list[str] = field(default_factory=lambda: list[str]())
    spool_path: Path | None = None
    storage_path: Path | None = None
    byte_count: int = 0
    played_samples: int = 0
    chars_per_second: float = 4.0
    voice: str = ""
    rate: float = 1.0
    playback_gain: float = 1.0
    tts_task_id: UUID | None = None
    tts_call_id: UUID | None = None
    first_audio_latency_ms: int | None = None
    tts_completed_latency_ms: int | None = None
    natural_complete: bool = False
    finalizing: bool = False
    source: rtc.AudioSource | None = None
    room: rtc.Room | None = None
    decoder: IncrementalOpusDecoder | None = None
    text_queue: asyncio.Queue[str | None] | None = None
    tts_task: asyncio.Task[TtsStreamResult] | None = None
    logical_call_id: UUID = field(default_factory=uuid4)
    tts_logical_call_id: UUID = field(default_factory=uuid4)
    llm_capture: ProviderCallCapture | None = None
    tts_capture: ProviderCallCapture | None = None
    media_fenced: bool = False
    callback_envelope: CallbackEnvelope | None = None

    @property
    def draft(self) -> str:
        return "".join(self.draft_parts)


@dataclass(slots=True)
class HostAudioRun:
    match_id: UUID
    action_key: str
    storage_path: str
    duration_ms: int
    context_version: int
    generation_id: UUID = field(default_factory=uuid4)
    attempt_no: int = 0
    callback_envelope: CallbackEnvelope | None = None
    task: asyncio.Task[None] | None = None
    source: rtc.AudioSource | None = None
    room: rtc.Room | None = None
    decoder: IncrementalOpusDecoder | None = None
    media_fenced: bool = False
    spool_path: Path | None = None


class AgentRuntime:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        callbacks: AgentRuntimeCallbacks,
        limiter: LlmCapacityLimiter | None = None,
        capture_writer: ProviderCaptureWriter | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._callbacks = callbacks
        self._limiter = limiter or LlmCapacityLimiter(settings.llm_global_concurrency)
        self._capture_writer = capture_writer
        self._runs: dict[UUID, AgentRun] = {}
        self._host_runs: dict[UUID, HostAudioRun] = {}
        self._decision_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._tts_connections: dict[UUID, QwenTtsConnection] = {}
        self._pending_media_starts: dict[UUID, object] = {}
        self._lock = asyncio.Lock()
        self._media_lock = asyncio.Lock()
        self._closed = False
        self._closed_matches: OrderedDict[UUID, None] = OrderedDict()

    def _remember_closed_match_locked(self, match_id: UUID) -> None:
        self._closed_matches[match_id] = None
        self._closed_matches.move_to_end(match_id)
        while len(self._closed_matches) > CLOSED_MATCH_TOMBSTONE_LIMIT:
            self._closed_matches.popitem(last=False)

    def _accepts_match_locked(self, match_id: UUID) -> bool:
        return not self._closed and match_id not in self._closed_matches

    def retire_pending_media_start(self, match_id: UUID) -> None:
        """Retire an admission even while old resource cleanup still holds the lock."""
        self._pending_media_starts.pop(match_id, None)

    def _media_start_is_current(self, match_id: UUID, token: object) -> bool:
        return (
            self._accepts_match_locked(match_id)
            and self._pending_media_starts.get(match_id) is token
        )

    def _require_current_agent_generation(self, run: AgentRun, generation_id: UUID) -> None:
        """Reject a retired provider result without retrying or applying its payload."""

        envelope = run.callback_envelope
        if (
            not self._accepts_match_locked(run.match_id)
            or self._runs.get(run.match_id) is not run
            or run.media_fenced
            or run.generation_id != generation_id
            or (
                envelope is not None
                and (envelope.match_id != run.match_id or envelope.generation_id != generation_id)
            )
        ):
            logger.info(
                "retired Agent provider result discarded",
                extra={
                    "error_code": "agent_result_stale",
                    "match_id": str(run.match_id),
                    "generation_id": str(generation_id),
                },
            )
            raise asyncio.CancelledError

    def agent_callback_is_current(
        self,
        *,
        envelope: CallbackEnvelope,
        action_key: str,
        agent_profile_id: UUID,
    ) -> bool:
        """Identify the registered Agent attempt without ordering historical rows."""
        run = self._runs.get(envelope.match_id)
        return (
            self._accepts_match_locked(envelope.match_id)
            and run is not None
            and not run.media_fenced
            and run.action_key == action_key
            and run.agent_profile_id == agent_profile_id
            and envelope.generation_id is not None
            and run.generation_id == envelope.generation_id
            and run.speech_id == envelope.speech_id
            and run.callback_envelope == envelope
            and envelope.connection_epoch is None
        )

    def agent_startup_callback_is_current(
        self,
        *,
        envelope: CallbackEnvelope,
        logical_call_id: UUID,
        action_key: str,
        agent_profile_id: UUID,
    ) -> bool:
        """Identify a configuration failure before a generation row exists."""
        run = self._runs.get(envelope.match_id)
        return (
            self._accepts_match_locked(envelope.match_id)
            and run is not None
            and not run.media_fenced
            and run.logical_call_id == logical_call_id
            and run.action_key == action_key
            and run.agent_profile_id == agent_profile_id
            and run.generation_id is None
            and envelope.generation_id is None
            and run.speech_id is None
            and envelope.speech_id is None
            and run.callback_envelope == envelope
            and envelope.connection_epoch is None
        )

    def agent_text_callback_is_current(
        self,
        *,
        envelope: CallbackEnvelope,
        action_key: str,
        agent_profile_id: UUID,
    ) -> bool:
        """Keep pre-playback text bound to the same generation after first PCM."""
        run = self._runs.get(envelope.match_id)
        current = run.callback_envelope if run is not None else None
        if current is None or not self.agent_callback_is_current(
            envelope=current, action_key=action_key, agent_profile_id=agent_profile_id
        ):
            return False
        # Normalize only the comparison. The producer's immutable envelope
        # remains unchanged when its bounded pending text is consumed.
        return current == envelope or (
            envelope.speech_id is None
            and current.speech_id is not None
            and replace(current, speech_id=None) == envelope
        )

    @property
    def capacity_limiter(self) -> LlmCapacityLimiter:
        return self._limiter

    @staticmethod
    def _run_envelope(run: AgentRun, *, attempt_no: int | None = None) -> CallbackEnvelope:
        if run.callback_envelope is not None:
            return replace(
                run.callback_envelope,
                attempt_no=attempt_no or run.callback_envelope.attempt_no,
            )
        return CallbackEnvelope(
            match_id=run.match_id,
            speech_id=run.speech_id,
            attempt_no=attempt_no or 1,
            generation_id=run.generation_id,
            connection_epoch=None,
            context_version=0,
            opportunity_id=None,
            opportunity_generation=None,
        )

    async def start_agent(
        self,
        *,
        match_id: UUID,
        action_key: str,
        agent_profile_id: UUID,
        duration_ms: int,
        side: str | None = None,
        context_version: int = 0,
        opportunity_id: UUID | None = None,
        opportunity_generation: int | None = None,
    ) -> None:
        token = object()
        self._pending_media_starts[match_id] = token
        try:
            async with self._lock:
                if not self._media_start_is_current(match_id, token):
                    return
                current_host = self._host_runs.pop(match_id, None)
                if current_host is not None:
                    await self._cancel_host_run(current_host)
                    if not self._media_start_is_current(match_id, token):
                        return
                current = self._runs.get(match_id)
                if current is not None and current.action_key == action_key:
                    # A failed/completed task can remain visible briefly while its
                    # callback is being handled. It must not block recovery of the
                    # same action.
                    if current.task is not None and current.task.done():
                        self._runs.pop(match_id, None)
                    else:
                        return
                if current is not None:
                    await self._cancel_run(current)
                    if not self._media_start_is_current(match_id, token):
                        return
                run = AgentRun(
                    match_id=match_id,
                    action_key=action_key,
                    agent_profile_id=agent_profile_id,
                    duration_ms=duration_ms,
                    side=side,
                    callback_envelope=CallbackEnvelope(
                        match_id=match_id,
                        speech_id=None,
                        attempt_no=1,
                        generation_id=None,
                        connection_epoch=None,
                        context_version=context_version,
                        opportunity_id=opportunity_id,
                        opportunity_generation=opportunity_generation,
                    ),
                )
                self._runs[match_id] = run
                run.task = asyncio.create_task(
                    self._run_with_retry(run), name=f"agent-run-{match_id}-{action_key}"
                )
        finally:
            if self._pending_media_starts.get(match_id) is token:
                self._pending_media_starts.pop(match_id, None)

    async def start_host_playback(
        self,
        *,
        match_id: UUID,
        action_key: str,
        storage_path: str,
        duration_ms: int,
        context_version: int,
    ) -> None:
        """Publish one host asset through the same bounded LiveKit path as agents.

        The actor remains authoritative: this task only reports completion after
        the PCM source has drained, so a slow browser or HTTP fetch cannot skip
        the beginning of an announcement or advance the next action early.
        """
        token = object()
        self._pending_media_starts[match_id] = token
        try:
            async with self._lock:
                if not self._media_start_is_current(match_id, token):
                    return
                current_agent = self._runs.pop(match_id, None)
                if current_agent is not None:
                    await self._cancel_run(current_agent)
                    if not self._media_start_is_current(match_id, token):
                        return
                current = self._host_runs.pop(match_id, None)
                if current is not None:
                    await self._cancel_host_run(current)
                    if not self._media_start_is_current(match_id, token):
                        return
                run = HostAudioRun(
                    match_id=match_id,
                    action_key=action_key,
                    storage_path=storage_path,
                    duration_ms=duration_ms,
                    context_version=context_version,
                )
                self._host_runs[match_id] = run
                run.task = asyncio.create_task(
                    self._run_host_with_retry(run), name=f"host-audio-{match_id}-{action_key}"
                )
        finally:
            if self._pending_media_starts.get(match_id) is token:
                self._pending_media_starts.pop(match_id, None)

    async def reset_host(self, match_id: UUID) -> None:
        self.retire_pending_media_start(match_id)
        run = self._host_runs.pop(match_id, None)
        if run is not None:
            await self._cancel_host_run(run)

    async def fence_host(self, match_id: UUID) -> None:
        self.retire_pending_media_start(match_id)
        run = self._host_runs.get(match_id)
        if run is None:
            return
        # Each host run serializes its own frames. Fence synchronously rather
        # than waiting behind a native capture call holding a shared lock.
        run.media_fenced = True
        if run.source is not None:
            _try_agent_cleanup(run.source.clear_queue, resource="host_audio_queue")

    def host_callback_is_current(self, *, envelope: CallbackEnvelope, action_key: str) -> bool:
        """Check the registered host attempt without borrowing an Agent identity."""
        run = self._host_runs.get(envelope.match_id)
        return (
            self._accepts_match_locked(envelope.match_id)
            and run is not None
            and not run.media_fenced
            and run.action_key == action_key
            and run.generation_id == envelope.generation_id
            and run.attempt_no == envelope.attempt_no
            and run.context_version == envelope.context_version
            and run.callback_envelope == envelope
            and envelope.speech_id is None
            and envelope.connection_epoch is None
            and envelope.opportunity_id is None
            and envelope.opportunity_generation is None
        )

    def _require_current_host_run(
        self, run: HostAudioRun, envelope: CallbackEnvelope | None = None
    ) -> None:
        if (
            not self._accepts_match_locked(run.match_id)
            or self._host_runs.get(run.match_id) is not run
            or run.media_fenced
            or (
                envelope is not None
                and not self.host_callback_is_current(envelope=envelope, action_key=run.action_key)
            )
        ):
            raise asyncio.CancelledError

    async def decide_free_debate(
        self,
        *,
        match_id: UUID,
        action_key: str,
        side: str,
        agent_profile_ids: list[UUID],
        decision_round_id: UUID,
        envelope: CallbackEnvelope,
    ) -> None:
        """Run and report each free-debate decision independently."""

        current_task = asyncio.current_task()
        if current_task is None:
            return
        async with self._lock:
            if not self._accepts_match_locked(match_id):
                return
            previous = self._decision_tasks.get(match_id)
            self._decision_tasks[match_id] = current_task
        if previous is not None and previous is not current_task:
            await self._cancel_agent_task(previous)

        async def decide(agent_profile_id: UUID) -> None:
            started = asyncio.get_running_loop().time()
            run = AgentRun(
                match_id=match_id,
                action_key=action_key,
                agent_profile_id=agent_profile_id,
                duration_ms=0,
                side=side,
            )
            last_error: Exception | None = None
            logical_call_id = uuid4()
            for attempt in (1, 2):
                leases: tuple[asyncio.Semaphore, asyncio.Semaphore] | None = None
                client: OpenAIStreamingClient | None = None
                value: tuple[bool, float | None, str | None] | None = None
                external_call_id: UUID | None = None
                raw_response: str | None = None
                provider_capture = new_provider_capture(self._capture_writer)
                callback_envelope = replace(envelope, match_id=match_id, attempt_no=attempt)

                async def release_provider() -> None:
                    nonlocal leases, client
                    if leases is not None:
                        held_leases, leases = leases, None
                        self._limiter.release(held_leases)
                    if client is not None:
                        closing_client, client = client, None
                        await _close_llm_client(closing_client)

                try:
                    config = await self._load_config(run)
                    callback_envelope = replace(
                        envelope,
                        match_id=match_id,
                        attempt_no=attempt,
                        context_version=config.context_version,
                    )
                    boolean_decision = config.experiment_mode or config.formal_4v4
                    decision_messages = list(config.decision_messages or config.messages)
                    if boolean_decision:
                        decision_messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "这是自由辩论快速决策。只输出 JSON，不要 Markdown："
                                    '{"should_speak":true或false,"decision_reason":"20字以内理由"}。'
                                    "decision_reason 必须填写且不超过 20 个字。"
                                    "结合当前辩论上下文判断是否值得发言。"
                                ),
                            }
                        )
                    elif not config.decision_messages:
                        decision_messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "这是自由辩论快速决策。请结合当前辩论上下文判断是否值得发言。"
                                ),
                            }
                        )
                    external_call_id = await _bounded_decision_capture(
                        self._start_decision_call(
                            config=config,
                            decision_round_id=decision_round_id,
                            envelope=callback_envelope,
                            messages=decision_messages,
                            attempt_no=attempt,
                            logical_call_id=logical_call_id,
                        ),
                        phase="start",
                    )
                    leases = await self._limiter.acquire(
                        config.model_key, config.model_limit, timeout_seconds=3.0
                    )
                    client = OpenAIStreamingClient(
                        base_url=config.base_url,
                        api_key=config.api_key,
                        model=config.model_id,
                        connection_timeout_seconds=LLM_FAST_DECISION_TIMEOUT_SECONDS,
                        first_token_timeout_seconds=LLM_FAST_DECISION_TIMEOUT_SECONDS,
                        idle_timeout_seconds=LLM_FAST_DECISION_TIMEOUT_SECONDS,
                    )
                    result = await client.stream_chat(
                        messages=decision_messages,
                        max_tokens=32 if boolean_decision else 64,
                        generation_params={
                            **config.generation_params,
                            **(
                                {
                                    "temperature": 0.75,
                                    "top_p": 0.9,
                                    "enable_thinking": False,
                                }
                                if boolean_decision
                                else {"enable_thinking": False}
                            ),
                        },
                        on_delta=_ignore_delta,
                        capture=provider_capture,
                    )
                    raw_response = result.text
                    await release_provider()
                    if boolean_decision:
                        parsed_decision = _parse_experiment_decision(result.text)
                        candidate_value = (parsed_decision[0], None, parsed_decision[1])
                    else:
                        parsed_decision = _parse_decision(result.text)
                        candidate_value = (*parsed_decision, None)
                    await self._finish_external_call(
                        external_call_id,
                        response_payload={"text": result.text},
                        first_result_latency_ms=getattr(result, "first_token_latency_ms", None),
                        completed_latency_ms=getattr(result, "completed_latency_ms", None),
                        completion_tokens=getattr(result, "completion_tokens", None),
                        provider_capture=provider_capture,
                    )
                    value = candidate_value
                except asyncio.CancelledError:
                    await release_provider()
                    await self._fail_external_call(
                        external_call_id,
                        "cancelled",
                        cancelled=True,
                        provider_capture=provider_capture,
                    )
                    raise
                except Exception as error:
                    last_error = error
                    await release_provider()
                    await self._fail_external_call(
                        external_call_id,
                        _error_code(error),
                        response_payload=(
                            {"text": raw_response} if raw_response is not None else None
                        ),
                        provider_capture=provider_capture,
                    )
                finally:
                    await release_provider()
                    provider_capture.close()
                if value is not None:
                    await self._callbacks.report_free_decision(
                        envelope=callback_envelope,
                        action_key=action_key,
                        agent_profile_id=agent_profile_id,
                        side=side,
                        decision_round_id=decision_round_id,
                        should_speak=value[0],
                        willingness=value[1],
                        decision_reason=value[2],
                        failed=False,
                        attempt_no=attempt,
                        duration_ms=max(
                            0,
                            int((asyncio.get_running_loop().time() - started) * 1000),
                        ),
                        error_code=None,
                    )
                    return
            assert last_error is not None
            await self._callbacks.report_free_decision(
                envelope=replace(envelope, match_id=match_id, attempt_no=2),
                action_key=action_key,
                agent_profile_id=agent_profile_id,
                side=side,
                decision_round_id=decision_round_id,
                should_speak=None,
                willingness=None,
                decision_reason=None,
                failed=True,
                attempt_no=2,
                duration_ms=max(
                    0,
                    int((asyncio.get_running_loop().time() - started) * 1000),
                ),
                error_code=_error_code(last_error),
            )

        try:
            await asyncio.gather(*(decide(item) for item in agent_profile_ids))
        finally:
            if self._decision_tasks.get(match_id) is current_task:
                self._decision_tasks.pop(match_id, None)

    async def finalize_agent(self, match_id: UUID, speech_id: UUID, reason: str) -> None:
        run = self._runs.get(match_id)
        if run is None or run.speech_id != speech_id or run.finalizing:
            return
        run.finalizing = True
        truncated = reason != "COMPLETED" or not run.natural_complete
        if truncated:
            if run.source is not None:
                _try_agent_cleanup(run.source.clear_queue, resource="agent_audio_queue")
            if run.task is not None and not run.task.done():
                await self._cancel_agent_task(run.task)
            # A truncated pipeline may still own the reusable TTS connection
            # lock while it cleans up. Discard it before the next action so a
            # slow provider close cannot stall the whole match indefinitely.
            await self._discard_tts_connection(match_id)
        draft = run.draft
        played_ms = run.played_samples * 1000 // 48_000
        # The LLM draft is the authoritative Agent transcript. Playback may be
        # truncated by the stage timer, but TTS never rewrites formal context.
        final_text = draft
        if run.spool_path is None or run.storage_path is None or run.generation_id is None:
            await self._callbacks.handle_agent_failure(
                envelope=self._run_envelope(run), error_code="tts_audio_empty"
            )
            return
        if run.spool_path.exists():
            run.storage_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(run.spool_path, run.storage_path)
        try:
            async with asyncio.timeout(AGENT_FINALIZATION_TIMEOUT_SECONDS):
                await self._persist_final(run)
                if self._runs.get(match_id) is run:
                    self._runs.pop(match_id, None)
                callback_kwargs: dict[str, Any] = {
                    "envelope": self._run_envelope(run),
                    "final_text": final_text,
                    "llm_draft_text": draft,
                    "audio_storage_path": str(run.storage_path),
                    "audio_duration_ms": played_ms,
                    "audio_truncated": truncated,
                }
                for callback_attempt in range(2):
                    try:
                        await self._callbacks.finalize_agent_speech(**callback_kwargs)
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        if callback_attempt == 1:
                            raise
                        logger.warning(
                            "Agent finalization callback failed; retrying",
                            extra={
                                "error_code": "agent_finalization_callback_retry",
                                "match_id": str(match_id),
                                "speech_id": str(speech_id),
                                "generation_id": str(run.generation_id),
                            },
                        )
                        await asyncio.sleep(0.05)
        except TimeoutError:
            logger.error(
                "agent finalization timed out",
                extra={
                    "error_code": "agent_finalization_timeout",
                    "match_id": str(match_id),
                    "speech_id": str(speech_id),
                    "generation_id": str(run.generation_id),
                },
            )
            await self._callbacks.handle_agent_failure(
                envelope=self._run_envelope(run), error_code="agent_finalization_timeout"
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            original = getattr(error, "orig", None)
            diagnostic: dict[str, str] = {"exception_type": type(error).__name__}
            sqlstate = getattr(original, "sqlstate", None)
            if isinstance(sqlstate, str):
                diagnostic["sqlstate"] = sqlstate
            constraint = getattr(getattr(original, "diag", None), "constraint_name", None)
            if isinstance(constraint, str) and constraint.isascii() and len(constraint) <= 128:
                diagnostic["constraint"] = constraint
            logger.exception(
                "agent finalization failed",
                extra={
                    "error_code": "agent_finalization_failed",
                    "match_id": str(match_id),
                    "speech_id": str(speech_id),
                    "generation_id": str(run.generation_id),
                    "details": diagnostic,
                },
            )
            await self._callbacks.handle_agent_failure(
                envelope=self._run_envelope(run), error_code="agent_finalization_failed"
            )
        finally:
            try:
                await asyncio.wait_for(self._close_run(run), timeout=3.0)
            except TimeoutError:
                logger.warning(
                    "agent media cleanup timed out",
                    extra={"error_code": "agent_cleanup_timeout"},
                )

    async def reset_agent(self, match_id: UUID) -> None:
        self.retire_pending_media_start(match_id)
        run = self._runs.pop(match_id, None)
        if run is not None:
            await self._cancel_run(run)

    async def fence_agent(self, match_id: UUID) -> None:
        """Synchronously invalidate media before an interruption is broadcast."""
        self.retire_pending_media_start(match_id)
        run = self._runs.get(match_id)
        if run is None:
            return
        # Native playback may already hold the media lock. Retire provider
        # callbacks now; the lock below only serializes clearing the old queue.
        run.media_fenced = True
        async with self._media_lock:
            if run.source is not None:
                _try_agent_cleanup(run.source.clear_queue, resource="agent_audio_queue")

    async def cancel_free_decision(self, match_id: UUID) -> None:
        task = self._decision_tasks.pop(match_id, None)
        if task is not None and task is not asyncio.current_task():
            await self._cancel_agent_task(task)

    async def close_match(self, match_id: UUID) -> None:
        self.retire_pending_media_start(match_id)
        async with self._lock:
            if self._closed or match_id in self._closed_matches:
                return
            self._remember_closed_match_locked(match_id)
        await self.cancel_free_decision(match_id)
        await self.reset_agent(match_id)
        await self.reset_host(match_id)
        connection = self._tts_connections.pop(match_id, None)
        if connection is not None:
            await connection.close()

    async def close(self) -> None:
        self._pending_media_starts.clear()
        async with self._lock:
            self._closed = True
            for match_id in tuple(self._runs) + tuple(self._host_runs):
                self._remember_closed_match_locked(match_id)
        for match_id in tuple(self._decision_tasks):
            await self.cancel_free_decision(match_id)
        for match_id in tuple(self._runs):
            await self.reset_agent(match_id)
        for match_id in tuple(self._host_runs):
            await self.reset_host(match_id)
        for connection in tuple(self._tts_connections.values()):
            await connection.close()
        self._tts_connections.clear()

    async def _run_with_retry(self, run: AgentRun) -> None:
        try:
            for attempt in (1, 2):
                if (
                    not self._accepts_match_locked(run.match_id)
                    or self._runs.get(run.match_id) is not run
                    or run.media_fenced
                ):
                    return
                # A configuration failure owns this startup attempt, not the
                # preceding retry's already failed generation row.
                run.generation_id = None
                run.callback_envelope = replace(
                    self._run_envelope(run), attempt_no=attempt, generation_id=None
                )
                try:
                    config = await self._load_config(run)
                    if (
                        not self._accepts_match_locked(run.match_id)
                        or self._runs.get(run.match_id) is not run
                        or run.media_fenced
                    ):
                        return
                    run.callback_envelope = CallbackEnvelope(
                        match_id=run.match_id,
                        speech_id=None,
                        attempt_no=attempt,
                        generation_id=None,
                        connection_epoch=None,
                        context_version=config.context_version,
                        opportunity_id=config.opportunity_id,
                        opportunity_generation=config.opportunity_generation,
                    )
                    run.chars_per_second = config.chars_per_second
                    run.voice = config.voice
                    run.playback_gain = config.playback_gain
                    run.rate = config.rate
                    generation_id = await self._create_generation(config, attempt)
                    run.generation_id = generation_id
                    assert run.callback_envelope is not None
                    run.callback_envelope = replace(
                        run.callback_envelope, generation_id=generation_id
                    )
                    try:
                        self._require_current_agent_generation(run, generation_id)
                    except asyncio.CancelledError:
                        # A committed generation may return after reset has
                        # already retired this run without knowing its UUID.
                        # Finish only that exact row before rejecting all IO.
                        await _bounded_provider_capture(
                            self._mark_generation_failed(generation_id, "agent_cancelled"),
                            call_kind="generation",
                            phase="retire_startup",
                            match_id=run.match_id,
                            generation_id=generation_id,
                        )
                        raise
                    await self._execute(run, config, generation_id)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if (
                        not self._accepts_match_locked(run.match_id)
                        or self._runs.get(run.match_id) is not run
                        or run.media_fenced
                    ):
                        raise asyncio.CancelledError from error
                    code = _error_code(error)
                    original = getattr(error, "orig", None)
                    diagnostic: dict[str, str] = {"exception_type": type(error).__name__}
                    sqlstate = getattr(original, "sqlstate", None)
                    if isinstance(sqlstate, str):
                        diagnostic["sqlstate"] = sqlstate
                    constraint = getattr(getattr(original, "diag", None), "constraint_name", None)
                    if (
                        isinstance(constraint, str)
                        and constraint.isascii()
                        and len(constraint) <= 128
                    ):
                        diagnostic["constraint"] = constraint
                    logger.exception(
                        "agent pipeline failed",
                        extra={
                            "error_code": code,
                            "match_id": str(run.match_id),
                            "generation_id": str(run.generation_id) if run.generation_id else None,
                            "details": diagnostic,
                        },
                    )
                    playback_started = run.speech_id is not None
                    await self._mark_generation_failed(
                        run.generation_id,
                        code,
                        llm_capture=run.llm_capture,
                        tts_capture=run.tts_capture,
                        capture_owner=run,
                    )
                    # Any failed attempt may have left a provider task in an
                    # indeterminate state. Do not reuse its match-scoped
                    # socket on a retry or the next action.
                    if run.tts_task is not None:
                        await self._discard_tts_connection(run.match_id)
                    if playback_started:
                        await self._callbacks.handle_agent_failure(
                            envelope=self._run_envelope(run, attempt_no=attempt),
                            error_code=code,
                            logical_call_id=run.logical_call_id,
                        )
                    await self._cleanup_attempt(run)
                    if (
                        not self._accepts_match_locked(run.match_id)
                        or self._runs.get(run.match_id) is not run
                        or run.media_fenced
                    ):
                        return
                    if attempt == 1 and not playback_started:
                        if run.generation_id is not None:
                            await self._callbacks.publish_agent_retry(
                                envelope=self._run_envelope(run, attempt_no=attempt),
                                error_code=code,
                            )
                        # Let the provider connection and bounded media
                        # cleanup settle before the single allowed retry.
                        await asyncio.sleep(AGENT_PROVIDER_RETRY_BACKOFF_SECONDS)
                        continue
                    if not playback_started:
                        await self._callbacks.handle_agent_failure(
                            envelope=self._run_envelope(run, attempt_no=attempt),
                            error_code=code,
                            logical_call_id=run.logical_call_id,
                        )
                    return
        finally:
            # A fenced run is still owned by the authority's pending reset or
            # finalizer. Keep its handle until that handler takes ownership.
            # Other terminal tasks must not block recovery of the same action.
            if self._runs.get(run.match_id) is run and not run.media_fenced:
                self._runs.pop(run.match_id, None)

    async def _discard_tts_connection(self, match_id: UUID) -> None:
        connection = self._tts_connections.pop(match_id, None)
        if connection is None:
            return
        try:
            await asyncio.wait_for(connection.close(), timeout=2.0)
        except TimeoutError:
            logger.warning(
                "stale TTS connection cleanup timed out",
                extra={"error_code": "tts_connection_cleanup_timeout"},
            )

    @staticmethod
    def _raise_tts_task_failure(task: asyncio.Task[TtsStreamResult]) -> None:
        """Turn a completed TTS task into a stable provider error."""

        try:
            task.result()
        except asyncio.CancelledError as error:
            raise TtsProviderError("tts_cancelled") from error
        except TtsProviderError:
            raise
        except Exception as error:
            raise TtsProviderError("tts_provider_failed") from error
        raise TtsProviderError("tts_stream_ended")

    async def _enqueue_tts_text(self, run: AgentRun, delta: str | None) -> None:
        """Enqueue text while observing a failed or exited TTS consumer."""

        queue = run.text_queue
        tts_task = run.tts_task
        if queue is None or tts_task is None:
            raise TtsProviderError("tts_not_started")
        if tts_task.done():
            self._raise_tts_task_failure(tts_task)

        put_task = asyncio.create_task(queue.put(delta), name="agent-tts-queue-put")
        try:
            done, _ = await asyncio.wait({put_task, tts_task}, return_when=asyncio.FIRST_COMPLETED)
            if tts_task in done:
                put_task.cancel()
                await asyncio.gather(put_task, return_exceptions=True)
                self._raise_tts_task_failure(tts_task)
            await put_task
            # A provider failure can race with a successful queue put. Surface
            # it before accepting another delta rather than filling a queue
            # with no consumer.
            if tts_task.done():
                self._raise_tts_task_failure(tts_task)
        finally:
            if not put_task.done():
                put_task.cancel()
                await asyncio.gather(put_task, return_exceptions=True)

    async def _execute(self, run: AgentRun, config: AgentConfig, generation_id: UUID) -> None:
        self._require_current_agent_generation(run, generation_id)
        run.draft_parts.clear()
        run.speech_id = None
        run.played_samples = 0
        run.natural_complete = False
        run.byte_count = 0
        storage_root = Path(self._settings.agent_audio_storage_dir)
        directory = storage_root / str(run.match_id)
        directory.mkdir(parents=True, exist_ok=True)
        run.spool_path = directory / f".{generation_id}.ogg.part"
        run.storage_path = directory / f"{generation_id}.ogg"
        run.spool_path.write_bytes(b"")
        decoder = IncrementalOpusDecoder()
        run.decoder = decoder
        try:
            await decoder.start(run.spool_path)
            self._require_current_agent_generation(run, generation_id)
            run.room, run.source = await self._connect_publisher(run.match_id)
            self._require_current_agent_generation(run, generation_id)
        except BaseException:
            if not run.finalizing and self._runs.get(run.match_id) is not run:
                # Cleanup may have detached this decoder while its startup
                # await was still finishing. Retain its exact local owner.
                if run.decoder is not decoder:
                    await _bounded_agent_cleanup_call(decoder.close, resource="agent_decoder")
                await self._cleanup_attempt(run)
            raise
        run.text_queue = asyncio.Queue(maxsize=AGENT_TTS_QUEUE_SIZE)

        connection = self._tts_connections.get(run.match_id)
        if connection is None:
            api_key = self._settings.tts_api_key or self._settings.asr_api_key
            if api_key is None:
                raise TtsProviderError("tts_not_configured")
            connection = QwenTtsConnection(
                url=self._settings.tts_ws_url,
                api_key=api_key.get_secret_value(),
                model=self._settings.tts_model,
                workspace_id=self._settings.tts_workspace_id,
            )
            self._tts_connections[run.match_id] = connection

        spool = run.spool_path.open("ab", buffering=0)

        async def on_audio(chunk: bytes) -> None:
            self._require_current_agent_generation(run, generation_id)
            spool.write(chunk)
            run.byte_count += len(chunk)
            assert run.decoder is not None
            run.decoder.notify_written(run.byte_count)

        async def start_tts_if_needed() -> None:
            self._require_current_agent_generation(run, generation_id)
            if run.tts_task is None:
                assert run.text_queue is not None
                run.tts_call_id = await _bounded_provider_capture(
                    self._start_tts_call(run, config, generation_id),
                    call_kind="tts",
                    phase="start",
                    match_id=run.match_id,
                    generation_id=generation_id,
                )
                self._require_current_agent_generation(run, generation_id)
                run.tts_capture = new_provider_capture(self._capture_writer)
                run.tts_task = asyncio.create_task(
                    connection.synthesize(
                        iter_text_queue(run.text_queue),
                        voice=config.voice,
                        rate=config.rate,
                        on_audio=on_audio,
                        capture=run.tts_capture,
                    ),
                    name=f"agent-tts-{generation_id}",
                )

        async def on_delta(delta: str) -> None:
            self._require_current_agent_generation(run, generation_id)
            run.draft_parts.append(delta)
            await self._callbacks.publish_agent_text_delta(
                envelope=self._run_envelope(run), text=delta
            )
            await start_tts_if_needed()
            self._require_current_agent_generation(run, generation_id)
            await self._enqueue_tts_text(run, delta)

        client: OpenAIStreamingClient | None = None
        leases: tuple[asyncio.Semaphore, asyncio.Semaphore] | None = None
        playback: asyncio.Task[None] | None = None
        try:
            run.llm_capture = new_provider_capture(self._capture_writer)
            await _bounded_provider_capture(
                self._start_generation_call(
                    generation_id, config, logical_call_id=run.logical_call_id
                ),
                call_kind="llm",
                phase="start",
                match_id=run.match_id,
                generation_id=generation_id,
            )
            if (
                not self._accepts_match_locked(run.match_id)
                or self._runs.get(run.match_id) is not run
                or run.media_fenced
            ):
                raise asyncio.CancelledError
            client = OpenAIStreamingClient(
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model_id,
            )
            leases = await self._limiter.acquire(
                config.model_key, config.model_limit, timeout_seconds=3.0
            )
            if (
                not self._accepts_match_locked(run.match_id)
                or self._runs.get(run.match_id) is not run
                or run.media_fenced
            ):
                raise asyncio.CancelledError
            playback = asyncio.create_task(
                self._play_pcm(run, generation_id), name=f"agent-play-{generation_id}"
            )
            result = await client.stream_chat(
                messages=config.messages,
                max_tokens=config.max_tokens,
                generation_params=config.generation_params,
                on_delta=on_delta,
                capture=run.llm_capture,
            )
            held_leases, leases = leases, None
            self._limiter.release(held_leases)
            self._require_current_agent_generation(run, generation_id)
            closing_client, client = client, None
            await _close_llm_client(closing_client)
            self._require_current_agent_generation(run, generation_id)
            await self._mark_generation_llm_ready(
                generation_id, result, run.llm_capture, capture_owner=run
            )
            self._require_current_agent_generation(run, generation_id)
            await self._callbacks.agent_text_generated(
                envelope=self._run_envelope(run), text=result.text
            )
            self._require_current_agent_generation(run, generation_id)
            assert run.text_queue is not None
            assert run.tts_task is not None
            await self._enqueue_tts_text(run, None)
            self._require_current_agent_generation(run, generation_id)
            tts_result = await run.tts_task
            self._require_current_agent_generation(run, generation_id)
            run.tts_task_id = tts_result.task_id
            run.first_audio_latency_ms = tts_result.first_audio_latency_ms
            run.tts_completed_latency_ms = tts_result.completed_latency_ms
            await self._finish_external_call(
                run.tts_call_id,
                request_payload={
                    "text": result.text,
                    "voice": config.voice,
                    "rate": config.rate,
                    "mode": "server_commit",
                    "format": "opus",
                },
                response_payload={
                    "task_id": str(tts_result.task_id),
                    "audio_bytes": tts_result.byte_count,
                },
                first_result_latency_ms=tts_result.first_audio_latency_ms,
                completed_latency_ms=tts_result.completed_latency_ms,
                audio_bytes=run.byte_count,
                provider_capture=run.tts_capture,
                capture_owner=run,
            )
            self._require_current_agent_generation(run, generation_id)
            assert run.decoder is not None
            run.decoder.finish_input()
            await playback
            self._require_current_agent_generation(run, generation_id)
            assert run.source is not None
            await run.source.wait_for_playout()
            self._require_current_agent_generation(run, generation_id)
            run.natural_complete = True
            if run.speech_id is None:
                raise TtsProviderError("tts_audio_empty")
            await self._callbacks.finish_agent_playback(envelope=self._run_envelope(run))
        finally:
            if leases is not None:
                held_leases, leases = leases, None
                self._limiter.release(held_leases)
            try:
                spool.close()
            except OSError as error:
                logger.warning(
                    "Agent spool cleanup failed",
                    extra={
                        "error_code": "agent_spool_cleanup_failed",
                        "exception_type": type(error).__name__,
                    },
                )
            try:
                if client is not None:
                    closing_client, client = client, None
                    await _close_llm_client(closing_client)
            finally:
                try:
                    if playback is not None and not playback.done():
                        await self._cancel_agent_task(playback)
                finally:
                    # Fencing only invalidates output; the authority still
                    # needs its registered run/spool for reset or finalization.
                    # Clean only an attempt already detached from that registry.
                    if (
                        run.media_fenced
                        and not run.finalizing
                        and run.generation_id == generation_id
                        and self._runs.get(run.match_id) is not run
                    ):
                        await self._cleanup_attempt(run)

    async def _play_pcm(self, run: AgentRun, generation_id: UUID) -> None:
        assert run.decoder is not None
        assert run.source is not None
        source = run.source
        frame_no = 0
        async for pcm in run.decoder.frames():
            async with self._media_lock:
                if (
                    run.media_fenced
                    or self._runs.get(run.match_id) is not run
                    or run.generation_id != generation_id
                ):
                    return
                if run.speech_id is None:
                    run.speech_id = uuid4()
                    run.callback_envelope = replace(
                        self._run_envelope(run), speech_id=run.speech_id
                    )
                    assert run.storage_path is not None
                    await self._start_agent_playback_with_retry(run)
                pcm = apply_pcm16_gain(pcm, run.playback_gain)
                samples = len(pcm) // 2
                frame = rtc.AudioFrame(
                    data=pcm,
                    sample_rate=48_000,
                    num_channels=1,
                    samples_per_channel=samples,
                )
                await source.capture_frame(frame)
                run.played_samples += samples
                frame_no += 1
            if frame_no % 5 == 0:
                assert run.speech_id is not None
                played_ms = run.played_samples * 1000 // 48_000
                subtitle = _played_prefix(run.draft, played_ms, run.chars_per_second)
                await self._callbacks.publish_agent_subtitle(
                    envelope=self._run_envelope(run), text=subtitle, played_ms=played_ms
                )

    async def _run_host_with_retry(self, run: HostAudioRun) -> None:
        last_error = "host_audio_failed"
        identity = CallbackEnvelope(
            match_id=run.match_id,
            speech_id=None,
            attempt_no=1,
            generation_id=run.generation_id,
            connection_epoch=None,
            context_version=run.context_version,
            opportunity_id=None,
            opportunity_generation=None,
        )
        envelope = identity
        try:
            for attempt in range(1, HOST_AUDIO_ATTEMPTS + 1):
                self._require_current_host_run(run)
                run.attempt_no = attempt
                envelope = replace(identity, attempt_no=attempt)
                run.callback_envelope = envelope
                self._require_current_host_run(run, envelope)
                try:
                    timeout_seconds = host_audio_attempt_timeout_seconds(run.duration_ms)
                    await asyncio.wait_for(self._play_host_audio(run), timeout=timeout_seconds)
                    self._require_current_host_run(run, envelope)
                    await self._callbacks.host_audio_finished(
                        envelope=envelope, action_key=run.action_key
                    )
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._require_current_host_run(run, envelope)
                    if getattr(error, "code", None) == "stale_callback":
                        return
                    last_error = _error_code(error)
                    logger.warning(
                        "host audio playback failed; retrying",
                        extra={
                            "error_code": last_error,
                            "match_id": str(run.match_id),
                            "action_key": run.action_key,
                            "generation_id": str(run.generation_id),
                            "attempt_no": attempt,
                        },
                    )
                    if attempt == 1:
                        await asyncio.sleep(HOST_AUDIO_RETRY_BACKOFF_SECONDS)
                finally:
                    await self._close_host_attempt_bounded(run)
            self._require_current_host_run(run, envelope)
            await self._callbacks.host_audio_failed(
                envelope=envelope, action_key=run.action_key, error_code=last_error
            )
        except asyncio.CancelledError:
            raise
        finally:
            if self._host_runs.get(run.match_id) is run:
                self._host_runs.pop(run.match_id, None)

    async def _play_host_audio(self, run: HostAudioRun) -> None:
        envelope = run.callback_envelope
        if envelope is None:
            raise asyncio.CancelledError
        self._require_current_host_run(run, envelope)
        root = Path(self._settings.host_audio_storage_dir).resolve()
        candidate = Path(run.storage_path)
        source_path = candidate if candidate.is_absolute() else root / candidate
        source_path = source_path.resolve()
        if root not in source_path.parents or not source_path.is_file():
            raise TtsProviderError("host_audio_unavailable")
        spool_root = Path(self._settings.match_audio_storage_dir) / str(run.match_id)
        await asyncio.to_thread(spool_root.mkdir, parents=True, exist_ok=True)
        self._require_current_host_run(run, envelope)
        spool_path = spool_root / f".host-{run.generation_id}-{envelope.attempt_no}.ogg"
        run.spool_path = spool_path
        copy_task = asyncio.create_task(asyncio.to_thread(shutil.copyfile, source_path, spool_path))

        def remove_retired_copy(task: asyncio.Task[Any]) -> None:
            _consume_detached_cleanup(task)
            cleanup = asyncio.create_task(
                _bounded_agent_cleanup(
                    asyncio.to_thread(spool_path.unlink, missing_ok=True), resource="host_spool"
                )
            )
            cleanup.add_done_callback(_consume_detached_cleanup)

        try:
            # Shielding retains the thread's actual completion signal. An old
            # copy may finish after reset unlinks its path; only that attempt's
            # task removes the late file, never a replacement attempt's spool.
            await asyncio.shield(copy_task)
            self._require_current_host_run(run, envelope)
        except asyncio.CancelledError:
            copy_task.add_done_callback(remove_retired_copy)
            raise
        run.room, run.source = await self._connect_publisher(
            run.match_id, identity_prefix="jx-host", track_name="host-announcement"
        )
        self._require_current_host_run(run, envelope)
        run.decoder = IncrementalOpusDecoder()
        await run.decoder.start(run.spool_path)
        self._require_current_host_run(run, envelope)
        copied_size = await asyncio.to_thread(run.spool_path.stat)
        self._require_current_host_run(run, envelope)
        run.decoder.notify_written(copied_size.st_size)
        run.decoder.finish_input()

        await self._capture_host_silence(run, 800)
        self._require_current_host_run(run, envelope)
        assert run.decoder is not None
        async for pcm in run.decoder.frames():
            await self._capture_current_host_pcm(run, envelope, pcm)
        await self._capture_host_silence(run, 600)
        self._require_current_host_run(run, envelope)
        assert run.source is not None
        await run.source.wait_for_playout()
        self._require_current_host_run(run, envelope)

    async def _capture_host_silence(self, run: HostAudioRun, duration_ms: int) -> None:
        envelope = run.callback_envelope
        if envelope is None:
            raise asyncio.CancelledError
        frame = b"\x00" * (48_000 // 50 * 2)
        for _ in range(max(0, (duration_ms + 19) // 20)):
            await self._capture_current_host_pcm(run, envelope, frame)

    async def _capture_current_host_pcm(
        self, run: HostAudioRun, envelope: CallbackEnvelope, pcm: bytes
    ) -> None:
        self._require_current_host_run(run, envelope)
        source = run.source
        try:
            await self._capture_pcm(source, pcm)
        except (Exception, asyncio.CancelledError):
            try:
                self._require_current_host_run(run, envelope)
            except asyncio.CancelledError:
                if source is not None:
                    _try_agent_cleanup(source.clear_queue, resource="host_audio_queue")
                raise
            raise
        try:
            self._require_current_host_run(run, envelope)
        except asyncio.CancelledError:
            # A frame already in native capture cannot be withdrawn. Clear
            # only its retired source after it returns, never the replacement.
            if source is not None:
                _try_agent_cleanup(source.clear_queue, resource="host_audio_queue")
            raise

    @staticmethod
    async def _capture_pcm(source: rtc.AudioSource | None, pcm: bytes) -> None:
        if source is None:
            raise TtsProviderError("host_audio_output_unavailable")
        frame_bytes = 48_000 // 50 * 2
        if len(pcm) < frame_bytes:
            pcm = pcm + b"\x00" * (frame_bytes - len(pcm))
        samples = len(pcm) // 2
        await source.capture_frame(
            rtc.AudioFrame(
                data=pcm,
                sample_rate=48_000,
                num_channels=1,
                samples_per_channel=samples,
            )
        )

    async def _close_host_attempt(self, run: HostAudioRun) -> None:
        decoder, run.decoder = run.decoder, None
        source, run.source = run.source, None
        room, run.room = run.room, None
        spool_path, run.spool_path = run.spool_path, None
        cleanup: list[Coroutine[Any, Any, None]] = []
        if decoder is not None:
            cleanup.append(_bounded_agent_cleanup_call(decoder.close, resource="host_decoder"))
        if source is not None:
            _try_agent_cleanup(source.clear_queue, resource="host_audio_queue")
            cleanup.append(_bounded_agent_cleanup_call(source.aclose, resource="host_audio_source"))
        if room is not None:
            cleanup.append(_bounded_agent_cleanup_call(room.disconnect, resource="host_room"))
        if spool_path is not None:
            cleanup.append(
                _bounded_agent_cleanup(
                    asyncio.to_thread(spool_path.unlink, missing_ok=True), resource="host_spool"
                )
            )
        if cleanup:
            await asyncio.gather(*cleanup)

    async def _close_host_attempt_bounded(self, run: HostAudioRun) -> None:
        task = asyncio.create_task(self._close_host_attempt(run))
        try:
            done, _ = await asyncio.wait({task}, timeout=HOST_AUDIO_ATTEMPT_CLEANUP_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(_consume_detached_cleanup)
            raise
        if not done:
            task.cancel()
            task.add_done_callback(_consume_detached_cleanup)
            logger.warning(
                "host audio cleanup timed out", extra={"error_code": "host_cleanup_timeout"}
            )
            return
        try:
            task.result()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "host audio cleanup failed",
                extra={
                    "error_code": "host_cleanup_failed",
                    "exception_type": type(error).__name__,
                },
            )

    async def _cancel_host_run(self, run: HostAudioRun) -> None:
        run.media_fenced = True
        if run.source is not None:
            _try_agent_cleanup(run.source.clear_queue, resource="host_audio_queue")
        if run.task is not None and not run.task.done():
            await self._cancel_agent_task(run.task)
        await self._close_host_attempt_bounded(run)

    async def _start_agent_playback_with_retry(self, run: AgentRun) -> None:
        """Commit the first playback event without failing on one transient error."""
        assert run.storage_path is not None
        for callback_attempt in range(2):
            try:
                await self._callbacks.start_agent_playback(
                    envelope=self._run_envelope(run),
                    agent_profile_id=run.agent_profile_id,
                    audio_storage_path=str(run.storage_path),
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                if callback_attempt == 1:
                    raise
                logger.warning(
                    "Agent playback-start callback failed; retrying",
                    extra={
                        "error_code": "agent_playback_start_callback_retry",
                        "match_id": str(run.match_id),
                        "speech_id": str(run.speech_id) if run.speech_id else None,
                        "generation_id": str(run.generation_id) if run.generation_id else None,
                    },
                )
                await asyncio.sleep(0.05)

    async def _connect_publisher(
        self,
        match_id: UUID,
        *,
        identity_prefix: str = "jx-agent",
        track_name: str = "agent-speech",
    ) -> tuple[rtc.Room, rtc.AudioSource]:
        if (
            self._settings.livekit_url is None
            or self._settings.livekit_api_key is None
            or self._settings.livekit_api_secret is None
        ):
            raise TtsProviderError("livekit_not_configured")
        room_name = f"jx-match-{match_id}"
        token = (
            api.AccessToken(
                self._settings.livekit_api_key.get_secret_value(),
                self._settings.livekit_api_secret.get_secret_value(),
            )
            .with_identity(f"{identity_prefix}-{match_id}-{uuid4().hex[:8]}")
            .with_ttl(timedelta(minutes=10))
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=False,
                    can_publish_data=False,
                )
            )
            .to_jwt()
        )
        room = rtc.Room()
        source: rtc.AudioSource | None = None
        track: rtc.LocalAudioTrack | None = None
        connection_task: asyncio.Task[None] | None = None
        try:
            connection_task = asyncio.create_task(
                room.connect(self._settings.livekit_url, token), name="jx-publisher-connect"
            )
            connection_task.add_done_callback(_consume_detached_cleanup)
            # Cancelling SDK 1.1.14 connect can discard its completion waiter
            # before the native ready handshake. Retire our wait, not that join.
            await asyncio.shield(connection_task)
            source = rtc.AudioSource(48_000, 1, queue_size_ms=200)
            track = rtc.LocalAudioTrack.create_audio_track(track_name, source)
            options = rtc.TrackPublishOptions()
            options.source = rtc.TrackSource.SOURCE_MICROPHONE
            await room.local_participant.publish_track(track, options)
            return room, source
        except BaseException:
            track = None  # No public Track close; release our startup reference.
            if connection_task is not None and not connection_task.done():
                connection_task.add_done_callback(
                    lambda completed: _retired_publisher_connection_finished(completed, room=room)
                )
            else:
                cleanup = _schedule_publisher_startup_cleanup(room, source)
                # A repeated caller cancellation must leave the independently
                # bounded wrappers running, including not-yet-entered factories.
                await asyncio.wait(cleanup)
            raise

    async def _load_config(self, run: AgentRun) -> AgentConfig:
        if self._settings.llm_key_encryption_key is None:
            raise LlmProviderError("model_encryption_not_configured")
        async with self._session_factory() as session:
            match = await session.get(Match, run.match_id)
            if match is None:
                raise LlmProviderError("match_not_found")
            experiment_attempt = await session.scalar(
                select(ExperimentMatchAttempt).where(
                    ExperimentMatchAttempt.match_id == run.match_id
                )
            )
            experiment_mode = experiment_attempt is not None
            runtime_snapshot = match.runtime_snapshot or {}
            formal_4v4 = bool(runtime_snapshot.get("formal_4v4", False))
            competition_mode = experiment_mode or formal_4v4
            room = await session.get(Room, match.room_id)
            agent = await session.get(AgentProfile, run.agent_profile_id)
            if room is None or agent is None or agent.status != "ENABLED":
                raise LlmProviderError("agent_unavailable")
            format_snapshot = room.format_snapshot
            frozen_agent = _snapshot_agent(format_snapshot, run.agent_profile_id)
            if format_snapshot is not None and frozen_agent is None:
                raise LlmProviderError("agent_unavailable")
            model = await session.get(ModelProfile, agent.model_profile_id)
            voice = await session.get(VoiceProfile, agent.voice_profile_id)
            if model is None or model.api_key_ciphertext is None or model.api_key_nonce is None:
                raise LlmProviderError("model_profile_unavailable")
            if frozen_agent is None and (
                model.status != "ENABLED" or not model.base_url or not model.model_id
            ):
                raise LlmProviderError("model_profile_unavailable")
            if voice is None or (frozen_agent is None and voice.status != "ENABLED"):
                raise LlmProviderError("voice_profile_unavailable")
            frozen_model = (
                cast(dict[str, Any], frozen_agent.get("model", {})) if frozen_agent else {}
            )
            frozen_voice = (
                cast(dict[str, Any], frozen_agent.get("voice", {})) if frozen_agent else {}
            )
            base_url = str(frozen_model.get("base_url") or model.base_url or "")
            model_id = str(frozen_model.get("model_id") or model.model_id or "")
            if not base_url or not model_id:
                raise LlmProviderError("model_profile_unavailable")
            participants = list(
                (
                    await session.scalars(
                        select(MatchParticipant)
                        .where(MatchParticipant.match_id == run.match_id)
                        .limit(8)
                    )
                ).all()
            )
            participant = next(
                (
                    item
                    for item in participants
                    if item.kind == "AGENT" and item.agent_profile_id == run.agent_profile_id
                ),
                None,
            )
            if participant is None:
                raise LlmProviderError("agent_unavailable")
            speeches = list(
                (
                    await session.scalars(
                        select(Speech)
                        .where(
                            Speech.match_id == run.match_id,
                            Speech.status == "FINALIZED",
                            Speech.display_text.is_not(None),
                        )
                        .order_by(Speech.created_at.desc(), Speech.id.desc())
                        .limit(AGENT_HISTORY_RECORD_LIMIT + 1)
                    )
                ).all()
            )
            record_truncated = len(speeches) > AGENT_HISTORY_RECORD_LIMIT
            speeches = speeches[:AGENT_HISTORY_RECORD_LIMIT]
            # Only the authoritative source may contribute unfinished context.
            # Unrelated STARTED rows and revoked attempts never become history.
            source_speech: Speech | None = None
            opportunity_value = runtime_snapshot.get(
                "allocated_opportunity_id"
            ) or runtime_snapshot.get("opportunity_id")
            if competition_mode and opportunity_value:
                opportunity = await session.get(FreeDebateOpportunity, UUID(str(opportunity_value)))
                if opportunity is not None and opportunity.match_id == run.match_id:
                    source_id = opportunity.source_speech_id or opportunity.frozen_context.get(
                        "source_speech_id"
                    )
                    if source_id:
                        source_speech = await session.get(Speech, UUID(str(source_id)))
                        if (
                            source_speech is None
                            or source_speech.match_id != run.match_id
                            or source_speech.status not in {"STARTED", "FINALIZING", "FINALIZED"}
                        ):
                            source_speech = None
            if source_speech is not None and all(
                speech.id != source_speech.id for speech in speeches
            ):
                speeches.append(source_speech)
                speeches.sort(key=lambda speech: (speech.created_at, speech.id), reverse=True)
                if len(speeches) > AGENT_HISTORY_RECORD_LIMIT:
                    record_truncated = True
                    removed = next(
                        speech for speech in reversed(speeches) if speech.id != source_speech.id
                    )
                    speeches.remove(removed)
            speeches.reverse()
            topic = room.topic_snapshot
            effective_side = run.side or _side_for_action(run.action_key, room.rule_snapshot)
            if effective_side != participant.side:
                raise LlmProviderError("agent_side_invalid")
            affirmative_position = str(topic.get("affirmative_text") or "支持正方对辩题的明确判断")
            negative_position = str(topic.get("negative_text") or "支持反方对辩题的明确判断")
            position, current_position = _prompt_side_values(
                effective_side,
                affirmative_position=affirmative_position,
                negative_position=negative_position,
            )
            by_seat = {(item.side, item.seat_no): item for item in participants}
            history: list[dict[str, Any]] = [
                {
                    "speech_id": str(speech.id),
                    "participant_id": str(by_seat[(speech.side, speech.seat_no)].id)
                    if (speech.side, speech.seat_no) in by_seat
                    else None,
                    "stage": _stage_name_for_action(speech.action_key, room.rule_snapshot),
                    "speaker": _speaker_label(speech.side, speech.seat_no),
                    "side": speech.side,
                    "seat_no": speech.seat_no,
                    "speaker_kind": "ADMIN_PLACEHOLDER"
                    if speech.origin == "ADMIN_PLACEHOLDER"
                    else speech.speaker_kind,
                    "agent_profile_id": (
                        str(speech.agent_profile_id)
                        if speech.agent_profile_id is not None
                        else None
                    ),
                    "relation": (
                        "ADMIN_PLACEHOLDER"
                        if speech.origin == "ADMIN_PLACEHOLDER"
                        else "SELF"
                        if speech.agent_profile_id == run.agent_profile_id
                        else "TEAMMATE"
                        if speech.side == effective_side
                        else "OPPONENT"
                    ),
                    "status": speech.status,
                    "content_state": (
                        "FINAL"
                        if speech.status == "FINALIZED"
                        else "PROVISIONAL"
                        if speech.display_text
                        else "PENDING"
                    ),
                    "origin": speech.origin or "ORIGINAL",
                    "delivery": "NOT_SPOKEN_ADMIN_PLACEHOLDER"
                    if speech.origin == "ADMIN_PLACEHOLDER"
                    else "PARTICIPANT_SPEECH",
                    "content": speech.display_text or "",
                }
                for speech in speeches
            ]
            recent_opponent = next(
                (
                    item
                    for item in reversed(history)
                    if item.get("relation") == "OPPONENT" and item.get("content")
                ),
                None,
            )
            priority_id = (
                str(recent_opponent["speech_id"])
                if recent_opponent is not None
                else str(source_speech.id)
                if source_speech is not None
                else None
            )
            history, history_truncated = _bound_history(history, priority_speech_id=priority_id)
            history_truncated = history_truncated or record_truncated
            history = [item for item in history if item.get("speech_id")]
            source_context = (
                {
                    "speech_id": str(source_speech.id),
                    "status": source_speech.status,
                    "content_state": "FINAL"
                    if source_speech.status == "FINALIZED"
                    else "PROVISIONAL"
                    if source_speech.display_text
                    else "PENDING",
                    "included_in_history": any(
                        item["speech_id"] == str(source_speech.id) for item in history
                    ),
                }
                if source_speech is not None
                else None
            )
            chars_per_second = float(
                frozen_voice.get("chars_per_second") or voice.chars_per_second or 4.0
            )
            token_per_char = float(frozen_model.get("token_per_char") or model.token_per_char)
            target_chars = max(
                20,
                int((run.duration_ms / 1000) * chars_per_second * 0.85),
            )
            max_tokens = max(32, int(target_chars * token_per_char + 16))
            current_stage, next_stage = _current_and_next_stage(run.action_key, room.rule_snapshot)
            identity = {
                "participant_id": str(participant.id),
                "agent_profile_id": str(run.agent_profile_id),
                "side": effective_side,
                "seat_no": participant.seat_no,
                "stance": current_position,
                "topic": str(topic.get("title", "")),
                "stage": current_stage,
                "next_stage": next_stage,
                "action_key": run.action_key,
            }
            runtime_snapshot = match.runtime_snapshot
            affirmative_remaining_ms = int(
                runtime_snapshot.get("free_affirmative_remaining_ms") or 0
            )
            negative_remaining_ms = int(runtime_snapshot.get("free_negative_remaining_ms") or 0)
            side_remaining_ms = (
                affirmative_remaining_ms
                if effective_side == "AFFIRMATIVE"
                else negative_remaining_ms
            )
            opponent_remaining_ms = (
                negative_remaining_ms
                if effective_side == "AFFIRMATIVE"
                else affirmative_remaining_ms
            )
            messages: list[dict[str, str]] = []
            decision_messages: list[dict[str, str]] | None = None
            params: dict[str, Any] = {}
            stage_key = _stage_key_for_action(run.action_key, room.rule_snapshot)
            if format_snapshot and format_snapshot.get("schema") != "rule-config-v1":
                stage_key = _stage_kind_for_action(run.action_key, room.rule_snapshot)
            speech_template = _snapshot_prompt(
                format_snapshot,
                agent_profile_id=run.agent_profile_id,
                stage_key=stage_key,
                purpose="SPEECH",
            )
            decision_template = _snapshot_prompt(
                format_snapshot,
                agent_profile_id=run.agent_profile_id,
                stage_key=stage_key,
                purpose="DECISION",
            )
            prompt_version: str | None = None
            action_kind = _action_kind_for(run.action_key, room.rule_snapshot)
            if speech_template is not None:
                assert participant is not None
                assert frozen_agent is not None
                assert format_snapshot is not None
                prompt_variables = {
                    "TOPIC": str(topic.get("title", "")),
                    "POSITION": position,
                    "STANCE": current_position,
                    "AFFIRMATIVE_STANCE": affirmative_position,
                    "NEGATIVE_STANCE": negative_position,
                    "SEAT": _seat_label(participant.seat_no),
                    "DEBATER_SEAT": _seat_label(participant.seat_no),
                    "STAGE_NAME": current_stage,
                    "MAX_SPEECH_SECONDS": str(max(1, run.duration_ms // 1000)),
                    "TARGET_CHAR_COUNT": str(target_chars),
                    "DEBATE_HISTORY": HISTORY_REFERENCE,
                    "SIDE_REMAINING_MS": str(side_remaining_ms),
                    "OPPONENT_REMAINING_MS": str(opponent_remaining_ms),
                    "CURRENT_STAGE": current_stage,
                    "NEXT_STAGE": next_stage,
                }
                speech_prompt = _render_snapshot_prompt(
                    str(speech_template["template_text"]), prompt_variables
                )
                messages = [{"role": "user", "content": speech_prompt}]
                if decision_template is not None:
                    decision_messages = [
                        {
                            "role": "user",
                            "content": _render_snapshot_prompt(
                                str(decision_template["template_text"]), prompt_variables
                            ),
                        }
                    ]
                params = {
                    **cast(dict[str, Any], frozen_model.get("generation_params", {})),
                    **cast(dict[str, Any], frozen_agent.get("generation_params", {})),
                }
                prompt_version = (
                    f"{format_snapshot.get('rule_key')}@r{format_snapshot.get('config_revision')}"
                    if format_snapshot.get("schema") == "rule-config-v1"
                    else f"{format_snapshot.get('format_key')}@{format_snapshot.get('version')}"
                )
            elif experiment_mode:
                assert participant is not None
                messages = [
                    {
                        "role": "user",
                        "content": render_experiment_speech_prompt(
                            side=effective_side,
                            seat_no=participant.seat_no,
                            topic=str(topic.get("title", "")),
                            affirmative_stance=affirmative_position,
                            negative_stance=negative_position,
                            side_remaining_ms=side_remaining_ms,
                            opponent_remaining_ms=opponent_remaining_ms,
                            history=history,
                            history_reference=HISTORY_REFERENCE,
                            max_speech_seconds=max(1, run.duration_ms // 1000),
                        )
                        if action_kind == "FREE_DEBATE"
                        else (
                            f"当前为{current_stage}，"
                            f"本次发言最多{max(1, run.duration_ms // 1000)}秒，"
                            f"目标{target_chars}字。请按权威身份和规范上下文完成本阶段发言。\n"
                            + HISTORY_REFERENCE
                        ),
                    }
                ]
                decision_messages = [
                    {
                        "role": "user",
                        "content": render_experiment_decision_prompt(
                            side=effective_side,
                            seat_no=participant.seat_no,
                            topic=str(topic.get("title", "")),
                            affirmative_stance=affirmative_position,
                            negative_stance=negative_position,
                            side_remaining_ms=side_remaining_ms,
                            opponent_remaining_ms=opponent_remaining_ms,
                            history=history,
                            history_reference=HISTORY_REFERENCE,
                        ),
                    }
                ]
                params = {"temperature": 0.75, "top_p": 0.9, "enable_thinking": False}
                prompt_version = EXPERIMENT_PROMPT_VERSION
            else:
                decision_messages = None
            if not experiment_mode and speech_template is None:
                messages = [
                    {
                        "role": "user",
                        "content": (
                            f"{agent.debater_prompt}\n"
                            f"当前动作：{_debate_position(run.action_key, room.rule_snapshot)}。"
                            f"请根据规范辩论上下文完成{current_stage}发言，目标{target_chars}字。"
                        ),
                    },
                ]
                params = {**model.generation_params, **agent.generation_params}
            if decision_template is not None:
                action_kind = "FREE_DEBATE"
            messages = assemble_agent_messages(
                template=messages[0]["content"],
                identity=identity,
                history=history,
                context_version=match.context_version,
                history_truncated=history_truncated,
                purpose="SPEECH",
                action_kind=action_kind,
                source=source_context,
                system_prompt=(agent.system_prompt or "")
                if not experiment_mode and speech_template is None
                else "",
            )
            decision_messages = assemble_agent_messages(
                template=decision_messages[0]["content"]
                if decision_messages
                else "请结合规范辩论上下文判断当前是否值得发言。",
                identity=identity,
                history=history,
                context_version=match.context_version,
                history_truncated=history_truncated,
                purpose="DECISION",
                action_kind="FREE_DEBATE",
                source=source_context,
            )
            configured_max_tokens = params.pop("max_tokens", None)
            max_tokens = min(max_tokens, LLM_MAX_COMPLETION_TOKENS)
            if isinstance(configured_max_tokens, (int, float)) and not isinstance(
                configured_max_tokens, bool
            ):
                max_tokens = min(max_tokens, max(32, int(configured_max_tokens)))
            api_key = decrypt_secret(
                model.api_key_ciphertext,
                model.api_key_nonce,
                self._settings.llm_key_encryption_key.get_secret_value(),
            )
            requested_voice = str(frozen_voice.get("provider_voice") or voice.provider_voice)
            effective_voice = effective_tts_voice(self._settings.tts_model, requested_voice)
            if effective_voice != requested_voice:
                logger.warning(
                    "legacy TTS voice corrected",
                    extra={
                        "error_code": "tts_legacy_voice_corrected",
                        "match_id": str(run.match_id),
                        "details": {
                            "requested_voice": requested_voice,
                            "effective_voice": effective_voice,
                        },
                    },
                )
            return AgentConfig(
                match_id=run.match_id,
                action_key=run.action_key,
                agent_profile_id=run.agent_profile_id,
                context_version=match.context_version,
                match_seed=match.match_seed,
                model_key=str(frozen_model.get("id") or model.id),
                base_url=base_url,
                model_id=model_id,
                api_key=api_key,
                model_limit=int(frozen_model.get("max_concurrency") or model.max_concurrency),
                generation_params=params,
                max_tokens=max_tokens,
                messages=messages,
                voice=effective_voice,
                rate=float(frozen_voice.get("rate") or voice.rate),
                chars_per_second=chars_per_second,
                playback_gain=float(frozen_voice.get("playback_gain") or voice.playback_gain),
                experiment_mode=experiment_mode,
                formal_4v4=formal_4v4,
                decision_messages=decision_messages,
                prompt_version=prompt_version,
                context_policy_version=AGENT_CONTEXT_POLICY_VERSION,
                history_truncated=history_truncated,
                opportunity_id=(
                    UUID(str(runtime_snapshot["opportunity_id"]))
                    if runtime_snapshot.get("opportunity_id")
                    else None
                ),
                opportunity_generation=(
                    int(runtime_snapshot["opportunity_generation"])
                    if runtime_snapshot.get("opportunity_generation")
                    and int(runtime_snapshot["opportunity_generation"]) > 0
                    else None
                ),
            )

    async def _start_decision_call(
        self,
        *,
        config: AgentConfig,
        decision_round_id: UUID,
        envelope: CallbackEnvelope,
        messages: list[dict[str, str]],
        attempt_no: int,
        logical_call_id: UUID,
    ) -> UUID:
        call_id = uuid4()
        async with self._session_factory() as session:
            async with session.begin():
                decision = await session.scalar(
                    select(AgentFreeDebateDecision).where(
                        AgentFreeDebateDecision.match_id == config.match_id,
                        AgentFreeDebateDecision.decision_round_id == decision_round_id,
                        AgentFreeDebateDecision.agent_profile_id == config.agent_profile_id,
                    )
                )
                session.add(
                    ExternalCall(
                        id=call_id,
                        call_kind="LLM_DECISION",
                        provider="OPENAI_COMPATIBLE",
                        operation="chat.completions.stream",
                        model=config.model_id,
                        attempt_no=attempt_no,
                        status="STARTED",
                        match_id=config.match_id,
                        speech_id=envelope.speech_id,
                        agent_decision_id=decision.id if decision is not None else None,
                        decision_round_id=decision_round_id,
                        opportunity_id=envelope.opportunity_id,
                        connection_epoch=envelope.connection_epoch,
                        context_version=envelope.context_version,
                        capture_version=PROVIDER_CAPTURE_VERSION,
                        capture_storage_mode="ASYNC_V1",
                        captured_at=datetime.now(UTC),
                        source_kind="MATCH",
                        logical_call_id=logical_call_id,
                        started_at=datetime.now(UTC),
                    )
                )
        return call_id

    async def _start_generation_call(
        self, generation_id: UUID, config: AgentConfig, *, logical_call_id: UUID
    ) -> UUID:
        call_id = uuid4()
        async with self._session_factory() as session:
            async with session.begin():
                generation = await session.get(AgentGeneration, generation_id)
                if generation is None:
                    raise LlmProviderError("agent_generation_not_found")
                session.add(
                    ExternalCall(
                        id=call_id,
                        call_kind="LLM_SPEECH",
                        provider="OPENAI_COMPATIBLE",
                        operation="chat.completions.stream",
                        model=config.model_id,
                        attempt_no=generation.attempt_no,
                        status="STARTED",
                        match_id=config.match_id,
                        agent_generation_id=generation_id,
                        generation_id=generation_id,
                        context_version=config.context_version,
                        capture_version=PROVIDER_CAPTURE_VERSION,
                        capture_storage_mode="ASYNC_V1",
                        captured_at=datetime.now(UTC),
                        source_kind="MATCH",
                        logical_call_id=logical_call_id,
                        started_at=datetime.now(UTC),
                    )
                )
        return call_id

    async def _start_tts_call(
        self, run: AgentRun, config: AgentConfig, generation_id: UUID
    ) -> UUID:
        call_id = uuid4()
        async with self._session_factory() as session:
            async with session.begin():
                session.add(
                    ExternalCall(
                        id=call_id,
                        call_kind="TTS",
                        provider="BAILIAN",
                        operation="duplex.server_commit",
                        model=self._settings.tts_model,
                        voice=config.voice,
                        attempt_no=1,
                        status="STARTED",
                        match_id=run.match_id,
                        agent_generation_id=generation_id,
                        generation_id=generation_id,
                        context_version=config.context_version,
                        capture_version=PROVIDER_CAPTURE_VERSION,
                        capture_storage_mode="ASYNC_V1",
                        captured_at=datetime.now(UTC),
                        source_kind="MATCH",
                        logical_call_id=run.tts_logical_call_id,
                        started_at=datetime.now(UTC),
                    )
                )
        return call_id

    async def _finish_external_call(
        self,
        call_id: UUID | None,
        *,
        request_payload: dict[str, Any] | None = None,
        response_payload: dict[str, Any] | None = None,
        first_result_latency_ms: int | None = None,
        completed_latency_ms: int | None = None,
        completion_tokens: int | None = None,
        audio_bytes: int | None = None,
        audio_duration_ms: int | None = None,
        provider_capture: ProviderCallCapture | None = None,
        capture_owner: AgentRun | None = None,
    ) -> None:
        call: ExternalCall | None = None
        try:
            if call_id is None:
                return
            async with self._session_factory() as session:
                async with session.begin():
                    call = await session.get(ExternalCall, call_id, with_for_update=True)
                    if call is None:
                        return
                    if request_payload is not None:
                        call.request_blob_id = await store_content_blob(
                            session, content_kind="REQUEST", payload=request_payload
                        )
                    if response_payload is not None:
                        call.response_blob_id = await store_content_blob(
                            session, content_kind="RESPONSE", payload=response_payload
                        )
                    call.status = "SUCCEEDED"
                    call.capture_storage_mode = "ASYNC_V1"
                    call.first_result_latency_ms = first_result_latency_ms
                    call.completed_latency_ms = completed_latency_ms
                    call.completion_tokens = completion_tokens
                    call.audio_bytes = audio_bytes
                    call.audio_duration_ms = audio_duration_ms
                    call.first_result_at = _at_latency(call.started_at, first_result_latency_ms)
                    call.completed_at = datetime.now(UTC)
                    await session.flush()
            self._submit_committed_capture(call, provider_capture, capture_owner=capture_owner)
        finally:
            if provider_capture is not None and self._owns_capture(capture_owner, provider_capture):
                provider_capture.close()

    @staticmethod
    def _owns_capture(owner: AgentRun | None, capture: ProviderCallCapture) -> bool:
        return owner is None or owner.llm_capture is capture or owner.tts_capture is capture

    def _submit_committed_capture(
        self,
        call: ExternalCall | None,
        capture: ProviderCallCapture | None,
        *,
        capture_owner: AgentRun | None = None,
    ) -> None:
        """Transfer bounded diagnostics only after the authoritative commit.

        No await separates prepare/submit. A cancellation owner that detached
        the capture is responsible for its historical handoff instead.
        """
        if call is None or capture is None or not self._owns_capture(capture_owner, capture):
            return
        submission: CaptureSubmission | None = None
        try:
            submission = prepare_capture(self._capture_writer, call, capture)
            submit_capture(self._capture_writer, submission)
        except Exception as error:
            # Optional diagnostics cannot reverse a successful business result
            # or turn it into another provider attempt.
            logger.warning(
                "committed Agent capture unavailable",
                extra={
                    "error_code": "agent_capture_unavailable",
                    "external_call_id": str(call.id),
                    "exception_type": type(error).__name__,
                },
            )
        finally:
            if submission is not None:
                submission.close()
            capture.close()

    async def _fail_external_call(
        self,
        call_id: UUID | None,
        error_code: str,
        *,
        cancelled: bool = False,
        response_payload: dict[str, Any] | None = None,
        provider_capture: ProviderCallCapture | None = None,
    ) -> None:
        submission: CaptureSubmission | None = None
        try:
            if call_id is None:
                return
            async with self._session_factory() as session:
                async with session.begin():
                    call = await session.get(ExternalCall, call_id, with_for_update=True)
                    if call is None or call.status != "STARTED":
                        return
                    if response_payload is not None:
                        call.response_blob_id = await store_content_blob(
                            session, content_kind="RESPONSE", payload=response_payload
                        )
                    call.status = "CANCELLED" if cancelled else "FAILED"
                    call.capture_storage_mode = "ASYNC_V1"
                    call.error_code = error_code
                    call.completed_at = datetime.now(UTC)
                    await session.flush()
                    if provider_capture is not None:
                        submission = prepare_capture(self._capture_writer, call, provider_capture)
            submit_capture(self._capture_writer, submission)
        finally:
            if submission is not None:
                submission.close()
            if provider_capture is not None:
                provider_capture.close()

    async def _create_generation(self, config: AgentConfig, attempt_no: int) -> UUID:
        generation_id = uuid4()
        snapshot = {
            "action_key": config.action_key,
            "context_version": config.context_version,
            "model_id": config.model_id,
            "max_tokens": config.max_tokens,
            "message_count": len(config.messages),
            "prompt_version": config.prompt_version,
            "context_policy_version": config.context_policy_version,
            "history_truncated": config.history_truncated,
            "capture_version": CAPTURE_VERSION,
        }
        async with self._session_factory() as session:
            async with session.begin():
                request_payload = {
                    "model": config.model_id,
                    "messages": config.messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "max_tokens": config.max_tokens,
                    "prompt_version": config.prompt_version,
                    "context_policy_version": config.context_policy_version,
                    "history_truncated": config.history_truncated,
                    "generation_params": {
                        **config.generation_params,
                        "enable_thinking": config.generation_params.get("enable_thinking", False),
                    },
                }
                request_blob_id = await store_content_blob(
                    session, content_kind="REQUEST", payload=request_payload
                )
                session.add(
                    AgentGeneration(
                        id=generation_id,
                        match_id=config.match_id,
                        action_key=config.action_key,
                        agent_profile_id=config.agent_profile_id,
                        context_version=config.context_version,
                        attempt_no=attempt_no,
                        input_snapshot=snapshot,
                        call_type="LLM_SPEECH",
                        provider="OPENAI_COMPATIBLE",
                        model_snapshot=config.model_id,
                        generation_params_snapshot=cast(
                            dict[str, Any], request_payload["generation_params"]
                        ),
                        request_blob_id=request_blob_id,
                        capture_version=CAPTURE_VERSION,
                        capture_completeness="COMPLETE",
                    )
                )
        return generation_id

    async def _mark_generation_llm_ready(
        self,
        generation_id: UUID,
        result: object,
        provider_capture: ProviderCallCapture | None,
        *,
        capture_owner: AgentRun | None = None,
    ) -> None:
        from .llm import LlmStreamResult

        if not isinstance(result, LlmStreamResult):
            return
        call: ExternalCall | None = None
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    generation = await session.get(
                        AgentGeneration, generation_id, with_for_update=True
                    )
                    if (
                        generation is None
                        or generation.status not in {"STARTED", "LLM_READY", "PLAYING"}
                        or generation.error_code is not None
                    ):
                        logger.info(
                            "retired Agent LLM readiness discarded",
                            extra={
                                "error_code": "agent_result_stale",
                                "generation_id": str(generation_id),
                            },
                        )
                        raise asyncio.CancelledError
                    generation.status = "PLAYING"
                    generation.first_token_latency_ms = result.first_token_latency_ms
                    generation.completed_latency_ms = result.completed_latency_ms
                    generation.completion_tokens = result.completion_tokens
                    generation.response_blob_id = await store_content_blob(
                        session,
                        content_kind="RESPONSE",
                        payload={"text": result.text},
                    )
                    call = await session.scalar(
                        select(ExternalCall)
                        .where(
                            ExternalCall.agent_generation_id == generation_id,
                            ExternalCall.call_kind == "LLM_SPEECH",
                        )
                        .with_for_update()
                    )
                    if call is not None:
                        completed_at = datetime.now(UTC)
                        call.status = "SUCCEEDED"
                        call.capture_storage_mode = "ASYNC_V1"
                        call.first_result_at = _at_latency(
                            call.started_at, result.first_token_latency_ms
                        )
                        call.completed_at = completed_at
                        call.first_result_latency_ms = result.first_token_latency_ms
                        call.completed_latency_ms = result.completed_latency_ms
                        call.completion_tokens = result.completion_tokens
                        await session.flush()
            # The immutable proof was accepted before commit. Actor advancement
            # does not turn a successfully committed historical call stale.
            self._submit_committed_capture(call, provider_capture, capture_owner=capture_owner)
        finally:
            if provider_capture is not None and self._owns_capture(capture_owner, provider_capture):
                provider_capture.close()

    async def _capture_generation_llm_ready(
        self,
        generation_id: UUID,
        call_id: UUID,
        provider_capture: ProviderCallCapture,
    ) -> UUID | None:
        """Accept a legacy direct helper invocation without synchronous raw I/O."""

        submission: CaptureSubmission | None = None
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    generation = await session.get(AgentGeneration, generation_id)
                    if (
                        generation is None
                        or generation.status not in {"PLAYING", "FINALIZED"}
                        or generation.error_code is not None
                    ):
                        return None
                    call = await session.scalar(
                        select(ExternalCall)
                        .where(
                            ExternalCall.id == call_id,
                            ExternalCall.agent_generation_id == generation_id,
                            ExternalCall.call_kind == "LLM_SPEECH",
                            ExternalCall.status == "SUCCEEDED",
                            ExternalCall.error_code.is_(None),
                        )
                        .with_for_update()
                    )
                    if call is None:
                        return None
                    call.capture_storage_mode = "ASYNC_V1"
                    await session.flush()
                    submission = prepare_capture(self._capture_writer, call, provider_capture)
            submit_capture(self._capture_writer, submission)
            return call_id
        finally:
            if submission is not None:
                submission.close()
            provider_capture.close()

    async def _persist_final(self, run: AgentRun) -> None:
        assert run.generation_id is not None
        assert run.storage_path is not None
        async with self._session_factory() as session:
            async with session.begin():
                generation = await session.get(
                    AgentGeneration, run.generation_id, with_for_update=True
                )
                if generation is not None:
                    generation.status = "FINALIZED"
                    generation.completed_at = datetime.now(UTC)
                asset = await session.scalar(
                    select(AgentAudioAsset).where(
                        AgentAudioAsset.generation_id == run.generation_id
                    )
                )
                if asset is None:
                    session.add(
                        AgentAudioAsset(
                            generation_id=run.generation_id,
                            task_id=str(run.tts_task_id or run.generation_id),
                            voice=run.voice,
                            rate=run.rate,
                            status="FINALIZED",
                            storage_path=str(run.storage_path),
                            byte_count=run.byte_count,
                            first_audio_latency_ms=run.first_audio_latency_ms,
                            tts_completed_latency_ms=run.tts_completed_latency_ms,
                            pcm_sample_count=run.played_samples,
                            duration_ms=run.played_samples * 1000 // 48_000,
                            finalized_at=datetime.now(UTC),
                        )
                    )
                tts_call = (
                    await session.get(ExternalCall, run.tts_call_id, with_for_update=True)
                    if run.tts_call_id is not None
                    else None
                )
                if tts_call is not None:
                    duration_ms = run.played_samples * 1000 // 48_000
                    tts_call.audio_bytes = run.byte_count
                    tts_call.audio_duration_ms = duration_ms

    async def _mark_generation_failed(
        self,
        generation_id: UUID | None,
        error_code: str,
        *,
        llm_capture: ProviderCallCapture | None = None,
        tts_capture: ProviderCallCapture | None = None,
        capture_owner: AgentRun | None = None,
    ) -> None:
        calls: list[ExternalCall] = []
        try:
            if generation_id is None:
                return
            async with self._session_factory() as session:
                async with session.begin():
                    generation = await session.get(
                        AgentGeneration, generation_id, with_for_update=True
                    )
                    if generation is None or generation.status in {"FINALIZED", "CANCELLED"}:
                        return
                    if generation.status == "FAILED":
                        # Later cancellation cleanup must retain the original
                        # failure identity and its first completion timestamp.
                        error_code = generation.error_code or error_code
                    else:
                        generation.status = "FAILED"
                        generation.error_code = error_code
                        generation.completed_at = datetime.now(UTC)
                    calls = list(
                        (
                            await session.scalars(
                                select(ExternalCall)
                                .where(
                                    ExternalCall.agent_generation_id == generation_id,
                                    ExternalCall.status.in_(("STARTED", "SUCCEEDED")),
                                )
                                .with_for_update()
                            )
                        ).all()
                    )
                    for call in calls:
                        if call.status != "STARTED":
                            # A successful commit may have completed while its
                            # awaiter was cancelled. Keep its result identity;
                            # the retirement owner can still retain its raw.
                            continue
                        call.status = "FAILED"
                        call.capture_storage_mode = "ASYNC_V1"
                        call.error_code = error_code
                        call.completed_at = datetime.now(UTC)
                    await session.flush()
            for call in calls:
                capture = llm_capture if call.call_kind == "LLM_SPEECH" else tts_capture
                if call.status == "SUCCEEDED" and (
                    capture is None or (capture.request is None and capture.response is None)
                ):
                    continue
                self._submit_committed_capture(call, capture, capture_owner=capture_owner)
        finally:
            if llm_capture is not None and self._owns_capture(capture_owner, llm_capture):
                llm_capture.close()
            if tts_capture is not None and self._owns_capture(capture_owner, tts_capture):
                tts_capture.close()

    async def _capture_failed_generation(
        self,
        generation_id: UUID,
        error_code: str,
        captures: dict[UUID, ProviderCallCapture],
    ) -> UUID | None:
        """Accept legacy direct calls using the asynchronous capture owner."""

        submissions: list[CaptureSubmission] = []
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    generation = await session.get(AgentGeneration, generation_id)
                    if (
                        generation is None
                        or generation.status != "FAILED"
                        or generation.error_code != error_code
                    ):
                        return None
                    calls = list(
                        (
                            await session.scalars(
                                select(ExternalCall)
                                .where(
                                    ExternalCall.id.in_(captures),
                                    ExternalCall.agent_generation_id == generation_id,
                                    ExternalCall.status == "FAILED",
                                    ExternalCall.error_code == error_code,
                                )
                                .with_for_update()
                            )
                        ).all()
                    )
                    for call in calls:
                        call.capture_storage_mode = "ASYNC_V1"
                    await session.flush()
                    for call in calls:
                        submission = prepare_capture(self._capture_writer, call, captures[call.id])
                        if submission is not None:
                            submissions.append(submission)
            for submission in submissions:
                submit_capture(self._capture_writer, submission)
            return generation_id
        finally:
            for submission in submissions:
                submission.close()
            for capture in captures.values():
                capture.close()

    async def _cleanup_attempt(self, run: AgentRun) -> None:
        # Detach the complete retired attempt before the first await. Late
        # cleanup must only hold this snapshot, never a later attempt's refs.
        tts_task, run.tts_task = run.tts_task, None
        decoder, run.decoder = run.decoder, None
        source, run.source = run.source, None
        room, run.room = run.room, None
        spool_path, run.spool_path = run.spool_path, None
        run.text_queue = None
        for capture in (run.llm_capture, run.tts_capture):
            if capture is not None:
                capture.close()
        run.llm_capture = run.tts_capture = None
        if decoder is not None:
            _try_agent_cleanup(decoder.finish_input, resource="agent_decoder_input")
        if source is not None:
            _try_agent_cleanup(source.clear_queue, resource="agent_audio_queue")
        if spool_path is not None:
            _try_agent_cleanup(lambda: spool_path.unlink(missing_ok=True), resource="agent_spool")

        # Start every independent close before waiting. The TTS cancellation
        # budget and native cleanup budgets cannot starve another resource.
        cleanup_tasks: list[asyncio.Task[None]] = []
        if tts_task is not None:
            cleanup_tasks.append(asyncio.create_task(self._cancel_agent_task(tts_task)))
        if decoder is not None:
            cleanup_tasks.append(
                asyncio.create_task(
                    _bounded_agent_cleanup_call(decoder.close, resource="agent_decoder")
                )
            )
        if source is not None:
            cleanup_tasks.append(
                asyncio.create_task(
                    _bounded_agent_cleanup_call(source.aclose, resource="agent_audio_source")
                )
            )
        if room is not None:
            cleanup_tasks.append(
                asyncio.create_task(
                    _bounded_agent_cleanup_call(room.disconnect, resource="agent_room")
                )
            )
        if not cleanup_tasks:
            return
        try:
            await asyncio.wait(cleanup_tasks)
        except asyncio.CancelledError:
            # Never wait for cancellation-resistant provider/native children
            # here. Each bounded wrapper also consumes its detached child.
            for task in cleanup_tasks:
                task.cancel()
                task.add_done_callback(_consume_detached_cleanup)
            raise
        for task in cleanup_tasks:
            _consume_detached_cleanup(task)

    async def _cancel_run(self, run: AgentRun) -> None:
        # Fence publication before awaiting any potentially stuck LiveKit/API cleanup.
        # A cancelled generation must never enqueue another frame after reset commits.
        run.media_fenced = True
        llm_capture, run.llm_capture = run.llm_capture, None
        tts_capture, run.tts_capture = run.tts_capture, None
        try:
            async with self._media_lock:
                if run.source is not None:
                    _try_agent_cleanup(run.source.clear_queue, resource="agent_audio_queue")
            # Close the match-scoped socket before cancelling its task. The
            # detached captures remain owned until this retirement completes.
            await self._discard_tts_connection(run.match_id)
            if run.task is not None and not run.task.done():
                await self._cancel_agent_task(run.task)
            try:
                await asyncio.wait_for(
                    self._cleanup_attempt(run), timeout=AGENT_TASK_CANCEL_TIMEOUT_SECONDS
                )
            except TimeoutError:
                logger.warning(
                    "agent attempt cleanup timed out",
                    extra={"error_code": "agent_cleanup_timeout"},
                )
            if run.generation_id is not None:
                await self._mark_generation_failed(
                    run.generation_id,
                    "agent_cancelled",
                    llm_capture=llm_capture,
                    tts_capture=tts_capture,
                )
        finally:
            if llm_capture is not None:
                llm_capture.close()
            if tts_capture is not None:
                tts_capture.close()

    async def _cancel_agent_task(self, task: asyncio.Task[Any]) -> None:
        task.cancel()
        try:
            done, pending = await asyncio.wait({task}, timeout=AGENT_TASK_CANCEL_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            task.add_done_callback(_consume_detached_cleanup)
            raise
        if pending:
            logger.warning(
                "agent task cancellation timed out",
                extra={
                    "error_code": "agent_cancel_timeout",
                    "task_name": task.get_name(),
                },
            )
            # Cancellation is cooperative.  A provider/native task may still
            # finish later; keep a done callback attached so a late exception
            # is consumed even though the run has already been removed from
            # the match registry.
            task.add_done_callback(_consume_detached_cleanup)
            return
        # Consume unexpected cleanup errors so they do not become unhandled
        # event-loop warnings. Provider failures use the normal callback path.
        for completed in done:
            if not completed.cancelled():
                completed.exception()

    async def _close_run(self, run: AgentRun) -> None:
        decoder, run.decoder = run.decoder, None
        if decoder is not None:
            _try_agent_cleanup(decoder.finish_input, resource="agent_decoder_input")
            await _bounded_agent_cleanup(decoder.close(), resource="agent_decoder")
        source, run.source = run.source, None
        if source is not None:
            await _bounded_agent_cleanup(source.aclose(), resource="agent_audio_source")
        room, run.room = run.room, None
        if room is not None:
            await _bounded_agent_cleanup(room.disconnect(), resource="agent_room")


def _error_code(error: Exception) -> str:
    if isinstance(error, (LlmProviderError, TtsProviderError)):
        return error.code
    return "agent_pipeline_failed"


def _at_latency(started_at: datetime, latency_ms: int | None) -> datetime | None:
    if latency_ms is None:
        return None
    return started_at + timedelta(milliseconds=max(0, latency_ms))


AGENT_SUBTITLE_LEAD_MS = 300
AGENT_FINALIZATION_TIMEOUT_SECONDS = 15.0


def _played_prefix(text: str, played_ms: int, chars_per_second: float) -> str:
    count = max(
        0,
        int((played_ms + AGENT_SUBTITLE_LEAD_MS) / 1000 * chars_per_second),
    )
    return text[:count]


def _side_for_action(action_key: str, rule_snapshot: dict[str, Any]) -> str:
    for stage in rule_snapshot.get("stages", []):
        for action in stage.get("actions", []):
            if f"{stage.get('position')}:{action.get('position')}" == action_key:
                return str(action.get("side") or "")
    return ""


def _action_kind_for(action_key: str, rule_snapshot: dict[str, Any]) -> str:
    stage_position = int(action_key.split(":", 1)[0])
    for stage in rule_snapshot.get("stages", []):
        if int(stage.get("position", 0)) != stage_position:
            continue
        for action in stage.get("actions", []):
            if f"{stage_position}:{action.get('position')}" == action_key:
                return str(action.get("action_kind") or stage.get("stage_kind") or "FIXED_SPEECH")
        return str(stage.get("stage_kind") or "FIXED_SPEECH")
    return "FIXED_SPEECH"


def _prompt_side_values(
    side: str, *, affirmative_position: str, negative_position: str
) -> tuple[str, str]:
    if side == "AFFIRMATIVE":
        return "正方", affirmative_position
    if side == "NEGATIVE":
        return "反方", negative_position
    raise LlmProviderError("agent_side_invalid")


def _stage_name(stage: dict[str, Any]) -> str:
    position = int(stage.get("position", 0))
    return str(stage.get("name") or f"第 {position} 阶段")


def _stage_name_for_action(action_key: str, rule_snapshot: dict[str, Any]) -> str:
    stage_position = int(action_key.split(":", 1)[0])
    for stage in rule_snapshot.get("stages", []):
        if int(stage.get("position", 0)) == stage_position:
            return _stage_name(stage)
    return f"第 {stage_position} 阶段"


def _stage_key_for_action(action_key: str, rule_snapshot: dict[str, Any]) -> str:
    stage_position = int(action_key.split(":", 1)[0])
    return f"stage-{stage_position}"


def _stage_kind_for_action(action_key: str, rule_snapshot: dict[str, Any]) -> str:
    stage_position = int(action_key.split(":", 1)[0])
    for stage in rule_snapshot.get("stages", []):
        if int(stage.get("position", 0)) == stage_position:
            return f"stage-{stage_position}"
    return ""


def _seat_label(seat_no: int) -> str:
    return {1: "一辩", 2: "二辩", 3: "三辩", 4: "四辩"}.get(seat_no, f"{seat_no}辩")


def _current_and_next_stage(action_key: str, rule_snapshot: dict[str, Any]) -> tuple[str, str]:
    stage_position = int(action_key.split(":", 1)[0])
    raw_stages = cast(list[Any], rule_snapshot.get("stages", []))
    stages: list[dict[str, Any]] = [
        cast(dict[str, Any], stage) for stage in raw_stages if isinstance(stage, dict)
    ]
    stages.sort(key=lambda stage: int(stage.get("position", 0)))
    current = next(
        (stage for stage in stages if int(stage.get("position", 0)) == stage_position),
        {"position": stage_position},
    )
    following = next(
        (stage for stage in stages if int(stage.get("position", 0)) > stage_position), None
    )
    return _stage_name(current), _stage_name(following) if following else "比赛结束"


def _debate_position(action_key: str, rule_snapshot: dict[str, Any]) -> str:
    for stage in rule_snapshot.get("stages", []):
        for action in stage.get("actions", []):
            if f"{stage.get('position')}:{action.get('position')}" == action_key:
                if action.get("side") not in {"AFFIRMATIVE", "NEGATIVE"}:
                    return "自由辩论"
                side = "正方" if action.get("side") == "AFFIRMATIVE" else "反方"
                seat_no = action.get("seat_no")
                return f"{side}{seat_no}辩" if seat_no else side
    return "自由辩论"


def _speaker_label(side: str, seat_no: int) -> str:
    return f"{'正方' if side == 'AFFIRMATIVE' else '反方'}{seat_no}辩"


__all__ = ["AgentRuntime", "AgentRuntimeCallbacks"]

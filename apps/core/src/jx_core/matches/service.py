"""Persistence and in-process registry for one authoritative match actor."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..agent.runtime import AgentRuntime, AgentRuntimeCallbacks
from ..asr.protocol import FunAsrError
from ..audit.service import AuditService
from ..auth.errors import AuthError
from ..data_capture.provider import PROVIDER_CAPTURE_VERSION, ProviderCallCapture
from ..data_capture.provider_writer import (
    ProviderCaptureWriter,
    prepare_capture,
    submit_capture,
)
from ..experiments.permissions import is_experiment_side_controller, resolve_room_link
from ..models import (
    AgentFreeDebateDecision,
    AgentGeneration,
    AgentProfile,
    AsrSegment,
    BackgroundTask,
    ExperimentMatchAttempt,
    ExternalCall,
    FreeDebateOpportunity,
    HumanHandEvent,
    Match,
    MatchFile,
    MatchParticipant,
    PostmatchSurveyTask,
    Room,
    ScheduledMatch,
    Seat,
    SpeakerAllocation,
    Speech,
    User,
)
from ..models import MatchEvent as MatchEventRow
from ..runtime_identity import CallbackEnvelope
from ..survey_definitions import POSTMATCH_QUESTIONNAIRE_VERSION
from .domain import (
    AgentDecisionState,
    DebateParticipant,
    MatchAction,
    MatchActor,
    MatchCommand,
    MatchCommandResult,
    MatchDomainError,
    MatchEvent,
    MatchRuntimeState,
    MatchRuntimeView,
    compile_linear_actions,
)

logger = logging.getLogger("jx-core.matches")
HUMAN_SPEECH_START_TIMEOUT_SECONDS = 10.0
ASR_FINALIZATION_TIMEOUT_SECONDS = 20.0
SPEECH_MEDIA_RECOVERY_GRACE_SECONDS = 3.0
AGENT_SOURCE_FINAL_WAIT_SECONDS = 10.0
EMERGENCY_RESOURCE_CLEANUP_TIMEOUT_SECONDS = 5.0
TERMINAL_ACTOR_RETENTION_SECONDS = 60.0
LATE_EXPERIMENT_ROUND_LIMIT = 4096
PENDING_AGENT_FULL_TEXT_LIMIT = 128
_SPEECH_START_ERROR_CODES = frozenset(
    {
        "asr_not_configured",
        "asr_start_timeout",
        "asr_task_failed",
        "asr_stream_failed",
        "asr_callback_timeout",
        "asr_receiver_closed",
        "asr_finalization_pending",
        "asr_not_ready",
        "asr_recovery_required",
        "livekit_not_configured",
    }
)


def _terminal_room_end_at(
    *,
    match_status: str,
    current_room_end: datetime | None,
    match_end: datetime | None,
    now: datetime,
) -> datetime | None:
    """Return the idempotent room lifecycle boundary for a terminal match."""

    if match_status not in ("FINISHED", "TERMINATED"):
        return current_room_end
    return current_room_end or match_end or now


def _active_opportunity_for_source_query(*, match_id: UUID, source_speech_id: UUID) -> Any:
    """Build a bounded lookup for the opportunity owning one source speech.

    ``source_speech_id`` is the canonical column.  The JSON fallback keeps old
    rows (created before that column was backfilled) recoverable without
    materialising every ACTIVE opportunity in Python.  The ``LIMIT 1`` is an
    invariant guard against duplicate stale rows from an interrupted retry.
    """

    return (
        select(FreeDebateOpportunity)
        .where(
            FreeDebateOpportunity.match_id == match_id,
            FreeDebateOpportunity.status == "ACTIVE",
            (
                (FreeDebateOpportunity.source_speech_id == source_speech_id)
                | (
                    FreeDebateOpportunity.frozen_context["source_speech_id"].as_string()
                    == str(source_speech_id)
                )
            ),
        )
        .order_by(FreeDebateOpportunity.id)
        .limit(1)
        .with_for_update()
    )


_ASR_AUDIO_ERROR_CODES = frozenset(
    {"asr_empty_audio", "asr_pcm_queue_full", "asr_no_first_frame", "asr_audio_missing"}
)
_ASR_CONFIG_ERROR_CODES = frozenset(
    {"asr_not_configured", "asr_protocol_mismatch", "asr_identity_invalid", "asr_invalid_config"}
)


def classify_asr_error(code: str) -> Literal["AUDIO", "TRANSIENT", "CONFIG", "UNKNOWN"]:
    """Return a low-cardinality, provider-agnostic ASR failure class."""
    normalized = str(code or "").strip().lower()
    if normalized in _ASR_AUDIO_ERROR_CODES:
        return "AUDIO"
    if normalized in _ASR_CONFIG_ERROR_CODES:
        return "CONFIG"
    if any(
        token in normalized
        for token in ("rate", "limit", "thrott", "timeout", "network", "disconnect", "unavailable")
    ):
        return "TRANSIENT"
    if normalized in {"asr_task_failed", "asr_stream_failed"}:
        return "TRANSIENT"
    return "UNKNOWN"


def _speech_start_error_code(error: BaseException) -> str:
    if isinstance(error, TimeoutError):
        return "asr_start_timeout"
    code = str(getattr(error, "code", ""))
    return code if code in _SPEECH_START_ERROR_CODES else "asr_start_timeout"


def _file_size(path: str | None) -> int:
    if not path:
        return 0
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def _optional_uuid(value: object) -> UUID | None:
    """Parse external/event UUID values without accepting stringified nulls."""
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or normalized.lower() in {"none", "null"}:
        return None
    try:
        return UUID(normalized)
    except ValueError as error:
        raise MatchDomainError("invalid_event_payload") from error


def _required_uuid(value: object) -> UUID:
    parsed = _optional_uuid(value)
    if parsed is None:
        raise MatchDomainError("invalid_event_payload")
    return parsed


def _frozen_agent_name(room: Room, agent: AgentProfile) -> str:
    for raw in (room.format_snapshot or {}).get("agents", []):
        if isinstance(raw, dict):
            item = cast(dict[str, Any], raw)
            if str(item.get("id")) == str(agent.id):
                return str(item.get("name") or agent.name)
    return agent.name


def _action_snapshot(action: MatchAction) -> dict[str, Any]:
    return {
        "stage_position": action.stage_position,
        "action_position": action.action_position,
        "action_kind": action.action_kind,
        "duration_seconds": action.duration_seconds,
        "side": action.side,
        "seat_no": action.seat_no,
        "speaker_user_id": str(action.speaker_user_id) if action.speaker_user_id else None,
        "speaker_kind": action.speaker_kind,
        "agent_profile_id": str(action.agent_profile_id) if action.agent_profile_id else None,
        "host_audio_path": action.host_audio_path,
        "host_audio_duration_ms": action.host_audio_duration_ms,
        "participants": [
            {
                "side": item.side,
                "seat_no": item.seat_no,
                "user_id": str(item.user_id) if item.user_id else None,
                "agent_profile_id": (str(item.agent_profile_id) if item.agent_profile_id else None),
            }
            for item in action.participants
        ],
        "free_max_speech_seconds": action.free_max_speech_seconds,
        "free_starting_side": action.free_starting_side,
    }


class SpeechRuntime(Protocol):
    @property
    def model_id(self) -> str: ...

    async def start_speech(
        self,
        match_id: UUID,
        speech_id: UUID,
        user_id: UUID,
        envelope: CallbackEnvelope,
        *,
        max_audio_duration_ms: int = 180_000,
    ) -> None: ...

    async def finish_speech(
        self, match_id: UUID, speech_id: UUID, *, deadline_mono: float | None = None
    ) -> None: ...

    def finalization_callback_is_current(self, *, envelope: CallbackEnvelope) -> bool: ...

    def speech_callback_envelope(
        self, match_id: UUID, speech_id: UUID
    ) -> CallbackEnvelope | None: ...

    def recovery_source_info(
        self, match_id: UUID, speech_id: UUID
    ) -> Mapping[str, object] | None: ...

    async def restore_recovery_source(
        self,
        match_id: UUID,
        speech_id: UUID,
        user_id: UUID,
        envelope: CallbackEnvelope,
        *,
        audio_storage_path: str,
        audio_duration_ms: int,
        max_audio_duration_ms: int,
        last_segment_no: int = 0,
    ) -> None: ...

    async def retry_from_recording(
        self, match_id: UUID, speech_id: UUID, envelope: CallbackEnvelope, *, deadline_mono: float
    ) -> None: ...

    async def pause_speech(self, match_id: UUID, speech_id: UUID) -> None: ...

    async def reset_speech(self, match_id: UUID) -> None: ...

    async def close_match(self, match_id: UUID) -> None: ...

    async def close(self) -> None: ...


class PostmatchRuntime(Protocol):
    async def request_judge(self, match_id: UUID, *, force: bool = False) -> UUID | None: ...


def state_snapshot(state: MatchRuntimeState) -> dict[str, Any]:
    action = state.current_action
    return {
        "match_id": str(state.match_id),
        "match_seed": state.match_seed,
        "status": state.status,
        "action_state": state.action_state,
        "sequence": state.sequence,
        "current_action_index": state.current_action_index,
        "current_action": _action_snapshot(action) if action else None,
        "current_speech_id": str(state.current_speech_id) if state.current_speech_id else None,
        "current_speaker_user_id": (
            str(state.current_speaker_user_id) if state.current_speaker_user_id else None
        ),
        "current_agent_profile_id": (
            str(state.current_agent_profile_id) if state.current_agent_profile_id else None
        ),
        "interim_text": state.interim_text,
        "asr_failed_speech_id": str(state.asr_failed_speech_id)
        if state.asr_failed_speech_id
        else None,
        "speech_start_sequence": state.speech_start_sequence,
        "speech_remaining_ms": state.speech_remaining_ms,
        "speech_budget_start_ms": state.speech_budget_start_ms,
        "speech_consumed_ms": state.speech_consumed_ms,
        "speech_budget_settled": state.speech_budget_settled,
        "stage_generation": state.stage_generation,
        "stage_checkpoint": dict(state.stage_checkpoint) if state.stage_checkpoint else None,
        "emergency_operation": dict(state.emergency_operation)
        if state.emergency_operation
        else None,
        "host_audio_remaining_ms": state.host_audio_remaining_ms,
        "host_audio_deadline_ms": state.host_audio_deadline_ms,
        "current_speaker_side": state.current_speaker_side,
        "current_speaker_seat_no": state.current_speaker_seat_no,
        "free_holder_side": state.free_holder_side,
        "free_source_speech_id": str(state.free_source_speech_id)
        if state.free_source_speech_id
        else None,
        "free_affirmative_remaining_ms": state.free_affirmative_remaining_ms,
        "free_negative_remaining_ms": state.free_negative_remaining_ms,
        "hand_queue": [str(item) for item in state.hand_queue],
        "agent_hand_queue": [str(item) for item in state.agent_hand_queue],
        "agent_selection_mode": state.agent_selection_mode,
        "agent_decision_round_id": (
            str(state.agent_decision_round_id) if state.agent_decision_round_id else None
        ),
        "agent_decisions": [
            {
                "agent_profile_id": str(item.agent_profile_id),
                "side": item.side,
                "seat_no": item.seat_no,
                "status": item.status,
                "should_speak": item.should_speak,
                "decision_reason": item.decision_reason,
                "willingness": item.willingness,
                "result_order": item.result_order,
                "failed": item.failed,
            }
            for item in state.agent_decisions
        ],
        "hand_window_open": state.hand_window_open,
        "experiment_mode": state.experiment_mode,
        "formal_4v4": state.formal_4v4,
        "experiment_attempt_id": (
            str(state.experiment_attempt_id) if state.experiment_attempt_id else None
        ),
        "opportunity_id": str(state.opportunity_id) if state.opportunity_id else None,
        "allocated_opportunity_id": (
            str(state.allocated_opportunity_id) if state.allocated_opportunity_id else None
        ),
        "opportunity_generation": state.opportunity_generation,
        "selection_phase": state.selection_phase,
        "agent_effective_status": state.agent_effective_status,
        "selection_remaining_ms": state.selection_remaining_ms,
        "human_wait_remaining_ms": state.human_wait_remaining_ms,
        "paused_from_status": state.paused_from_status,
        "paused_from_action_state": state.paused_from_action_state,
        "pause_initiator_user_id": (
            str(state.pause_initiator_user_id) if state.pause_initiator_user_id else None
        ),
        "offline_user_id": str(state.offline_user_id) if state.offline_user_id else None,
        "offline_since_ms": {
            str(user_id): since_ms for user_id, since_ms in state.offline_since_ms
        },
        "connection_epochs": {str(user_id): epoch for user_id, epoch in state.connection_epochs},
        "error_code": state.error_code,
        "actions": [_action_snapshot(item) for item in state.actions],
    }


def _state_from_snapshot(match_id: UUID, snapshot: dict[str, Any]) -> MatchRuntimeState:
    actions: list[MatchAction] = []
    for raw in snapshot.get("actions", []):
        actions.append(
            MatchAction(
                stage_position=int(raw["stage_position"]),
                action_position=int(raw["action_position"]),
                action_kind=raw["action_kind"],
                duration_seconds=int(raw["duration_seconds"]),
                side=raw.get("side"),
                seat_no=raw.get("seat_no"),
                speaker_user_id=UUID(raw["speaker_user_id"])
                if raw.get("speaker_user_id")
                else None,
                speaker_kind=raw.get("speaker_kind", "HUMAN"),
                agent_profile_id=UUID(raw["agent_profile_id"])
                if raw.get("agent_profile_id")
                else None,
                host_audio_path=raw.get("host_audio_path"),
                host_audio_duration_ms=(
                    int(raw["host_audio_duration_ms"])
                    if raw.get("host_audio_duration_ms") is not None
                    else None
                ),
                participants=tuple(
                    DebateParticipant(
                        side=str(item["side"]),
                        seat_no=int(item["seat_no"]),
                        user_id=UUID(item["user_id"]) if item.get("user_id") else None,
                        agent_profile_id=(
                            UUID(item["agent_profile_id"]) if item.get("agent_profile_id") else None
                        ),
                    )
                    for item in raw.get("participants", [])
                ),
                free_max_speech_seconds=int(raw.get("free_max_speech_seconds", 60)),
                free_starting_side=str(raw.get("free_starting_side", "AFFIRMATIVE")),
            )
        )
    raw_agent_decisions = snapshot.get("agent_decisions", [])
    decision_items = (
        cast(list[dict[str, Any]], raw_agent_decisions)
        if isinstance(raw_agent_decisions, list)
        else []
    )
    decisions: list[AgentDecisionState] = []
    for item in decision_items:
        status_value = str(item.get("status", "DECIDING"))
        status = status_value if status_value in {"DECIDING", "HAND", "SKIP"} else "DECIDING"
        should_value = item.get("should_speak")
        willingness_value = item.get("willingness")
        result_order_value = item.get("result_order")
        decisions.append(
            AgentDecisionState(
                agent_profile_id=UUID(str(item["agent_profile_id"])),
                side=str(item["side"]),
                seat_no=int(item["seat_no"]),
                status=cast(Literal["DECIDING", "HAND", "SKIP"], status),
                should_speak=should_value if isinstance(should_value, bool) else None,
                decision_reason=(
                    str(item.get("decision_reason"))
                    if isinstance(item.get("decision_reason"), str)
                    else None
                ),
                willingness=(
                    float(willingness_value)
                    if isinstance(willingness_value, (int, float))
                    else None
                ),
                result_order=(
                    int(result_order_value) if isinstance(result_order_value, int) else None
                ),
                failed=bool(item.get("failed", False)),
            )
        )
    host_audio_remaining_ms = snapshot.get("host_audio_remaining_ms")
    host_audio_deadline_ms = snapshot.get("host_audio_deadline_ms")
    if isinstance(host_audio_deadline_ms, int):
        host_audio_remaining_ms = max(
            0, host_audio_deadline_ms - int(datetime.now(UTC).timestamp() * 1000)
        )
    return MatchRuntimeState(
        match_id=match_id,
        match_seed=int(snapshot.get("match_seed", 0)),
        status=snapshot["status"],
        action_state=snapshot["action_state"],
        actions=tuple(actions),
        current_action_index=int(snapshot.get("current_action_index", 0)),
        sequence=int(snapshot.get("sequence", 0)),
        current_speech_id=UUID(snapshot["current_speech_id"])
        if snapshot.get("current_speech_id")
        else None,
        current_speaker_user_id=UUID(snapshot["current_speaker_user_id"])
        if snapshot.get("current_speaker_user_id")
        else None,
        current_agent_profile_id=UUID(snapshot["current_agent_profile_id"])
        if snapshot.get("current_agent_profile_id")
        else None,
        interim_text=str(snapshot.get("interim_text", "")),
        asr_failed_speech_id=_optional_uuid(snapshot.get("asr_failed_speech_id")),
        speech_start_sequence=(
            int(snapshot["speech_start_sequence"])
            if snapshot.get("speech_start_sequence") is not None
            else None
        ),
        speech_remaining_ms=snapshot.get("speech_remaining_ms"),
        speech_budget_start_ms=snapshot.get("speech_budget_start_ms"),
        speech_consumed_ms=int(snapshot.get("speech_consumed_ms", 0)),
        speech_budget_settled=bool(snapshot.get("speech_budget_settled", False)),
        stage_generation=int(snapshot.get("stage_generation", 0)),
        stage_checkpoint=snapshot.get("stage_checkpoint"),
        emergency_operation=snapshot.get("emergency_operation"),
        host_audio_remaining_ms=host_audio_remaining_ms,
        host_audio_deadline_ms=None,
        current_speaker_side=snapshot.get("current_speaker_side"),
        current_speaker_seat_no=snapshot.get("current_speaker_seat_no"),
        free_holder_side=snapshot.get("free_holder_side"),
        free_source_speech_id=UUID(snapshot["free_source_speech_id"])
        if snapshot.get("free_source_speech_id")
        else None,
        free_affirmative_remaining_ms=snapshot.get("free_affirmative_remaining_ms"),
        free_negative_remaining_ms=snapshot.get("free_negative_remaining_ms"),
        hand_queue=tuple(UUID(item) for item in snapshot.get("hand_queue", [])),
        agent_hand_queue=tuple(UUID(item) for item in snapshot.get("agent_hand_queue", [])),
        agent_selection_mode=snapshot.get("agent_selection_mode"),
        agent_decision_round_id=(
            UUID(snapshot["agent_decision_round_id"])
            if snapshot.get("agent_decision_round_id")
            else None
        ),
        agent_decisions=tuple(decisions),
        hand_window_open=bool(snapshot.get("hand_window_open", False)),
        experiment_mode=bool(snapshot.get("experiment_mode", False)),
        formal_4v4=bool(snapshot.get("formal_4v4", False)),
        experiment_attempt_id=(
            UUID(snapshot["experiment_attempt_id"])
            if snapshot.get("experiment_attempt_id")
            else None
        ),
        opportunity_id=(
            UUID(snapshot["opportunity_id"]) if snapshot.get("opportunity_id") else None
        ),
        allocated_opportunity_id=(
            UUID(snapshot["allocated_opportunity_id"])
            if snapshot.get("allocated_opportunity_id")
            else None
        ),
        opportunity_generation=int(snapshot.get("opportunity_generation", 0)),
        selection_phase=snapshot.get("selection_phase"),
        agent_effective_status=snapshot.get("agent_effective_status"),
        selection_remaining_ms=snapshot.get("selection_remaining_ms"),
        human_wait_remaining_ms=snapshot.get("human_wait_remaining_ms"),
        paused_from_status=snapshot.get("paused_from_status"),
        paused_from_action_state=snapshot.get("paused_from_action_state"),
        pause_initiator_user_id=(
            UUID(snapshot["pause_initiator_user_id"])
            if snapshot.get("pause_initiator_user_id")
            else None
        ),
        offline_user_id=(
            UUID(snapshot["offline_user_id"]) if snapshot.get("offline_user_id") else None
        ),
        offline_since_ms=tuple(
            (UUID(user_id), int(since_ms))
            for user_id, since_ms in snapshot.get("offline_since_ms", {}).items()
        ),
        connection_epochs=tuple(
            (UUID(user_id), int(epoch))
            for user_id, epoch in snapshot.get("connection_epochs", {}).items()
        ),
        error_code=snapshot.get("error_code"),
    )


class MatchRuntimeManager(AgentRuntimeCallbacks):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        capture_writer: ProviderCaptureWriter | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._capture_writer = capture_writer
        self._closed = False
        self._actors: dict[UUID, MatchActor] = {}
        self._subscribers: dict[UUID, set[asyncio.Queue[MatchEvent]]] = {}
        self._lock = asyncio.Lock()
        self._speech_runtime: SpeechRuntime | None = None
        self._agent_runtime: AgentRuntime | None = None
        self._postmatch_runtime: PostmatchRuntime | None = None
        self._presence_runtime: Any | None = None
        self._terminal_callback: Callable[[UUID], Coroutine[Any, Any, None]] | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._terminal_eviction_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._speech_presence_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._speech_presence_grace: set[asyncio.Task[Any]] = set()
        self._asr_final_deadlines: dict[tuple[UUID, UUID], float] = {}
        self._asr_retry_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._prepared_speech_starts: set[tuple[UUID, str]] = set()
        self._pending_agent_full_text: dict[tuple[UUID, UUID], tuple[CallbackEnvelope, str]] = {}
        self._late_experiment_rounds: set[UUID] = set()
        self._late_experiment_round_matches: dict[UUID, UUID] = {}

    def set_speech_runtime(self, runtime: SpeechRuntime) -> None:
        self._speech_runtime = runtime

    def _asr_model_id(self) -> str:
        value = getattr(self._speech_runtime, "model_id", None)
        return value if isinstance(value, str) and value else "unknown"

    async def _retire_asr_diagnostics(
        self, session: AsyncSession, *, match_id: UUID, speech_ids: set[UUID]
    ) -> None:
        """Close unfinished diagnostics when authority explicitly retires their input.

        A retired callback cannot finish these rows later. Cancellation records
        that fact without claiming recognition succeeded or changing old results.
        """
        if not speech_ids:
            return
        now = datetime.now(UTC)
        for segment in (
            await session.scalars(
                select(AsrSegment)
                .where(AsrSegment.speech_id.in_(speech_ids), AsrSegment.status == "STARTED")
                .with_for_update()
            )
        ).all():
            segment.status = "DISCARDED"
            segment.error_code = "asr_cancelled"
            segment.finalized_at = now
        for call in (
            await session.scalars(
                select(ExternalCall)
                .where(
                    ExternalCall.match_id == match_id,
                    ExternalCall.speech_id.in_(speech_ids),
                    ExternalCall.call_kind == "ASR",
                    ExternalCall.status == "STARTED",
                )
                .with_for_update()
            )
        ).all():
            call.status = "CANCELLED"
            call.error_code = "asr_cancelled"
            call.completed_at = now

    def asr_recovery_available(self, state: MatchRuntimeState) -> bool:
        check = getattr(self._speech_runtime, "recovery_source_available", None)
        return bool(
            state.asr_failed_speech_id is not None
            and check is not None
            and check(state.match_id, state.asr_failed_speech_id)
        )

    def set_agent_runtime(self, runtime: AgentRuntime) -> None:
        self._agent_runtime = runtime

    def _retire_pending_media_start(self, match_id: UUID) -> None:
        runtime = self._agent_runtime
        if runtime is not None and getattr(type(runtime), "retire_pending_media_start", None):
            # A real runtime exposes this synchronous ownership fence. Looking
            # at the type keeps older async test doubles from inventing a hook.
            runtime.retire_pending_media_start(match_id)

    def set_postmatch_runtime(self, runtime: PostmatchRuntime) -> None:
        self._postmatch_runtime = runtime

    def set_presence_runtime(self, runtime: Any) -> None:
        self._presence_runtime = runtime

    def set_terminal_callback(self, callback: Callable[[UUID], Coroutine[Any, Any, None]]) -> None:
        self._terminal_callback = callback

    async def _speech_callback_envelope(
        self, *, match_id: UUID, speech_id: UUID, user_id: UUID | None
    ) -> CallbackEnvelope:
        actor = self._actors.get(match_id)
        state = actor.state if actor is not None else None
        logical_opportunity_id = (
            state.allocated_opportunity_id or state.opportunity_id if state is not None else None
        )
        session_factory = cast(Any, self._session_factory)
        if session_factory is None:
            return CallbackEnvelope(
                match_id=match_id,
                speech_id=speech_id,
                attempt_no=1,
                generation_id=None,
                connection_epoch=(
                    dict(state.connection_epochs).get(user_id, 1)
                    if state is not None and user_id is not None
                    else 1
                ),
                context_version=0,
                opportunity_id=logical_opportunity_id,
                opportunity_generation=(
                    state.opportunity_generation
                    if logical_opportunity_id is not None
                    and state is not None
                    and state.opportunity_generation > 0
                    else None
                ),
            )
        action = state.current_action if state is not None else None
        async with session_factory() as session:
            match = await session.get(Match, match_id)
            if match is None:
                raise MatchDomainError("match_not_found")
            existing = await session.get(Speech, speech_id)
            if existing is not None:
                if (
                    existing.match_id != match_id
                    or existing.user_id != user_id
                    or existing.speaker_kind != "HUMAN"
                    or existing.opportunity_id != logical_opportunity_id
                    or existing.status not in {"STARTED", "FINALIZING"}
                ):
                    raise MatchDomainError("match_state_conflict")
            attempt_query = select(func.coalesce(func.max(Speech.attempt_no), 0)).where(
                Speech.match_id == match_id
            )
            if logical_opportunity_id is not None:
                attempt_query = attempt_query.where(Speech.opportunity_id == logical_opportunity_id)
            elif action is not None:
                attempt_query = attempt_query.where(Speech.action_key == action.action_key)
            previous_attempt = await session.scalar(attempt_query)
            logical_opportunity = (
                await session.get(FreeDebateOpportunity, logical_opportunity_id)
                if logical_opportunity_id is not None
                else None
            )
            logical_opportunity_generation = (
                logical_opportunity.opportunity_generation
                if logical_opportunity is not None
                else None
            )
            context_version = match.context_version
        epoch = (
            dict(state.connection_epochs).get(user_id, 1)
            if state is not None and user_id is not None
            else 1
        )
        opportunity_id = logical_opportunity_id
        opportunity_generation = (
            (
                logical_opportunity_generation
                if logical_opportunity_generation is not None
                else state.opportunity_generation
                if state is not None and state.opportunity_generation > 0
                else None
            )
            if opportunity_id is not None
            else None
        )
        return CallbackEnvelope(
            match_id=match_id,
            speech_id=speech_id,
            attempt_no=existing.attempt_no
            if existing is not None
            else int(previous_attempt or 0) + 1,
            generation_id=None,
            connection_epoch=epoch,
            context_version=context_version,
            opportunity_id=opportunity_id,
            opportunity_generation=opportunity_generation,
        )

    def _asr_epoch_is_current(
        self,
        *,
        actor: MatchActor,
        speech: Speech,
        envelope: CallbackEnvelope,
        recording_recovery: bool = False,
    ) -> bool:
        expected_epoch = (
            dict(actor.state.connection_epochs).get(speech.user_id, 1)
            if speech.user_id is not None
            else None
        )
        if recording_recovery:
            retained = getattr(self._speech_runtime, "recovery_source_envelope", None)
            return (
                retained is not None and retained(envelope.match_id, envelope.speech_id) is envelope
            )
        if envelope.connection_epoch == expected_epoch:
            return True
        if getattr(speech, "status", None) != "FINALIZING" or self._speech_runtime is None:
            return False
        check = getattr(self._speech_runtime, "finalization_callback_is_current", None)
        return check is not None and bool(check(envelope=envelope))

    async def _asr_callback_is_current(
        self, *, actor: MatchActor, envelope: CallbackEnvelope, recording_recovery: bool = False
    ) -> bool:
        speech_id = envelope.speech_id
        # ASR callbacks are scoped to a speech attempt.  They never carry an
        # agent generation id; accepting one here would let a callback from a
        # different runtime family pass the generic identity check.
        if speech_id is None or envelope.generation_id is not None:
            return False
        owner = getattr(
            self._speech_runtime,
            "recovery_source_envelope" if recording_recovery else "speech_callback_envelope",
            None,
        )
        if owner is not None and owner(envelope.match_id, speech_id) is not envelope:
            return False
        async with self._session_factory() as session:
            speech = await session.get(Speech, speech_id)
            match = await session.get(Match, envelope.match_id)
            if speech is None or match is None or speech.match_id != envelope.match_id:
                return False
            opportunity = (
                await session.get(FreeDebateOpportunity, speech.opportunity_id)
                if speech.opportunity_id is not None
                else None
            )
        return (
            envelope.attempt_no == speech.attempt_no
            and self._asr_epoch_is_current(
                actor=actor, speech=speech, envelope=envelope, recording_recovery=recording_recovery
            )
            and getattr(speech, "status", "STARTED") in {"STARTED", "FINALIZING"}
            and envelope.context_version == match.context_version
            and envelope.opportunity_id == speech.opportunity_id
            and envelope.opportunity_generation
            == (opportunity.opportunity_generation if opportunity is not None else None)
        )

    async def _lock_current_asr_callback(
        self,
        *,
        session: AsyncSession,
        actor: MatchActor,
        envelope: CallbackEnvelope,
        speech_id: UUID,
        recording_recovery: bool = False,
    ) -> tuple[Match, Speech] | None:
        """Lock and re-check the durable ASR callback identity.

        The optimistic check in ``_asr_callback_is_current`` is useful for
        dropping stale work before opening a transaction.  The callback can
        race a match context/attempt update after that check, however, so all
        segment and capture writes repeat the check while holding the Match
        and Speech rows.  This keeps a late provider result from mutating a
        newer speech attempt.
        """

        if envelope.speech_id != speech_id or envelope.generation_id is not None:
            return None
        owner = getattr(
            self._speech_runtime,
            "recovery_source_envelope" if recording_recovery else "speech_callback_envelope",
            None,
        )
        if owner is not None and owner(envelope.match_id, speech_id) is not envelope:
            return None
        match = await session.scalar(
            select(Match).where(Match.id == envelope.match_id).with_for_update()
        )
        if match is None:
            return None
        speech = await session.scalar(
            select(Speech)
            .where(Speech.id == speech_id, Speech.match_id == envelope.match_id)
            .with_for_update()
        )
        if speech is None or speech.status not in {"STARTED", "FINALIZING"}:
            return None
        opportunity = (
            await session.get(FreeDebateOpportunity, speech.opportunity_id)
            if speech.opportunity_id is not None
            else None
        )
        if (
            envelope.attempt_no != speech.attempt_no
            or not self._asr_epoch_is_current(
                actor=actor, speech=speech, envelope=envelope, recording_recovery=recording_recovery
            )
            or envelope.context_version != match.context_version
            or envelope.opportunity_id != speech.opportunity_id
            or envelope.opportunity_generation
            != (opportunity.opportunity_generation if opportunity is not None else None)
        ):
            return None
        if owner is not None and owner(envelope.match_id, speech_id) is not envelope:
            return None
        return match, speech

    @staticmethod
    def _asr_call_matches_envelope(call: ExternalCall, envelope: CallbackEnvelope) -> bool:
        """Ensure provider capture/failure writes belong to this callback."""

        return (
            call.match_id == envelope.match_id
            and call.speech_id == envelope.speech_id
            and call.connection_epoch == envelope.connection_epoch
            and call.context_version == envelope.context_version
            and call.opportunity_id == envelope.opportunity_id
        )

    async def _prepare_command(
        self, state: MatchRuntimeState, command: MatchCommand
    ) -> MatchCommand:
        if (
            command.type == "system.error"
            and command.payload.get("error_code") == "internal_timer_failed"
            and command.message_id.startswith("internal-error:speech.deadline:")
        ):
            if command.actor_user_id is not None or command.host_guard_id is not None:
                raise MatchDomainError("stale_callback")
            timer_id = command.message_id.removeprefix("internal-error:speech.deadline:")
            owner = getattr(self._speech_runtime, "expired_input_owner", None)
            retire = getattr(self._speech_runtime, "retire_expired_input", None)
            envelope = owner(state.match_id, timer_id) if owner is not None else None
            if owner is not None and (
                envelope is None
                or state.status != "RUNNING"
                or state.action_state != "HUMAN_SPEAKING"
                or state.current_speech_id != envelope.speech_id
            ):
                # A late failure from an older timer must not error a replacement.
                raise MatchDomainError("stale_callback")
            if (
                envelope is not None
                and envelope.match_id == state.match_id
                and envelope.speech_id == state.current_speech_id
                and retire is not None
            ):
                deadline = await retire(state.match_id, timer_id, envelope=envelope)
                if deadline is None:
                    return command
                async with asyncio.timeout_at(deadline), self._session_factory() as session:
                    stored_match = await session.get(Match, state.match_id)
                    stored_speech = await session.get(Speech, state.current_speech_id)
                if (
                    stored_match is None
                    or stored_speech is None
                    or stored_speech.status != "STARTED"
                    or stored_match.runtime_snapshot.get("sequence") != state.sequence
                ):
                    # A lost commit acknowledgement is not permission to replace
                    # already-committed events with a guessed failure snapshot.
                    raise MatchDomainError("asr_recovery_required")
                logger.warning(
                    "ASR input deadline transaction failed; retained recording for recovery",
                    extra={
                        "error_code": "asr_deadline_commit_failed",
                        "match_id": str(state.match_id),
                        "speech_id": str(state.current_speech_id),
                    },
                )
                return replace(
                    command,
                    type="asr.failed",
                    payload={
                        "speech_id": str(state.current_speech_id),
                        "error_code": "asr_recovery_required",
                    },
                    asr_callback_envelope=envelope,
                    asr_deadline_mono=deadline,
                )
        if command.type not in {"match.resume", "resume.elapsed"}:
            return command
        from .resume import required_resume_reasons

        async with self._session_factory() as session:
            match = await session.get(Match, state.match_id)
            if match is None:
                raise MatchDomainError("match_not_found")
            reasons = await required_resume_reasons(session, match.room_id, state)
        return replace(command, payload={**command.payload, "reasons": reasons})

    async def continue_after_operation(self, match_id: UUID, *, operation_id: str) -> None:
        actor = await self.get_actor(match_id)
        operation = actor.state.emergency_operation
        if (
            not operation
            or operation.get("id") != operation_id
            or operation.get("status") != "SUCCEEDED"
            or not operation.get("auto_continue")
            or actor.state.status != "PAUSED"
            or actor.state.action_state != "RECOVERY_REQUIRED"
        ):
            return
        await self.submit(
            match_id,
            MatchCommand(
                "match.resume",
                f"operation-resume:{operation_id}",
                payload={"privileged": True, "completed_operation_id": operation_id},
            ),
        )

    async def _pre_commit(
        self,
        previous: MatchRuntimeState,
        candidate: MatchRuntimeState,
        events: tuple[MatchEvent, ...],
        command: MatchCommand,
    ) -> None:
        if command.asr_deadline_mono is None:
            await self._pre_commit_within_deadline(previous, candidate, events, command)
        else:
            async with asyncio.timeout_at(command.asr_deadline_mono):
                await self._pre_commit_within_deadline(previous, candidate, events, command)

    async def _pre_commit_within_deadline(
        self,
        previous: MatchRuntimeState,
        candidate: MatchRuntimeState,
        events: tuple[MatchEvent, ...],
        command: MatchCommand,
    ) -> None:
        if command.host_guard_id is not None and events:
            self._require_current_host_guard(previous.match_id, command.host_guard_id)
        if command.asr_callback_envelope is not None:
            actor = self._actors.get(previous.match_id)
            if actor is None or not await self._asr_callback_is_current(
                actor=actor,
                envelope=command.asr_callback_envelope,
                recording_recovery=command.asr_recording_recovery,
            ):
                raise MatchDomainError("stale_callback")
        agent_failure = command.agent_callback_envelope
        if agent_failure is not None and not self._agent_failure_target_is_current(
            previous, agent_failure, command.agent_logical_call_id
        ):
            raise MatchDomainError("stale_callback")
        host_envelope = command.host_callback_envelope
        if command.type == "host.finished" or host_envelope is not None:
            action_key = command.payload.get("action_key")
            if (
                not isinstance(host_envelope, CallbackEnvelope)
                or not isinstance(action_key, str)
                or host_envelope.match_id != previous.match_id
                or not await self._host_callback_is_current(
                    envelope=host_envelope, action_key=action_key
                )
            ):
                raise MatchDomainError("stale_callback")
        if command.type == "speech.media_ready" and events:
            if previous.current_speech_id is None or self._speech_runtime is None:
                raise MatchDomainError("stale_callback")
            getter = getattr(self._speech_runtime, "speech_callback_envelope", None)
            if getter is not None:
                current = getter(previous.match_id, previous.current_speech_id)
                if current is None or current.connection_epoch != command.payload.get(
                    "connection_epoch"
                ):
                    raise MatchDomainError("stale_callback")
        if command.type == "asr.retry.begin" and events:
            source = previous.asr_failed_speech_id
            available = getattr(self._speech_runtime, "recovery_source_available", None)
            getter = getattr(self._speech_runtime, "speech_callback_envelope", None)
            if source is None or (
                not available(previous.match_id, source)
                if available is not None
                else getter is None or getter(previous.match_id, source) is None
            ):
                raise MatchDomainError("asr_audio_missing")
        if command.type == "speech.start" and self._speech_runtime is not None:
            if previous.free_source_speech_id is not None:
                async with self._session_factory() as session:
                    source_status = await session.scalar(
                        select(Speech.status).where(
                            Speech.id == previous.free_source_speech_id,
                            Speech.match_id == previous.match_id,
                        )
                    )
                if source_status in {"STARTED", "FINALIZING"}:
                    # A preceding Agent has no ASR session for the receiver's
                    # input-closed guard. Wait for its authoritative final too,
                    # before freezing a new human's callback context.
                    raise MatchDomainError("asr_finalization_pending")
            speech_id = candidate.current_speech_id
            user_id = command.actor_user_id
            if speech_id is None or user_id is None:
                raise MatchDomainError("match_state_conflict")
            envelope = await self._speech_callback_envelope(
                match_id=previous.match_id,
                speech_id=speech_id,
                user_id=user_id,
            )
            try:
                async with asyncio.timeout(HUMAN_SPEECH_START_TIMEOUT_SECONDS):
                    await self._speech_runtime.start_speech(
                        previous.match_id,
                        speech_id,
                        user_id,
                        envelope,
                        max_audio_duration_ms=max(
                            1,
                            int(
                                candidate.speech_remaining_ms
                                if candidate.speech_remaining_ms is not None
                                else 180_000
                            ),
                        ),
                    )
            except Exception as error:
                if getattr(error, "code", None) in {
                    "asr_finalization_pending",
                    "asr_recovery_required",
                }:
                    raise MatchDomainError(
                        str(getattr(error, "code", "asr_recovery_required"))
                    ) from error
                try:
                    await self._speech_runtime.reset_speech(previous.match_id)
                except Exception as cleanup_error:
                    logger.warning(
                        "failed human speech start cleanup",
                        extra={
                            "error_code": "asr_start_cleanup_failed",
                            "match_id": str(previous.match_id),
                            "speech_id": str(speech_id),
                            "details": {"exception_type": type(cleanup_error).__name__},
                        },
                    )
                raise MatchDomainError(_speech_start_error_code(error)) from error
            self._prepared_speech_starts.add((previous.match_id, command.message_id))
            return
        if command.type not in {"speech.reset", "stage.reset", "speech.skip.begin"} or not events:
            return
        if (
            command.type in {"speech.reset", "speech.skip.begin"}
            and previous.free_source_speech_id is not None
            and previous.free_source_speech_id != previous.current_speech_id
            and cast(Any, self._session_factory) is not None
        ):
            async with self._session_factory() as session:
                source = await session.get(Speech, previous.free_source_speech_id)
            if source is not None and source.status in {"STARTED", "FINALIZING"}:
                # Changing context or resetting the shared receiver would retire
                # the preceding speaker's still-valid final, not this target.
                raise MatchDomainError("asr_finalization_pending")
        if command.payload.get("operation_id"):
            # Operator target/context must commit before irreversible media cleanup.
            return
        self._retire_pending_media_start(previous.match_id)
        if self._speech_runtime is not None:
            await self._speech_runtime.reset_speech(previous.match_id)
        if self._agent_runtime is not None:
            await self._agent_runtime.cancel_free_decision(previous.match_id)
            await self._agent_runtime.reset_agent(previous.match_id)
            if command.type == "stage.reset":
                await self._agent_runtime.reset_host(previous.match_id)
        for key in tuple(self._pending_agent_full_text):
            if key[0] == previous.match_id:
                self._pending_agent_full_text.pop(key, None)

    def _spawn(self, coroutine: Coroutine[Any, Any, None], *, name: str) -> asyncio.Task[None]:
        task = asyncio.create_task(coroutine, name=name)
        self._background_tasks.add(task)

        def finished(completed: asyncio.Task[None]) -> None:
            self._background_tasks.discard(completed)
            if completed.cancelled():
                return
            error = completed.exception()
            if error is not None:
                logger.error(
                    "match background task failed",
                    extra={
                        "error_code": getattr(error, "code", "background_task_failed"),
                        "task_name": completed.get_name(),
                    },
                )

        task.add_done_callback(finished)
        return task

    def _remember_pending_agent_full_text(
        self, *, match_id: UUID, generation_id: UUID, envelope: CallbackEnvelope, text: str
    ) -> None:
        """Keep a callback that arrived before its speech row bounded.

        The LLM stream already caps one response, but a missing speech row can
        otherwise retain one full response per generation until match teardown.
        Dict insertion order gives us a small FIFO without introducing another
        queue or cache.
        """

        key = (match_id, generation_id)
        if (
            key not in self._pending_agent_full_text
            and len(self._pending_agent_full_text) >= PENDING_AGENT_FULL_TEXT_LIMIT
        ):
            evicted_key = next(iter(self._pending_agent_full_text))
            self._pending_agent_full_text.pop(evicted_key, None)
            logger.warning(
                "pending agent text fence reached capacity",
                extra={
                    "error_code": "pending_agent_text_evicted",
                    "limit": PENDING_AGENT_FULL_TEXT_LIMIT,
                },
            )
        self._pending_agent_full_text[key] = (envelope, text)

    def _remember_late_experiment_round(self, *, match_id: UUID, round_id: UUID) -> None:
        """Keep late decision fences bounded when a provider never calls back."""

        self._late_experiment_rounds.add(round_id)
        self._late_experiment_round_matches[round_id] = match_id
        while len(self._late_experiment_rounds) > LATE_EXPERIMENT_ROUND_LIMIT:
            evicted = self._late_experiment_rounds.pop()
            self._late_experiment_round_matches.pop(evicted, None)

    def _forget_late_experiment_round(self, round_id: UUID) -> None:
        self._late_experiment_rounds.discard(round_id)
        self._late_experiment_round_matches.pop(round_id, None)

    def _forget_late_experiment_rounds_for_match(self, match_id: UUID) -> None:
        for round_id, owner_match_id in tuple(self._late_experiment_round_matches.items()):
            if owner_match_id == match_id:
                self._forget_late_experiment_round(round_id)

    async def _evict_terminal_actor_later(self, match_id: UUID, sequence: int) -> None:
        # This separate task starts after the terminal commit. The callback may
        # submit to the Actor, so it must never be awaited by Actor._publish.
        if self._terminal_callback is not None:
            await self._terminal_callback(match_id)
        await asyncio.sleep(TERMINAL_ACTOR_RETENTION_SECONDS)
        async with self._lock:
            actor = self._actors.get(match_id)
            if (
                actor is None
                or actor.state.sequence != sequence
                or actor.state.status not in ("FINISHED", "TERMINATED")
            ):
                return
            self._actors.pop(match_id, None)
            subscribers = self._subscribers.get(match_id)
            if not subscribers:
                self._subscribers.pop(match_id, None)
        await actor.close()

    async def close(self) -> None:
        self._closed = True
        eviction_tasks = tuple(self._terminal_eviction_tasks.values())
        self._terminal_eviction_tasks.clear()
        for task in eviction_tasks:
            task.cancel()
        if eviction_tasks:
            await asyncio.gather(*eviction_tasks, return_exceptions=True)
        presence_tasks = tuple(self._speech_presence_tasks.values())
        self._speech_presence_tasks.clear()
        self._asr_final_deadlines.clear()
        for task in presence_tasks:
            task.cancel()
        if presence_tasks:
            await asyncio.gather(*presence_tasks, return_exceptions=True)
        if self._speech_runtime is not None:
            await self._speech_runtime.close()
        if self._agent_runtime is not None:
            await self._agent_runtime.close()
        for task in tuple(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        async with self._lock:
            actors = list(self._actors.values())
            self._actors.clear()
            self._subscribers.clear()
            self._prepared_speech_starts.clear()
            self._pending_agent_full_text.clear()
            self._late_experiment_rounds.clear()
            self._late_experiment_round_matches.clear()
        for actor in actors:
            await actor.close()

    async def remove_terminal(self, match_id: UUID) -> None:
        """Forget a terminal actor before its durable match is administratively deleted."""
        async with self._lock:
            actor = self._actors.get(match_id)
            eviction_task = self._terminal_eviction_tasks.pop(match_id, None)
            if actor is None:
                self._subscribers.pop(match_id, None)
            else:
                if actor.state.status not in ("FINISHED", "TERMINATED"):
                    if eviction_task is not None:
                        self._terminal_eviction_tasks[match_id] = eviction_task
                    raise MatchDomainError("match_not_finished")
                self._actors.pop(match_id, None)
                self._subscribers.pop(match_id, None)
        if eviction_task is not None and eviction_task is not asyncio.current_task():
            eviction_task.cancel()
            await asyncio.gather(eviction_task, return_exceptions=True)
        if actor is None:
            return
        await actor.close()

    async def _publish(self, state: MatchRuntimeState, events: tuple[MatchEvent, ...]) -> None:
        stop = next(
            (
                event
                for event in events
                if event.type
                in {
                    "match.paused",
                    "stage.reset",
                    "speech.skip.started",
                    "speech.reset",
                    "match.system_recovery",
                    "match.finished",
                    "match.terminated",
                }
            ),
            None,
        )
        if stop is not None:
            source = (
                _optional_uuid(stop.payload.get("speech_id"))
                or state.asr_failed_speech_id
                or state.current_speech_id
            )
            getter = getattr(self._speech_runtime, "speech_callback_envelope", None)
            captured = getter(state.match_id, source) if getter is not None and source else None
            fence = getattr(self._speech_runtime, "fence_speech_input", None)
            if source is not None and fence is not None:
                fence(state.match_id, source)
            retry_task = self._asr_retry_tasks.pop(state.match_id, None)
            if retry_task is not None and retry_task is not asyncio.current_task():
                retry_task.cancel()
            if (
                stop.type == "match.paused"
                and source is not None
                and self._speech_runtime is not None
            ):
                self._spawn(
                    self._pause_human_input(state.match_id, source, captured),
                    name=f"asr-interruption-pause-{source}",
                )
        if any(
            event.type
            in {
                "stage.reset",
                "speech.skip.started",
                "match.paused",
                "match.error",
                "match.system_recovery",
                "match.finished",
                "match.terminated",
            }
            or (
                event.type in {"speech.ready", "agent.preparing", "speech.reset"}
                and event.payload.get("operation_id")
            )
            for event in events
        ):
            self._retire_pending_media_start(state.match_id)
        recovery = next(
            (
                event
                for event in events
                if (
                    event.type in {"stage.reset", "speech.skip.started"}
                    or (
                        event.type in {"speech.ready", "agent.preparing", "speech.reset"}
                        and event.payload.get("operation_id")
                    )
                )
            ),
            None,
        )
        if recovery is not None:
            cleanup_error: Exception | None = None
            closes: list[Callable[[UUID], Coroutine[Any, Any, None]]] = []
            if self._agent_runtime is not None:
                for name in ("fence_agent", "fence_host"):
                    fence = getattr(self._agent_runtime, name, None)
                    if fence is not None:
                        closes.append(fence)
            if self._speech_runtime is not None:
                closes.append(self._speech_runtime.reset_speech)
            if self._agent_runtime is not None:
                closes.extend(
                    (
                        self._agent_runtime.cancel_free_decision,
                        self._agent_runtime.reset_agent,
                    )
                )
                if recovery.type == "stage.reset":
                    closes.append(self._agent_runtime.reset_host)
            for close in closes:
                try:
                    async with asyncio.timeout(EMERGENCY_RESOURCE_CLEANUP_TIMEOUT_SECONDS):
                        await close(state.match_id)
                except Exception as error:
                    cleanup_error = error
            for key in tuple(self._pending_agent_full_text):
                if key[0] == state.match_id:
                    self._pending_agent_full_text.pop(key, None)
            if cleanup_error is not None:
                raise MatchDomainError("asr_start_timeout") from cleanup_error
        if self._closed:
            return
        interruption = any(
            event.type in ("match.paused", "match.error", "match.system_recovery")
            for event in events
        )
        if interruption and self._agent_runtime is not None:
            fence = getattr(self._agent_runtime, "fence_agent", None)
            if fence is not None:
                await fence(state.match_id)
            fence_host = getattr(self._agent_runtime, "fence_host", None)
            if fence_host is not None:
                await fence_host(state.match_id)
        for event in events:
            for queue in tuple(self._subscribers.get(event.match_id, ())):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    queue.put_nowait(event)
            speech_id = event.payload.get("speech_id")
            if speech_id and event.type in {"speech.started", "speech.media_ready"}:
                commit = getattr(self._speech_runtime, "commit_speech_start", None)
                getter = getattr(self._speech_runtime, "speech_callback_envelope", None)
                envelope = getter(event.match_id, UUID(str(speech_id))) if getter else None
                if commit is not None and state.speech_deadline_mono is not None:
                    # Provider readiness never admits PCM. Install this committed
                    # Core cutoff and exact owner before opening the input gate.
                    timer_id = (
                        f"internal:speech-deadline:{speech_id}:{event.sequence}"
                        if event.type == "speech.started"
                        else f"internal:media-ready:{speech_id}:{event.sequence - 1}"
                    )
                    final_deadline = commit(
                        event.match_id,
                        UUID(str(speech_id)),
                        envelope=envelope,
                        input_deadline_mono=state.speech_deadline_mono,
                        timer_message_id=timer_id,
                    )
                    if isinstance(final_deadline, float):
                        self._asr_final_deadlines[(event.match_id, UUID(str(speech_id)))] = (
                            final_deadline
                        )
            if speech_id and event.type in {"speech.finished", "asr.failed"}:
                self._asr_final_deadlines.pop((event.match_id, UUID(str(speech_id))), None)
            if event.type in {
                "stage.reset",
                "speech.skip.started",
                "speech.reset",
                "match.finished",
                "match.terminated",
            }:
                for key in tuple(self._asr_final_deadlines):
                    if key[0] == event.match_id:
                        self._asr_final_deadlines.pop(key, None)
            if event.type in ("match.offline", "match.online"):
                self._queue_speech_presence_transition(state, event)
            if event.type == "host.play" or (
                event.type == "match.resumed" and state.action_state == "HOST_ANNOUNCING"
            ):
                action = state.current_action
                if action is None or not action.host_audio_path:
                    continue
                if self._agent_runtime is not None:
                    context_version = await self._context_version(event.match_id)
                    self._spawn(
                        self._start_host_for_current_action(
                            match_id=event.match_id,
                            action_key=action.action_key,
                            storage_path=action.host_audio_path,
                            duration_ms=int(action.host_audio_duration_ms or 0),
                            context_version=context_version,
                            stage_generation=state.stage_generation,
                        ),
                        name=f"host-start-{event.match_id}-{action.action_key}",
                    )
                else:
                    self._spawn(
                        self._submit_agent_error(event.match_id, "tts_not_configured"),
                        name=f"host-config-error-{event.match_id}",
                    )
            elif (
                event.type == "speech.finalizing" and speech_id and self._speech_runtime is not None
            ):
                source_id = UUID(str(speech_id))
                key = (event.match_id, source_id)
                deadline = min(
                    self._asr_final_deadlines.get(key, float("inf")),
                    asyncio.get_running_loop().time() + ASR_FINALIZATION_TIMEOUT_SECONDS,
                )
                self._asr_final_deadlines[key] = deadline
                self._spawn(
                    self._finish_human_speech(event.match_id, source_id, deadline_mono=deadline),
                    name=f"asr-finish-{speech_id}",
                )
            elif event.type == "asr.retry.started" and speech_id:
                task = self._spawn(
                    self._retry_human_asr(event.match_id, UUID(str(speech_id))),
                    name=f"asr-retry-recording-{speech_id}",
                )
                self._asr_retry_tasks[event.match_id] = task

                def clear_retry(
                    completed: asyncio.Task[None], match_id: UUID = event.match_id
                ) -> None:
                    if self._asr_retry_tasks.get(match_id) is completed:
                        self._asr_retry_tasks.pop(match_id, None)

                task.add_done_callback(clear_retry)
            elif event.type == "agent.preparing":
                agent_profile_id = event.payload.get("agent_profile_id")
                action_key = event.payload.get("action_key")
                if self._agent_runtime is not None and agent_profile_id and action_key:
                    self._spawn(
                        self._start_agent_after_source_finalized(event),
                        name=f"agent-start-{event.match_id}-{action_key}",
                    )
                elif self._agent_runtime is None:
                    self._spawn(
                        self._submit_agent_error(event.match_id, "tts_not_configured"),
                        name=f"agent-config-error-{event.match_id}",
                    )
            elif event.type == "agent.finalizing" and speech_id:
                if self._agent_runtime is not None:
                    self._spawn(
                        self._agent_runtime.finalize_agent(
                            event.match_id,
                            UUID(str(speech_id)),
                            str(event.payload.get("reason", "COMPLETED")),
                        ),
                        name=f"agent-finalize-{speech_id}",
                    )
            elif event.type == "agent.decision_started":
                action = state.current_action
                side = str(event.payload.get("side", ""))
                candidates = [item.agent_profile_id for item in state.agent_decisions]
                decision_envelope = CallbackEnvelope(
                    match_id=event.match_id,
                    speech_id=(
                        _required_uuid(event.payload["source_speech_id"])
                        if event.payload.get("source_speech_id")
                        else None
                    ),
                    attempt_no=1,
                    generation_id=None,
                    connection_epoch=None,
                    context_version=0,
                    opportunity_id=(
                        _required_uuid(event.payload["opportunity_id"])
                        if event.payload.get("opportunity_id")
                        else None
                    ),
                    opportunity_generation=(
                        int(event.payload["opportunity_generation"])
                        if event.payload.get("opportunity_generation") is not None
                        and int(event.payload["opportunity_generation"]) > 0
                        else None
                    ),
                )
                if self._agent_runtime is not None and action is not None and candidates:
                    self._spawn(
                        self._agent_runtime.decide_free_debate(
                            match_id=event.match_id,
                            action_key=action.action_key,
                            side=side,
                            agent_profile_ids=candidates,
                            decision_round_id=_required_uuid(event.payload["decision_round_id"]),
                            envelope=decision_envelope,
                        ),
                        name=f"agent-decide-{event.match_id}-{event.payload['decision_round_id']}",
                    )
                elif candidates:
                    round_id = _required_uuid(event.payload["decision_round_id"])
                    for agent_profile_id in candidates:
                        self._spawn(
                            self._report_unavailable_decision(
                                match_id=event.match_id,
                                action_key=str(event.payload["action_key"]),
                                agent_profile_id=agent_profile_id,
                                side=side,
                                decision_round_id=round_id,
                                envelope=decision_envelope,
                            ),
                            name=f"agent-decision-error-{event.match_id}-{agent_profile_id}",
                        )
            if event.type == "speech.finished" and speech_id and state.free_competition_enabled:
                self._spawn(
                    self._start_current_experiment_decision(
                        match_id=event.match_id, source_speech_id=UUID(str(speech_id))
                    ),
                    name=f"source-ready-decision-{speech_id}",
                )
            if event.type in {"speech.skipped", "asr.retry.completed"} and event.payload.get(
                "operation_id"
            ):
                self._spawn(
                    self.continue_after_operation(
                        event.match_id, operation_id=str(event.payload["operation_id"])
                    ),
                    name=f"operation-continue-{event.match_id}-{event.payload['operation_id']}",
                )
            if event.type == "agent.playback_started" and event.payload.get("generation_id"):
                generation_id = _required_uuid(event.payload["generation_id"])
                if (event.match_id, generation_id) in self._pending_agent_full_text:
                    self._spawn(
                        self._consume_pending_agent_full_text(event.match_id, generation_id),
                        name=f"agent-full-text-{event.match_id}-{generation_id}",
                    )
            elif (
                event.type in ("match.paused", "match.error", "match.system_recovery")
                and self._agent_runtime is not None
            ):
                self._spawn(
                    self._reset_interrupted_agent(event.match_id),
                    name=f"agent-interruption-reset-{event.match_id}",
                )
            elif event.type in ("match.finished", "match.terminated"):
                self._forget_late_experiment_rounds_for_match(event.match_id)
                # A late LLM callback can arrive after the speech row was
                # discarded by reset/termination. Do not retain its complete
                # text for the lifetime of the process.
                for key in tuple(self._pending_agent_full_text):
                    if key[0] == event.match_id:
                        self._pending_agent_full_text.pop(key, None)
                close_presence = (
                    getattr(self._presence_runtime, "close_match", None)
                    if self._presence_runtime is not None
                    else None
                )
                if close_presence is not None:
                    self._spawn(
                        close_presence(event.match_id),
                        name=f"presence-close-{event.match_id}",
                    )
                if event.match_id not in self._terminal_eviction_tasks:
                    eviction = asyncio.create_task(
                        self._evict_terminal_actor_later(event.match_id, event.sequence),
                        name=f"terminal-actor-eviction-{event.match_id}",
                    )
                    self._terminal_eviction_tasks[event.match_id] = eviction

                    def clear_eviction(
                        completed: asyncio.Task[None], terminal_match_id: UUID = event.match_id
                    ) -> None:
                        if self._terminal_eviction_tasks.get(terminal_match_id) is completed:
                            self._terminal_eviction_tasks.pop(terminal_match_id, None)
                        # The eviction task runs after the match event has
                        # already been published.  Always retrieve a failed
                        # actor.close() result before dropping the last task
                        # reference, otherwise asyncio reports an unhandled
                        # exception long after the match ended.
                        if not completed.cancelled():
                            try:
                                completed.exception()
                            except BaseException:
                                pass

                    eviction.add_done_callback(clear_eviction)
                presence_task = self._speech_presence_tasks.pop(event.match_id, None)
                if presence_task is not None and presence_task is not asyncio.current_task():
                    presence_task.cancel()
                if self._speech_runtime is not None:
                    self._spawn(
                        self._speech_runtime.close_match(event.match_id),
                        name=f"asr-close-{event.match_id}",
                    )
                if self._agent_runtime is not None:
                    self._spawn(
                        self._agent_runtime.close_match(event.match_id),
                        name=f"agent-close-{event.match_id}",
                    )
                if event.type == "match.finished" and self._postmatch_runtime is not None:
                    self._spawn(
                        self._request_postmatch_judge(event.match_id),
                        name=f"judge-request-{event.match_id}",
                    )

    async def _pause_human_input(
        self, match_id: UUID, speech_id: UUID, envelope: CallbackEnvelope | None
    ) -> None:
        runtime = self._speech_runtime
        if runtime is None:
            return
        try:
            await runtime.pause_speech(match_id, speech_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if getattr(error, "code", None) != "asr_recovery_required" or envelope is None:
                raise
            actor = self._actors.get(match_id)
            retained = getattr(runtime, "recovery_source_envelope", None)
            if (
                actor is None
                or retained is None
                or retained(match_id, speech_id) is not envelope
                or not await self._asr_callback_is_current(
                    actor=actor, envelope=envelope, recording_recovery=True
                )
            ):
                return
            try:
                await actor.submit(
                    MatchCommand(
                        "asr.failed",
                        f"asr-paused-recording:{speech_id}:{uuid4()}",
                        payload={
                            "speech_id": str(speech_id),
                            "error_code": "asr_recovery_required",
                        },
                        asr_callback_envelope=envelope,
                        asr_recording_recovery=True,
                    )
                )
            except MatchDomainError as stale:
                if stale.code not in {"stale_callback", "match_not_running"}:
                    raise

    async def _retry_human_asr(self, match_id: UUID, speech_id: UUID) -> None:
        runtime = self._speech_runtime
        actor = self._actors.get(match_id)
        operation = dict(actor.state.emergency_operation or {}) if actor else {}
        if (
            runtime is None
            or operation.get("kind") != "asr.retry"
            or operation.get("status") != "PENDING"
        ):
            return
        deadline = asyncio.get_running_loop().time() + ASR_FINALIZATION_TIMEOUT_SECONDS
        self._asr_final_deadlines[(match_id, speech_id)] = deadline
        try:
            getter = getattr(runtime, "recovery_source_envelope", runtime.speech_callback_envelope)
            envelope = getter(match_id, speech_id)
            if envelope is None:
                raise MatchDomainError("asr_audio_missing")
            async with asyncio.timeout_at(deadline):
                await runtime.retry_from_recording(
                    match_id, speech_id, envelope, deadline_mono=deadline
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if actor is None:
                return
            try:
                await actor.submit(
                    MatchCommand(
                        "asr.retry.failed",
                        f"asr-retry-failed:{operation['id']}",
                        payload={
                            "operation_id": operation["id"],
                            "operation_generation": operation["generation"],
                            "speech_id": str(speech_id),
                            "context_version": operation["context_version"],
                            "error_code": "asr_finalization_timeout"
                            if isinstance(error, TimeoutError)
                            else str(getattr(error, "code", "asr_stream_failed")),
                        },
                    )
                )
            except MatchDomainError as stale:
                if stale.code not in {"stale_callback", "match_not_running"}:
                    raise

    async def _finish_human_speech(
        self, match_id: UUID, speech_id: UUID, *, deadline_mono: float
    ) -> None:
        runtime = self._speech_runtime
        if runtime is None:
            return
        getter = getattr(runtime, "speech_callback_envelope", None)
        envelope = getter(match_id, speech_id) if getter is not None else None
        if envelope is None:
            # Legacy runtime test doubles have no owner projection. Production
            # supplies the exact frozen object, never a guessed attempt/epoch.
            if getter is not None:
                return
            actor = self._actors.get(match_id)
            envelope = await self._speech_callback_envelope(
                match_id=match_id,
                speech_id=speech_id,
                user_id=actor.state.current_speaker_user_id if actor else None,
            )
        try:
            if asyncio.get_running_loop().time() >= deadline_mono:
                await self.handle_asr_failure(envelope=envelope, code="asr_finalization_timeout")
                return
            async with asyncio.timeout_at(deadline_mono):
                await runtime.finish_speech(match_id, speech_id, deadline_mono=deadline_mono)
        except TimeoutError:
            await self.handle_asr_failure(envelope=envelope, code="asr_finalization_timeout")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.handle_asr_failure(
                envelope=envelope, code=str(getattr(error, "code", "asr_stream_failed"))
            )

    async def _start_host_for_current_action(
        self,
        *,
        match_id: UUID,
        action_key: str,
        storage_path: str,
        duration_ms: int,
        context_version: int,
        stage_generation: int,
    ) -> None:
        actor, runtime = self._actors.get(match_id), self._agent_runtime
        if actor is None or runtime is None or self._closed:
            return
        state, action = actor.state, actor.state.current_action
        if (
            state.status != "RUNNING"
            or state.action_state != "HOST_ANNOUNCING"
            or state.stage_generation != stage_generation
            or action is None
            or action.action_key != action_key
            or action.host_audio_path != storage_path
        ):
            return
        current_context = await self._context_version(match_id)
        if actor.state is not state or current_context != context_version or self._closed:
            return
        await runtime.start_host_playback(
            match_id=match_id,
            action_key=action_key,
            storage_path=storage_path,
            duration_ms=duration_ms,
            context_version=context_version,
        )

    async def _start_agent_after_source_finalized(self, event: MatchEvent) -> None:
        """A selected Agent uses the context committed by its preceding speaker."""
        actor = self._actors.get(event.match_id)
        runtime = self._agent_runtime
        if actor is None or runtime is None:
            return
        opportunity_id = actor.state.allocated_opportunity_id or actor.state.opportunity_id
        source_speech_id = actor.state.free_source_speech_id
        opportunity_generation = actor.state.opportunity_generation
        stage_generation = actor.state.stage_generation
        deadline = asyncio.get_running_loop().time() + AGENT_SOURCE_FINAL_WAIT_SECONDS
        while True:
            state = actor.state
            action = state.current_action
            if (
                state.status != "RUNNING"
                or state.action_state != "AGENT_PREPARING"
                or action is None
                or action.action_key != event.payload.get("action_key")
                or str(state.current_agent_profile_id) != str(event.payload.get("agent_profile_id"))
                or state.stage_generation != stage_generation
                or state.opportunity_generation != opportunity_generation
                or (state.allocated_opportunity_id or state.opportunity_id) != opportunity_id
            ):
                return
            pending = False
            if state.free_competition_enabled and action.action_kind == "FREE_DEBATE":
                async with self._session_factory() as session:
                    opportunity = (
                        await session.get(FreeDebateOpportunity, opportunity_id)
                        if opportunity_id
                        else None
                    )
                    source_id = source_speech_id or (
                        opportunity.source_speech_id if opportunity else None
                    )
                    source = await session.get(Speech, source_id) if source_id is not None else None
                pending = source is not None and source.status in {"STARTED", "FINALIZING"}
            if pending:
                if asyncio.get_running_loop().time() >= deadline:
                    try:
                        await self.submit(
                            event.match_id,
                            MatchCommand(
                                type="system.error",
                                message_id=f"agent-source-wait:{event.match_id}:{event.sequence}",
                                payload={
                                    "error_code": "asr_finalization_pending",
                                    "source_wait_owner": {
                                        "action_key": event.payload.get("action_key"),
                                        "agent_profile_id": str(state.current_agent_profile_id),
                                        "stage_generation": stage_generation,
                                        "opportunity_generation": opportunity_generation,
                                        "opportunity_id": str(opportunity_id)
                                        if opportunity_id
                                        else None,
                                        "source_speech_id": str(source_speech_id)
                                        if source_speech_id
                                        else None,
                                    },
                                },
                            ),
                        )
                    except MatchDomainError:
                        pass
                    return
                await asyncio.sleep(0.05)
                continue
            context_version = await self._context_version(event.match_id)
            # Repeat the owner check after the database await, before creating a generation.
            if actor.state is not state:
                continue
            await runtime.start_agent(
                match_id=event.match_id,
                action_key=str(event.payload["action_key"]),
                agent_profile_id=UUID(str(event.payload["agent_profile_id"])),
                duration_ms=int(event.payload.get("duration_ms", 0)),
                side=str(event.payload["side"]) if event.payload.get("side") else None,
                context_version=context_version,
                opportunity_id=opportunity_id,
                opportunity_generation=opportunity_generation if opportunity_id else None,
            )
            return

    async def _context_version(self, match_id: UUID) -> int:
        async with self._session_factory() as session:
            value = await session.scalar(select(Match.context_version).where(Match.id == match_id))
        return int(value or 0)

    def _queue_speech_presence_transition(
        self, state: MatchRuntimeState, event: MatchEvent
    ) -> None:
        if self._speech_runtime is None or state.action_state != "HUMAN_SPEAKING":
            return
        speech_id = state.current_speech_id
        speaker_id = state.current_speaker_user_id
        event_user_id = event.payload.get("user_id")
        if speech_id is None or speaker_id is None or str(speaker_id) != str(event_user_id):
            return
        previous = self._speech_presence_tasks.get(event.match_id)
        if event.type == "match.online" and previous in self._speech_presence_grace:
            previous.cancel()

        async def transition() -> None:
            if previous is not None:
                await asyncio.gather(previous, return_exceptions=True)
            runtime = self._speech_runtime
            if runtime is None:
                return
            if event.type == "match.offline":
                owner = asyncio.current_task()
                if owner is not None:
                    self._speech_presence_grace.add(owner)
                try:
                    await asyncio.sleep(SPEECH_MEDIA_RECOVERY_GRACE_SECONDS)
                finally:
                    if owner is not None:
                        self._speech_presence_grace.discard(owner)
                actor = self._actors.get(event.match_id)
                if actor is None:
                    return
                current = actor.state
                if (
                    current.status != "RUNNING"
                    or current.action_state != "HUMAN_SPEAKING"
                    or current.current_speech_id != speech_id
                    or current.current_speaker_user_id != speaker_id
                    or speaker_id not in dict(current.offline_since_ms)
                ):
                    return
                getter = getattr(runtime, "speech_callback_envelope", None)
                captured = getter(event.match_id, speech_id) if getter is not None else None
                await self._pause_human_input(event.match_id, speech_id, captured)
                return
            actor = self._actors.get(event.match_id)
            if actor is None:
                return
            current = actor.state
            if (
                current.status != "RUNNING"
                or current.action_state != "HUMAN_SPEAKING"
                or current.current_speech_id != speech_id
                or current.current_speaker_user_id != speaker_id
                or speaker_id in dict(current.offline_since_ms)
            ):
                return
            envelope = await self._speech_callback_envelope(
                match_id=event.match_id,
                speech_id=speech_id,
                user_id=speaker_id,
            )
            try:
                getter = getattr(runtime, "speech_callback_envelope", None)
                previous_owner = getter(event.match_id, speech_id) if getter is not None else None
                if (
                    previous_owner is not None
                    and previous_owner.connection_epoch != envelope.connection_epoch
                ):
                    await self._pause_human_input(event.match_id, speech_id, previous_owner)
                    if actor.state.status != "RUNNING":
                        return
                await runtime.start_speech(
                    event.match_id,
                    speech_id,
                    speaker_id,
                    envelope,
                    max_audio_duration_ms=max(
                        1,
                        int(
                            current.speech_remaining_ms
                            if current.speech_remaining_ms is not None
                            else 180_000
                        ),
                    ),
                )
                ready = getattr(runtime, "wait_speech_ready", None)
                if ready is not None:
                    await ready(event.match_id, speech_id, seconds=3.0)
                await actor.submit(
                    MatchCommand(
                        "speech.media_ready",
                        f"media-ready:{speech_id}:{envelope.connection_epoch}:{event.sequence}",
                        actor_user_id=speaker_id,
                        payload={
                            "speech_id": str(speech_id),
                            "connection_epoch": envelope.connection_epoch,
                        },
                        asr_callback_envelope=(
                            getter(event.match_id, speech_id) if getter is not None else None
                        ),
                    )
                )
            except MatchDomainError as error:
                if error.code not in {"stale_callback", "match_not_running"}:
                    await self.handle_asr_failure(envelope=envelope, code=error.code)
            except Exception as error:
                owner = getattr(runtime, "speech_callback_envelope", None)
                captured = owner(event.match_id, speech_id) if owner is not None else None
                if captured is not None and captured == envelope:
                    envelope = captured
                await self.handle_asr_failure(
                    envelope=envelope,
                    code=str(getattr(error, "code", "asr_start_timeout")),
                )

        task = asyncio.create_task(
            transition(), name=f"asr-presence-{event.type}-{event.match_id}-{speech_id}"
        )
        self._speech_presence_tasks[event.match_id] = task
        if event.type == "match.offline":
            self._speech_presence_grace.add(task)
        self._background_tasks.add(task)

        def finished(completed: asyncio.Task[None]) -> None:
            self._background_tasks.discard(completed)
            self._speech_presence_grace.discard(completed)
            if self._speech_presence_tasks.get(event.match_id) is completed:
                self._speech_presence_tasks.pop(event.match_id, None)
            if completed.cancelled():
                return
            error = completed.exception()
            if error is not None:
                logger.error(
                    "speech presence transition failed",
                    extra={
                        "error_code": getattr(error, "code", "asr_presence_transition_failed"),
                        "match_id": str(event.match_id),
                        "event_type": event.type,
                    },
                )

        task.add_done_callback(finished)

    async def _request_postmatch_judge(self, match_id: UUID) -> None:
        if self._postmatch_runtime is not None:
            await self._postmatch_runtime.request_judge(match_id)

    async def _reset_interrupted_agent(self, match_id: UUID) -> None:
        """Stop both decision work and media before an interrupted action resumes."""
        if self._agent_runtime is None:
            return
        self._retire_pending_media_start(match_id)
        await self._agent_runtime.cancel_free_decision(match_id)
        await self._agent_runtime.reset_agent(match_id)

    async def _submit_agent_error(self, match_id: UUID, code: str) -> None:
        try:
            await self.submit(
                match_id,
                MatchCommand(
                    type="system.error",
                    message_id=f"agent-config-error:{match_id}:{code}",
                    payload={"error_code": code},
                ),
            )
        except MatchDomainError:
            return

    async def _report_unavailable_decision(
        self,
        *,
        match_id: UUID,
        action_key: str,
        agent_profile_id: UUID,
        side: str,
        decision_round_id: UUID,
        envelope: CallbackEnvelope,
    ) -> None:
        await self.report_free_decision(
            envelope=envelope,
            action_key=action_key,
            agent_profile_id=agent_profile_id,
            side=side,
            decision_round_id=decision_round_id,
            should_speak=None,
            willingness=None,
            decision_reason=None,
            failed=True,
            attempt_no=1,
            duration_ms=0,
            error_code="agent_unavailable",
        )

    async def publish_agent_text_delta(self, *, envelope: CallbackEnvelope, text: str) -> None:
        envelope.require("generation_id")
        match_id = envelope.match_id
        generation_id = envelope.generation_id
        assert generation_id is not None
        actor = self._actors.get(match_id)
        if actor is None:
            return
        await self._publish(
            actor.state,
            (
                MatchEvent(
                    type="agent.text_delta",
                    match_id=match_id,
                    sequence=actor.state.sequence,
                    server_time_ms=int(datetime.now(UTC).timestamp() * 1000),
                    payload={"generation_id": str(generation_id), "text": text},
                ),
            ),
        )

    async def _start_current_experiment_decision(
        self, *, match_id: UUID, source_speech_id: UUID
    ) -> None:
        actor = self._actors.get(match_id)
        if actor is None:
            return
        state = actor.state
        if (
            not state.free_competition_enabled
            or state.opportunity_id is None
            or state.selection_phase not in {"COMPETING", "MODEL_WAIT"}
            or state.agent_decision_round_id is not None
        ):
            return
        async with self._session_factory() as session:
            opportunity = await session.get(FreeDebateOpportunity, state.opportunity_id)
        if opportunity is None:
            return
        source_value = opportunity.frozen_context.get("source_speech_id")
        if str(source_value) != str(source_speech_id):
            return
        try:
            await self.submit(
                match_id,
                MatchCommand(
                    type="free.agent_decision_start",
                    message_id=(
                        f"free-agent-decision-start:{state.opportunity_id}:"
                        f"{state.opportunity_generation}"
                    ),
                    payload={
                        "opportunity_id": str(state.opportunity_id),
                        "opportunity_generation": state.opportunity_generation,
                        "source_speech_id": str(source_speech_id),
                        "trigger_kind": opportunity.trigger_kind,
                    },
                ),
            )
        except MatchDomainError as exc:
            if str(exc) != "stale_callback":
                raise

    def _agent_text_target_is_current(
        self, state: MatchRuntimeState, envelope: CallbackEnvelope
    ) -> bool:
        action = state.current_action
        return (
            state.status == "RUNNING"
            and state.free_competition_enabled
            and state.action_state in {"AGENT_PREPARING", "AGENT_SPEAKING", "AGENT_FINALIZING"}
            and action is not None
            and state.current_agent_profile_id is not None
            and self._agent_runtime is not None
            and self._agent_runtime.agent_text_callback_is_current(
                envelope=envelope,
                action_key=action.action_key,
                agent_profile_id=state.current_agent_profile_id,
            )
        )

    async def agent_text_generated(self, *, envelope: CallbackEnvelope, text: str) -> None:
        envelope.require("generation_id")
        match_id = envelope.match_id
        generation_id = envelope.generation_id
        assert generation_id is not None
        actor = self._actors.get(match_id)
        if actor is None or not self._agent_text_target_is_current(actor.state, envelope):
            return
        async with self._session_factory() as session:
            async with session.begin():
                match = await session.get(Match, match_id, with_for_update=True)
                generation = await session.get(AgentGeneration, generation_id, with_for_update=True)
                action = actor.state.current_action
                if (
                    generation is None
                    or match is None
                    or generation.match_id != match_id
                    or generation.call_type != "LLM_SPEECH"
                    or generation.status not in {"LLM_READY", "PLAYING", "FINALIZED"}
                    or generation.context_version != envelope.context_version
                    or generation.attempt_no != envelope.attempt_no
                    or generation.context_version != match.context_version
                    or action is None
                    or generation.action_key != action.action_key
                    or actor.state.current_agent_profile_id != generation.agent_profile_id
                ):
                    return
                speech = await session.scalar(
                    select(Speech)
                    .where(
                        Speech.match_id == match_id,
                        Speech.generation_id == generation_id,
                    )
                    .order_by(Speech.created_at.desc())
                    .limit(1)
                    .with_for_update()
                )
                # First PCM may bind the same generation while these database
                # awaits run. Recheck the original identity against that owner.
                if not self._agent_text_target_is_current(actor.state, envelope):
                    return
                if speech is None or actor.state.current_speech_id is None:
                    self._remember_pending_agent_full_text(
                        match_id=match_id,
                        generation_id=generation_id,
                        envelope=envelope,
                        text=text,
                    )
                    return
                if (
                    actor.state.current_speech_id != speech.id
                    or actor.state.current_agent_profile_id != speech.agent_profile_id
                    or speech.action_key != generation.action_key
                    or speech.opportunity_id != envelope.opportunity_id
                    or speech.speaker_kind != "AGENT"
                    or speech.status not in {"STARTED", "FINALIZING"}
                ):
                    return
                speech.llm_draft_text = text
                speech.display_text = text
                source_speech_id = speech.id
        await self._start_current_experiment_decision(
            match_id=match_id, source_speech_id=source_speech_id
        )

    async def _consume_pending_agent_full_text(self, match_id: UUID, generation_id: UUID) -> None:
        pending = self._pending_agent_full_text.pop((match_id, generation_id), None)
        if pending is not None:
            envelope, text = pending
            await self.agent_text_generated(envelope=envelope, text=text)

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
    ) -> object:
        match_id = envelope.match_id
        actor = await self.get_actor(match_id)
        if actor.state.free_competition_enabled:
            envelope.require("opportunity_id", "opportunity_generation")
        command = MatchCommand(
            type="free.agent_decision_result",
            message_id=(f"free-agent-decision:{match_id}:{decision_round_id}:{agent_profile_id}"),
            payload={
                "action_key": action_key,
                "agent_profile_id": str(agent_profile_id),
                "side": side,
                "decision_round_id": str(decision_round_id),
                "should_speak": should_speak,
                "willingness": willingness,
                "decision_reason": decision_reason,
                "failed": failed,
                "attempt_no": attempt_no,
                "duration_ms": duration_ms,
                "error_code": error_code,
            },
        )
        state = actor.state
        known_late_round = decision_round_id in self._late_experiment_rounds
        if state.free_competition_enabled and (
            known_late_round
            or state.selection_phase not in {"COMPETING", "MODEL_WAIT"}
            or state.agent_decision_round_id != decision_round_id
        ):
            await self._record_late_experiment_decision(
                match_id=match_id,
                agent_profile_id=agent_profile_id,
                decision_round_id=decision_round_id,
                should_speak=should_speak,
                decision_reason=decision_reason,
                failed=failed,
                attempt_no=attempt_no,
                duration_ms=duration_ms,
                error_code=error_code,
                stale=(not known_late_round and state.agent_decision_round_id != decision_round_id),
            )
            self._forget_late_experiment_round(decision_round_id)
            return actor.view()
        try:
            return await self.submit(match_id, command)
        except MatchDomainError as exc:
            if not actor.state.free_competition_enabled or str(exc) != "stale_callback":
                raise
            await self._record_late_experiment_decision(
                match_id=match_id,
                agent_profile_id=agent_profile_id,
                decision_round_id=decision_round_id,
                should_speak=should_speak,
                decision_reason=decision_reason,
                failed=failed,
                attempt_no=attempt_no,
                duration_ms=duration_ms,
                error_code=error_code,
                stale=actor.state.agent_decision_round_id != decision_round_id,
            )
            return actor.view()

    async def _record_late_experiment_decision(
        self,
        *,
        match_id: UUID,
        agent_profile_id: UUID,
        decision_round_id: UUID,
        should_speak: bool | None,
        decision_reason: str | None,
        failed: bool,
        attempt_no: int,
        duration_ms: int,
        error_code: str | None,
        stale: bool,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                decision = await session.scalar(
                    select(AgentFreeDebateDecision)
                    .where(
                        AgentFreeDebateDecision.match_id == match_id,
                        AgentFreeDebateDecision.decision_round_id == decision_round_id,
                        AgentFreeDebateDecision.agent_profile_id == agent_profile_id,
                    )
                    .with_for_update()
                )
                if decision is None or decision.opportunity_id is None:
                    return
                opportunity = await session.get(
                    FreeDebateOpportunity,
                    decision.opportunity_id,
                    with_for_update=True,
                )
                if opportunity is None:
                    return
                # Raw late output remains available for analysis, while the
                # effective status fixed at the cutoff is never changed.
                decision.status = "SKIP" if failed or should_speak is not True else "HAND"
                decision.should_speak = None if failed else should_speak
                decision.decision_reason = decision_reason
                decision.willingness = None
                decision.attempt_no = attempt_no
                decision.duration_ms = duration_ms
                decision.error_code = error_code or (
                    "STALE_EXPERIMENT_DECISION" if stale else "LATE_EXPERIMENT_DECISION"
                )
                decision.completed_at = datetime.now(UTC)
                decision.late = True
                decision.stale = stale

    async def publish_agent_subtitle(
        self, *, envelope: CallbackEnvelope, text: str, played_ms: int
    ) -> None:
        envelope.require("speech_id", "generation_id")
        match_id = envelope.match_id
        speech_id = envelope.speech_id
        assert speech_id is not None
        actor = self._actors.get(match_id)
        if actor is None or actor.state.current_speech_id != speech_id:
            return
        if not await actor.set_interim_text(text, speech_id=speech_id):
            return
        await self._publish(
            actor.state,
            (
                MatchEvent(
                    type="agent.subtitle",
                    match_id=match_id,
                    sequence=actor.state.sequence,
                    server_time_ms=int(datetime.now(UTC).timestamp() * 1000),
                    payload={"speech_id": str(speech_id), "text": text, "played_ms": played_ms},
                ),
            ),
        )

    async def publish_agent_retry(self, *, envelope: CallbackEnvelope, error_code: str) -> None:
        envelope.require("generation_id")
        match_id = envelope.match_id
        generation_id = envelope.generation_id
        assert generation_id is not None
        actor = self._actors.get(match_id)
        if actor is None:
            return
        await self._publish(
            actor.state,
            (
                MatchEvent(
                    type="agent.retrying",
                    match_id=match_id,
                    sequence=actor.state.sequence,
                    server_time_ms=int(datetime.now(UTC).timestamp() * 1000),
                    payload={"generation_id": str(generation_id), "error_code": error_code},
                ),
            ),
        )

    async def subscribe(self, match_id: UUID) -> asyncio.Queue[MatchEvent]:
        queue: asyncio.Queue[MatchEvent] = asyncio.Queue(maxsize=64)
        async with self._lock:
            self._subscribers.setdefault(match_id, set()).add(queue)
        return queue

    async def unsubscribe(self, match_id: UUID, queue: asyncio.Queue[MatchEvent]) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(match_id)
            if subscribers is None:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(match_id, None)

    async def publish_transcript_update(self, match_id: UUID, speech_id: UUID) -> None:
        """Broadcast a committed display-text change without changing runtime order."""

        actor = self._actors.get(match_id)
        if actor is None:
            return
        event = MatchEvent(
            type="transcript.updated",
            match_id=match_id,
            sequence=actor.state.sequence,
            server_time_ms=int(datetime.now(UTC).timestamp() * 1000),
            payload={"speech_id": str(speech_id)},
        )
        await self._publish(actor.state, (event,))

    async def publish_asr_interim(
        self, *, envelope: CallbackEnvelope, segment_no: int, text: str
    ) -> None:
        envelope.require("speech_id")
        match_id = envelope.match_id
        speech_id = envelope.speech_id
        assert speech_id is not None
        actor = self._actors.get(match_id)
        if (
            actor is None
            or actor.state.status != "RUNNING"
            or actor.state.current_speech_id != speech_id
        ):
            return
        if not await self._asr_callback_is_current(actor=actor, envelope=envelope):
            return

        def owner_is_current() -> bool:
            owner = getattr(self._speech_runtime, "speech_callback_envelope", None)
            return self._actors.get(match_id) is actor and (
                owner is None or owner(match_id, speech_id) is envelope
            )

        if not owner_is_current():
            return
        if not await actor.set_interim_text(
            text, speech_id=speech_id, owner_is_current=owner_is_current
        ):
            return
        await self._publish(
            actor.state,
            (
                MatchEvent(
                    type="asr.interim",
                    match_id=match_id,
                    sequence=actor.state.sequence,
                    server_time_ms=int(datetime.now(UTC).timestamp() * 1000),
                    payload={
                        "speech_id": str(speech_id),
                        "segment_no": segment_no,
                        "text": text,
                    },
                ),
            ),
        )

    async def persist_asr_segment(
        self,
        *,
        envelope: CallbackEnvelope,
        speech_id: UUID,
        segment_no: int,
        task_id: UUID,
        final_text: str,
        first_interim_latency_ms: int | None,
        final_latency_ms: int,
        pcm_sample_count: int,
    ) -> None:
        envelope.require("speech_id")
        if envelope.speech_id != speech_id:
            return
        match_id = envelope.match_id
        actor = self._actors.get(match_id)
        if actor is None or not await self._asr_callback_is_current(actor=actor, envelope=envelope):
            return
        async with self._session_factory() as session:
            async with session.begin():
                if (
                    await self._lock_current_asr_callback(
                        session=session,
                        actor=actor,
                        envelope=envelope,
                        speech_id=speech_id,
                    )
                    is None
                ):
                    return
                existing = await session.scalar(
                    select(AsrSegment)
                    .where(
                        AsrSegment.speech_id == speech_id,
                        AsrSegment.segment_no == segment_no,
                    )
                    .with_for_update()
                )
                if existing is not None and existing.task_id != task_id:
                    # A newer segment task owns this business segment.  The
                    # provider result is stale even when speech/context still
                    # look current.
                    return
                if existing is not None and existing.status in {"FAILED", "DISCARDED"}:
                    return
                segment = existing
                if segment is None:
                    segment = AsrSegment(
                        id=uuid4(),
                        speech_id=speech_id,
                        segment_no=segment_no,
                        task_id=task_id,
                        status="FINALIZED",
                    )
                call = await session.scalar(
                    select(ExternalCall)
                    .where(
                        ExternalCall.asr_segment_id == segment.id,
                        ExternalCall.status == "STARTED",
                    )
                    .with_for_update()
                )
                if call is not None and not self._asr_call_matches_envelope(call, envelope):
                    return
                completed_at = datetime.now(UTC)
                if existing is None:
                    segment.raw_final_text = final_text
                    segment.first_interim_latency_ms = first_interim_latency_ms
                    segment.final_latency_ms = final_latency_ms
                    segment.pcm_sample_count = pcm_sample_count
                    segment.finalized_at = completed_at
                    session.add(segment)
                else:
                    segment.status = "FINALIZED"
                    segment.raw_final_text = final_text
                    segment.first_interim_latency_ms = first_interim_latency_ms
                    segment.final_latency_ms = final_latency_ms
                    segment.pcm_sample_count = pcm_sample_count
                    segment.error_code = None
                    segment.finalized_at = completed_at
                completed_latency_ms = max(0, pcm_sample_count // 16 + final_latency_ms)
                if call is None:
                    call = ExternalCall(
                        call_kind="ASR",
                        capture_storage_mode="ASYNC_V1",
                        provider="BAILIAN",
                        operation="duplex.transcribe",
                        model=self._asr_model_id(),
                        attempt_no=1,
                        status="SUCCEEDED",
                        match_id=match_id,
                        speech_id=speech_id,
                        connection_epoch=envelope.connection_epoch,
                        context_version=envelope.context_version,
                        opportunity_id=envelope.opportunity_id,
                        asr_segment_id=segment.id,
                        started_at=completed_at - timedelta(milliseconds=completed_latency_ms),
                    )
                    session.add(call)
                call.status = "SUCCEEDED"
                call.first_result_at = (
                    call.started_at + timedelta(milliseconds=first_interim_latency_ms)
                    if first_interim_latency_ms is not None
                    else None
                )
                call.completed_at = completed_at
                call.first_result_latency_ms = first_interim_latency_ms
                call.completed_latency_ms = completed_latency_ms
        actor = self._actors.get(match_id)
        if actor is not None:
            await self._publish(
                actor.state,
                (
                    MatchEvent(
                        type="asr.segment_final",
                        match_id=match_id,
                        sequence=actor.state.sequence,
                        server_time_ms=int(datetime.now(UTC).timestamp() * 1000),
                        payload={
                            "speech_id": str(speech_id),
                            "segment_no": segment_no,
                            "text": final_text,
                        },
                    ),
                ),
            )

    async def start_asr_segment(
        self, *, envelope: CallbackEnvelope, segment_no: int, task_id: UUID
    ) -> None:
        envelope.require("speech_id")
        speech_id = envelope.speech_id
        assert speech_id is not None
        actor = self._actors.get(envelope.match_id)
        if actor is None or not await self._asr_callback_is_current(actor=actor, envelope=envelope):
            return
        async with self._session_factory() as session:
            async with session.begin():
                locked = await self._lock_current_asr_callback(
                    session=session,
                    actor=actor,
                    envelope=envelope,
                    speech_id=speech_id,
                )
                if locked is None:
                    return
                match, _speech = locked
                match_id = match.id
                task_segment = await session.scalar(
                    select(AsrSegment).where(AsrSegment.task_id == task_id).with_for_update()
                )
                if task_segment is not None:
                    if task_segment.speech_id != speech_id or task_segment.segment_no != segment_no:
                        return
                    return
                segment = await session.scalar(
                    select(AsrSegment)
                    .where(
                        AsrSegment.speech_id == speech_id,
                        AsrSegment.segment_no == segment_no,
                    )
                    .with_for_update()
                )
                if segment is None:
                    segment = AsrSegment(
                        id=uuid4(),
                        speech_id=speech_id,
                        segment_no=segment_no,
                        task_id=task_id,
                        status="STARTED",
                    )
                    session.add(segment)
                else:
                    if segment.task_id != task_id:
                        return
                    segment.task_id = task_id
                    segment.status = "STARTED"
                    segment.error_code = None
                previous_attempt = await session.scalar(
                    select(func.coalesce(func.max(ExternalCall.attempt_no), 0)).where(
                        ExternalCall.asr_segment_id == segment.id
                    )
                )
                session.add(
                    ExternalCall(
                        call_kind="ASR",
                        provider="BAILIAN",
                        operation="duplex.transcribe",
                        model=self._asr_model_id(),
                        attempt_no=int(previous_attempt or 0) + 1,
                        status="STARTED",
                        match_id=match_id,
                        speech_id=speech_id,
                        connection_epoch=envelope.connection_epoch,
                        context_version=envelope.context_version,
                        opportunity_id=envelope.opportunity_id,
                        asr_segment_id=segment.id,
                        capture_version=PROVIDER_CAPTURE_VERSION,
                        capture_storage_mode="ASYNC_V1",
                        captured_at=datetime.now(UTC),
                        source_kind="MATCH",
                        logical_call_id=segment.id,
                        started_at=datetime.now(UTC),
                    )
                )

    async def fail_asr_segment(
        self,
        *,
        envelope: CallbackEnvelope,
        segment_no: int,
        task_id: UUID,
        error_code: str,
    ) -> None:
        envelope.require("speech_id")
        speech_id = envelope.speech_id
        assert speech_id is not None
        actor = self._actors.get(envelope.match_id)
        if actor is None or not await self._asr_callback_is_current(actor=actor, envelope=envelope):
            return
        async with self._session_factory() as session:
            async with session.begin():
                if (
                    await self._lock_current_asr_callback(
                        session=session,
                        actor=actor,
                        envelope=envelope,
                        speech_id=speech_id,
                    )
                    is None
                ):
                    return
                segment = await session.scalar(
                    select(AsrSegment)
                    .where(
                        AsrSegment.speech_id == speech_id,
                        AsrSegment.segment_no == segment_no,
                        AsrSegment.task_id == task_id,
                    )
                    .with_for_update()
                )
                if segment is None:
                    return
                if segment.task_id != task_id:
                    return
                if segment.status != "STARTED":
                    return
                call = await session.scalar(
                    select(ExternalCall)
                    .where(
                        ExternalCall.asr_segment_id == segment.id,
                        ExternalCall.status == "STARTED",
                    )
                    .with_for_update()
                )
                if call is not None and not self._asr_call_matches_envelope(call, envelope):
                    return
                segment.status = "FAILED"
                segment.error_code = error_code
                segment.finalized_at = datetime.now(UTC)
                if call is not None:
                    call.status = "FAILED"
                    call.error_code = error_code
                    call.completed_at = datetime.now(UTC)

    async def persist_asr_capture(
        self, *, envelope: CallbackEnvelope, task_id: UUID, provider_capture: ProviderCallCapture
    ) -> None:
        submission = None
        try:
            envelope.require("speech_id")
            speech_id = envelope.speech_id
            assert speech_id is not None
            actor = self._actors.get(envelope.match_id)
            if actor is None or not await self._asr_callback_is_current(
                actor=actor, envelope=envelope
            ):
                return
            async with self._session_factory() as session:
                async with session.begin():
                    if (
                        await self._lock_current_asr_callback(
                            session=session,
                            actor=actor,
                            envelope=envelope,
                            speech_id=speech_id,
                        )
                        is None
                    ):
                        return
                    call = await session.scalar(
                        select(ExternalCall)
                        .join(AsrSegment, AsrSegment.id == ExternalCall.asr_segment_id)
                        .where(AsrSegment.speech_id == speech_id, AsrSegment.task_id == task_id)
                        .with_for_update()
                    )
                    if call is not None:
                        if not self._asr_call_matches_envelope(call, envelope):
                            return
                        call.capture_storage_mode = "ASYNC_V1"
                        await session.flush()
                        submission = prepare_capture(self._capture_writer, call, provider_capture)
            submit_capture(self._capture_writer, submission)
        finally:
            if submission is not None:
                submission.close()
            provider_capture.close()

    async def start_agent_playback(
        self,
        *,
        envelope: CallbackEnvelope,
        agent_profile_id: UUID,
        audio_storage_path: str,
    ) -> object:
        envelope.require("speech_id", "generation_id")
        match_id = envelope.match_id
        speech_id = envelope.speech_id
        generation_id = envelope.generation_id
        assert speech_id is not None and generation_id is not None
        actor = await self.get_actor(match_id)
        return await actor.submit(
            MatchCommand(
                type="agent.playback_started",
                message_id=f"agent-playback-start:{speech_id}",
                payload={
                    "speech_id": str(speech_id),
                    "generation_id": str(generation_id),
                    "agent_profile_id": str(agent_profile_id),
                    "audio_storage_path": audio_storage_path,
                },
            )
        )

    async def finish_agent_playback(self, *, envelope: CallbackEnvelope) -> object:
        envelope.require("speech_id")
        match_id = envelope.match_id
        speech_id = envelope.speech_id
        assert speech_id is not None
        actor = await self.get_actor(match_id)
        return await actor.submit(
            MatchCommand(
                type="agent.playback_finished",
                message_id=f"agent-playback-finished:{speech_id}",
                payload={"speech_id": str(speech_id)},
            )
        )

    async def finalize_agent_speech(
        self,
        *,
        envelope: CallbackEnvelope,
        final_text: str,
        llm_draft_text: str,
        audio_storage_path: str,
        audio_duration_ms: int,
        audio_truncated: bool,
    ) -> object:
        envelope.require("speech_id", "generation_id")
        match_id = envelope.match_id
        speech_id = envelope.speech_id
        generation_id = envelope.generation_id
        assert speech_id is not None and generation_id is not None
        actor = await self.get_actor(match_id)
        action = actor.state.current_action
        if (
            actor.state.free_competition_enabled
            and action is not None
            and action.action_kind == "FREE_DEBATE"
            and (
                actor.state.action_state != "AGENT_FINALIZING"
                or actor.state.current_speech_id != speech_id
            )
        ):
            await self._persist_late_experiment_agent_final(
                match_id=match_id,
                speech_id=speech_id,
                generation_id=generation_id,
                final_text=final_text,
                llm_draft_text=llm_draft_text,
                audio_storage_path=audio_storage_path,
                audio_duration_ms=audio_duration_ms,
                audio_truncated=audio_truncated,
            )
            return actor.view()
        try:
            return await self.submit(
                match_id,
                MatchCommand(
                    type="agent.finalized",
                    message_id=f"agent-finalized:{speech_id}",
                    payload={
                        "speech_id": str(speech_id),
                        "generation_id": str(generation_id),
                        "final_text": final_text,
                        "llm_draft_text": llm_draft_text,
                        "audio_storage_path": audio_storage_path,
                        "audio_duration_ms": audio_duration_ms,
                        "audio_truncated": audio_truncated,
                    },
                ),
            )
        except MatchDomainError as error:
            if str(error) not in {"stale_callback", "match_state_conflict"}:
                raise
            logger.info(
                "late Agent finalization ignored",
                extra={
                    "error_code": "agent_finalization_stale",
                    "match_id": str(match_id),
                    "speech_id": str(speech_id),
                    "generation_id": str(generation_id),
                },
            )
            return actor.view()

    async def _persist_late_experiment_agent_final(
        self,
        *,
        match_id: UUID,
        speech_id: UUID,
        generation_id: UUID,
        final_text: str,
        llm_draft_text: str,
        audio_storage_path: str,
        audio_duration_ms: int,
        audio_truncated: bool,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                speech = await session.get(Speech, speech_id, with_for_update=True)
                if (
                    speech is None
                    or speech.match_id != match_id
                    or speech.generation_id != generation_id
                ):
                    return
                if speech.status in {"FINALIZED", "RESET"}:
                    return
                finalized_at = datetime.now(UTC)
                speech.status = "FINALIZED"
                speech.display_text = final_text
                speech.llm_draft_text = llm_draft_text
                speech.audio_storage_path = audio_storage_path
                speech.audio_duration_ms = audio_duration_ms
                speech.audio_truncated = audio_truncated
                speech.finalized_at = finalized_at
                speech.ended_at = speech.ended_at or finalized_at
                if speech.opportunity_id is not None:
                    allocated = await session.get(
                        FreeDebateOpportunity,
                        speech.opportunity_id,
                        with_for_update=True,
                    )
                    if allocated is not None:
                        allocated.status = "COMPLETED"
                        allocated.execution_fact = "AI_SPOKE"
                        allocated.completed_at = finalized_at
                # A late agent callback must locate only its source opportunity.  Scanning
                # every ACTIVE opportunity for the match makes callback memory and lock
                # time grow with stale rows after retries/recovery.  The durable
                # ``source_speech_id`` column is populated when the opportunity is opened
                # and gives us a bounded match/source lookup.
                source = await session.scalar(
                    _active_opportunity_for_source_query(
                        match_id=match_id, source_speech_id=speech_id
                    )
                )
                if source is not None:
                    source.source_speech_id = speech_id
                    source.source_ended_at = speech.ended_at
                match = await session.get(Match, match_id, with_for_update=True)
                if match is not None:
                    match.context_version += 1
                existing_file = await session.scalar(
                    select(MatchFile.id).where(
                        MatchFile.match_id == match_id,
                        MatchFile.file_key == f"agent-{speech.id}",
                    )
                )
                if existing_file is None:
                    session.add(
                        MatchFile(
                            match_id=match_id,
                            speech_id=speech.id,
                            owner_user_id=None,
                            file_key=f"agent-{speech.id}",
                            file_kind="AGENT_RAW",
                            status="READY" if audio_storage_path else "FAILED",
                            storage_path=audio_storage_path or None,
                            codec="ogg_opus" if audio_storage_path else None,
                            byte_count=_file_size(audio_storage_path),
                            duration_ms=audio_duration_ms,
                            expires_at=datetime.now(UTC) + timedelta(days=30),
                            error_code=None if audio_storage_path else "agent_audio_missing",
                        )
                    )

    def _agent_failure_target_is_current(
        self,
        state: MatchRuntimeState,
        envelope: CallbackEnvelope,
        logical_call_id: UUID | None = None,
    ) -> bool:
        action = state.current_action
        if (
            state.match_id != envelope.match_id
            or state.status != "RUNNING"
            or state.action_state not in {"AGENT_PREPARING", "AGENT_SPEAKING", "AGENT_FINALIZING"}
            or action is None
            or state.current_agent_profile_id is None
            or state.current_speech_id != envelope.speech_id
            or envelope.connection_epoch is not None
        ):
            return False
        if envelope.generation_id is None:
            return (
                logical_call_id is not None
                and state.action_state == "AGENT_PREPARING"
                and self._agent_runtime is not None
                and self._agent_runtime.agent_startup_callback_is_current(
                    envelope=envelope,
                    logical_call_id=logical_call_id,
                    action_key=action.action_key,
                    agent_profile_id=state.current_agent_profile_id,
                )
            )
        if state.action_state == "AGENT_FINALIZING" and envelope.speech_id is not None:
            # Successful media persistence releases the run before its Actor
            # finalization callback. The locked Speech below remains its owner.
            return True
        return self._agent_runtime is not None and self._agent_runtime.agent_callback_is_current(
            envelope=envelope,
            action_key=action.action_key,
            agent_profile_id=state.current_agent_profile_id,
        )

    async def _require_locked_agent_failure(
        self,
        session: AsyncSession,
        match: Match,
        previous: MatchRuntimeState,
        envelope: CallbackEnvelope,
        logical_call_id: UUID | None,
    ) -> None:
        action = previous.current_action
        if (
            not self._agent_failure_target_is_current(previous, envelope, logical_call_id)
            or action is None
            or match.context_version != envelope.context_version
        ):
            raise MatchDomainError("stale_callback")
        if envelope.generation_id is None:
            return
        generation = await session.get(
            AgentGeneration, envelope.generation_id, with_for_update=True
        )
        if (
            generation is None
            or generation.match_id != match.id
            or generation.action_key != action.action_key
            or generation.agent_profile_id != previous.current_agent_profile_id
            or generation.call_type != "LLM_SPEECH"
            or generation.context_version != envelope.context_version
            or match.context_version != envelope.context_version
            or generation.attempt_no != envelope.attempt_no
        ):
            raise MatchDomainError("stale_callback")
        if envelope.speech_id is not None:
            speech = await session.get(Speech, envelope.speech_id, with_for_update=True)
            if (
                speech is None
                or speech.match_id != match.id
                or speech.speaker_kind != "AGENT"
                or speech.agent_profile_id != previous.current_agent_profile_id
                or speech.generation_id != envelope.generation_id
                or speech.action_key != action.action_key
                or speech.opportunity_id != envelope.opportunity_id
                or speech.status not in {"STARTED", "FINALIZING"}
            ):
                raise MatchDomainError("stale_callback")
            if envelope.opportunity_id is not None:
                opportunity = await session.get(FreeDebateOpportunity, envelope.opportunity_id)
                if (
                    opportunity is None
                    or opportunity.match_id != match.id
                    or opportunity.opportunity_generation != envelope.opportunity_generation
                ):
                    raise MatchDomainError("stale_callback")
        if not self._agent_failure_target_is_current(previous, envelope, logical_call_id):
            raise MatchDomainError("stale_callback")

    async def handle_agent_failure(
        self,
        *,
        envelope: CallbackEnvelope,
        error_code: str,
        logical_call_id: UUID | None = None,
    ) -> None:
        match_id = envelope.match_id
        generation_id = envelope.generation_id
        actor = self._actors.get(match_id)
        if actor is None:
            return
        state = actor.state
        if state.status != "RUNNING" or state.action_state not in {
            "AGENT_PREPARING",
            "AGENT_SPEAKING",
            "AGENT_FINALIZING",
        }:
            return
        if not self._agent_failure_target_is_current(state, envelope, logical_call_id):
            return
        payload: dict[str, Any] = {
            "error_code": error_code,
            "generation_id": str(generation_id) if generation_id else None,
        }
        try:
            await self.submit(
                match_id,
                MatchCommand(
                    type="system.error",
                    message_id=(
                        f"agent-error:{match_id}:{generation_id or logical_call_id}:{error_code}"
                    ),
                    payload=payload,
                    agent_callback_envelope=envelope,
                    agent_logical_call_id=logical_call_id,
                ),
            )
        except MatchDomainError as error:
            if str(error) not in {"stale_callback", "match_state_conflict"}:
                raise

    async def _host_callback_is_current(
        self, *, envelope: CallbackEnvelope, action_key: str
    ) -> bool:
        if not self._host_callback_matches_runtime(envelope=envelope, action_key=action_key):
            return False
        async with self._session_factory() as session:
            context_version = await session.scalar(
                select(Match.context_version).where(Match.id == envelope.match_id)
            )
        return (
            context_version is not None
            and envelope.context_version == context_version
            and self._host_callback_matches_runtime(envelope=envelope, action_key=action_key)
        )

    def _host_callback_matches_runtime(
        self, *, envelope: CallbackEnvelope, action_key: str
    ) -> bool:
        actor = self._actors.get(envelope.match_id)
        if actor is None or actor.state.status != "RUNNING":
            return False
        action = actor.state.current_action
        if actor.state.action_state != "HOST_ANNOUNCING" or action is None:
            return False
        return (
            action.action_key == action_key
            and envelope.generation_id is not None
            and self._agent_runtime is not None
            and self._agent_runtime.host_callback_is_current(
                envelope=envelope, action_key=action_key
            )
        )

    async def host_audio_finished(self, *, envelope: CallbackEnvelope, action_key: str) -> None:
        if not await self._host_callback_is_current(envelope=envelope, action_key=action_key):
            return
        try:
            await self.submit(
                envelope.match_id,
                MatchCommand(
                    type="host.finished",
                    message_id=(
                        f"host-finished:{envelope.match_id}:{action_key}:"
                        f"{envelope.generation_id}:{envelope.attempt_no}"
                    ),
                    payload={
                        "authorized": True,
                        "action_key": action_key,
                        "generation_id": str(envelope.generation_id),
                    },
                    host_callback_envelope=envelope,
                ),
            )
        except MatchDomainError as error:
            if error.code != "stale_callback" and self._host_callback_matches_runtime(
                envelope=envelope, action_key=action_key
            ):
                raise

    def _require_current_host_guard(self, match_id: UUID, guard_id: UUID) -> None:
        actor = self._actors.get(match_id)
        if actor is None or not actor.host_guard_is_current(guard_id):
            raise MatchDomainError("stale_callback")

    def _require_locked_host_identity(self, command: MatchCommand, match: Match) -> None:
        envelope = command.host_callback_envelope
        if envelope is not None:
            action_key = command.payload.get("action_key")
            if (
                match.context_version != envelope.context_version
                or not isinstance(action_key, str)
                or not self._host_callback_matches_runtime(envelope=envelope, action_key=action_key)
            ):
                raise MatchDomainError("stale_callback")
        if command.host_guard_id is not None:
            self._require_current_host_guard(match.id, command.host_guard_id)

    async def host_audio_failed(
        self, *, envelope: CallbackEnvelope, action_key: str, error_code: str
    ) -> None:
        if not await self._host_callback_is_current(envelope=envelope, action_key=action_key):
            return
        try:
            await self.submit(
                envelope.match_id,
                MatchCommand(
                    type="system.error",
                    message_id=(
                        f"host-error:{envelope.match_id}:{action_key}:"
                        f"{envelope.generation_id}:{envelope.attempt_no}"
                    ),
                    payload={
                        "error_code": error_code,
                        "action_key": action_key,
                    },
                    host_callback_envelope=envelope,
                ),
            )
        except MatchDomainError as error:
            if error.code != "stale_callback" and self._host_callback_matches_runtime(
                envelope=envelope, action_key=action_key
            ):
                raise

    async def finalize_asr_speech(
        self,
        *,
        envelope: CallbackEnvelope,
        final_text: str,
        first_interim_latency_ms: int | None,
        final_latency_ms: int,
        audio_duration_ms: int,
        audio_storage_path: str | None,
        audio_recording_error: str | None,
    ) -> MatchCommandResult:
        envelope.require("speech_id")
        match_id = envelope.match_id
        speech_id = envelope.speech_id
        assert speech_id is not None
        actor = await self.get_actor(match_id)
        if not await self._asr_callback_is_current(actor=actor, envelope=envelope):
            return MatchCommandResult(state=actor.state, events=())
        action = actor.state.current_action
        operation = actor.state.emergency_operation
        retrying = bool(
            operation
            and operation.get("kind") == "asr.retry"
            and operation.get("status") == "PENDING"
        )
        if (
            not retrying
            and actor.state.free_competition_enabled
            and action is not None
            and action.action_kind == "FREE_DEBATE"
        ):
            if (
                actor.state.action_state != "SPEECH_FINALIZING"
                or actor.state.current_speech_id != speech_id
            ):
                await self._persist_late_experiment_asr(
                    envelope=envelope,
                    match_id=match_id,
                    speech_id=speech_id,
                    final_text=final_text,
                    first_interim_latency_ms=first_interim_latency_ms,
                    final_latency_ms=final_latency_ms,
                    audio_duration_ms=audio_duration_ms,
                    audio_storage_path=audio_storage_path,
                    audio_recording_error=audio_recording_error,
                )
                await self._start_current_experiment_decision(
                    match_id=match_id, source_speech_id=speech_id
                )
                return MatchCommandResult(state=actor.state, events=())
        return await self.submit(
            match_id,
            MatchCommand(
                type="asr.finalized",
                asr_callback_envelope=envelope,
                asr_deadline_mono=self._asr_final_deadlines.get((match_id, speech_id)),
                message_id=f"asr-finalized:{speech_id}",
                payload={
                    "speech_id": str(speech_id),
                    "final_text": final_text,
                    "first_interim_latency_ms": first_interim_latency_ms,
                    "final_latency_ms": final_latency_ms,
                    "audio_duration_ms": audio_duration_ms,
                    "audio_storage_path": audio_storage_path,
                    "audio_recording_error": audio_recording_error,
                },
            ),
        )

    async def _persist_late_experiment_asr(
        self,
        *,
        envelope: CallbackEnvelope | None = None,
        match_id: UUID,
        speech_id: UUID,
        final_text: str,
        first_interim_latency_ms: int | None,
        final_latency_ms: int,
        audio_duration_ms: int,
        audio_storage_path: str | None,
        audio_recording_error: str | None,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                match = await session.get(Match, match_id, with_for_update=True)
                if match is None or (
                    envelope is not None and envelope.context_version != match.context_version
                ):
                    return
                speech = await session.get(Speech, speech_id, with_for_update=True)
                if speech is None or speech.match_id != match_id:
                    return
                if speech.status not in {"STARTED", "FINALIZING"}:
                    return
                if envelope is not None:
                    actor = self._actors.get(match_id)
                    if (
                        envelope.speech_id != speech.id
                        or envelope.attempt_no != speech.attempt_no
                        or envelope.opportunity_id != speech.opportunity_id
                        or actor is None
                        or not self._asr_epoch_is_current(
                            actor=actor, speech=speech, envelope=envelope
                        )
                    ):
                        return
                speech.status = "FINALIZED"
                speech.finalized_at = datetime.now(UTC)
                speech.ended_at = speech.ended_at or speech.finalized_at
                speech.asr_raw_final_text = final_text
                speech.display_text = final_text
                speech.first_interim_latency_ms = first_interim_latency_ms
                speech.final_latency_ms = final_latency_ms
                speech.audio_duration_ms = audio_duration_ms
                speech.audio_storage_path = audio_storage_path
                if speech.opportunity_id is not None:
                    allocated_opportunity = await session.get(
                        FreeDebateOpportunity,
                        speech.opportunity_id,
                        with_for_update=True,
                    )
                    if allocated_opportunity is not None:
                        allocated_opportunity.status = "COMPLETED"
                        allocated_opportunity.execution_fact = "HUMAN_SPOKE"
                        allocated_opportunity.completed_at = speech.finalized_at
                # Keep the late-ASR path bounded for the same reason as the agent path
                # above.  A match can retain many historical opportunities, but only one
                # active opportunity can own a given source speech.
                source_opportunity = await session.scalar(
                    _active_opportunity_for_source_query(
                        match_id=match_id, source_speech_id=speech_id
                    )
                )
                if source_opportunity is not None:
                    source_opportunity.source_speech_id = speech_id
                    source_opportunity.source_ended_at = speech.ended_at
                match.context_version += 1
                existing_file = await session.scalar(
                    select(MatchFile.id).where(
                        MatchFile.match_id == match_id,
                        MatchFile.file_key == f"human-{speech.id}",
                    )
                )
                if existing_file is None:
                    session.add(
                        MatchFile(
                            match_id=match_id,
                            speech_id=speech.id,
                            owner_user_id=speech.user_id,
                            file_key=f"human-{speech.id}",
                            file_kind="HUMAN_RAW",
                            status="READY" if audio_storage_path else "FAILED",
                            storage_path=audio_storage_path,
                            codec=("pcm_s16le_16000_mono" if audio_storage_path else None),
                            byte_count=_file_size(audio_storage_path),
                            duration_ms=audio_duration_ms,
                            expires_at=datetime.now(UTC) + timedelta(days=30),
                            error_code=audio_recording_error,
                        )
                    )

    async def _start_late_asr_decision(self, *, match_id: UUID, source_speech_id: UUID) -> None:
        actor = self._actors.get(match_id)
        if actor is None or not actor.state.free_competition_enabled:
            return
        state = actor.state
        action = state.current_action
        if action is None or action.action_kind != "FREE_DEBATE":
            return
        round_id = uuid4()
        async with self._session_factory() as session:
            async with session.begin():
                opportunity = await session.scalar(
                    _active_opportunity_for_source_query(
                        match_id=match_id, source_speech_id=source_speech_id
                    )
                )
                match = await session.get(Match, match_id)
                if opportunity is None or match is None:
                    return
                side = opportunity.side
                agents = tuple(
                    item
                    for item in action.participants
                    if item.side == side and item.agent_profile_id is not None
                )
                if state.experiment_mode and not state.formal_4v4:
                    agents = agents[:1]
                if not agents:
                    return
                existing = await session.scalar(
                    select(AgentFreeDebateDecision.id).where(
                        AgentFreeDebateDecision.opportunity_id == opportunity.id
                    )
                )
                if existing is not None:
                    return
                for agent in agents:
                    assert agent.agent_profile_id is not None
                    session.add(
                        AgentFreeDebateDecision(
                            match_id=match_id,
                            action_key=action.action_key,
                            decision_round_id=round_id,
                            context_version=match.context_version,
                            agent_profile_id=agent.agent_profile_id,
                            side=side,
                            seat_no=agent.seat_no,
                            status="DECIDING",
                            opportunity_id=opportunity.id,
                            deadline_at=opportunity.selection_deadline_at,
                            trigger_kind=opportunity.trigger_kind,
                            effective_status="TECHNICAL_MISSING",
                            late=True,
                            error_code="ASR_LATE",
                            started_at=datetime.now(UTC),
                        )
                    )
                opportunity.decision_fact = "TECHNICAL_MISSING"
        self._remember_late_experiment_round(match_id=match_id, round_id=round_id)
        if self._agent_runtime is not None:
            decision_envelope = CallbackEnvelope(
                match_id=match_id,
                speech_id=source_speech_id,
                attempt_no=1,
                generation_id=None,
                connection_epoch=None,
                context_version=match.context_version,
                opportunity_id=opportunity.id,
                opportunity_generation=opportunity.opportunity_generation,
            )
            self._spawn(
                self._agent_runtime.decide_free_debate(
                    match_id=match_id,
                    action_key=action.action_key,
                    side=side,
                    agent_profile_ids=[
                        agent.agent_profile_id
                        for agent in agents
                        if agent.agent_profile_id is not None
                    ],
                    decision_round_id=round_id,
                    envelope=decision_envelope,
                ),
                name=f"late-asr-decision-{match_id}-{round_id}",
            )

    async def _persist_late_asr_failure(
        self, *, actor: MatchActor, envelope: CallbackEnvelope, code: str
    ) -> None:
        """Settle a preceding source without failing its newly selected owner."""
        if not await self._asr_callback_is_current(actor=actor, envelope=envelope):
            return
        async with self._session_factory() as session:
            async with session.begin():
                match = await session.get(Match, envelope.match_id, with_for_update=True)
                speech = await session.get(Speech, envelope.speech_id, with_for_update=True)
                if match is None or speech is None:
                    return
                opportunity = (
                    await session.get(FreeDebateOpportunity, speech.opportunity_id)
                    if speech.opportunity_id is not None
                    else None
                )
                if (
                    actor.state.status != "RUNNING"
                    or actor.state.free_source_speech_id != speech.id
                    or actor.state.current_speech_id == speech.id
                    or envelope.match_id != speech.match_id
                    or envelope.generation_id is not None
                    or envelope.attempt_no != speech.attempt_no
                    or envelope.context_version != match.context_version
                    or envelope.connection_epoch
                    != (
                        dict(actor.state.connection_epochs).get(speech.user_id, 1)
                        if speech.user_id is not None
                        else None
                    )
                    or envelope.opportunity_id != speech.opportunity_id
                    or envelope.opportunity_generation
                    != (opportunity.opportunity_generation if opportunity else None)
                    or speech.status not in {"STARTED", "FINALIZING"}
                ):
                    return
                speech.status = "FAILED"
                speech.asr_error_code = code
                speech.ended_at = speech.ended_at or datetime.now(UTC)
                if opportunity is not None:
                    opportunity.status = "COMPLETED"
                    opportunity.execution_fact = "HUMAN_SPOKE"
                    opportunity.completed_at = datetime.now(UTC)

    async def handle_asr_failure(self, *, envelope: CallbackEnvelope, code: str) -> None:
        envelope.require("speech_id")
        match_id = envelope.match_id
        speech_id = envelope.speech_id
        assert speech_id is not None
        actor = await self.get_actor(match_id)
        operation = actor.state.emergency_operation
        retrying = bool(
            operation
            and operation.get("kind") == "asr.retry"
            and operation.get("status") == "PENDING"
        )
        if actor.state.status != "RUNNING" and not retrying:
            return
        if not retrying and (
            actor.state.current_speech_id != speech_id
            or actor.state.action_state
            not in {
                "HUMAN_SPEAKING",
                "SPEECH_FINALIZING",
            }
        ):
            if (
                actor.state.free_competition_enabled
                and actor.state.free_source_speech_id == speech_id
            ):
                pass
            else:
                return
        if not await self._asr_callback_is_current(actor=actor, envelope=envelope):
            return
        try:
            await actor.submit(
                MatchCommand(
                    type="asr.failed",
                    message_id=(
                        f"asr-error:{speech_id}:{envelope.attempt_no}:"
                        f"{operation.get('id') if retrying and operation else 'initial'}"
                    ),
                    payload={
                        "speech_id": str(speech_id),
                        "error_code": code,
                        "asr_failure_class": classify_asr_error(code),
                    },
                    asr_callback_envelope=envelope,
                )
            )
        except MatchDomainError as error:
            if error.code not in {"stale_callback", "match_not_running"}:
                raise

    async def _persist_started_speech(
        self,
        session: AsyncSession,
        candidate: MatchRuntimeState,
        event: MatchEvent,
    ) -> None:
        speech_id = event.payload.get("speech_id")
        current = candidate.current_action
        if (
            speech_id is None
            or current is None
            or not (
                current.speaker_user_id is not None
                or current.agent_profile_id is not None
                or candidate.current_speaker_user_id is not None
                or candidate.current_agent_profile_id is not None
            )
        ):
            return
        previous_query = select(func.coalesce(func.max(Speech.attempt_no), 0)).where(
            Speech.match_id == candidate.match_id,
            Speech.action_key == current.action_key,
        )
        opportunity_id = event.payload.get("opportunity_id")
        if opportunity_id:
            previous_query = previous_query.where(
                Speech.opportunity_id == UUID(str(opportunity_id))
            )
        previous_attempt = await session.scalar(previous_query)
        existing_speech = await session.get(Speech, UUID(str(speech_id)), with_for_update=True)
        if existing_speech is not None:
            expected_opportunity = (
                _required_uuid(opportunity_id)
                if candidate.free_competition_enabled and opportunity_id
                else None
            )
            if (
                existing_speech.match_id != candidate.match_id
                or existing_speech.action_key != current.action_key
                or existing_speech.user_id != candidate.current_speaker_user_id
                or existing_speech.agent_profile_id != candidate.current_agent_profile_id
                or existing_speech.side != (candidate.current_speaker_side or current.side)
                or existing_speech.seat_no != (candidate.current_speaker_seat_no or current.seat_no)
                or existing_speech.opportunity_id != expected_opportunity
                or existing_speech.status not in {"STARTED", "FINALIZING"}
            ):
                raise MatchDomainError("match_state_conflict")
            existing_speech.status = "STARTED"
            existing_speech.ended_at = None
            existing_speech.finalized_at = None
            return
        session.add(
            Speech(
                id=UUID(str(speech_id)),
                match_id=candidate.match_id,
                opportunity_id=(
                    _required_uuid(event.payload["opportunity_id"])
                    if candidate.free_competition_enabled and event.payload.get("opportunity_id")
                    else None
                ),
                action_key=current.action_key,
                user_id=candidate.current_speaker_user_id,
                side=candidate.current_speaker_side or current.side or "AFFIRMATIVE",
                seat_no=candidate.current_speaker_seat_no or current.seat_no or 1,
                speaker_kind=(
                    "AGENT" if candidate.current_agent_profile_id is not None else "HUMAN"
                ),
                agent_profile_id=candidate.current_agent_profile_id,
                generation_id=(
                    _required_uuid(event.payload["generation_id"])
                    if event.payload.get("generation_id")
                    else None
                ),
                audio_storage_path=(
                    str(event.payload["audio_storage_path"])
                    if event.payload.get("audio_storage_path")
                    else None
                ),
                attempt_no=int(previous_attempt or 0) + 1,
            )
        )

    async def _retire_paused_agent_speech(
        self,
        session: AsyncSession,
        match: Match,
        previous: MatchRuntimeState,
    ) -> None:
        """Retire only the Agent speech revoked by this committed pause.

        Only the Speech record is retired. A formal finalizer keeps ownership
        of its generation, spool and asset; its late callback remains stale.
        """
        action = previous.current_action
        if (
            previous.current_speech_id is None
            or previous.current_agent_profile_id is None
            or previous.action_state not in {"AGENT_SPEAKING", "AGENT_FINALIZING"}
            or action is None
        ):
            return
        speech = await session.get(Speech, previous.current_speech_id, with_for_update=True)
        if (
            speech is None
            or speech.match_id != match.id
            or speech.speaker_kind != "AGENT"
            or speech.agent_profile_id != previous.current_agent_profile_id
            or speech.action_key != action.action_key
            or speech.status
            != ("FINALIZING" if previous.action_state == "AGENT_FINALIZING" else "STARTED")
            or speech.generation_id is None
        ):
            return
        generation = await session.get(AgentGeneration, speech.generation_id, with_for_update=True)
        if (
            generation is None
            or generation.match_id != match.id
            or generation.agent_profile_id != speech.agent_profile_id
            or generation.action_key != speech.action_key
            or generation.call_type != "LLM_SPEECH"
            or generation.status not in {"STARTED", "LLM_READY", "PLAYING", "FAILED", "FINALIZED"}
            or (generation.status == "FINALIZED" and speech.status != "FINALIZING")
        ):
            return
        speech.status = "RESET"
        speech.ended_at = speech.ended_at or datetime.now(UTC)

    async def _commit(
        self,
        previous: MatchRuntimeState,
        candidate: MatchRuntimeState,
        events: tuple[MatchEvent, ...],
        command: MatchCommand,
    ) -> None:
        if command.asr_deadline_mono is None:
            await self._commit_within_deadline(previous, candidate, events, command)
        else:
            async with asyncio.timeout_at(command.asr_deadline_mono):
                await self._commit_within_deadline(previous, candidate, events, command)

    async def _commit_within_deadline(
        self,
        previous: MatchRuntimeState,
        candidate: MatchRuntimeState,
        events: tuple[MatchEvent, ...],
        command: MatchCommand,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                match = await session.scalar(
                    select(Match).where(Match.id == candidate.match_id).with_for_update()
                )
                if match is None or match.sequence != previous.sequence:
                    raise MatchDomainError("match_state_conflict")
                if command.asr_callback_envelope is not None:
                    envelope = command.asr_callback_envelope
                    actor = self._actors.get(candidate.match_id)
                    if (
                        actor is None
                        or envelope.speech_id is None
                        or await self._lock_current_asr_callback(
                            session=session,
                            actor=actor,
                            envelope=envelope,
                            speech_id=envelope.speech_id,
                            recording_recovery=command.asr_recording_recovery,
                        )
                        is None
                    ):
                        raise MatchDomainError("stale_callback")
                agent_failure = command.agent_callback_envelope
                if agent_failure is not None:
                    await self._require_locked_agent_failure(
                        session, match, previous, agent_failure, command.agent_logical_call_id
                    )
                if (
                    events
                    and command.payload.get("operation_id")
                    and command.type
                    not in {
                        "speech.skip.fail",
                        "speech.skip.cancel",
                    }
                ):
                    expected_context = command.payload.get(
                        "expected_context_version", command.payload.get("context_version")
                    )
                    if expected_context != match.context_version:
                        raise MatchDomainError("stale_callback")
                if command.type == "match.pause" and any(
                    event.type == "match.paused" for event in events
                ):
                    await self._retire_paused_agent_speech(session, match, previous)
                if command.host_callback_envelope is not None or (command.host_guard_id and events):
                    self._require_locked_host_identity(command, match)
                match.status = candidate.status
                match.sequence = candidate.sequence
                match.runtime_snapshot = state_snapshot(candidate)
                action = candidate.current_action
                match.current_stage_position = action.stage_position if action else None
                match.current_action_position = action.action_position if action else None
                match.current_speech_id = candidate.current_speech_id
                if candidate.status == "RUNNING" and match.started_at is None:
                    match.started_at = datetime.now(UTC)
                if candidate.status in ("FINISHED", "TERMINATED"):
                    match.ended_at = datetime.now(UTC)
                    session.add(
                        BackgroundTask(
                            task_type="POSTMATCH_AUDIO",
                            payload={"match_id": str(candidate.match_id)},
                            max_attempts=2,
                        )
                    )
                    session.add(
                        MatchFile(
                            match_id=candidate.match_id,
                            file_key="replay",
                            file_kind="MATCH_REPLAY",
                            status="PROCESSING",
                            byte_count=0,
                        )
                    )
                    if candidate.status == "FINISHED":
                        room = await session.get(Room, match.room_id)
                        snapshot = room.format_snapshot if room is not None else None
                        if (
                            candidate.experiment_attempt_id is None
                            and isinstance(snapshot, dict)
                            and bool(snapshot.get("postmatch_questionnaire_enabled"))
                        ):
                            participants = list(
                                (
                                    await session.scalars(
                                        select(MatchParticipant).where(
                                            MatchParticipant.match_id == candidate.match_id,
                                            MatchParticipant.kind == "HUMAN",
                                            MatchParticipant.user_id.is_not(None),
                                        )
                                    )
                                ).all()
                            )
                            for participant in participants:
                                if participant.user_id is not None:
                                    session.add(
                                        PostmatchSurveyTask(
                                            match_id=candidate.match_id,
                                            user_id=participant.user_id,
                                            questionnaire_version=POSTMATCH_QUESTIONNAIRE_VERSION,
                                        )
                                    )
                        human_count = await session.scalar(
                            select(func.count())
                            .select_from(MatchParticipant)
                            .where(
                                MatchParticipant.match_id == candidate.match_id,
                                MatchParticipant.kind == "HUMAN",
                            )
                        )
                        if int(human_count or 0) == 0:
                            match.archived_at = match.ended_at
                        else:
                            session.add(
                                BackgroundTask(
                                    task_type="TRANSCRIPT_AUTO_ARCHIVE",
                                    payload={"match_id": str(candidate.match_id)},
                                    available_at=match.ended_at + timedelta(hours=24),
                                    max_attempts=2,
                                )
                            )
                if (
                    candidate.free_competition_enabled
                    and candidate.experiment_attempt_id is not None
                ):
                    experiment_attempt = await session.get(
                        ExperimentMatchAttempt,
                        candidate.experiment_attempt_id,
                        with_for_update=True,
                    )
                    if experiment_attempt is None:
                        raise MatchDomainError("match_state_conflict")
                    scheduled_experiment = await session.get(
                        ScheduledMatch,
                        experiment_attempt.scheduled_match_id,
                        with_for_update=True,
                    )
                    if scheduled_experiment is None:
                        raise MatchDomainError("match_state_conflict")
                    if candidate.status == "FINISHED":
                        experiment_attempt.status = "COMPLETED"
                        experiment_attempt.ended_at = datetime.now(UTC)
                        scheduled_experiment.status = "COMPLETED"
                        scheduled_experiment.effective_attempt_id = experiment_attempt.id
                    elif candidate.status == "TERMINATED":
                        experiment_attempt.status = "TERMINATED"
                        experiment_attempt.termination_reason = str(
                            command.payload.get("reason", "管理员终止")
                        )
                        experiment_attempt.ended_at = datetime.now(UTC)
                        scheduled_experiment.status = "INCOMPLETE"
                    elif candidate.status in {"PAUSED", "SYSTEM_RECOVERY", "ERROR"}:
                        experiment_attempt.status = "PAUSED"
                        scheduled_experiment.status = "PAUSED"
                    elif candidate.status in {"START_COUNTDOWN", "RUNNING"}:
                        experiment_attempt.status = "RUNNING"
                        scheduled_experiment.status = "RUNNING"
                room = await session.get(Room, match.room_id, with_for_update=True)
                if room is not None:
                    room.status = (
                        "RUNNING"
                        if candidate.status in ("START_COUNTDOWN", "RUNNING")
                        else "PAUSED"
                        if candidate.status in ("PAUSED", "SYSTEM_RECOVERY", "ERROR")
                        else "FINISHED"
                        if candidate.status == "FINISHED"
                        else "TERMINATED"
                        if candidate.status == "TERMINATED"
                        else room.status
                    )
                    if candidate.status in ("FINISHED", "TERMINATED"):
                        # Keep the room lifecycle boundary in the same DB
                        # transaction as the authoritative MatchActor commit.
                        # This timestamp is also the recovery/cleanup fence
                        # for presence and LiveKit resources.
                        room.ended_at = _terminal_room_end_at(
                            match_status=candidate.status,
                            current_room_end=room.ended_at,
                            match_end=match.ended_at,
                            now=datetime.now(UTC),
                        )
                referenced_speech_ids = {
                    _required_uuid(event.payload["source_speech_id"])
                    for event in events
                    if event.type == "free.opportunity_opened"
                    and event.payload.get("source_speech_id")
                }
                for event in events:
                    speech_id = event.payload.get("speech_id")
                    if (
                        event.type in ("speech.started", "agent.playback_started")
                        and speech_id
                        and UUID(str(speech_id)) in referenced_speech_ids
                    ):
                        await self._persist_started_speech(session, candidate, event)
                        # The next opportunity references this speech.  Persist
                        # the parent before any later query can autoflush that FK.
                        await session.flush()
                for event in events:
                    session.add(
                        MatchEventRow(
                            match_id=event.match_id,
                            sequence=event.sequence,
                            event_type=event.type,
                            payload=dict(event.payload),
                        )
                    )
                    if (
                        event.type == "free.opportunity_invalidated"
                        and candidate.free_competition_enabled
                    ):
                        opportunity = await session.get(
                            FreeDebateOpportunity,
                            _required_uuid(event.payload["opportunity_id"]),
                            with_for_update=True,
                        )
                        if opportunity is not None:
                            opportunity.status = "INVALIDATED"
                            opportunity.invalidated_reason = str(event.payload["reason"])
                            opportunity.completed_at = datetime.fromtimestamp(
                                event.server_time_ms / 1000, UTC
                            )
                            decisions = list(
                                (
                                    await session.scalars(
                                        select(AgentFreeDebateDecision)
                                        .where(
                                            AgentFreeDebateDecision.opportunity_id == opportunity.id
                                        )
                                        .with_for_update()
                                    )
                                ).all()
                            )
                            for decision in decisions:
                                decision.stale = True
                                decision.invalidated_reason = str(event.payload["reason"])
                                decision.effective_status = "TECHNICAL_MISSING"
                    elif (
                        event.type == "free.opportunity_opened"
                        and candidate.free_competition_enabled
                    ):
                        opportunity_id = _required_uuid(event.payload["opportunity_id"])
                        existing = await session.get(
                            FreeDebateOpportunity, opportunity_id, with_for_update=True
                        )
                        if existing is None:
                            session.add(
                                FreeDebateOpportunity(
                                    id=opportunity_id,
                                    match_id=candidate.match_id,
                                    experiment_attempt_id=candidate.experiment_attempt_id,
                                    sequence_no=int(event.payload["opportunity_generation"]),
                                    side=str(event.payload["side"]),
                                    trigger_kind=str(event.payload["trigger_kind"]),
                                    source_speech_id=(
                                        _required_uuid(event.payload["source_speech_id"])
                                        if event.payload.get("source_speech_id")
                                        else None
                                    ),
                                    context_version=match.context_version,
                                    opportunity_generation=int(
                                        event.payload["opportunity_generation"]
                                    ),
                                    selection_phase="COMPETING",
                                    decision_fact="PENDING",
                                    allocation_fact="PENDING",
                                    frozen_context={
                                        "action_key": str(event.payload["action_key"]),
                                        "side": str(event.payload["side"]),
                                        "source_speech_id": (
                                            str(event.payload["source_speech_id"])
                                            if event.payload.get("source_speech_id")
                                            else None
                                        ),
                                        "affirmative_remaining_ms": (
                                            candidate.free_affirmative_remaining_ms
                                        ),
                                        "negative_remaining_ms": (
                                            candidate.free_negative_remaining_ms
                                        ),
                                    },
                                    opened_at=datetime.fromtimestamp(
                                        event.server_time_ms / 1000, UTC
                                    ),
                                )
                            )
                    elif event.type == "agent.decision_started":
                        decision_round_id = _required_uuid(event.payload["decision_round_id"])
                        opportunity_id = (
                            _required_uuid(event.payload["opportunity_id"])
                            if event.payload.get("opportunity_id")
                            else None
                        )
                        if candidate.free_competition_enabled:
                            if opportunity_id is None:
                                raise MatchDomainError("match_state_conflict")
                            opportunity = await session.get(
                                FreeDebateOpportunity, opportunity_id, with_for_update=True
                            )
                            if opportunity is None:
                                opportunity = FreeDebateOpportunity(
                                    id=opportunity_id,
                                    match_id=candidate.match_id,
                                    experiment_attempt_id=candidate.experiment_attempt_id,
                                    sequence_no=int(event.payload["opportunity_generation"]),
                                    side=str(event.payload["side"]),
                                    trigger_kind=str(event.payload["trigger_kind"]),
                                    source_speech_id=(
                                        _required_uuid(event.payload["source_speech_id"])
                                        if event.payload.get("source_speech_id")
                                        else None
                                    ),
                                    context_version=match.context_version,
                                    opportunity_generation=int(
                                        event.payload["opportunity_generation"]
                                    ),
                                    selection_phase="COMPETING",
                                    decision_fact="PENDING",
                                    allocation_fact="PENDING",
                                    frozen_context={
                                        "action_key": str(event.payload["action_key"]),
                                        "side": str(event.payload["side"]),
                                        "affirmative_remaining_ms": (
                                            candidate.free_affirmative_remaining_ms
                                        ),
                                        "negative_remaining_ms": (
                                            candidate.free_negative_remaining_ms
                                        ),
                                    },
                                    opened_at=datetime.fromtimestamp(
                                        event.server_time_ms / 1000, UTC
                                    ),
                                    selection_deadline_at=datetime.fromtimestamp(
                                        event.server_time_ms / 1000, UTC
                                    )
                                    + timedelta(seconds=3),
                                )
                                session.add(opportunity)
                                # The decision rows reference this opportunity by
                                # foreign key.  Flush the parent explicitly before
                                # adding children so SQLAlchemy cannot order the
                                # inserts in the opposite direction.
                                await session.flush()
                        for raw_agent in event.payload.get("agents", []):
                            session.add(
                                AgentFreeDebateDecision(
                                    match_id=candidate.match_id,
                                    action_key=str(event.payload["action_key"]),
                                    decision_round_id=decision_round_id,
                                    context_version=match.context_version,
                                    agent_profile_id=UUID(str(raw_agent["agent_profile_id"])),
                                    side=str(event.payload["side"]),
                                    seat_no=int(raw_agent["seat_no"]),
                                    status="DECIDING",
                                    opportunity_id=opportunity_id,
                                    deadline_at=(
                                        datetime.fromtimestamp(event.server_time_ms / 1000, UTC)
                                        + timedelta(
                                            seconds=(
                                                self._actors[
                                                    candidate.match_id
                                                ].agent_decision_remaining_seconds(candidate)
                                            )
                                        )
                                        if candidate.free_competition_enabled
                                        and candidate.agent_decision_deadline_mono is not None
                                        else None
                                    ),
                                    trigger_kind=(
                                        str(event.payload["trigger_kind"])
                                        if candidate.free_competition_enabled
                                        else None
                                    ),
                                    effective_status=(
                                        "PENDING" if candidate.free_competition_enabled else None
                                    ),
                                    started_at=datetime.fromtimestamp(
                                        event.server_time_ms / 1000, UTC
                                    ),
                                )
                            )
                    elif event.type == "agent.decision_progress":
                        decision = await session.scalar(
                            select(AgentFreeDebateDecision)
                            .where(
                                AgentFreeDebateDecision.match_id == candidate.match_id,
                                AgentFreeDebateDecision.decision_round_id
                                == _required_uuid(event.payload["decision_round_id"]),
                                AgentFreeDebateDecision.agent_profile_id
                                == _required_uuid(event.payload["agent_profile_id"]),
                            )
                            .with_for_update()
                        )
                        if decision is None:
                            raise MatchDomainError("match_state_conflict")
                        decision.status = str(event.payload["status"])
                        decision.should_speak = event.payload.get("should_speak")
                        decision.decision_reason = event.payload.get("decision_reason")
                        decision.willingness = event.payload.get("willingness")
                        decision.attempt_no = int(event.payload.get("attempt_no", 1))
                        decision.duration_ms = int(event.payload.get("duration_ms", 0))
                        decision.error_code = (
                            str(event.payload["error_code"])
                            if event.payload.get("error_code")
                            else None
                        )
                        decision.result_order = int(event.payload["result_order"])
                        decision.human_hand_at_result = bool(
                            event.payload.get("human_hand_at_result", False)
                        )
                        decision.completed_at = datetime.fromtimestamp(
                            event.server_time_ms / 1000, UTC
                        )
                        if candidate.free_competition_enabled:
                            decision.effective_status = (
                                "TECHNICAL_MISSING"
                                if bool(event.payload.get("failed"))
                                else "RAISE"
                                if bool(event.payload.get("should_speak"))
                                else "SKIP"
                            )
                            if decision.opportunity_id is not None:
                                opportunity = await session.get(
                                    FreeDebateOpportunity,
                                    decision.opportunity_id,
                                    with_for_update=True,
                                )
                                if opportunity is not None:
                                    opportunity.decision_fact = cast(str, decision.effective_status)
                    elif (
                        event.type in {"hand.raised", "hand.cancelled"}
                        and candidate.free_competition_enabled
                    ):
                        opportunity_value = event.payload.get("opportunity_id")
                        if opportunity_value and command.actor_user_id is not None:
                            session.add(
                                HumanHandEvent(
                                    opportunity_id=UUID(str(opportunity_value)),
                                    user_id=command.actor_user_id,
                                    event_type=(
                                        "RAISE" if event.type == "hand.raised" else "CANCEL"
                                    ),
                                    server_sequence=event.sequence,
                                    connection_epoch=(
                                        int(event.payload["connection_epoch"])
                                        if event.payload.get("connection_epoch") is not None
                                        else None
                                    ),
                                    accepted_at=datetime.fromtimestamp(
                                        event.server_time_ms / 1000, UTC
                                    ),
                                )
                            )
                    elif (
                        event.type == "hand.window_opened"
                        and candidate.free_competition_enabled
                        and event.payload.get("duration_ms") == 3000
                        and candidate.opportunity_id is not None
                    ):
                        opportunity = await session.get(
                            FreeDebateOpportunity,
                            candidate.opportunity_id,
                            with_for_update=True,
                        )
                        if opportunity is not None:
                            actor = self._actors.get(candidate.match_id)
                            remaining = (
                                max(0.0, actor.agent_decision_remaining_seconds(candidate))
                                if actor is not None
                                and candidate.agent_decision_deadline_mono is not None
                                else 3.0
                            )
                            deadline = datetime.fromtimestamp(
                                event.server_time_ms / 1000, UTC
                            ) + timedelta(seconds=remaining)
                            opportunity.source_ended_at = (
                                opportunity.source_ended_at
                                or datetime.fromtimestamp(event.server_time_ms / 1000, UTC)
                            )
                            opportunity.selection_deadline_at = deadline
                            decision_rows = list(
                                (
                                    await session.scalars(
                                        select(AgentFreeDebateDecision)
                                        .where(
                                            AgentFreeDebateDecision.opportunity_id == opportunity.id
                                        )
                                        .with_for_update()
                                    )
                                ).all()
                            )
                            for decision_row in decision_rows:
                                decision_row.deadline_at = deadline
                    elif (
                        event.type == "free.model_wait_started"
                        and candidate.opportunity_id is not None
                    ):
                        opportunity = await session.get(
                            FreeDebateOpportunity, candidate.opportunity_id, with_for_update=True
                        )
                        if opportunity is not None:
                            opportunity.selection_phase = "MODEL_WAIT"
                            deadline = datetime.fromtimestamp(
                                event.server_time_ms / 1000, UTC
                            ) + timedelta(milliseconds=int(event.payload["duration_ms"]))
                            opportunity.selection_deadline_at = deadline
                            rows = list(
                                (
                                    await session.scalars(
                                        select(AgentFreeDebateDecision)
                                        .where(
                                            AgentFreeDebateDecision.opportunity_id == opportunity.id
                                        )
                                        .with_for_update()
                                    )
                                ).all()
                            )
                            for row in rows:
                                row.deadline_at = deadline
                    elif event.type == "hand.window_closed" and candidate.free_competition_enabled:
                        if candidate.opportunity_id is not None:
                            pending_decisions = list(
                                (
                                    await session.scalars(
                                        select(AgentFreeDebateDecision)
                                        .where(
                                            AgentFreeDebateDecision.opportunity_id
                                            == candidate.opportunity_id,
                                            AgentFreeDebateDecision.status == "DECIDING",
                                        )
                                        .with_for_update()
                                    )
                                ).all()
                            )
                            for pending in pending_decisions:
                                pending.status = "SKIP"
                                pending.error_code = "DECISION_DEADLINE_EXCEEDED"
                                pending.effective_status = "TECHNICAL_MISSING"
                                pending.completed_at = datetime.fromtimestamp(
                                    event.server_time_ms / 1000, UTC
                                )
                    elif (
                        event.type == "free.human_wait_started"
                        and candidate.free_competition_enabled
                    ):
                        opportunity = await session.get(
                            FreeDebateOpportunity,
                            _required_uuid(event.payload["opportunity_id"]),
                            with_for_update=True,
                        )
                        if opportunity is not None:
                            opportunity.selection_phase = "HUMAN_ONLY_WAIT"
                            opportunity.decision_fact = (
                                "TECHNICAL_MISSING"
                                if event.payload.get("decision_fact") == "TECHNICAL_MISSING"
                                else "SKIP"
                            )
                            opportunity.allocation_fact = "WAITING_FOR_HUMAN"
                            opportunity.human_wait_deadline_at = datetime.fromtimestamp(
                                event.server_time_ms / 1000, UTC
                            ) + timedelta(seconds=60)
                    elif (
                        event.type == "match.paused"
                        and candidate.free_competition_enabled
                        and event.payload.get("reason") == "HUMAN_WAIT_TIMEOUT"
                        and event.payload.get("opportunity_id")
                    ):
                        opportunity = await session.get(
                            FreeDebateOpportunity,
                            _required_uuid(event.payload["opportunity_id"]),
                            with_for_update=True,
                        )
                        if opportunity is not None:
                            opportunity.allocation_fact = "PAUSED_WITHOUT_SPEAKER"
                            opportunity.execution_fact = "NO_SPEECH"
                    elif (
                        event.type in {"speech.finished", "agent.finalized"}
                        and candidate.free_competition_enabled
                        and event.payload.get("opportunity_id")
                    ):
                        opportunity = await session.get(
                            FreeDebateOpportunity,
                            _required_uuid(event.payload["opportunity_id"]),
                            with_for_update=True,
                        )
                        if opportunity is not None:
                            opportunity.status = "COMPLETED"
                            opportunity.execution_fact = (
                                "AI_SPOKE" if event.type == "agent.finalized" else "HUMAN_SPOKE"
                            )
                            opportunity.completed_at = datetime.fromtimestamp(
                                event.server_time_ms / 1000, UTC
                            )
                        if (
                            candidate.opportunity_id is not None
                            and candidate.opportunity_id
                            != _required_uuid(event.payload["opportunity_id"])
                            and event.payload.get("speech_id")
                        ):
                            source_opportunity = await session.get(
                                FreeDebateOpportunity,
                                candidate.opportunity_id,
                                with_for_update=True,
                            )
                            if source_opportunity is not None:
                                source_opportunity.source_speech_id = UUID(
                                    str(event.payload["speech_id"])
                                )
                                source_ended_at = datetime.fromtimestamp(
                                    event.server_time_ms / 1000, UTC
                                )
                                source_opportunity.source_ended_at = (
                                    source_opportunity.source_ended_at or source_ended_at
                                )
                                actor = self._actors.get(candidate.match_id)
                                remaining = (
                                    actor.agent_decision_remaining_seconds(candidate)
                                    if actor is not None
                                    else 3.0
                                )
                                source_opportunity.selection_deadline_at = (
                                    source_ended_at + timedelta(seconds=remaining)
                                )
                                decision_rows = list(
                                    (
                                        await session.scalars(
                                            select(AgentFreeDebateDecision)
                                            .where(
                                                AgentFreeDebateDecision.opportunity_id
                                                == source_opportunity.id
                                            )
                                            .with_for_update()
                                        )
                                    ).all()
                                )
                                for decision_row in decision_rows:
                                    decision_row.deadline_at = (
                                        source_opportunity.selection_deadline_at
                                    )
                    elif event.type == "free.selection_locked":
                        # Human-priority selection can happen while no agent
                        # decision round exists.  In that path the domain
                        # intentionally omits decision_round_id; do not turn
                        # the otherwise valid hand raise into a 500.
                        decision_round_value = event.payload.get("decision_round_id")
                        if decision_round_value:
                            decision_round_id = UUID(str(decision_round_value))
                            decision_rows = list(
                                (
                                    await session.scalars(
                                        select(AgentFreeDebateDecision)
                                        .where(
                                            AgentFreeDebateDecision.match_id == candidate.match_id,
                                            AgentFreeDebateDecision.decision_round_id
                                            == decision_round_id,
                                        )
                                        .with_for_update()
                                    )
                                ).all()
                            )
                        else:
                            decision_rows = []
                        agent_ranks = {
                            agent_id: len(candidate.hand_queue) + index + 1
                            for index, agent_id in enumerate(candidate.agent_hand_queue)
                        }
                        selected_agent_id = (
                            _required_uuid(event.payload["agent_profile_id"])
                            if event.payload.get("agent_profile_id")
                            else None
                        )
                        for decision in decision_rows:
                            decision.final_queue_rank = agent_ranks.get(decision.agent_profile_id)
                            decision.human_hand_at_lock = bool(candidate.hand_queue)
                            decision.selected = (
                                selected_agent_id is not None
                                and decision.agent_profile_id == selected_agent_id
                            )
                            decision.fallback = bool(
                                decision.selected
                                and event.payload.get("agent_selection_mode") == "FALLBACK"
                            )
                        if candidate.free_competition_enabled and event.payload.get(
                            "opportunity_id"
                        ):
                            opportunity_id = _required_uuid(event.payload["opportunity_id"])
                            opportunity = await session.get(
                                FreeDebateOpportunity, opportunity_id, with_for_update=True
                            )
                            if opportunity is None:
                                raise MatchDomainError("match_state_conflict")
                            speaker_kind = str(event.payload["speaker_kind"])
                            opportunity.selection_phase = "ALLOCATED"
                            opportunity.allocation_fact = (
                                "HUMAN_SELECTED" if speaker_kind == "HUMAN" else "AI_SELECTED"
                            )
                            session.add(
                                SpeakerAllocation(
                                    opportunity_id=opportunity_id,
                                    speaker_kind=speaker_kind,
                                    user_id=(
                                        _required_uuid(event.payload["speaker_user_id"])
                                        if event.payload.get("speaker_user_id")
                                        else None
                                    ),
                                    agent_profile_id=(
                                        _required_uuid(event.payload["agent_profile_id"])
                                        if event.payload.get("agent_profile_id")
                                        else None
                                    ),
                                    reason=(
                                        "HUMAN_PRIORITY"
                                        if speaker_kind == "HUMAN"
                                        else "AGENT_RAISE"
                                    ),
                                    effective=True,
                                    allocated_at=datetime.fromtimestamp(
                                        event.server_time_ms / 1000, UTC
                                    ),
                                )
                            )
                for event in events:
                    speech_id = event.payload.get("speech_id")
                    if event.type in ("speech.started", "agent.playback_started") and speech_id:
                        await self._persist_started_speech(session, candidate, event)
                    elif event.type == "asr.retry.started" and speech_id:
                        speech = await session.get(
                            Speech, UUID(str(speech_id)), with_for_update=True
                        )
                        if (
                            speech is None
                            or speech.status != "FAILED"
                            or speech.speaker_kind != "HUMAN"
                        ):
                            raise MatchDomainError("match_state_conflict")
                        speech.status = "FINALIZING"
                        speech.asr_error_code = None
                    elif event.type == "asr.failed" and speech_id:
                        speech = await session.get(
                            Speech, UUID(str(speech_id)), with_for_update=True
                        )
                        if speech is not None:
                            speech.status = "FAILED"
                            speech.asr_error_code = str(
                                event.payload.get("error_code", "asr_task_failed")
                            )
                            speech.ended_at = speech.ended_at or datetime.now(UTC)
                            info_getter = getattr(
                                type(self._speech_runtime), "recovery_source_info", None
                            )
                            info = (
                                self._speech_runtime.recovery_source_info(
                                    candidate.match_id, speech.id
                                )
                                if info_getter is not None and self._speech_runtime is not None
                                else None
                            )
                            if info is not None:
                                path = info.get("audio_storage_path")
                                duration = info.get("audio_duration_ms")
                                prefix = info.get("confirmed_prefix")
                                if isinstance(path, str) and isinstance(duration, int):
                                    speech.audio_storage_path = path
                                    speech.audio_duration_ms = duration
                                    if isinstance(prefix, str) and prefix:
                                        speech.display_text = prefix
                    elif event.type == "speech.finalizing" and speech_id:
                        speech = await session.get(
                            Speech, UUID(str(speech_id)), with_for_update=True
                        )
                        if speech is not None:
                            speech.status = "FINALIZING"
                            speech.finish_reason = str(event.payload.get("reason", ""))
                            speech.ended_at = datetime.now(UTC)
                    elif event.type == "speech.finished" and speech_id:
                        speech = await session.get(
                            Speech, UUID(str(speech_id)), with_for_update=True
                        )
                        if speech is not None:
                            speech.status = "FINALIZED"
                            speech.finish_reason = str(
                                event.payload.get("reason") or speech.finish_reason or ""
                            )
                            speech.ended_at = speech.ended_at or datetime.now(UTC)
                            speech.finalized_at = datetime.now(UTC)
                            final_text = str(event.payload.get("final_text", ""))
                            speech.asr_raw_final_text = final_text
                            speech.display_text = final_text
                            speech.first_interim_latency_ms = event.payload.get(
                                "first_interim_latency_ms"
                            )
                            speech.final_latency_ms = event.payload.get("final_latency_ms")
                            speech.audio_duration_ms = event.payload.get("audio_duration_ms")
                            storage_path = event.payload.get("audio_storage_path")
                            speech.audio_storage_path = str(storage_path) if storage_path else None
                            file_values = {
                                "speech_id": speech.id,
                                "owner_user_id": speech.user_id,
                                "file_kind": "HUMAN_RAW",
                                "status": "READY" if storage_path else "FAILED",
                                "storage_path": str(storage_path) if storage_path else None,
                                "codec": "pcm_s16le_16000_mono" if storage_path else None,
                                "byte_count": _file_size(
                                    str(storage_path) if storage_path else None
                                ),
                                "duration_ms": speech.audio_duration_ms,
                                "expires_at": datetime.now(UTC) + timedelta(days=30),
                                "error_code": (
                                    str(event.payload.get("audio_recording_error"))
                                    if event.payload.get("audio_recording_error")
                                    else None
                                ),
                            }
                            existing_file = await session.scalar(
                                select(MatchFile)
                                .where(
                                    MatchFile.match_id == candidate.match_id,
                                    MatchFile.file_key == f"human-{speech.id}",
                                )
                                .with_for_update()
                            )
                            if existing_file is None:
                                session.add(
                                    MatchFile(
                                        match_id=candidate.match_id,
                                        file_key=f"human-{speech.id}",
                                        **file_values,
                                    )
                                )
                            else:
                                for key, value in file_values.items():
                                    setattr(existing_file, key, value)
                    elif event.type == "agent.finalizing" and speech_id:
                        speech = await session.get(
                            Speech, UUID(str(speech_id)), with_for_update=True
                        )
                        if speech is not None:
                            speech.status = "FINALIZING"
                            speech.finish_reason = str(event.payload.get("reason", ""))
                            speech.ended_at = datetime.now(UTC)
                    elif event.type == "agent.finalized" and speech_id:
                        speech = await session.get(
                            Speech, UUID(str(speech_id)), with_for_update=True
                        )
                        if speech is not None:
                            if str(speech.generation_id) != str(event.payload.get("generation_id")):
                                raise MatchDomainError("stale_callback")
                            speech.status = "FINALIZED"
                            speech.finish_reason = str(event.payload.get("reason", ""))
                            speech.display_text = str(event.payload.get("final_text", ""))
                            speech.llm_draft_text = str(event.payload.get("llm_draft_text", ""))
                            speech.audio_storage_path = str(
                                event.payload.get("audio_storage_path", "")
                            )
                            speech.audio_duration_ms = int(
                                event.payload.get("audio_duration_ms", 0)
                            )
                            speech.audio_truncated = bool(event.payload.get("audio_truncated"))
                            speech.finalized_at = datetime.now(UTC)
                            file_key = f"agent-{speech.id}"
                            agent_file = await session.scalar(
                                select(MatchFile)
                                .where(
                                    MatchFile.match_id == candidate.match_id,
                                    MatchFile.file_key == file_key,
                                )
                                .with_for_update()
                            )
                            file_values = {
                                "speech_id": speech.id,
                                "owner_user_id": None,
                                "file_kind": "AGENT_RAW",
                                "status": "READY" if speech.audio_storage_path else "FAILED",
                                "storage_path": speech.audio_storage_path or None,
                                "codec": "ogg_opus" if speech.audio_storage_path else None,
                                "byte_count": _file_size(speech.audio_storage_path),
                                "duration_ms": speech.audio_duration_ms,
                                "expires_at": datetime.now(UTC) + timedelta(days=30),
                                "error_code": (
                                    None if speech.audio_storage_path else "agent_audio_missing"
                                ),
                            }
                            if agent_file is None:
                                session.add(
                                    MatchFile(
                                        match_id=candidate.match_id,
                                        file_key=file_key,
                                        **file_values,
                                    )
                                )
                            else:
                                for key, value in file_values.items():
                                    setattr(agent_file, key, value)
                            match.context_version += 1
                if command.type == "system.recover" and previous.asr_failed_speech_id is not None:
                    source = await session.get(
                        Speech, previous.asr_failed_speech_id, with_for_update=True
                    )
                    if source is not None and source.match_id == candidate.match_id:
                        source.status = "FAILED"
                        source.asr_error_code = source.asr_error_code or "asr_recovery_required"
                        await self._retire_asr_diagnostics(
                            session, match_id=candidate.match_id, speech_ids={source.id}
                        )
                if (
                    command.type in ("speech.reset", "system.recover")
                    and previous.current_speech_id is not None
                    and not bool(command.payload.get("preserve_failed"))
                    and not (
                        command.type == "system.recover"
                        and previous.asr_failed_speech_id is not None
                    )
                ):
                    speech = await session.get(
                        Speech, previous.current_speech_id, with_for_update=True
                    )
                    if speech is not None:
                        speech.status = "RESET"
                        speech.ended_at = datetime.now(UTC)
                        await self._retire_asr_diagnostics(
                            session, match_id=candidate.match_id, speech_ids={speech.id}
                        )
                    match.context_version += 1
                if (
                    command.type == "speech.reset"
                    and events
                    and (
                        previous.current_speech_id is None or command.payload.get("preserve_failed")
                    )
                ):
                    match.context_version += 1
                for event in events:
                    if event.type not in {
                        "stage.reset",
                        "speech.skip.started",
                        "speech.skipped",
                        "speech.skip.failed",
                    }:
                        continue
                    payload = event.payload
                    if event.type in {"stage.reset", "speech.skip.started"}:
                        query = select(Speech).where(Speech.match_id == candidate.match_id)
                        if event.type == "stage.reset":
                            query = query.where(Speech.action_key.in_(payload["action_keys"]))
                        elif previous.current_speech_id is not None:
                            query = query.where(Speech.id == previous.current_speech_id)
                        else:
                            query = query.where(Speech.id == UUID(int=0))
                        retired_speeches = (await session.scalars(query.with_for_update())).all()
                        for row in retired_speeches:
                            row.status = "RESET"
                            row.ended_at = row.ended_at or datetime.now(UTC)
                        await self._retire_asr_diagnostics(
                            session,
                            match_id=candidate.match_id,
                            speech_ids={row.id for row in retired_speeches},
                        )
                        opportunities = select(FreeDebateOpportunity).where(
                            FreeDebateOpportunity.match_id == candidate.match_id,
                            FreeDebateOpportunity.status != "INVALIDATED",
                        )
                        if event.type == "stage.reset":
                            opportunities = opportunities.where(
                                func.jsonb_extract_path_text(
                                    FreeDebateOpportunity.frozen_context, "action_key"
                                ).in_(payload["action_keys"])
                            )
                        else:
                            opportunities = opportunities.where(
                                FreeDebateOpportunity.id.in_(
                                    [
                                        item
                                        for item in (previous.opportunity_id,)
                                        if item is not None
                                        and previous.current_speech_id is not None
                                        and item != previous.allocated_opportunity_id
                                    ]
                                )
                            )
                        for opportunity in (
                            await session.scalars(opportunities.with_for_update())
                        ).all():
                            opportunity.status = "INVALIDATED"
                            opportunity.invalidated_reason = "ADMIN_RECOVERY"
                            opportunity.completed_at = datetime.now(UTC)
                            for decision in (
                                await session.scalars(
                                    select(AgentFreeDebateDecision)
                                    .where(AgentFreeDebateDecision.opportunity_id == opportunity.id)
                                    .with_for_update()
                                )
                            ).all():
                                decision.stale = True
                                decision.invalidated_reason = "ADMIN_RECOVERY"
                            for allocation in (
                                await session.scalars(
                                    select(SpeakerAllocation)
                                    .where(SpeakerAllocation.opportunity_id == opportunity.id)
                                    .with_for_update()
                                )
                            ).all():
                                allocation.effective = False
                        match.context_version += 1
                    elif event.type == "speech.skipped":
                        if payload.get("opportunity_id"):
                            allocated = await session.get(
                                FreeDebateOpportunity,
                                UUID(str(payload["opportunity_id"])),
                                with_for_update=True,
                            )
                            if allocated is not None:
                                allocated.status = "COMPLETED"
                                allocated.execution_fact = "NO_SPEECH"
                                allocated.completed_at = datetime.now(UTC)
                        old = (
                            await session.get(Speech, UUID(str(payload["source_speech_id"])))
                            if payload.get("source_speech_id")
                            else None
                        )
                        session.add(
                            Speech(
                                id=UUID(str(payload["speech_id"])),
                                match_id=candidate.match_id,
                                action_key=str(payload["action_key"]),
                                user_id=UUID(str(payload["user_id"]))
                                if payload.get("user_id")
                                else None,
                                agent_profile_id=UUID(str(payload["agent_profile_id"]))
                                if payload.get("agent_profile_id")
                                else None,
                                speaker_kind=str(payload["speaker_kind"]),
                                side=str(payload["side"]),
                                seat_no=int(payload["seat_no"]),
                                status="FINALIZED",
                                origin="ADMIN_PLACEHOLDER",
                                display_text=str(payload["final_text"]),
                                finish_reason="ADMIN_SKIP",
                                audio_duration_ms=0,
                                attempt_no=(old.attempt_no + 1 if old else 1),
                                created_at=old.created_at if old else datetime.now(UTC),
                                ended_at=datetime.now(UTC),
                                finalized_at=datetime.now(UTC),
                            )
                        )
                        match.context_version += 1
                    AuditService().record(
                        session,
                        actor_user_id=command.actor_user_id,
                        action=f"admin.match.{event.type}",
                        target_type="match",
                        target_id=str(candidate.match_id),
                        details={
                            "operation_id": payload.get("id"),
                            "action_key": payload.get("action_key"),
                        },
                    )
                if (
                    events
                    and command.type == "speech.reset"
                    and command.payload.get("operation_id")
                ):
                    AuditService().record(
                        session,
                        actor_user_id=command.actor_user_id,
                        action="admin.match.speech.reset",
                        target_type="match",
                        target_id=str(candidate.match_id),
                        details={"operation_id": command.payload["operation_id"]},
                    )
                if (
                    candidate.free_competition_enabled
                    and candidate.status == "FINISHED"
                    and candidate.experiment_attempt_id is not None
                ):
                    attempt = await session.get(
                        ExperimentMatchAttempt, candidate.experiment_attempt_id
                    )
                    if attempt is None:
                        raise MatchDomainError("match_state_conflict")
                    scheduled = await session.get(ScheduledMatch, attempt.scheduled_match_id)
                    if scheduled is None:
                        raise MatchDomainError("match_state_conflict")
                    existing_postmatch_task = await session.scalar(
                        select(BackgroundTask.id).where(
                            BackgroundTask.task_type == "EXPERIMENT_POSTMATCH",
                            func.jsonb_extract_path_text(BackgroundTask.payload, "attempt_id")
                            == str(attempt.id),
                        )
                    )
                    if existing_postmatch_task is None:
                        session.add(
                            BackgroundTask(
                                task_type="EXPERIMENT_POSTMATCH",
                                payload={
                                    "attempt_id": str(attempt.id),
                                    "scheduled_match_id": str(scheduled.id),
                                    "match_id": str(candidate.match_id),
                                },
                                max_attempts=2,
                            )
                        )

                if (
                    candidate.stage_checkpoint
                    and candidate.stage_generation != previous.stage_generation
                ):
                    cast(dict[str, Any], candidate.stage_checkpoint)["entry_context_version"] = (
                        match.context_version
                    )
                    match.runtime_snapshot = state_snapshot(candidate)
                if command.host_callback_envelope is not None or (command.host_guard_id and events):
                    # Finish host-specific reads/writes before the last
                    # identity checkpoint. A stale callback rolls the whole
                    # transaction back even if ORM autoflush already ran.
                    await session.flush()
                    self._require_locked_host_identity(command, match)

        if command.type == "speech.start":
            self._prepared_speech_starts.discard((candidate.match_id, command.message_id))

    async def recover_unfinished(self) -> int:
        """Rehydrate active matches into a safe, non-playing recovery state."""

        async with self._session_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(Match).where(
                            Match.status.in_(
                                (
                                    "START_PENDING_RUNTIME",
                                    "START_COUNTDOWN",
                                    "RUNNING",
                                    "PAUSED",
                                    "SYSTEM_RECOVERY",
                                    "ERROR",
                                )
                            )
                        )
                    )
                ).all()
            )
        recovered = 0
        for row in rows:
            async with self._lock:
                if row.id in self._actors:
                    continue
                state = _state_from_snapshot(row.id, row.runtime_snapshot)
                actor = MatchActor(
                    state,
                    commit=self._commit,
                    publish=self._publish,
                    pre_commit=self._pre_commit,
                    prepare_command=self._prepare_command,
                )
                await actor.start()
                if (
                    row.status
                    in (
                        "START_PENDING_RUNTIME",
                        "START_COUNTDOWN",
                        "RUNNING",
                    )
                    or state.asr_failed_speech_id is not None
                ):
                    try:
                        await actor.submit(
                            MatchCommand(
                                type="system.recover",
                                message_id=f"system-recover:{row.id}:{row.sequence}",
                            )
                        )
                    except Exception:
                        await actor.close()
                        raise
                self._actors[row.id] = actor
                await self._restore_failed_asr_recording(actor.state)
                recovered += 1
        return recovered

    async def _restore_failed_asr_recording(self, state: MatchRuntimeState) -> None:
        """Restore a disk-only recovery owner; only explicit retry may run ASR."""
        runtime = self._speech_runtime
        source_id = state.asr_failed_speech_id
        if (
            runtime is None
            or source_id is None
            or getattr(type(runtime), "restore_recovery_source", None) is None
        ):
            return
        async with self._session_factory() as session:
            source = await session.get(Speech, source_id)
            match = await session.get(Match, state.match_id)
            if (
                source is None
                or match is None
                or source.match_id != state.match_id
                or source.speaker_kind != "HUMAN"
                or source.status != "FAILED"
                or source.user_id is None
                or not source.audio_storage_path
                or source.audio_duration_ms is None
            ):
                return
            action = next(
                (item for item in state.actions if item.action_key == source.action_key), None
            )
            if action is None:
                return
            maximum_ms = (
                action.free_max_speech_seconds
                if action.action_kind == "FREE_DEBATE"
                else action.duration_seconds
            ) * 1000
            opportunity = (
                await session.get(FreeDebateOpportunity, source.opportunity_id)
                if source.opportunity_id is not None
                else None
            )
            envelope = CallbackEnvelope(
                match_id=state.match_id,
                speech_id=source.id,
                attempt_no=source.attempt_no,
                generation_id=None,
                connection_epoch=dict(state.connection_epochs).get(source.user_id, 1),
                context_version=match.context_version,
                opportunity_id=source.opportunity_id,
                opportunity_generation=(
                    opportunity.opportunity_generation if opportunity is not None else None
                ),
            )
            last_segment_no = int(
                await session.scalar(
                    select(func.coalesce(func.max(AsrSegment.segment_no), 0)).where(
                        AsrSegment.speech_id == source.id
                    )
                )
                or 0
            )
            try:
                await runtime.restore_recovery_source(
                    state.match_id,
                    source.id,
                    source.user_id,
                    envelope,
                    audio_storage_path=source.audio_storage_path,
                    audio_duration_ms=source.audio_duration_ms,
                    max_audio_duration_ms=maximum_ms,
                    last_segment_no=last_segment_no,
                )
            except (ValueError, OSError, FunAsrError):
                logger.warning(
                    "failed ASR recording unavailable after restart",
                    extra={"match_id": str(state.match_id), "error_code": "asr_audio_missing"},
                )

    async def start_room_match(
        self,
        session: AsyncSession,
        *,
        room_id: UUID,
        actor_user_id: UUID,
        actor_role: str,
        launch_runtime: bool = True,
    ) -> MatchRuntimeState:
        async with self._lock:
            async with session.begin():
                room = await session.scalar(
                    select(Room).where(Room.id == room_id).with_for_update()
                )
                if room is None:
                    raise AuthError("room_unavailable")
                experiment_link = await resolve_room_link(session, room_id=room_id)
                if experiment_link is None:
                    if actor_role != "ADMIN" and room.organizer_user_id != actor_user_id:
                        raise AuthError("forbidden")
                elif actor_role != "ADMIN":
                    if experiment_link.scheduled_match_kind == "TRAINING":
                        if room.organizer_user_id != actor_user_id:
                            raise AuthError("forbidden")
                    elif not await is_experiment_side_controller(
                        session,
                        scheduled_match_id=experiment_link.scheduled_match_id,
                        user_id=actor_user_id,
                    ):
                        raise AuthError("forbidden")
                existing = await session.scalar(select(Match).where(Match.room_id == room_id))
                if existing is not None:
                    if existing.status not in ("FINISHED", "TERMINATED"):
                        actor = self._actors.get(existing.id)
                        return (
                            actor.state
                            if actor is not None
                            else _state_from_snapshot(existing.id, existing.runtime_snapshot)
                        )
                    raise AuthError("room_locked")
                if room.status != "START_PENDING_RUNTIME":
                    raise AuthError("match_state_conflict")
                seats = (await session.scalars(select(Seat).where(Seat.room_id == room_id))).all()
                seat_map = {
                    (seat.side, seat.seat_no): seat.user_id
                    for seat in seats
                    if seat.occupant_type == "HUMAN"
                }
                agent_seat_map = {
                    (seat.side, seat.seat_no): seat.agent_profile_id
                    for seat in seats
                    if seat.occupant_type == "AGENT" and seat.agent_profile_id is not None
                }
                actions = compile_linear_actions(room.rule_snapshot, seat_map, agent_seat_map)
                formal_4v4 = {(seat.side, seat.seat_no) for seat in seats} == {
                    (side, seat_no)
                    for side in ("AFFIRMATIVE", "NEGATIVE")
                    for seat_no in range(1, 5)
                }
                if any(
                    action.host_audio_path
                    and (
                        action.host_audio_duration_ms is None or action.host_audio_duration_ms <= 0
                    )
                    for action in actions
                ):
                    raise AuthError("host_audio_duration_unavailable")
                match_id = uuid4()
                state = MatchRuntimeState(
                    match_id=match_id,
                    status="START_PENDING_RUNTIME",
                    action_state="NOT_STARTED",
                    actions=actions,
                    match_seed=match_id.int % (2**63 - 1),
                    experiment_mode=experiment_link is not None,
                    formal_4v4=formal_4v4,
                    experiment_attempt_id=(
                        experiment_link.attempt_id if experiment_link is not None else None
                    ),
                )
                session.add(
                    Match(
                        id=match_id,
                        room_id=room_id,
                        status=state.status,
                        match_seed=match_id.int % (2**63 - 1),
                        runtime_snapshot=state_snapshot(state),
                    )
                )
                if experiment_link is not None:
                    attempt = await session.get(
                        ExperimentMatchAttempt,
                        experiment_link.attempt_id,
                        with_for_update=True,
                    )
                    scheduled = await session.get(
                        ScheduledMatch,
                        experiment_link.scheduled_match_id,
                        with_for_update=True,
                    )
                    if (
                        attempt is None
                        or scheduled is None
                        or attempt.status not in {"CREATED", "WAITING"}
                    ):
                        raise AuthError("match_state_conflict")
                    attempt.match_id = match_id
                    attempt.status = "RUNNING"
                    attempt.started_at = datetime.now(UTC)
                    scheduled.status = "RUNNING"
                user_ids = [
                    seat.user_id
                    for seat in seats
                    if seat.occupant_type == "HUMAN" and seat.user_id is not None
                ]
                agent_ids = [
                    seat.agent_profile_id
                    for seat in seats
                    if seat.occupant_type == "AGENT" and seat.agent_profile_id is not None
                ]
                users = {
                    row.id: row
                    for row in (
                        await session.scalars(select(User).where(User.id.in_(user_ids)))
                    ).all()
                }
                agents = {
                    row.id: row
                    for row in (
                        await session.scalars(
                            select(AgentProfile).where(AgentProfile.id.in_(agent_ids))
                        )
                    ).all()
                }
                for seat in seats:
                    if seat.occupant_type == "HUMAN" and seat.user_id is not None:
                        user = users.get(seat.user_id)
                        if user is not None:
                            session.add(
                                MatchParticipant(
                                    match_id=match_id,
                                    kind="HUMAN",
                                    user_id=user.id,
                                    display_name=user.real_name,
                                    side=seat.side,
                                    seat_no=seat.seat_no,
                                )
                            )
                    elif seat.occupant_type == "AGENT" and seat.agent_profile_id is not None:
                        agent = agents.get(seat.agent_profile_id)
                        if agent is not None:
                            session.add(
                                MatchParticipant(
                                    match_id=match_id,
                                    kind="AGENT",
                                    agent_profile_id=agent.id,
                                    display_name=_frozen_agent_name(room, agent),
                                    side=seat.side,
                                    seat_no=seat.seat_no,
                                )
                            )
                await session.flush()
            actor = MatchActor(
                state,
                commit=self._commit,
                publish=self._publish,
                pre_commit=self._pre_commit,
                prepare_command=self._prepare_command,
            )
            await actor.start()
            self._actors[match_id] = actor
        if launch_runtime:
            result = await actor.submit(
                MatchCommand(type="runtime.start", message_id=f"runtime-start:{match_id}")
            )
            return result.state
        return state

    async def start_existing_match_runtime(
        self,
        session: AsyncSession,
        *,
        room_id: UUID,
        actor_user_id: UUID,
        actor_role: str,
    ) -> MatchRuntimeState:
        """Pass the explicit host start gate for a prepared match."""
        existing_match = await session.scalar(select(Match).where(Match.room_id == room_id))
        if existing_match is None:
            # Compatibility for older clients that call runtime-start directly
            # after the room start request.
            return await self.start_room_match(
                session,
                room_id=room_id,
                actor_user_id=actor_user_id,
                actor_role=actor_role,
                launch_runtime=True,
            )
        await session.rollback()
        async with self._lock:
            async with session.begin():
                match = await session.scalar(
                    select(Match).where(Match.room_id == room_id).with_for_update()
                )
                room = await session.scalar(
                    select(Room).where(Room.id == room_id).with_for_update()
                )
                if room is None:
                    raise AuthError("room_unavailable")
                if actor_role != "ADMIN" and room.organizer_user_id != actor_user_id:
                    raise AuthError("forbidden")
                if match is None:
                    raise AuthError("match_state_conflict")
                if match.status != "START_PENDING_RUNTIME":
                    if match.status in {"START_COUNTDOWN", "RUNNING", "PAUSED"}:
                        actor = self._actors.get(match.id)
                        if actor is not None:
                            return actor.state
                    raise AuthError("match_state_conflict")
                actor = self._actors.get(match.id)
                if actor is None:
                    state = _state_from_snapshot(match.id, match.runtime_snapshot)
                    actor = MatchActor(
                        state,
                        commit=self._commit,
                        publish=self._publish,
                        pre_commit=self._pre_commit,
                        prepare_command=self._prepare_command,
                    )
                    await actor.start()
                    self._actors[match.id] = actor
            result = await actor.submit(
                MatchCommand(type="runtime.start", message_id=f"runtime-start:{match.id}")
            )
            return result.state

    async def get_actor(self, match_id: UUID) -> MatchActor:
        actor = self._actors.get(match_id)
        if actor is None:
            raise MatchDomainError("match_not_found")
        return actor

    async def ensure_actor(self, session: AsyncSession, match_id: UUID) -> MatchActor:
        """Load an active actor on demand for operator recovery after a restart."""
        actor = self._actors.get(match_id)
        if actor is not None:
            return actor
        match = await session.get(Match, match_id)
        if match is None:
            raise MatchDomainError("match_not_found")
        if match.status in ("FINISHED", "TERMINATED"):
            raise MatchDomainError("match_not_running")
        actor = MatchActor(
            _state_from_snapshot(match.id, match.runtime_snapshot),
            commit=self._commit,
            publish=self._publish,
            pre_commit=self._pre_commit,
            prepare_command=self._prepare_command,
        )
        await actor.start()
        async with self._lock:
            existing = self._actors.get(match_id)
            if existing is not None:
                await actor.close()
                return existing
            self._actors[match_id] = actor
        return actor

    async def submit(self, match_id: UUID, command: MatchCommand) -> MatchCommandResult:
        actor = await self.get_actor(match_id)
        previous_state = actor.state
        if (
            command.type == "match.resume"
            and self._agent_runtime is not None
            and previous_state.status in ("PAUSED", "ERROR", "SYSTEM_RECOVERY")
            and previous_state.paused_from_action_state
            in ("AGENT_PREPARING", "AGENT_SPEAKING", "AGENT_FINALIZING")
        ):
            # Recovery must not race a pending interruption cleanup.
            await self._reset_interrupted_agent(match_id)
        if command.type == "speech.start":
            start_key = (match_id, command.message_id)
            try:
                result = await actor.submit(command)
            except Exception:
                if start_key in self._prepared_speech_starts and self._speech_runtime is not None:
                    self._prepared_speech_starts.discard(start_key)
                    try:
                        await self._speech_runtime.reset_speech(match_id)
                    except Exception as cleanup_error:
                        logger.warning(
                            "failed committed speech start cleanup",
                            extra={
                                "error_code": "asr_start_cleanup_failed",
                                "match_id": str(match_id),
                                "details": {"exception_type": type(cleanup_error).__name__},
                            },
                        )
                raise
            self._prepared_speech_starts.discard(start_key)
            return result
        result = await actor.submit(command)
        if command.type == "match.pause":
            if (
                self._speech_runtime is not None
                and previous_state.action_state == "HUMAN_SPEAKING"
                and previous_state.current_speech_id is not None
            ):
                self._spawn(
                    self._speech_runtime.pause_speech(match_id, previous_state.current_speech_id),
                    name=f"asr-pause-{previous_state.current_speech_id}",
                )
        if command.type == "match.terminate" and self._speech_runtime is not None:
            await self._speech_runtime.close_match(match_id)
        if command.type == "match.terminate" and self._agent_runtime is not None:
            await self._agent_runtime.close_match(match_id)
        return result

    async def snapshot(self, session: AsyncSession, match_id: UUID) -> MatchRuntimeState:
        actor = self._actors.get(match_id)
        if actor is not None:
            return actor.state
        match = await session.get(Match, match_id)
        if match is None:
            raise AuthError("match_not_found")
        return _state_from_snapshot(match.id, match.runtime_snapshot)

    async def snapshot_view(self, session: AsyncSession, match_id: UUID) -> MatchRuntimeView:
        actor = self._actors.get(match_id)
        if actor is not None:
            return actor.view()
        state = await self.snapshot(session, match_id)
        return MatchRuntimeView(
            state=state,
            speech_remaining_ms=state.speech_remaining_ms,
            countdown_remaining_ms=None,
            free_affirmative_remaining_ms=state.free_affirmative_remaining_ms,
            free_negative_remaining_ms=state.free_negative_remaining_ms,
        )


__all__ = ["MatchRuntimeManager", "state_snapshot"]

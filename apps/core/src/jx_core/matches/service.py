"""Persistence and in-process registry for one authoritative match actor."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..agent.runtime import AgentRuntime, AgentRuntimeCallbacks
from ..auth.errors import AuthError
from ..data_capture.provider import PROVIDER_CAPTURE_VERSION, ProviderCallCapture
from ..data_capture.provider_persistence import persist_provider_capture
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
_SPEECH_START_ERROR_CODES = frozenset(
    {
        "asr_not_configured",
        "asr_start_timeout",
        "asr_task_failed",
        "asr_stream_failed",
        "livekit_not_configured",
    }
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
    async def start_speech(
        self, match_id: UUID, speech_id: UUID, user_id: UUID, envelope: CallbackEnvelope
    ) -> None: ...

    async def finish_speech(self, match_id: UUID, speech_id: UUID) -> None: ...

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
        "speech_start_sequence": state.speech_start_sequence,
        "speech_remaining_ms": state.speech_remaining_ms,
        "host_audio_remaining_ms": state.host_audio_remaining_ms,
        "host_audio_deadline_ms": state.host_audio_deadline_ms,
        "current_speaker_side": state.current_speaker_side,
        "current_speaker_seat_no": state.current_speaker_seat_no,
        "free_holder_side": state.free_holder_side,
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
        speech_start_sequence=(
            int(snapshot["speech_start_sequence"])
            if snapshot.get("speech_start_sequence") is not None
            else None
        ),
        speech_remaining_ms=snapshot.get("speech_remaining_ms"),
        host_audio_remaining_ms=host_audio_remaining_ms,
        host_audio_deadline_ms=None,
        current_speaker_side=snapshot.get("current_speaker_side"),
        current_speaker_seat_no=snapshot.get("current_speaker_seat_no"),
        free_holder_side=snapshot.get("free_holder_side"),
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
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._actors: dict[UUID, MatchActor] = {}
        self._subscribers: dict[UUID, set[asyncio.Queue[MatchEvent]]] = {}
        self._lock = asyncio.Lock()
        self._speech_runtime: SpeechRuntime | None = None
        self._agent_runtime: AgentRuntime | None = None
        self._postmatch_runtime: PostmatchRuntime | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._speech_presence_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._prepared_speech_starts: set[tuple[UUID, str]] = set()
        self._pending_agent_full_text: dict[tuple[UUID, UUID], tuple[CallbackEnvelope, str]] = {}
        self._late_experiment_rounds: set[UUID] = set()

    def set_speech_runtime(self, runtime: SpeechRuntime) -> None:
        self._speech_runtime = runtime

    def set_agent_runtime(self, runtime: AgentRuntime) -> None:
        self._agent_runtime = runtime

    def set_postmatch_runtime(self, runtime: PostmatchRuntime) -> None:
        self._postmatch_runtime = runtime

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
                    if state is not None and state.opportunity_generation > 0
                    else None
                ),
            )
        action = state.current_action if state is not None else None
        async with session_factory() as session:
            match = await session.get(Match, match_id)
            if match is None:
                raise MatchDomainError("match_not_found")
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
            logical_opportunity_generation
            if logical_opportunity_generation is not None
            else state.opportunity_generation
            if state is not None and state.opportunity_generation > 0
            else None
        )
        return CallbackEnvelope(
            match_id=match_id,
            speech_id=speech_id,
            attempt_no=int(previous_attempt or 0) + 1,
            generation_id=None,
            connection_epoch=epoch,
            context_version=context_version,
            opportunity_id=opportunity_id,
            opportunity_generation=opportunity_generation,
        )

    async def _asr_callback_is_current(
        self, *, actor: MatchActor, envelope: CallbackEnvelope
    ) -> bool:
        speech_id = envelope.speech_id
        if speech_id is None:
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
        expected_epoch = (
            dict(actor.state.connection_epochs).get(speech.user_id, 1)
            if speech.user_id is not None
            else None
        )
        return (
            envelope.attempt_no == speech.attempt_no
            and envelope.connection_epoch == expected_epoch
            and envelope.context_version == match.context_version
            and envelope.opportunity_id == speech.opportunity_id
            and envelope.opportunity_generation
            == (opportunity.opportunity_generation if opportunity is not None else None)
        )

    async def _pre_commit(
        self,
        previous: MatchRuntimeState,
        candidate: MatchRuntimeState,
        events: tuple[MatchEvent, ...],
        command: MatchCommand,
    ) -> None:
        if command.type == "speech.start" and self._speech_runtime is not None:
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
                        previous.match_id, speech_id, user_id, envelope
                    )
            except Exception as error:
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
        if command.type != "speech.reset":
            return
        if self._speech_runtime is not None:
            await self._speech_runtime.reset_speech(previous.match_id)
        if self._agent_runtime is not None:
            await self._agent_runtime.cancel_free_decision(previous.match_id)
            await self._agent_runtime.reset_agent(previous.match_id)
        for key in tuple(self._pending_agent_full_text):
            if key[0] == previous.match_id:
                self._pending_agent_full_text.pop(key, None)

    def _spawn(self, coroutine: Coroutine[Any, Any, None], *, name: str) -> None:
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

    async def close(self) -> None:
        presence_tasks = tuple(self._speech_presence_tasks.values())
        self._speech_presence_tasks.clear()
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
        for actor in actors:
            await actor.close()

    async def remove_terminal(self, match_id: UUID) -> None:
        """Forget a terminal actor before its durable match is administratively deleted."""
        async with self._lock:
            actor = self._actors.get(match_id)
            if actor is None:
                self._subscribers.pop(match_id, None)
                return
            if actor.state.status not in ("FINISHED", "TERMINATED"):
                raise MatchDomainError("match_not_finished")
            self._actors.pop(match_id, None)
            self._subscribers.pop(match_id, None)
        await actor.close()

    async def _publish(self, state: MatchRuntimeState, events: tuple[MatchEvent, ...]) -> None:
        interruption = any(
            event.type in ("match.paused", "match.error", "match.system_recovery")
            for event in events
        )
        if interruption and self._agent_runtime is not None:
            fence = getattr(self._agent_runtime, "fence_agent", None)
            if fence is not None:
                await fence(state.match_id)
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
            if event.type in ("match.offline", "match.online"):
                self._queue_speech_presence_transition(state, event)
            if event.type == "speech.finalizing" and speech_id and self._speech_runtime is not None:
                self._spawn(
                    self._speech_runtime.finish_speech(event.match_id, UUID(str(speech_id))),
                    name=f"asr-finish-{speech_id}",
                )
            elif event.type == "agent.preparing":
                agent_profile_id = event.payload.get("agent_profile_id")
                action_key = event.payload.get("action_key")
                if self._agent_runtime is not None and agent_profile_id and action_key:
                    self._spawn(
                        self._agent_runtime.start_agent(
                            match_id=event.match_id,
                            action_key=str(action_key),
                            agent_profile_id=UUID(str(agent_profile_id)),
                            duration_ms=int(event.payload.get("duration_ms", 0)),
                            side=(
                                str(event.payload["side"]) if event.payload.get("side") else None
                            ),
                        ),
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

        async def transition() -> None:
            if previous is not None:
                await asyncio.gather(previous, return_exceptions=True)
            runtime = self._speech_runtime
            if runtime is None:
                return
            if event.type == "match.offline":
                actor = self._actors.get(event.match_id)
                if actor is None:
                    return
                current = actor.state
                if (
                    current.status != "RUNNING"
                    or current.action_state != "HUMAN_SPEAKING"
                    or current.current_speech_id != speech_id
                    or current.current_speaker_user_id != speaker_id
                ):
                    return
                await runtime.pause_speech(event.match_id, speech_id)
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
            try:
                envelope = await self._speech_callback_envelope(
                    match_id=event.match_id,
                    speech_id=speech_id,
                    user_id=speaker_id,
                )
                await runtime.start_speech(event.match_id, speech_id, speaker_id, envelope)
            except Exception as error:
                await self.handle_asr_failure(
                    envelope=CallbackEnvelope(
                        match_id=event.match_id,
                        speech_id=speech_id,
                        attempt_no=1,
                        generation_id=None,
                        connection_epoch=None,
                        context_version=0,
                        opportunity_id=None,
                        opportunity_generation=None,
                    ),
                    code=str(getattr(error, "code", "asr_start_timeout")),
                )

        task = asyncio.create_task(
            transition(), name=f"asr-presence-{event.type}-{event.match_id}-{speech_id}"
        )
        self._speech_presence_tasks[event.match_id] = task
        self._background_tasks.add(task)

        def finished(completed: asyncio.Task[None]) -> None:
            self._background_tasks.discard(completed)
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
            or state.selection_phase != "COMPETING"
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

    async def agent_text_generated(self, *, envelope: CallbackEnvelope, text: str) -> None:
        envelope.require("generation_id")
        match_id = envelope.match_id
        generation_id = envelope.generation_id
        assert generation_id is not None
        actor = self._actors.get(match_id)
        if actor is None or not actor.state.free_competition_enabled:
            return
        async with self._session_factory() as session:
            async with session.begin():
                generation = await session.get(AgentGeneration, generation_id)
                match = await session.get(Match, match_id)
                action = actor.state.current_action
                if (
                    generation is None
                    or match is None
                    or generation.match_id != match_id
                    or generation.call_type != "LLM_SPEECH"
                    or generation.status not in {"LLM_READY", "PLAYING", "FINALIZED"}
                    or generation.context_version != match.context_version
                    or action is None
                    or generation.action_key != action.action_key
                    or actor.state.current_agent_profile_id != generation.agent_profile_id
                ):
                    return
                latest_generation_id = await session.scalar(
                    select(AgentGeneration.id)
                    .where(
                        AgentGeneration.match_id == match_id,
                        AgentGeneration.action_key == generation.action_key,
                        AgentGeneration.agent_profile_id == generation.agent_profile_id,
                        AgentGeneration.call_type == "LLM_SPEECH",
                    )
                    .order_by(
                        AgentGeneration.attempt_no.desc(),
                        AgentGeneration.created_at.desc(),
                    )
                    .limit(1)
                )
                if latest_generation_id != generation_id:
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
                if speech is None:
                    self._pending_agent_full_text[(match_id, generation_id)] = (envelope, text)
                    return
                if (
                    actor.state.current_speech_id != speech.id
                    or actor.state.current_agent_profile_id != speech.agent_profile_id
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
        if state.experiment_mode and (
            known_late_round
            or state.selection_phase != "COMPETING"
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
            self._late_experiment_rounds.discard(decision_round_id)
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
        if actor is None or actor.state.current_speech_id != speech_id:
            return
        if not await actor.set_interim_text(text, speech_id=speech_id):
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
        async with self._session_factory() as session:
            async with session.begin():
                stored_match_id = await session.scalar(
                    select(Speech.match_id).where(Speech.id == speech_id)
                )
                if stored_match_id != match_id:
                    return
                existing = await session.scalar(
                    select(AsrSegment).where(
                        AsrSegment.speech_id == speech_id,
                        AsrSegment.segment_no == segment_no,
                    )
                )
                completed_at = datetime.now(UTC)
                if existing is None:
                    segment = AsrSegment(
                        id=uuid4(),
                        speech_id=speech_id,
                        segment_no=segment_no,
                        task_id=task_id,
                        status="FINALIZED",
                        raw_final_text=final_text,
                        first_interim_latency_ms=first_interim_latency_ms,
                        final_latency_ms=final_latency_ms,
                        pcm_sample_count=pcm_sample_count,
                        finalized_at=completed_at,
                    )
                    session.add(segment)
                else:
                    segment = existing
                    segment.status = "FINALIZED"
                    segment.raw_final_text = final_text
                    segment.first_interim_latency_ms = first_interim_latency_ms
                    segment.final_latency_ms = final_latency_ms
                    segment.pcm_sample_count = pcm_sample_count
                    segment.error_code = None
                    segment.finalized_at = completed_at
                call = await session.scalar(
                    select(ExternalCall)
                    .where(
                        ExternalCall.asr_segment_id == segment.id,
                        ExternalCall.status == "STARTED",
                    )
                    .with_for_update()
                )
                completed_latency_ms = max(0, pcm_sample_count // 16 + final_latency_ms)
                if call is None:
                    call = ExternalCall(
                        call_kind="ASR",
                        provider="BAILIAN",
                        operation="duplex.transcribe",
                        model="fun-asr-realtime",
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
        async with self._session_factory() as session:
            async with session.begin():
                match_id = await session.scalar(
                    select(Speech.match_id).where(Speech.id == speech_id)
                )
                if match_id is None:
                    return
                if await session.scalar(select(AsrSegment.id).where(AsrSegment.task_id == task_id)):
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
                        model="fun-asr-realtime",
                        attempt_no=int(previous_attempt or 0) + 1,
                        status="STARTED",
                        match_id=match_id,
                        speech_id=speech_id,
                        connection_epoch=envelope.connection_epoch,
                        context_version=envelope.context_version,
                        opportunity_id=envelope.opportunity_id,
                        asr_segment_id=segment.id,
                        capture_version=PROVIDER_CAPTURE_VERSION,
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
        async with self._session_factory() as session:
            async with session.begin():
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
                segment.status = "FAILED"
                segment.error_code = error_code
                segment.finalized_at = datetime.now(UTC)
                call = await session.scalar(
                    select(ExternalCall)
                    .where(
                        ExternalCall.asr_segment_id == segment.id,
                        ExternalCall.status == "STARTED",
                    )
                    .with_for_update()
                )
                if call is not None:
                    call.status = "FAILED"
                    call.error_code = error_code
                    call.completed_at = datetime.now(UTC)

    async def persist_asr_capture(
        self, *, envelope: CallbackEnvelope, task_id: UUID, provider_capture: ProviderCallCapture
    ) -> None:
        envelope.require("speech_id")
        async with self._session_factory() as session:
            async with session.begin():
                call = await session.scalar(
                    select(ExternalCall)
                    .join(AsrSegment, AsrSegment.id == ExternalCall.asr_segment_id)
                    .where(AsrSegment.task_id == task_id)
                    .with_for_update()
                )
                if call is not None:
                    if call.match_id != envelope.match_id or call.speech_id != envelope.speech_id:
                        return
                    await persist_provider_capture(session, call, provider_capture)

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
                if speech.status == "FINALIZED":
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
                source_opportunities = list(
                    (
                        await session.scalars(
                            select(FreeDebateOpportunity)
                            .where(
                                FreeDebateOpportunity.match_id == match_id,
                                FreeDebateOpportunity.status == "ACTIVE",
                            )
                            .with_for_update()
                        )
                    ).all()
                )
                source = next(
                    (
                        item
                        for item in source_opportunities
                        if str(item.frozen_context.get("source_speech_id")) == str(speech_id)
                    ),
                    None,
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

    async def handle_agent_failure(self, *, envelope: CallbackEnvelope, error_code: str) -> None:
        match_id = envelope.match_id
        generation_id = envelope.generation_id
        actor = await self.get_actor(match_id)
        state = actor.state
        if state.status != "RUNNING" or state.action_state not in {
            "AGENT_PREPARING",
            "AGENT_SPEAKING",
            "AGENT_FINALIZING",
        }:
            return
        if generation_id is not None:
            action = state.current_action
            if action is None or state.current_agent_profile_id is None:
                return
            async with self._session_factory() as session:
                generation = await session.get(AgentGeneration, generation_id)
                latest_id = await session.scalar(
                    select(AgentGeneration.id)
                    .where(
                        AgentGeneration.match_id == match_id,
                        AgentGeneration.action_key == action.action_key,
                        AgentGeneration.agent_profile_id == state.current_agent_profile_id,
                    )
                    .order_by(AgentGeneration.created_at.desc(), AgentGeneration.id.desc())
                    .limit(1)
                )
            if generation is None or generation.match_id != match_id or latest_id != generation_id:
                return
        await self.submit(
            match_id,
            MatchCommand(
                type="system.error",
                message_id=f"agent-error:{match_id}:{generation_id}:{error_code}",
                payload={
                    "error_code": error_code,
                    "generation_id": str(generation_id) if generation_id else None,
                },
            ),
        )

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
        if (
            actor.state.free_competition_enabled
            and action is not None
            and action.action_kind == "FREE_DEBATE"
        ):
            await self._start_current_experiment_decision(
                match_id=match_id, source_speech_id=speech_id
            )
            if (
                actor.state.action_state != "SPEECH_FINALIZING"
                or actor.state.current_speech_id != speech_id
            ):
                await self._persist_late_experiment_asr(
                    match_id=match_id,
                    speech_id=speech_id,
                    final_text=final_text,
                    first_interim_latency_ms=first_interim_latency_ms,
                    final_latency_ms=final_latency_ms,
                    audio_duration_ms=audio_duration_ms,
                    audio_storage_path=audio_storage_path,
                    audio_recording_error=audio_recording_error,
                )
                await self._start_late_asr_decision(match_id=match_id, source_speech_id=speech_id)
                return MatchCommandResult(state=actor.state, events=())
        return await self.submit(
            match_id,
            MatchCommand(
                type="asr.finalized",
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
                speech = await session.get(Speech, speech_id, with_for_update=True)
                if speech is None or speech.match_id != match_id:
                    return
                if speech.status not in {"STARTED", "FINALIZING"}:
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
                source_opportunities = list(
                    (
                        await session.scalars(
                            select(FreeDebateOpportunity)
                            .where(
                                FreeDebateOpportunity.match_id == match_id,
                                FreeDebateOpportunity.status == "ACTIVE",
                            )
                            .with_for_update()
                        )
                    ).all()
                )
                source_opportunity = next(
                    (
                        item
                        for item in source_opportunities
                        if str(item.frozen_context.get("source_speech_id")) == str(speech_id)
                    ),
                    None,
                )
                if source_opportunity is not None:
                    source_opportunity.source_speech_id = speech_id
                    source_opportunity.source_ended_at = speech.ended_at
                match = await session.get(Match, match_id, with_for_update=True)
                if match is not None:
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
                opportunities = list(
                    (
                        await session.scalars(
                            select(FreeDebateOpportunity)
                            .where(
                                FreeDebateOpportunity.match_id == match_id,
                                FreeDebateOpportunity.status == "ACTIVE",
                            )
                            .with_for_update()
                        )
                    ).all()
                )
                opportunity = next(
                    (
                        item
                        for item in opportunities
                        if str(item.frozen_context.get("source_speech_id")) == str(source_speech_id)
                    ),
                    None,
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
        self._late_experiment_rounds.add(round_id)
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

    async def handle_asr_failure(self, *, envelope: CallbackEnvelope, code: str) -> None:
        envelope.require("speech_id")
        match_id = envelope.match_id
        speech_id = envelope.speech_id
        assert speech_id is not None
        actor = await self.get_actor(match_id)
        if (
            actor.state.status != "RUNNING"
            or actor.state.current_speech_id != speech_id
            or actor.state.action_state not in {"HUMAN_SPEAKING", "SPEECH_FINALIZING"}
        ):
            return
        action = actor.state.current_action
        late_free_finalizing = (
            actor.state.action_state == "SPEECH_FINALIZING"
            and actor.state.free_competition_enabled
            and action is not None
            and action.action_kind == "FREE_DEBATE"
        )
        speaker_id = actor.state.current_speaker_user_id
        if speaker_id is not None and speaker_id in dict(actor.state.offline_since_ms):
            return
        if not await self._asr_callback_is_current(actor=actor, envelope=envelope):
            return
        failure_class = classify_asr_error(code)
        async with self._session_factory() as session:
            async with session.begin():
                speech = await session.get(Speech, speech_id, with_for_update=True)
                if speech is None or speech.match_id != match_id:
                    return
                speech.status = "FAILED"
                speech.asr_error_code = code
                speech.ended_at = datetime.now(UTC)
                failure_query = (
                    select(func.count())
                    .select_from(Speech)
                    .where(
                        Speech.match_id == match_id,
                        Speech.status == "FAILED",
                    )
                )
                if speech.opportunity_id is not None:
                    failure_query = failure_query.where(
                        Speech.opportunity_id == speech.opportunity_id
                    )
                else:
                    failure_query = failure_query.where(Speech.action_key == speech.action_key)
                failures = await session.scalar(failure_query)
        # In competitive free debate the next-speaker window opens while ASR
        # drains.  A provider close error can race a buffered final result.
        # Keep the failed attempt for diagnostics, but never reset the Actor
        # after speech.finish has already been accepted: that would erase the
        # speech identity and ask the same human to speak again.  The existing
        # bounded selection window continues the match, and a genuinely late
        # final result is persisted by the late-finalization path.
        if late_free_finalizing:
            return
        # Configuration/protocol failures cannot be repaired by replaying the
        # same request.  Surface them immediately for operator recovery.
        if failure_class != "CONFIG" and int(failures or 0) < 2:
            await actor.submit(
                MatchCommand(
                    type="speech.reset",
                    message_id=f"asr-retry:{speech_id}",
                    actor_user_id=actor.state.current_speaker_user_id,
                    payload={"privileged": True, "preserve_failed": True},
                )
            )
            await self._publish(
                actor.state,
                (
                    MatchEvent(
                        type="asr.retry_required",
                        match_id=match_id,
                        sequence=actor.state.sequence,
                        server_time_ms=int(datetime.now(UTC).timestamp() * 1000),
                        payload={
                            "speech_id": str(speech_id),
                            "error_code": code,
                            "asr_failure_class": failure_class,
                        },
                    ),
                ),
            )
            return
        await actor.submit(
            MatchCommand(
                type="system.error",
                message_id=f"asr-error:{speech_id}",
                payload={
                    "error_code": code,
                    "asr_failure": True,
                    "asr_failure_class": failure_class,
                },
            )
        )

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

    async def _commit(
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
                                        + timedelta(seconds=3)
                                        if candidate.free_competition_enabled
                                        and candidate.selection_deadline_mono is not None
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
                            deadline = datetime.fromtimestamp(
                                event.server_time_ms / 1000, UTC
                            ) + timedelta(seconds=3)
                            opportunity.source_ended_at = datetime.fromtimestamp(
                                event.server_time_ms / 1000, UTC
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
                                source_opportunity.source_ended_at = source_ended_at
                                source_opportunity.selection_deadline_at = (
                                    source_ended_at + timedelta(seconds=3)
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
                            speech.finish_reason = str(event.payload.get("reason", ""))
                            speech.ended_at = datetime.now(UTC)
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
                if (
                    command.type in ("speech.reset", "system.recover")
                    and previous.current_speech_id is not None
                    and not bool(command.payload.get("preserve_failed"))
                ):
                    speech = await session.get(
                        Speech, previous.current_speech_id, with_for_update=True
                    )
                    if speech is not None:
                        speech.status = "RESET"
                        speech.ended_at = datetime.now(UTC)
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
            state = _state_from_snapshot(row.id, row.runtime_snapshot)
            actor = MatchActor(
                state, commit=self._commit, publish=self._publish, pre_commit=self._pre_commit
            )
            await actor.start()
            if row.status in ("START_PENDING_RUNTIME", "START_COUNTDOWN", "RUNNING"):
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
            async with self._lock:
                self._actors[row.id] = actor
            recovered += 1
        return recovered

    async def start_room_match(
        self,
        session: AsyncSession,
        *,
        room_id: UUID,
        actor_user_id: UUID,
        actor_role: str,
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
                                    display_name=agent.name,
                                    side=seat.side,
                                    seat_no=seat.seat_no,
                                )
                            )
                await session.flush()
            actor = MatchActor(
                state, commit=self._commit, publish=self._publish, pre_commit=self._pre_commit
            )
            await actor.start()
            self._actors[match_id] = actor
        result = await actor.submit(
            MatchCommand(type="runtime.start", message_id=f"runtime-start:{match_id}")
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

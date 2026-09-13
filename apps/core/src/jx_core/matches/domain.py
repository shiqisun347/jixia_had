"""Single-queue authoritative runtime for ordinary linear match actions."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from time import monotonic, time
from typing import Any, Literal, cast
from uuid import UUID, uuid4

MatchStatus = Literal[
    "START_PENDING_RUNTIME",
    "START_COUNTDOWN",
    "RUNNING",
    "FINISHED",
    "TERMINATED",
    "SYSTEM_RECOVERY",
    "ERROR",
    "PAUSED",
]
ActionState = Literal[
    "NOT_STARTED",
    "HOST_ANNOUNCING",
    "PREPARING",
    "HUMAN_READY_TO_START",
    "HUMAN_SPEAKING",
    "SPEECH_FINALIZING",
    "AGENT_PREPARING",
    "AGENT_SPEAKING",
    "AGENT_FINALIZING",
    "ACTION_FINISHED",
    "MATCH_FINISHED",
    "RECOVERY_REQUIRED",
    "FREE_SELECTING",
    "RESUME_COUNTDOWN",
]
ActionKind = Literal[
    "HUMAN_SPEECH",
    "AGENT_SPEECH",
    "PREPARATION",
    "FREE_DEBATE",
    "HOST_AUDIO",
]

FREE_MINIMUM_SPEECH_MS = 3_000
logger = logging.getLogger("jx-core.matches.actor")


def _uuid_text(value: UUID | None) -> str | None:
    """Serialize nullable UUIDs without ever producing the literal 'None'."""
    return str(value) if value is not None else None


def _payload_uuid(value: object, *, required: bool = False) -> UUID | None:
    if value is None or (isinstance(value, str) and value.strip().lower() in {"", "none", "null"}):
        if required:
            raise MatchDomainError("invalid_event_payload")
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as error:
        raise MatchDomainError("invalid_event_payload") from error


@dataclass(frozen=True, slots=True)
class DebateParticipant:
    side: str
    seat_no: int
    user_id: UUID | None = None
    agent_profile_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class AgentDecisionState:
    agent_profile_id: UUID
    side: str
    seat_no: int
    status: Literal["DECIDING", "HAND", "SKIP"] = "DECIDING"
    should_speak: bool | None = None
    decision_reason: str | None = None
    willingness: float | None = None
    result_order: int | None = None
    failed: bool = False


class MatchDomainError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class MatchAction:
    stage_position: int
    action_position: int
    action_kind: ActionKind
    duration_seconds: int
    side: str | None = None
    seat_no: int | None = None
    speaker_user_id: UUID | None = None
    speaker_kind: Literal["HUMAN", "AGENT"] = "HUMAN"
    agent_profile_id: UUID | None = None
    host_audio_path: str | None = None
    host_audio_duration_ms: int | None = None
    participants: tuple[DebateParticipant, ...] = ()
    free_max_speech_seconds: int = 60
    free_starting_side: str = "AFFIRMATIVE"

    @property
    def action_key(self) -> str:
        return f"{self.stage_position}:{self.action_position}"


@dataclass(frozen=True, slots=True)
class MatchRuntimeState:
    match_id: UUID
    status: MatchStatus
    action_state: ActionState
    actions: tuple[MatchAction, ...]
    match_seed: int = 0
    current_action_index: int = 0
    sequence: int = 0
    current_speech_id: UUID | None = None
    current_speaker_user_id: UUID | None = None
    current_agent_profile_id: UUID | None = None
    interim_text: str = ""
    speech_start_sequence: int | None = None
    speech_deadline_mono: float | None = None
    human_start_deadline_mono: float | None = None
    speech_remaining_ms: int | None = None
    host_audio_remaining_ms: int | None = None
    host_audio_deadline_mono: float | None = None
    host_audio_deadline_ms: int | None = None
    current_speaker_side: str | None = None
    current_speaker_seat_no: int | None = None
    free_holder_side: str | None = None
    free_affirmative_remaining_ms: int | None = None
    free_negative_remaining_ms: int | None = None
    hand_queue: tuple[UUID, ...] = ()
    agent_hand_queue: tuple[UUID, ...] = ()
    agent_selection_mode: Literal["VOLUNTEER", "FALLBACK", "ALL_AGENT_SKIP_RANDOM"] | None = None
    agent_decision_round_id: UUID | None = None
    agent_decisions: tuple[AgentDecisionState, ...] = ()
    hand_window_open: bool = False
    experiment_mode: bool = False
    formal_4v4: bool = False
    experiment_attempt_id: UUID | None = None
    opportunity_id: UUID | None = None
    allocated_opportunity_id: UUID | None = None
    opportunity_generation: int = 0
    selection_phase: Literal["COMPETING", "HUMAN_ONLY_WAIT", "ALLOCATED"] | None = None
    agent_effective_status: (
        Literal["WAITING", "DECIDING", "RAISE", "SKIP", "TECHNICAL_MISSING"] | None
    ) = None
    selection_deadline_mono: float | None = None
    selection_remaining_ms: int | None = None
    human_wait_deadline_mono: float | None = None
    human_wait_remaining_ms: int | None = None
    paused_from_status: MatchStatus | None = None
    paused_from_action_state: ActionState | None = None
    pause_initiator_user_id: UUID | None = None
    offline_user_id: UUID | None = None
    offline_since_ms: tuple[tuple[UUID, int], ...] = ()
    connection_epochs: tuple[tuple[UUID, int], ...] = ()
    error_code: str | None = None

    @property
    def free_competition_enabled(self) -> bool:
        """The v2 competition path applies to formal 4v4 and research matches."""
        return self.formal_4v4 or self.experiment_mode

    @property
    def current_action(self) -> MatchAction | None:
        if 0 <= self.current_action_index < len(self.actions):
            return self.actions[self.current_action_index]
        return None


@dataclass(frozen=True, slots=True)
class MatchRuntimeView:
    state: MatchRuntimeState
    speech_remaining_ms: int | None
    countdown_remaining_ms: int | None
    free_affirmative_remaining_ms: int | None
    free_negative_remaining_ms: int | None
    host_audio_remaining_ms: int | None = None


@dataclass(frozen=True, slots=True)
class MatchCommand:
    type: str
    message_id: str
    actor_user_id: UUID | None = None
    payload: Mapping[str, Any] = field(default_factory=lambda: dict[str, Any]())


@dataclass(frozen=True, slots=True)
class MatchEvent:
    type: str
    match_id: UUID
    sequence: int
    server_time_ms: int
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class MatchCommandResult:
    state: MatchRuntimeState
    events: tuple[MatchEvent, ...]
    duplicate: bool = False


@dataclass(slots=True)
class _Envelope:
    command: MatchCommand
    future: asyncio.Future[MatchCommandResult]


CommitHook = Callable[
    [MatchRuntimeState, MatchRuntimeState, tuple[MatchEvent, ...], MatchCommand],
    Awaitable[None],
]
PublishHook = Callable[[MatchRuntimeState, tuple[MatchEvent, ...]], Awaitable[None]]
PreCommitHook = Callable[
    [MatchRuntimeState, MatchRuntimeState, tuple[MatchEvent, ...], MatchCommand],
    Awaitable[None],
]


def compile_linear_actions(
    rule_snapshot: Mapping[str, Any],
    seats: Mapping[tuple[str, int], UUID | None],
    agent_seats: Mapping[tuple[str, int], UUID] | None = None,
) -> tuple[MatchAction, ...]:
    raw_host_audio = rule_snapshot.get("host_audio", [])
    if not isinstance(raw_host_audio, list):
        raise MatchDomainError("rule_snapshot_invalid")
    raw_host_audio = cast(list[Any], raw_host_audio)
    host_audio: dict[str, tuple[str, int | None]] = {}
    for raw_item in raw_host_audio:
        if not isinstance(raw_item, Mapping):
            raise MatchDomainError("rule_snapshot_invalid")
        item = cast(Mapping[str, Any], raw_item)
        segment_key = item.get("segment_key")
        storage_path = item.get("storage_path")
        duration_value = item.get("duration_ms")
        if segment_key and storage_path:
            host_audio[str(segment_key)] = (
                str(storage_path),
                duration_value if isinstance(duration_value, int) and duration_value > 0 else None,
            )
    compiled: list[MatchAction] = []
    raw_stages = rule_snapshot.get("stages", [])
    if not isinstance(raw_stages, list):
        raise MatchDomainError("rule_snapshot_invalid")
    raw_stages = cast(list[Any], raw_stages)
    for raw_stage in raw_stages:
        if not isinstance(raw_stage, Mapping):
            raise MatchDomainError("rule_snapshot_invalid")
        stage = cast(Mapping[str, Any], raw_stage)
        stage_position = int(stage.get("position", 0))
        stage_kind = str(stage.get("stage_kind", ""))
        host_entry = host_audio.get(f"stage-{stage_position}-start")
        end_host_entry = host_audio.get(f"stage-{stage_position}-end")
        host_path = host_entry[0] if host_entry else None
        host_duration_ms = host_entry[1] if host_entry else None
        end_host_path = end_host_entry[0] if end_host_entry else None
        end_host_duration_ms = end_host_entry[1] if end_host_entry else None
        if stage_kind == "END":
            if host_path:
                compiled.append(
                    MatchAction(
                        stage_position=stage_position,
                        action_position=0,
                        action_kind="HOST_AUDIO",
                        duration_seconds=0,
                        host_audio_path=host_path,
                        host_audio_duration_ms=host_duration_ms,
                    )
                )
            if end_host_path:
                compiled.append(
                    MatchAction(
                        stage_position=stage_position,
                        action_position=1,
                        action_kind="HOST_AUDIO",
                        duration_seconds=0,
                        host_audio_path=end_host_path,
                        host_audio_duration_ms=end_host_duration_ms,
                    )
                )
            break
        if stage_kind == "FREE_DEBATE":
            parameters_value = stage.get("parameters")
            parameters: Mapping[str, Any] = (
                cast(Mapping[str, Any], parameters_value)
                if isinstance(parameters_value, Mapping)
                else cast(Mapping[str, Any], {})
            )
            participants: list[DebateParticipant] = []
            for (side, seat_no), user_id in seats.items():
                if user_id is not None:
                    participants.append(
                        DebateParticipant(side=side, seat_no=seat_no, user_id=user_id)
                    )
            for (side, seat_no), agent_profile_id in (agent_seats or {}).items():
                participants.append(
                    DebateParticipant(
                        side=side,
                        seat_no=seat_no,
                        agent_profile_id=agent_profile_id,
                    )
                )
            if not any(item.side == "AFFIRMATIVE" for item in participants) or not any(
                item.side == "NEGATIVE" for item in participants
            ):
                raise MatchDomainError("free_debate_participants_required")
            compiled.append(
                MatchAction(
                    stage_position=stage_position,
                    action_position=0,
                    action_kind="FREE_DEBATE",
                    duration_seconds=int(stage.get("duration_seconds", 0)),
                    host_audio_path=host_path,
                    host_audio_duration_ms=host_duration_ms,
                    participants=tuple(
                        sorted(participants, key=lambda item: (item.side, item.seat_no))
                    ),
                    free_max_speech_seconds=int(parameters.get("max_speech_seconds", 60)),
                    free_starting_side=str(parameters.get("starting_side", "AFFIRMATIVE")),
                )
            )
            if end_host_path:
                compiled.append(
                    MatchAction(
                        stage_position=stage_position,
                        action_position=10_000,
                        action_kind="HOST_AUDIO",
                        duration_seconds=0,
                        host_audio_path=end_host_path,
                        host_audio_duration_ms=end_host_duration_ms,
                    )
                )
            continue
        if stage_kind == "PREPARATION":
            compiled.append(
                MatchAction(
                    stage_position=stage_position,
                    action_position=0,
                    action_kind="PREPARATION",
                    duration_seconds=int(stage.get("duration_seconds", 0)),
                    host_audio_path=host_path,
                    host_audio_duration_ms=host_duration_ms,
                )
            )
            if end_host_path:
                compiled.append(
                    MatchAction(
                        stage_position=stage_position,
                        action_position=10_000,
                        action_kind="HOST_AUDIO",
                        duration_seconds=0,
                        host_audio_path=end_host_path,
                        host_audio_duration_ms=end_host_duration_ms,
                    )
                )
            continue
        if stage_kind != "FIXED_SPEECH":
            raise MatchDomainError("rule_snapshot_invalid")
        raw_actions = stage.get("actions", [])
        if not isinstance(raw_actions, list) or not raw_actions:
            raise MatchDomainError("rule_snapshot_invalid")
        raw_actions = cast(list[Any], raw_actions)
        for raw_action in raw_actions:
            if not isinstance(raw_action, Mapping):
                raise MatchDomainError("rule_snapshot_invalid")
            action = cast(Mapping[str, Any], raw_action)
            side = str(action.get("side", ""))
            seat_no = int(action.get("seat_no", 0))
            speaker_user_id = seats.get((side, seat_no))
            agent_profile_id = (agent_seats or {}).get((side, seat_no))
            if speaker_user_id is None and agent_profile_id is None:
                raise MatchDomainError("human_speaker_required")
            compiled.append(
                MatchAction(
                    stage_position=stage_position,
                    action_position=int(action.get("position", 0)),
                    action_kind=(
                        "AGENT_SPEECH" if agent_profile_id is not None else "HUMAN_SPEECH"
                    ),
                    duration_seconds=int(action.get("duration_seconds", 0)),
                    side=side,
                    seat_no=seat_no,
                    speaker_user_id=speaker_user_id,
                    speaker_kind="AGENT" if agent_profile_id is not None else "HUMAN",
                    agent_profile_id=agent_profile_id,
                    host_audio_path=host_path
                    if not compiled or compiled[-1].stage_position != stage_position
                    else None,
                    host_audio_duration_ms=host_duration_ms
                    if not compiled or compiled[-1].stage_position != stage_position
                    else None,
                )
            )
        if end_host_path:
            compiled.append(
                MatchAction(
                    stage_position=stage_position,
                    action_position=10_000,
                    action_kind="HOST_AUDIO",
                    duration_seconds=0,
                    host_audio_path=end_host_path,
                    host_audio_duration_ms=end_host_duration_ms,
                )
            )
    if not compiled:
        raise MatchDomainError("rule_snapshot_invalid")
    return tuple(compiled)


class MatchActor:
    def __init__(
        self,
        state: MatchRuntimeState,
        *,
        queue_size: int = 128,
        clock: Callable[[], float] = monotonic,
        wall_clock: Callable[[], float] = time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        commit: CommitHook | None = None,
        publish: PublishHook | None = None,
        pre_commit: PreCommitHook | None = None,
        idempotency_size: int = 256,
    ) -> None:
        self.state: MatchRuntimeState = state
        self._queue: asyncio.Queue[_Envelope] = asyncio.Queue(maxsize=queue_size)
        self._clock = clock
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._commit = commit
        self._publish = publish
        self._pre_commit = pre_commit
        self._idempotency_size = idempotency_size
        self._idempotency: OrderedDict[str, MatchCommandResult] = OrderedDict()
        self._runner: asyncio.Task[None] | None = None
        self._timer: asyncio.Task[None] | None = None
        self._timer_deadline_mono: float | None = None
        self._timer_command_type: str | None = None
        self._offline_timers: dict[UUID, asyncio.Task[None]] = {}
        self._offline_users: set[UUID] = {user_id for user_id, _ in state.offline_since_ms} or (
            {state.offline_user_id} if state.offline_user_id is not None else set()
        )
        # WebSocket reconnects can make an older connection's close callback
        # arrive after the newer connection has already joined. Keep the
        # newest observed epoch so that stale lifecycle events cannot mark a
        # reconnected participant offline again.
        self._connection_epochs: dict[UUID, int] = dict(state.connection_epochs)
        self._processing = False
        self._pending_cancel_timer = False
        self._pending_timer: tuple[float, str, str, Mapping[str, Any] | None] | None = None

    async def start(self) -> None:
        if self._runner is None:
            self._runner = asyncio.create_task(
                self._run(), name=f"match-actor-{self.state.match_id}"
            )
            if self.state.status == "RUNNING":
                for user_id in tuple(self._offline_users):
                    self._schedule_offline_expiry(user_id)

    async def close(self) -> None:
        self._cancel_timer()
        offline_tasks = tuple(self._offline_timers.values())
        self._cancel_all_offline_expiries()
        self._offline_users.clear()
        if offline_tasks:
            await asyncio.gather(*offline_tasks, return_exceptions=True)
        if self._runner is not None:
            self._runner.cancel()
            try:
                await self._runner
            except asyncio.CancelledError:
                pass
            self._runner = None

    async def submit(self, command: MatchCommand) -> MatchCommandResult:
        if self._runner is None:
            raise MatchDomainError("match_actor_not_started")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[MatchCommandResult] = loop.create_future()
        try:
            self._queue.put_nowait(_Envelope(command=command, future=future))
        except asyncio.QueueFull as error:
            raise MatchDomainError("match_actor_busy") from error
        return await future

    def view(self) -> MatchRuntimeView:
        """Return effective display timing without mutating runtime state."""

        speech_remaining_ms = self.state.speech_remaining_ms
        if self.state.speech_deadline_mono is not None:
            speech_remaining_ms = max(
                0, int((self.state.speech_deadline_mono - self._clock()) * 1000)
            )
        elif self.state.action_state in ("SPEECH_FINALIZING", "AGENT_FINALIZING"):
            speech_remaining_ms = None

        affirmative_remaining_ms = self.state.free_affirmative_remaining_ms
        negative_remaining_ms = self.state.free_negative_remaining_ms
        if (
            self.state.speech_deadline_mono is not None
            and self.state.speech_remaining_ms is not None
            and speech_remaining_ms is not None
            and self.state.current_action is not None
            and self.state.current_action.action_kind == "FREE_DEBATE"
        ):
            elapsed_ms = max(0, self.state.speech_remaining_ms - speech_remaining_ms)
            if (
                self.state.current_speaker_side == "AFFIRMATIVE"
                and affirmative_remaining_ms is not None
            ):
                affirmative_remaining_ms = max(0, affirmative_remaining_ms - elapsed_ms)
            elif (
                self.state.current_speaker_side == "NEGATIVE" and negative_remaining_ms is not None
            ):
                negative_remaining_ms = max(0, negative_remaining_ms - elapsed_ms)

        countdown_remaining_ms: int | None = None
        if self._timer_deadline_mono is not None and self._timer_command_type in (
            "countdown.elapsed",
            "resume.elapsed",
        ):
            countdown_remaining_ms = max(0, int((self._timer_deadline_mono - self._clock()) * 1000))
        host_audio_remaining_ms = self.state.host_audio_remaining_ms
        if self.state.host_audio_deadline_mono is not None:
            host_audio_remaining_ms = max(
                0, int((self.state.host_audio_deadline_mono - self._clock()) * 1000)
            )
        return MatchRuntimeView(
            state=self.state,
            speech_remaining_ms=speech_remaining_ms,
            host_audio_remaining_ms=host_audio_remaining_ms,
            countdown_remaining_ms=countdown_remaining_ms,
            free_affirmative_remaining_ms=affirmative_remaining_ms,
            free_negative_remaining_ms=negative_remaining_ms,
        )

    async def set_interim_text(self, text: str, *, speech_id: UUID) -> bool:
        """Update transient ASR/agent text without advancing match state."""
        # A command may currently be awaiting its database commit. Updating
        # state during that window would be overwritten by candidate_state
        # when the commit completes. Wait for the Actor transaction boundary.
        while self._processing:
            await asyncio.sleep(0)
        if (
            self.state.status != "RUNNING"
            or self.state.action_state
            not in (
                "HUMAN_SPEAKING",
                "SPEECH_FINALIZING",
                "AGENT_SPEAKING",
                "AGENT_FINALIZING",
            )
            or self.state.current_speech_id != speech_id
        ):
            return False
        self.state = replace(self.state, interim_text=text)
        return True

    async def _run(self) -> None:
        while True:
            envelope = await self._queue.get()
            try:
                result = await self._process(envelope.command)
                if not envelope.future.done():
                    envelope.future.set_result(result)
            except Exception as error:
                if not envelope.future.done():
                    envelope.future.set_exception(error)
            finally:
                self._queue.task_done()

    async def _process(self, command: MatchCommand) -> MatchCommandResult:
        cached = self._idempotency.get(command.message_id)
        if cached is not None:
            return replace(cached, duplicate=True)
        if self.state.status in ("FINISHED", "TERMINATED"):
            raise MatchDomainError("match_not_running")
        if self.state.status in ("SYSTEM_RECOVERY", "ERROR") and command.type not in (
            "match.terminate",
            "match.resume",
        ):
            raise MatchDomainError("match_not_running")
        if self.state.status == "PAUSED" and command.type not in (
            "match.resume",
            "resume.elapsed",
            "match.terminate",
            "member.online",
        ):
            raise MatchDomainError("match_not_running")
        handlers = {
            "system.recover": self._system_recover,
            "system.error": self._system_error,
            "runtime.start": self._runtime_start,
            "countdown.elapsed": self._countdown_elapsed,
            "host.elapsed": self._host_elapsed,
            "host.finished": self._host_finished,
            "preparation.elapsed": self._preparation_elapsed,
            "human.start_timeout": self._human_start_timeout,
            "speech.start": self._speech_start,
            "speech.finish": self._speech_finish,
            "speech.deadline": self._speech_deadline,
            "asr.finalized": self._asr_finalized,
            "agent.playback_started": self._agent_playback_started,
            "agent.playback_finished": self._agent_playback_finished,
            "agent.finalized": self._agent_finalized,
            "hand.raise": self._hand_raise,
            "hand.cancel": self._hand_cancel,
            "hand.window_closed": self._hand_window_closed,
            "human.wait_timeout": self._human_wait_timeout,
            "free.agent_decision_start": self._free_agent_decision_start,
            "free.agent_decision_result": self._free_agent_decision_result,
            "match.pause": self._match_pause,
            "match.resume": self._match_resume,
            "resume.elapsed": self._resume_elapsed,
            "member.offline": self._member_offline,
            "member.online": self._member_online,
            "offline.expired": self._offline_expired,
            "speech.reset": self._speech_reset,
            "match.terminate": self._match_terminate,
        }
        handler: Callable[[MatchCommand], tuple[MatchEvent, ...]] | None = handlers.get(
            command.type
        )
        if handler is None:
            raise MatchDomainError("match_command_unknown")
        previous_state = self.state
        previous_connection_epochs = self._connection_epochs.copy()
        self._processing = True
        self._pending_cancel_timer = False
        self._pending_timer = None
        candidate_state = previous_state
        events: tuple[MatchEvent, ...] = ()
        try:
            events = handler(command)
            candidate_state = self.state
            self.state = previous_state
            if self._pre_commit is not None:
                await self._pre_commit(previous_state, candidate_state, events, command)
            if self._commit is not None:
                await self._commit(previous_state, candidate_state, events, command)
            self.state = candidate_state
            if self._pending_cancel_timer:
                self._cancel_timer_now()
            pending_timer = self._get_pending_timer()
            if pending_timer is not None:
                self._schedule_internal_now(*pending_timer)
            if command.type == "member.offline" and command.actor_user_id is not None and events:
                self._offline_users.add(command.actor_user_id)
                self._schedule_offline_expiry(command.actor_user_id)
            elif command.type == "member.online" and command.actor_user_id is not None and events:
                self._offline_users.discard(command.actor_user_id)
                self._cancel_offline_expiry(command.actor_user_id)
            elif command.type == "match.terminate" or (
                command.type == "match.resume"
                and candidate_state.action_state == "RESUME_COUNTDOWN"
            ):
                self._cancel_all_offline_expiries()
                if command.type == "match.resume":
                    self._offline_users.clear()
        except Exception:
            self.state = previous_state
            self._connection_epochs = previous_connection_epochs
            raise
        finally:
            self._processing = False
            self._pending_cancel_timer = False
            self._pending_timer = None
        if self._publish is not None and events:
            await self._publish(candidate_state, events)
        result = MatchCommandResult(state=self.state, events=events)
        self._idempotency[command.message_id] = result
        self._idempotency.move_to_end(command.message_id)
        while len(self._idempotency) > self._idempotency_size:
            self._idempotency.popitem(last=False)
        return result

    def _event(self, event_type: str, payload: Mapping[str, Any]) -> MatchEvent:
        sequence = self.state.sequence + 1
        self.state = replace(self.state, sequence=sequence)
        return MatchEvent(
            type=event_type,
            match_id=self.state.match_id,
            sequence=sequence,
            server_time_ms=int(self._wall_clock() * 1000),
            payload=payload,
        )

    def _runtime_start(self, _: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.status != "START_PENDING_RUNTIME":
            raise MatchDomainError("match_state_conflict")
        self.state = replace(self.state, status="START_COUNTDOWN", action_state="NOT_STARTED")
        event = self._event("match.countdown", {"duration_ms": 3000})
        self._schedule_internal(3.0, "countdown.elapsed", "internal:start-countdown")
        return (event,)

    def _countdown_elapsed(self, _: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.status != "START_COUNTDOWN":
            raise MatchDomainError("match_state_conflict")
        self.state = replace(self.state, status="RUNNING")
        return self._enter_current_action()

    def _enter_current_action(self) -> tuple[MatchEvent, ...]:
        action = self.state.current_action
        if action is None:
            self.state = replace(
                self.state,
                status="FINISHED",
                action_state="MATCH_FINISHED",
                current_speech_id=None,
                interim_text="",
            )
            return (self._event("match.finished", {}),)
        if action.host_audio_path:
            duration_ms = action.host_audio_duration_ms
            self.state = replace(
                self.state,
                action_state="HOST_ANNOUNCING",
                current_speaker_user_id=None,
                current_agent_profile_id=None,
                current_speaker_side=None,
                current_speaker_seat_no=None,
                current_speech_id=None,
                interim_text="",
                speech_deadline_mono=None,
                human_start_deadline_mono=None,
                speech_remaining_ms=None,
                host_audio_remaining_ms=duration_ms,
                host_audio_deadline_mono=(
                    self._clock() + duration_ms / 1000 if duration_ms else None
                ),
                host_audio_deadline_ms=(
                    int(self._wall_clock() * 1000) + duration_ms if duration_ms else None
                ),
                hand_queue=(),
                agent_hand_queue=(),
                agent_selection_mode=None,
                agent_decision_round_id=None,
                agent_decisions=(),
                hand_window_open=False,
            )
            if duration_ms:
                self._schedule_internal(
                    duration_ms / 1000, "host.elapsed", f"internal:host:{action.action_key}"
                )
            host_event = self._event(
                "host.play",
                {
                    "action_key": action.action_key,
                    "storage_path": action.host_audio_path,
                    "duration_ms": duration_ms,
                    "remaining_ms": duration_ms,
                },
            )
            if action.action_kind == "FREE_DEBATE" and self.state.free_competition_enabled:
                total_ms = action.duration_seconds * 1000
                self.state = replace(
                    self.state,
                    free_holder_side=action.free_starting_side,
                    free_affirmative_remaining_ms=total_ms,
                    free_negative_remaining_ms=total_ms,
                    hand_window_open=True,
                )
                started = self._event(
                    "free_debate.started",
                    {
                        "stage_position": action.stage_position,
                        "holder": action.free_starting_side,
                        "affirmative_remaining_ms": total_ms,
                        "negative_remaining_ms": total_ms,
                    },
                )
                decision = self._start_free_agent_decisions(
                    action.free_starting_side,
                    trigger_kind="INITIAL_HOST",
                    start_deadline=False,
                )
                return (
                    host_event,
                    started,
                    self._event(
                        "hand.window_opened",
                        {"side": action.free_starting_side, "duration_ms": None},
                    ),
                    decision,
                )
            return (host_event,)
        return self._activate_action(action)

    def _host_finished(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.action_state != "HOST_ANNOUNCING":
            raise MatchDomainError("match_state_conflict")
        if not bool(command.payload.get("authorized")):
            raise MatchDomainError("forbidden")
        action = self.state.current_action
        if action is None:
            raise MatchDomainError("match_state_conflict")
        if action.action_kind == "HOST_AUDIO":
            return self._advance_action("host.finished", {})
        return self._activate_action(action)

    def _host_elapsed(self, _: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.action_state != "HOST_ANNOUNCING":
            return ()
        action = self.state.current_action
        if action is None:
            return ()
        if action.action_kind == "HOST_AUDIO":
            return self._advance_action("host.finished", {})
        return (self._event("host.finished", {}), *self._activate_action(action))

    def _activate_action(self, action: MatchAction) -> tuple[MatchEvent, ...]:
        self.state = replace(
            self.state,
            host_audio_remaining_ms=None,
            host_audio_deadline_mono=None,
            host_audio_deadline_ms=None,
        )
        if action.action_kind == "PREPARATION":
            duration_ms = action.duration_seconds * 1000
            self.state = replace(
                self.state,
                action_state="PREPARING",
                human_start_deadline_mono=None,
                speech_remaining_ms=duration_ms,
                speech_deadline_mono=self._clock() + duration_ms / 1000,
            )
            event = self._event(
                "preparation.started",
                {"action_key": action.action_key, "duration_ms": action.duration_seconds * 1000},
            )
            self._schedule_internal(
                action.duration_seconds,
                "preparation.elapsed",
                f"internal:preparation:{action.action_key}",
            )
            return (event,)
        if action.action_kind == "FREE_DEBATE":
            total_ms = action.duration_seconds * 1000
            reuse_initial_opportunity = (
                self.state.free_competition_enabled
                and self.state.opportunity_id is not None
                and self.state.agent_decision_round_id is not None
            )
            self.state = replace(
                self.state,
                action_state="FREE_SELECTING",
                current_speaker_user_id=None,
                current_agent_profile_id=None,
                current_speaker_side=None,
                current_speaker_seat_no=None,
                current_speech_id=None,
                interim_text="",
                speech_deadline_mono=None,
                human_start_deadline_mono=None,
                speech_remaining_ms=None,
                free_holder_side=(
                    self.state.free_holder_side
                    if reuse_initial_opportunity
                    else action.free_starting_side
                ),
                free_affirmative_remaining_ms=(
                    self.state.free_affirmative_remaining_ms
                    if reuse_initial_opportunity
                    else total_ms
                ),
                free_negative_remaining_ms=(
                    self.state.free_negative_remaining_ms if reuse_initial_opportunity else total_ms
                ),
                hand_queue=self.state.hand_queue if reuse_initial_opportunity else (),
                agent_hand_queue=(self.state.agent_hand_queue if reuse_initial_opportunity else ()),
                agent_selection_mode=None,
                agent_decision_round_id=(
                    self.state.agent_decision_round_id if reuse_initial_opportunity else None
                ),
                agent_decisions=(self.state.agent_decisions if reuse_initial_opportunity else ()),
                hand_window_open=True,
                selection_deadline_mono=(
                    self._clock() + 3.0 if reuse_initial_opportunity else None
                ),
                selection_remaining_ms=3_000 if reuse_initial_opportunity else None,
            )
            started = self._event(
                "free_debate.started",
                {
                    "stage_position": action.stage_position,
                    "holder": action.free_starting_side,
                    "affirmative_remaining_ms": total_ms,
                    "negative_remaining_ms": total_ms,
                },
            )
            opened = self._event(
                "hand.window_opened",
                {"side": action.free_starting_side, "duration_ms": 3000},
            )
            if reuse_initial_opportunity:
                self._schedule_internal(3.0, "hand.window_closed", "internal:free-initial")
                return (opened,)
            decision_started = self._start_free_agent_decisions(
                action.free_starting_side,
                trigger_kind="INITIAL_HOST",
            )
            self._schedule_internal(3.0, "hand.window_closed", "internal:free-initial")
            return (started, opened, decision_started)
        if action.action_kind == "AGENT_SPEECH":
            if action.agent_profile_id is None:
                raise MatchDomainError("match_state_conflict")
            self.state = replace(
                self.state,
                action_state="AGENT_PREPARING",
                current_speaker_user_id=None,
                current_agent_profile_id=action.agent_profile_id,
                current_speaker_side=action.side,
                current_speaker_seat_no=action.seat_no,
                current_speech_id=None,
                speech_deadline_mono=None,
                human_start_deadline_mono=None,
                speech_remaining_ms=action.duration_seconds * 1000,
                agent_hand_queue=(),
                agent_selection_mode=None,
                agent_decision_round_id=None,
                agent_decisions=(),
            )
            return (
                self._event(
                    "agent.preparing",
                    {
                        "action_key": action.action_key,
                        "agent_profile_id": str(action.agent_profile_id),
                        "side": action.side,
                        "seat_no": action.seat_no,
                        "duration_ms": action.duration_seconds * 1000,
                    },
                ),
            )
        self.state = replace(
            self.state,
            action_state="HUMAN_READY_TO_START",
            current_speaker_user_id=action.speaker_user_id,
            current_agent_profile_id=None,
            current_speaker_side=action.side or self.state.current_speaker_side,
            current_speaker_seat_no=action.seat_no or self.state.current_speaker_seat_no,
            current_speech_id=None,
            speech_deadline_mono=None,
            speech_remaining_ms=action.duration_seconds * 1000,
            agent_hand_queue=(),
            agent_selection_mode=None,
            agent_decision_round_id=None,
            agent_decisions=(),
        )
        self._schedule_human_start_timeout()
        return (
            self._event(
                "speech.ready",
                {
                    "action_key": action.action_key,
                    "speaker_user_id": str(action.speaker_user_id),
                },
            ),
        )

    def _preparation_elapsed(self, _: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.action_state != "PREPARING":
            raise MatchDomainError("match_state_conflict")
        return self._advance_action("preparation.finished", {})

    @staticmethod
    def _opposite_side(side: str | None) -> str:
        return "NEGATIVE" if side == "AFFIRMATIVE" else "AFFIRMATIVE"

    def _free_remaining_ms(self, side: str) -> int:
        value = (
            self.state.free_affirmative_remaining_ms
            if side == "AFFIRMATIVE"
            else self.state.free_negative_remaining_ms
        )
        return int(value or 0)

    def _free_turn_duration_ms(self, action: MatchAction, side: str) -> int:
        return min(action.free_max_speech_seconds * 1000, self._free_remaining_ms(side))

    def _free_human(self, user_id: UUID, side: str) -> DebateParticipant | None:
        action = self.state.current_action
        if action is None or action.action_kind != "FREE_DEBATE":
            return None
        return next(
            (item for item in action.participants if item.side == side and item.user_id == user_id),
            None,
        )

    def _start_free_agent_decisions(
        self,
        side: str,
        *,
        trigger_kind: Literal[
            "INITIAL_HOST", "HUMAN_SPEECH", "AGENT_SPEECH", "SAME_SIDE_CONTINUATION"
        ]
        | None = None,
        source_speech_id: UUID | None = None,
        start_deadline: bool = True,
    ) -> MatchEvent:
        action = self.state.current_action
        if action is None or action.action_kind != "FREE_DEBATE":
            raise MatchDomainError("match_state_conflict")
        round_id = uuid4()
        decisions = tuple(
            AgentDecisionState(
                agent_profile_id=item.agent_profile_id,
                side=item.side,
                seat_no=item.seat_no,
            )
            for item in sorted(action.participants, key=lambda participant: participant.seat_no)
            if item.side == side and item.agent_profile_id is not None
        )
        if self.state.experiment_mode and not self.state.formal_4v4:
            decisions = decisions[:1]
        reuse_opportunity = (
            self.state.free_competition_enabled and self.state.opportunity_id is not None
        )
        self.state = replace(
            self.state,
            agent_decision_round_id=round_id,
            agent_decisions=decisions,
            agent_hand_queue=(),
            agent_selection_mode=None,
            opportunity_id=(
                self.state.opportunity_id
                if reuse_opportunity
                else uuid4()
                if self.state.free_competition_enabled
                else self.state.opportunity_id
            ),
            opportunity_generation=(
                self.state.opportunity_generation
                if reuse_opportunity
                else self.state.opportunity_generation + 1
                if self.state.free_competition_enabled
                else self.state.opportunity_generation
            ),
            selection_phase=(
                "COMPETING" if self.state.free_competition_enabled else self.state.selection_phase
            ),
            agent_effective_status=(
                "DECIDING"
                if self.state.free_competition_enabled
                else self.state.agent_effective_status
            ),
            human_wait_deadline_mono=None,
            human_wait_remaining_ms=None,
            selection_deadline_mono=(
                self._clock() + 3.0
                if self.state.free_competition_enabled and start_deadline
                else self.state.selection_deadline_mono
            ),
            selection_remaining_ms=(
                3_000
                if self.state.free_competition_enabled and start_deadline
                else self.state.selection_remaining_ms
            ),
        )
        return self._event(
            "agent.decision_started",
            {
                "action_key": action.action_key,
                "side": side,
                "decision_round_id": str(round_id),
                "opportunity_id": (
                    str(self.state.opportunity_id) if self.state.free_competition_enabled else None
                ),
                "opportunity_generation": self.state.opportunity_generation,
                "trigger_kind": trigger_kind
                or ("INITIAL_HOST" if self.state.opportunity_generation == 1 else "HUMAN_SPEECH"),
                "source_speech_id": (
                    str(source_speech_id) if source_speech_id is not None else None
                ),
                "agents": [
                    {
                        "agent_profile_id": str(item.agent_profile_id),
                        "seat_no": item.seat_no,
                    }
                    for item in decisions
                ],
            },
        )

    def _open_experiment_opportunity(
        self,
        *,
        side: str,
        trigger_kind: Literal["HUMAN_SPEECH", "AGENT_SPEECH", "SAME_SIDE_CONTINUATION"],
        source_speech_id: UUID,
    ) -> MatchEvent:
        opportunity_id = uuid4()
        self.state = replace(
            self.state,
            free_holder_side=side,
            opportunity_id=opportunity_id,
            opportunity_generation=self.state.opportunity_generation + 1,
            selection_phase="COMPETING",
            agent_effective_status="WAITING",
            selection_deadline_mono=None,
            selection_remaining_ms=None,
            human_wait_deadline_mono=None,
            human_wait_remaining_ms=None,
            hand_queue=(),
            agent_hand_queue=(),
            agent_selection_mode=None,
            agent_decision_round_id=None,
            agent_decisions=(),
            hand_window_open=True,
        )
        return self._event(
            "free.opportunity_opened",
            {
                "action_key": self.state.current_action.action_key
                if self.state.current_action is not None
                else None,
                "opportunity_id": str(opportunity_id),
                "opportunity_generation": self.state.opportunity_generation,
                "side": side,
                "trigger_kind": trigger_kind,
                "source_speech_id": str(source_speech_id),
            },
        )

    def _free_agent_decision_start(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if (
            not self.state.free_competition_enabled
            or self.state.selection_phase != "COMPETING"
            or self.state.opportunity_id is None
            or str(command.payload.get("opportunity_id")) != str(self.state.opportunity_id)
            or int(command.payload.get("opportunity_generation", -1))
            != self.state.opportunity_generation
        ):
            raise MatchDomainError("stale_callback")
        if self.state.agent_decision_round_id is not None:
            return ()
        side = self.state.free_holder_side
        if side is None:
            raise MatchDomainError("match_state_conflict")
        return (
            self._start_free_agent_decisions(
                side,
                trigger_kind=cast(
                    Literal["HUMAN_SPEECH", "AGENT_SPEECH", "SAME_SIDE_CONTINUATION"],
                    command.payload.get("trigger_kind", "HUMAN_SPEECH"),
                ),
                source_speech_id=(_payload_uuid(command.payload.get("source_speech_id"))),
                start_deadline=False,
            ),
        )

    def _agent_tie_break(self, agent_profile_id: UUID) -> int:
        """Return a round-specific stable pseudo-random tie-break value.

        The value is derived from the match seed, decision round and Agent ID so
        equal willingness does not always privilege the lowest seat number,
        while replaying the same persisted round remains deterministic.
        """
        round_id = self.state.agent_decision_round_id
        round_value = round_id.int if round_id is not None else 0
        return (self.state.match_seed ^ round_value ^ agent_profile_id.int) & ((1 << 128) - 1)

    def _sorted_agent_hands(self, decisions: tuple[AgentDecisionState, ...]) -> tuple[UUID, ...]:
        return tuple(
            item.agent_profile_id
            for item in sorted(
                (item for item in decisions if item.status == "HAND"),
                key=lambda item: (
                    -(item.willingness if item.willingness is not None else 0.0),
                    self._agent_tie_break(item.agent_profile_id),
                ),
            )
        )

    def _queue_snapshot_event(self, reason: str) -> MatchEvent:
        combined = [
            {"kind": "HUMAN", "participant_id": str(user_id), "rank": index + 1}
            for index, user_id in enumerate(self.state.hand_queue)
        ]
        human_count = len(combined)
        combined.extend(
            {
                "kind": "AGENT",
                "participant_id": str(agent_id),
                "rank": human_count + index + 1,
            }
            for index, agent_id in enumerate(self.state.agent_hand_queue)
        )
        return self._event(
            "free.queue_reordered",
            {
                "decision_round_id": (
                    str(self.state.agent_decision_round_id)
                    if self.state.agent_decision_round_id is not None
                    else None
                ),
                "side": self.state.free_holder_side,
                "reason": reason,
                "human_queue": [str(item) for item in self.state.hand_queue],
                "agent_queue": [str(item) for item in self.state.agent_hand_queue],
                "combined_queue": combined,
            },
        )

    def _hand_raise(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if not self.state.hand_window_open or command.actor_user_id is None:
            raise MatchDomainError("hand_window_closed")
        side = (
            self.state.free_holder_side
            if self.state.action_state == "FREE_SELECTING"
            or (
                self.state.free_competition_enabled and self.state.action_state == "HOST_ANNOUNCING"
            )
            else self._opposite_side(self.state.current_speaker_side)
        )
        if side is None or self._free_human(command.actor_user_id, side) is None:
            raise MatchDomainError("hand_not_eligible")
        if self._free_remaining_ms(side) <= 0:
            raise MatchDomainError("hand_not_eligible")
        if command.actor_user_id in self.state.hand_queue:
            raise MatchDomainError("hand_already_raised")
        queue = (*self.state.hand_queue, command.actor_user_id)
        self.state = replace(self.state, hand_queue=queue)
        events = (
            self._event(
                "hand.raised",
                {
                    "user_id": str(command.actor_user_id),
                    "side": side,
                    "order": len(queue),
                    "opportunity_id": str(self.state.opportunity_id),
                    "connection_epoch": command.payload.get("connection_epoch"),
                },
            ),
            self._queue_snapshot_event("HUMAN_RAISED"),
        )
        if self.state.free_competition_enabled and self.state.selection_phase == "HUMAN_ONLY_WAIT":
            return (*events, *self._select_free_human(command.actor_user_id, side))
        return events

    def _hand_cancel(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if not self.state.hand_window_open:
            raise MatchDomainError("hand_window_closed")
        if command.actor_user_id is None or command.actor_user_id not in self.state.hand_queue:
            raise MatchDomainError("hand_not_raised")
        queue = tuple(item for item in self.state.hand_queue if item != command.actor_user_id)
        self.state = replace(self.state, hand_queue=queue)
        return (
            self._event(
                "hand.cancelled",
                {
                    "user_id": str(command.actor_user_id),
                    "opportunity_id": str(self.state.opportunity_id),
                    "connection_epoch": command.payload.get("connection_epoch"),
                },
            ),
            self._queue_snapshot_event("HUMAN_CANCELLED"),
        )

    def _hand_window_closed(self, _: MatchCommand) -> tuple[MatchEvent, ...]:
        valid_experiment_finalizing = (
            self.state.free_competition_enabled
            and self.state.action_state
            in {
                "SPEECH_FINALIZING",
                "AGENT_FINALIZING",
            }
        )
        if (
            self.state.action_state != "FREE_SELECTING" and not valid_experiment_finalizing
        ) or not self.state.hand_window_open:
            return ()
        if self.state.free_competition_enabled:
            timed_out = any(item.status == "DECIDING" for item in self.state.agent_decisions) or (
                not self.state.agent_decisions and self.state.agent_effective_status == "WAITING"
            )
            decisions = tuple(
                replace(item, status="SKIP", failed=True) if item.status == "DECIDING" else item
                for item in self.state.agent_decisions
            )
            self.state = replace(
                self.state,
                hand_window_open=False,
                agent_decisions=decisions,
                agent_hand_queue=self._sorted_agent_hands(decisions),
                agent_effective_status=(
                    "TECHNICAL_MISSING" if timed_out else self.state.agent_effective_status
                ),
                selection_deadline_mono=None,
                selection_remaining_ms=0,
            )
        else:
            self.state = replace(self.state, hand_window_open=False)
        closed = self._event("hand.window_closed", {"side": self.state.free_holder_side})
        return (closed, *self._lock_free_selection_if_ready())

    def _free_agent_decision_result(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.action_state != "FREE_SELECTING" and not (
            self.state.free_competition_enabled
            and self.state.selection_phase == "COMPETING"
            and self.state.action_state
            in {
                "HOST_ANNOUNCING",
                "HUMAN_SPEAKING",
                "SPEECH_FINALIZING",
                "AGENT_PREPARING",
                "AGENT_SPEAKING",
                "AGENT_FINALIZING",
            }
        ):
            raise MatchDomainError("stale_callback")
        action = self.state.current_action
        if action is None or str(command.payload.get("action_key")) != action.action_key:
            raise MatchDomainError("stale_callback")
        if str(command.payload.get("decision_round_id")) != str(self.state.agent_decision_round_id):
            raise MatchDomainError("stale_callback")
        agent_profile_id = _payload_uuid(command.payload.get("agent_profile_id"), required=True)
        assert agent_profile_id is not None
        existing = next(
            (
                item
                for item in self.state.agent_decisions
                if item.agent_profile_id == agent_profile_id
            ),
            None,
        )
        if existing is None or existing.status != "DECIDING":
            raise MatchDomainError("stale_callback")
        failed = bool(command.payload.get("failed", False))
        should_speak_value = command.payload.get("should_speak")
        reason_value = command.payload.get("decision_reason")
        decision_reason: str | None = None
        willingness_value = command.payload.get("willingness")
        if failed:
            should_speak = None
            willingness = None
        else:
            if not isinstance(should_speak_value, bool) or (
                not self.state.free_competition_enabled
                and not isinstance(willingness_value, (int, float))
            ):
                raise MatchDomainError("stale_callback")
            should_speak = should_speak_value
            willingness = (
                None
                if self.state.free_competition_enabled
                else max(0.0, min(1.0, float(cast(float, willingness_value))))
            )
            decision_reason = (
                reason_value.strip() if isinstance(reason_value, str) else "未提供理由"
            )
            if not decision_reason or len(decision_reason) > 20:
                raise MatchDomainError("stale_callback")
        if failed:
            decision_reason = None
        result_order = 1 + max(
            (item.result_order or 0 for item in self.state.agent_decisions), default=0
        )
        updated = tuple(
            replace(
                item,
                status="HAND" if should_speak else "SKIP",
                should_speak=should_speak,
                decision_reason=decision_reason,
                willingness=willingness,
                result_order=result_order,
                failed=failed,
            )
            if item.agent_profile_id == agent_profile_id
            else item
            for item in self.state.agent_decisions
        )
        self.state = replace(
            self.state,
            agent_decisions=updated,
            agent_hand_queue=self._sorted_agent_hands(updated),
            agent_effective_status=(
                "TECHNICAL_MISSING"
                if self.state.free_competition_enabled and failed
                else "RAISE"
                if self.state.free_competition_enabled and should_speak
                else "SKIP"
                if self.state.free_competition_enabled
                else self.state.agent_effective_status
            ),
        )
        progress = self._event(
            "agent.decision_progress",
            {
                "action_key": action.action_key,
                "decision_round_id": _uuid_text(self.state.agent_decision_round_id),
                "agent_profile_id": str(agent_profile_id),
                "status": "SKIP" if failed else "HAND" if should_speak else "SKIP",
                "should_speak": should_speak,
                "decision_reason": decision_reason,
                "willingness": willingness,
                "failed": failed,
                "attempt_no": int(command.payload.get("attempt_no", 1)),
                "duration_ms": int(command.payload.get("duration_ms", 0)),
                "error_code": command.payload.get("error_code"),
                "result_order": result_order,
                "human_hand_at_result": bool(self.state.hand_queue),
            },
        )
        reordered = self._queue_snapshot_event("AGENT_DECISION_COMPLETED")
        return (progress, reordered, *self._lock_free_selection_if_ready())

    def _lock_free_selection_if_ready(self) -> tuple[MatchEvent, ...]:
        if self.state.free_competition_enabled:
            return self._lock_experiment_selection_if_ready()
        if self.state.hand_window_open or any(
            item.status == "DECIDING" for item in self.state.agent_decisions
        ):
            return ()
        action = self.state.current_action
        side = self.state.free_holder_side
        if action is None or action.action_kind != "FREE_DEBATE" or side is None:
            raise MatchDomainError("match_state_conflict")
        valid_hands = tuple(
            user_id
            for user_id in self.state.hand_queue
            if self._free_human(user_id, side) is not None and self._free_remaining_ms(side) > 0
        )
        if valid_hands != self.state.hand_queue:
            self.state = replace(self.state, hand_queue=valid_hands)
        if self.state.hand_queue:
            selected_user_id = self.state.hand_queue[0]
            participant = self._free_human(selected_user_id, side)
            if participant is None:
                raise MatchDomainError("match_state_conflict")
            duration_ms = self._free_turn_duration_ms(action, side)
            self.state = replace(
                self.state,
                action_state="HUMAN_READY_TO_START",
                current_speaker_user_id=selected_user_id,
                current_agent_profile_id=None,
                current_speaker_side=side,
                current_speaker_seat_no=participant.seat_no,
                speech_remaining_ms=duration_ms,
                agent_selection_mode=None,
            )
            self._schedule_human_start_timeout()
            locked = self._event(
                "free.selection_locked",
                {
                    "decision_round_id": _uuid_text(self.state.agent_decision_round_id),
                    "speaker_kind": "HUMAN",
                    "speaker_user_id": str(selected_user_id),
                },
            )
            ready = self._event(
                "speech.ready",
                {
                    "action_key": action.action_key,
                    "speaker_user_id": str(selected_user_id),
                    "side": side,
                    "seat_no": participant.seat_no,
                    "duration_ms": duration_ms,
                },
            )
            return locked, ready

        selected_decision: AgentDecisionState | None = None
        mode: Literal["VOLUNTEER", "FALLBACK"] = "FALLBACK"
        if self.state.agent_hand_queue:
            selected_id = self.state.agent_hand_queue[0]
            selected_decision = next(
                item for item in self.state.agent_decisions if item.agent_profile_id == selected_id
            )
            mode = "VOLUNTEER"
        else:
            valid_skips = sorted(
                (item for item in self.state.agent_decisions if not item.failed),
                key=lambda item: (
                    -(item.willingness if item.willingness is not None else 0.0),
                    self._agent_tie_break(item.agent_profile_id),
                ),
            )
            if valid_skips:
                selected_decision = valid_skips[0]
            elif self.state.agent_decisions:
                ordered = sorted(
                    self.state.agent_decisions,
                    key=lambda item: self._agent_tie_break(item.agent_profile_id),
                )
                selected_decision = ordered[self.state.match_seed % len(ordered)]
        if selected_decision is None:
            self.state = replace(
                self.state,
                status="ERROR",
                action_state="RECOVERY_REQUIRED",
                error_code="agent_unavailable",
            )
            return (self._event("match.error", {"error_code": "agent_unavailable"}),)
        duration_ms = self._free_turn_duration_ms(action, side)
        self.state = replace(
            self.state,
            action_state="AGENT_PREPARING",
            current_speaker_user_id=None,
            current_agent_profile_id=selected_decision.agent_profile_id,
            current_speaker_side=side,
            current_speaker_seat_no=selected_decision.seat_no,
            speech_remaining_ms=duration_ms,
            agent_selection_mode=mode,
        )
        locked = self._event(
            "free.selection_locked",
            {
                "decision_round_id": _uuid_text(self.state.agent_decision_round_id),
                "speaker_kind": "AGENT",
                "agent_profile_id": str(selected_decision.agent_profile_id),
                "agent_selection_mode": mode,
                "all_decisions_failed": all(item.failed for item in self.state.agent_decisions),
            },
        )
        preparing = self._event(
            "agent.preparing",
            {
                "action_key": action.action_key,
                "agent_profile_id": str(selected_decision.agent_profile_id),
                "side": side,
                "seat_no": selected_decision.seat_no,
                "duration_ms": duration_ms,
            },
        )
        return locked, preparing

    def _select_free_human(self, selected_user_id: UUID, side: str) -> tuple[MatchEvent, ...]:
        action = self.state.current_action
        participant = self._free_human(selected_user_id, side)
        if action is None or action.action_kind != "FREE_DEBATE" or participant is None:
            raise MatchDomainError("match_state_conflict")
        self._cancel_timer()
        duration_ms = self._free_turn_duration_ms(action, side)
        self.state = replace(
            self.state,
            action_state="HUMAN_READY_TO_START",
            current_speaker_user_id=selected_user_id,
            current_agent_profile_id=None,
            current_speaker_side=side,
            current_speaker_seat_no=participant.seat_no,
            speech_remaining_ms=duration_ms,
            agent_selection_mode=None,
            selection_phase=(
                "ALLOCATED" if self.state.free_competition_enabled else self.state.selection_phase
            ),
            human_wait_deadline_mono=None,
            human_wait_remaining_ms=None,
            hand_window_open=False,
        )
        self._schedule_human_start_timeout()
        return (
            self._event(
                "free.selection_locked",
                {
                    "decision_round_id": _uuid_text(self.state.agent_decision_round_id),
                    "opportunity_id": str(self.state.opportunity_id),
                    "speaker_kind": "HUMAN",
                    "speaker_user_id": str(selected_user_id),
                },
            ),
            self._event(
                "speech.ready",
                {
                    "action_key": action.action_key,
                    "speaker_user_id": str(selected_user_id),
                    "side": side,
                    "seat_no": participant.seat_no,
                    "duration_ms": duration_ms,
                },
            ),
        )

    def _lock_experiment_selection_if_ready(self) -> tuple[MatchEvent, ...]:
        if self.state.selection_phase == "HUMAN_ONLY_WAIT":
            return ()
        if self.state.hand_window_open:
            return ()
        action = self.state.current_action
        side = self.state.free_holder_side
        if action is None or action.action_kind != "FREE_DEBATE" or side is None:
            raise MatchDomainError("match_state_conflict")
        valid_hands = tuple(
            user_id
            for user_id in self.state.hand_queue
            if self._free_human(user_id, side) is not None and self._free_remaining_ms(side) > 0
        )
        if valid_hands != self.state.hand_queue:
            self.state = replace(self.state, hand_queue=valid_hands)
        if self.state.hand_queue:
            return self._select_free_human(self.state.hand_queue[0], side)
        eligible = sorted(
            (item for item in self.state.agent_decisions if item.status == "HAND"),
            key=lambda item: self._agent_tie_break(item.agent_profile_id),
        )
        selected = eligible[self.state.match_seed % len(eligible)] if eligible else None
        if selected is not None:
            duration_ms = self._free_turn_duration_ms(action, side)
            self.state = replace(
                self.state,
                action_state="AGENT_PREPARING",
                current_speaker_user_id=None,
                current_agent_profile_id=selected.agent_profile_id,
                current_speaker_side=side,
                current_speaker_seat_no=selected.seat_no,
                speech_remaining_ms=duration_ms,
                agent_selection_mode="VOLUNTEER",
                selection_phase="ALLOCATED",
                agent_effective_status="RAISE",
            )
            return (
                self._event(
                    "free.selection_locked",
                    {
                        "decision_round_id": _uuid_text(self.state.agent_decision_round_id),
                        "opportunity_id": str(self.state.opportunity_id),
                        "speaker_kind": "AGENT",
                        "agent_profile_id": str(selected.agent_profile_id),
                        "agent_selection_mode": "VOLUNTEER",
                    },
                ),
                self._event(
                    "agent.preparing",
                    {
                        "action_key": action.action_key,
                        "agent_profile_id": str(selected.agent_profile_id),
                        "side": side,
                        "seat_no": selected.seat_no,
                        "duration_ms": duration_ms,
                    },
                ),
            )
        side_has_human = any(
            item.side == side and item.user_id is not None for item in action.participants
        )
        all_skipped = bool(self.state.agent_decisions) and all(
            item.status == "SKIP" and not item.failed for item in self.state.agent_decisions
        )
        if selected is None and not side_has_human and all_skipped:
            ordered = sorted(
                self.state.agent_decisions,
                key=lambda item: self._agent_tie_break(item.agent_profile_id),
            )
            selected = ordered[self.state.match_seed % len(ordered)]
            duration_ms = self._free_turn_duration_ms(action, side)
            self.state = replace(
                self.state,
                action_state="AGENT_PREPARING",
                current_speaker_user_id=None,
                current_agent_profile_id=selected.agent_profile_id,
                current_speaker_side=side,
                current_speaker_seat_no=selected.seat_no,
                speech_remaining_ms=duration_ms,
                agent_selection_mode="ALL_AGENT_SKIP_RANDOM",
                selection_phase="ALLOCATED",
                agent_effective_status="SKIP",
            )
            return (
                self._event(
                    "free.selection_locked",
                    {
                        "decision_round_id": _uuid_text(self.state.agent_decision_round_id),
                        "opportunity_id": str(self.state.opportunity_id),
                        "speaker_kind": "AGENT",
                        "agent_profile_id": str(selected.agent_profile_id),
                        "agent_selection_mode": "ALL_AGENT_SKIP_RANDOM",
                        "fallback_reason": "ALL_AGENT_DECISIONS_SKIPPED",
                    },
                ),
                self._event(
                    "agent.preparing",
                    {
                        "action_key": action.action_key,
                        "agent_profile_id": str(selected.agent_profile_id),
                        "side": side,
                        "seat_no": selected.seat_no,
                        "duration_ms": duration_ms,
                    },
                ),
            )
        decision_fact = self.state.agent_effective_status
        deadline = self._clock() + 60.0
        self.state = replace(
            self.state,
            action_state="FREE_SELECTING",
            selection_phase="HUMAN_ONLY_WAIT",
            agent_effective_status="SKIP",
            hand_window_open=True,
            human_wait_deadline_mono=deadline,
            human_wait_remaining_ms=60_000,
        )
        self._schedule_internal(
            60.0,
            "human.wait_timeout",
            f"internal:human-wait:{self.state.opportunity_id}:{self.state.opportunity_generation}",
        )
        return (
            self._event(
                "free.human_wait_started",
                {
                    "opportunity_id": str(self.state.opportunity_id),
                    "side": side,
                    "duration_ms": 60_000,
                    "agent_status": "SKIP",
                    "decision_fact": decision_fact,
                },
            ),
        )

    def _human_wait_timeout(self, _: MatchCommand) -> tuple[MatchEvent, ...]:
        if (
            not self.state.free_competition_enabled
            or self.state.action_state != "FREE_SELECTING"
            or self.state.selection_phase != "HUMAN_ONLY_WAIT"
            or self.state.human_wait_deadline_mono is None
        ):
            return ()
        remaining = self.state.human_wait_deadline_mono - self._clock()
        if remaining > 0:
            self._schedule_internal(
                remaining,
                "human.wait_timeout",
                f"internal:human-wait-recheck:{self.state.opportunity_id}",
            )
            return ()
        self._cancel_timer()
        self.state = replace(
            self.state,
            status="PAUSED",
            action_state="RECOVERY_REQUIRED",
            hand_window_open=False,
            human_wait_deadline_mono=None,
            human_wait_remaining_ms=0,
            paused_from_status="RUNNING",
            paused_from_action_state="FREE_SELECTING",
            pause_initiator_user_id=None,
            error_code="HUMAN_WAIT_TIMEOUT",
        )
        return (
            self._event(
                "match.paused",
                {
                    "reason": "HUMAN_WAIT_TIMEOUT",
                    "opportunity_id": str(self.state.opportunity_id),
                },
            ),
        )

    def _speech_start(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.action_state != "HUMAN_READY_TO_START":
            raise MatchDomainError("match_state_conflict")
        if command.actor_user_id != self.state.current_speaker_user_id:
            raise MatchDomainError("not_current_speaker")
        action = self.state.current_action
        if action is None:
            raise MatchDomainError("match_state_conflict")
        # A human turn paused while speaking is resumed from the same
        # authoritative speech identity.  Reset explicitly clears the ID and
        # therefore starts a new attempt; an ordinary first start allocates it.
        speech_id = self.state.current_speech_id or (
            _payload_uuid(command.payload.get("speech_id")) or uuid4()
        )
        duration_ms = self.state.speech_remaining_ms
        if duration_ms is None:
            duration_ms = action.duration_seconds * 1000
        deadline = self._clock() + duration_ms / 1000
        restarting_free_turn = action.action_kind == "FREE_DEBATE" and self.state.hand_window_open
        allocated_opportunity_id = self.state.opportunity_id or self.state.allocated_opportunity_id
        self.state = replace(
            self.state,
            action_state="HUMAN_SPEAKING",
            current_speech_id=speech_id,
            interim_text="",
            current_speaker_side=action.side or self.state.current_speaker_side,
            current_speaker_seat_no=action.seat_no or self.state.current_speaker_seat_no,
            speech_deadline_mono=deadline,
            human_start_deadline_mono=None,
            speech_remaining_ms=duration_ms,
            hand_queue=(
                self.state.hand_queue
                if restarting_free_turn or action.action_kind != "FREE_DEBATE"
                else ()
            ),
            agent_hand_queue=(
                () if action.action_kind == "FREE_DEBATE" else self.state.agent_hand_queue
            ),
            agent_selection_mode=(
                None if action.action_kind == "FREE_DEBATE" else self.state.agent_selection_mode
            ),
            agent_decision_round_id=(
                None if action.action_kind == "FREE_DEBATE" else self.state.agent_decision_round_id
            ),
            agent_decisions=(
                () if action.action_kind == "FREE_DEBATE" else self.state.agent_decisions
            ),
            hand_window_open=action.action_kind == "FREE_DEBATE",
            allocated_opportunity_id=(
                allocated_opportunity_id
                if self.state.free_competition_enabled and action.action_kind == "FREE_DEBATE"
                else self.state.allocated_opportunity_id
            ),
        )
        event = self._event(
            "speech.started",
            {
                "speech_id": str(speech_id),
                "speaker_user_id": str(command.actor_user_id),
                "duration_ms": duration_ms,
                "opportunity_id": (
                    str(allocated_opportunity_id)
                    if self.state.free_competition_enabled and allocated_opportunity_id is not None
                    else None
                ),
            },
        )
        self.state = replace(self.state, speech_start_sequence=event.sequence)
        self._schedule_internal(
            duration_ms / 1000,
            "speech.deadline",
            f"internal:speech-deadline:{speech_id}:{event.sequence}",
            payload={"speech_id": str(speech_id), "start_sequence": event.sequence},
        )
        if duration_ms <= 0:
            return (event, *self._finish_speech("TIME_LIMIT"))
        if action.action_kind != "FREE_DEBATE":
            return (event,)
        if self.state.free_competition_enabled:
            opposite_side = self._opposite_side(self.state.current_speaker_side)
            next_holder = (
                opposite_side
                if self._free_remaining_ms(opposite_side) >= FREE_MINIMUM_SPEECH_MS
                else str(self.state.current_speaker_side)
            )
            opened = self._open_experiment_opportunity(
                side=next_holder,
                trigger_kind=(
                    "SAME_SIDE_CONTINUATION"
                    if next_holder == self.state.current_speaker_side
                    else "HUMAN_SPEECH"
                ),
                source_speech_id=speech_id,
            )
            return (
                event,
                opened,
                self._event(
                    "hand.window_opened",
                    {"side": self.state.free_holder_side, "duration_ms": None},
                ),
            )
        return (
            event,
            self._event(
                "hand.window_opened",
                {"side": self._opposite_side(self.state.current_speaker_side), "duration_ms": None},
            ),
        )

    def _human_start_timeout(self, _: MatchCommand) -> tuple[MatchEvent, ...]:
        deadline = self.state.human_start_deadline_mono
        if (
            self.state.status != "RUNNING"
            or self.state.action_state != "HUMAN_READY_TO_START"
            or deadline is None
        ):
            return ()
        remaining_seconds = deadline - self._clock()
        if remaining_seconds > 0:
            self._schedule_internal(
                remaining_seconds,
                "human.start_timeout",
                f"internal:human-start-recheck:{self.state.sequence}",
            )
            return ()
        self._cancel_timer()
        self.state = replace(
            self.state,
            status="PAUSED",
            action_state="RECOVERY_REQUIRED",
            human_start_deadline_mono=None,
            error_code="HUMAN_START_TIMEOUT",
            paused_from_status="RUNNING",
            paused_from_action_state="HUMAN_READY_TO_START",
            pause_initiator_user_id=None,
        )
        return (self._event("match.paused", {"reason": "HUMAN_START_TIMEOUT"}),)

    def _agent_playback_started(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.action_state != "AGENT_PREPARING":
            raise MatchDomainError("match_state_conflict")
        action = self.state.current_action
        expected_agent_id = (
            self.state.current_agent_profile_id
            if action is not None and action.action_kind == "FREE_DEBATE"
            else action.agent_profile_id
            if action is not None
            else None
        )
        if action is None or expected_agent_id is None:
            raise MatchDomainError("match_state_conflict")
        if str(command.payload.get("agent_profile_id")) != str(expected_agent_id):
            raise MatchDomainError("stale_callback")
        speech_id = _payload_uuid(command.payload.get("speech_id"), required=True)
        assert speech_id is not None
        duration_ms = self.state.speech_remaining_ms
        if duration_ms is None:
            duration_ms = action.duration_seconds * 1000
        restarting_free_turn = action.action_kind == "FREE_DEBATE" and self.state.hand_window_open
        allocated_opportunity_id = self.state.opportunity_id or self.state.allocated_opportunity_id
        self.state = replace(
            self.state,
            action_state="AGENT_SPEAKING",
            current_speech_id=speech_id,
            interim_text="",
            current_speaker_side=action.side or self.state.current_speaker_side,
            current_speaker_seat_no=action.seat_no or self.state.current_speaker_seat_no,
            speech_deadline_mono=self._clock() + duration_ms / 1000,
            speech_remaining_ms=duration_ms,
            hand_queue=(
                self.state.hand_queue
                if restarting_free_turn or action.action_kind != "FREE_DEBATE"
                else ()
            ),
            agent_hand_queue=(
                () if action.action_kind == "FREE_DEBATE" else self.state.agent_hand_queue
            ),
            agent_selection_mode=(
                None if action.action_kind == "FREE_DEBATE" else self.state.agent_selection_mode
            ),
            agent_decision_round_id=(
                None if action.action_kind == "FREE_DEBATE" else self.state.agent_decision_round_id
            ),
            agent_decisions=(
                () if action.action_kind == "FREE_DEBATE" else self.state.agent_decisions
            ),
            hand_window_open=action.action_kind == "FREE_DEBATE",
            allocated_opportunity_id=(
                allocated_opportunity_id
                if self.state.free_competition_enabled and action.action_kind == "FREE_DEBATE"
                else self.state.allocated_opportunity_id
            ),
        )
        event = self._event(
            "agent.playback_started",
            {
                **dict(command.payload),
                "speech_id": str(speech_id),
                "duration_ms": duration_ms,
                "opportunity_id": (
                    str(allocated_opportunity_id)
                    if self.state.free_competition_enabled and allocated_opportunity_id is not None
                    else None
                ),
            },
        )
        self.state = replace(self.state, speech_start_sequence=event.sequence)
        self._schedule_internal(
            duration_ms / 1000,
            "speech.deadline",
            f"internal:agent-deadline:{speech_id}:{event.sequence}",
            payload={"speech_id": str(speech_id), "start_sequence": event.sequence},
        )
        if action.action_kind != "FREE_DEBATE":
            return (event,)
        if self.state.free_competition_enabled:
            opposite_side = self._opposite_side(self.state.current_speaker_side)
            next_holder = (
                opposite_side
                if self._free_remaining_ms(opposite_side) >= FREE_MINIMUM_SPEECH_MS
                else str(self.state.current_speaker_side)
            )
            opened = self._open_experiment_opportunity(
                side=next_holder,
                trigger_kind=(
                    "SAME_SIDE_CONTINUATION"
                    if next_holder == self.state.current_speaker_side
                    else "AGENT_SPEECH"
                ),
                source_speech_id=speech_id,
            )
            return (
                event,
                opened,
                self._event(
                    "hand.window_opened",
                    {"side": self.state.free_holder_side, "duration_ms": None},
                ),
            )
        return (
            event,
            self._event(
                "hand.window_opened",
                {"side": self._opposite_side(self.state.current_speaker_side), "duration_ms": None},
            ),
        )

    def _speech_finish(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.action_state != "HUMAN_SPEAKING":
            raise MatchDomainError("match_state_conflict")
        if command.actor_user_id != self.state.current_speaker_user_id:
            raise MatchDomainError("not_current_speaker")
        return self._finish_speech("EARLY")

    def _speech_deadline(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        expected_sequence = self.state.speech_start_sequence
        if (
            command.payload
            and expected_sequence is not None
            and (
                str(command.payload.get("speech_id")) != str(self.state.current_speech_id)
                or command.payload.get("start_sequence") != expected_sequence
            )
        ):
            return ()
        if self.state.action_state == "AGENT_SPEAKING":
            return self._finish_agent_speech("TIME_LIMIT")
        if self.state.action_state != "HUMAN_SPEAKING":
            return ()
        return self._finish_speech("TIME_LIMIT")

    def _agent_playback_finished(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.action_state != "AGENT_SPEAKING":
            raise MatchDomainError("match_state_conflict")
        speech_id = self.state.current_speech_id
        if speech_id is None or str(command.payload.get("speech_id")) != str(speech_id):
            raise MatchDomainError("stale_callback")
        return self._finish_agent_speech("COMPLETED")

    def _finish_agent_speech(self, reason: str) -> tuple[MatchEvent, ...]:
        self._cancel_timer()
        self.state = replace(
            self.state,
            action_state="AGENT_FINALIZING",
            speech_start_sequence=None,
            speech_deadline_mono=None,
        )
        event = self._event(
            "agent.finalizing",
            {"speech_id": str(self.state.current_speech_id), "reason": reason},
        )
        action = self.state.current_action
        if (
            self.state.free_competition_enabled
            and action is not None
            and action.action_kind == "FREE_DEBATE"
            and self.state.opportunity_id is not None
        ):
            self.state = replace(
                self.state,
                selection_deadline_mono=self._clock() + 3.0,
                selection_remaining_ms=3_000,
            )
            self._schedule_internal(
                3.0,
                "hand.window_closed",
                f"internal:free-agent-end:{self.state.current_speech_id}",
            )
            return (
                event,
                self._event(
                    "hand.window_opened",
                    {"side": self.state.free_holder_side, "duration_ms": 3000},
                ),
            )
        return (event,)

    def _agent_finalized(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.action_state != "AGENT_FINALIZING":
            raise MatchDomainError("match_state_conflict")
        speech_id = self.state.current_speech_id
        if speech_id is None or str(command.payload.get("speech_id")) != str(speech_id):
            raise MatchDomainError("stale_callback")
        if (
            self.state.current_action is not None
            and self.state.current_action.action_kind == "FREE_DEBATE"
        ):
            return self._finish_free_turn("agent.finalized", dict(command.payload))
        return self._advance_action("agent.finalized", dict(command.payload))

    def _finish_speech(self, reason: str) -> tuple[MatchEvent, ...]:
        self._cancel_timer()
        speech_id = self.state.current_speech_id
        self.state = replace(
            self.state,
            action_state="SPEECH_FINALIZING",
            speech_start_sequence=None,
            speech_deadline_mono=None,
        )
        event = self._event(
            "speech.finalizing",
            {"speech_id": str(speech_id), "reason": reason},
        )
        action = self.state.current_action
        if (
            self.state.free_competition_enabled
            and action is not None
            and action.action_kind == "FREE_DEBATE"
            and self.state.opportunity_id is not None
        ):
            self.state = replace(
                self.state,
                selection_deadline_mono=self._clock() + 3.0,
                selection_remaining_ms=3_000,
            )
            self._schedule_internal(
                3.0,
                "hand.window_closed",
                f"internal:free-human-end:{speech_id}",
            )
            return (
                event,
                self._event(
                    "hand.window_opened",
                    {"side": self.state.free_holder_side, "duration_ms": 3000},
                ),
            )
        return (event,)

    def _asr_finalized(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.action_state != "SPEECH_FINALIZING":
            raise MatchDomainError("match_state_conflict")
        speech_id = self.state.current_speech_id
        if speech_id is None or str(command.payload.get("speech_id")) != str(speech_id):
            raise MatchDomainError("stale_callback")
        if (
            self.state.current_action is not None
            and self.state.current_action.action_kind == "FREE_DEBATE"
        ):
            return self._finish_free_turn("speech.finished", dict(command.payload))
        return self._advance_action("speech.finished", dict(command.payload))

    def _finish_free_turn(
        self, event_type: str, payload: Mapping[str, Any]
    ) -> tuple[MatchEvent, ...]:
        action = self.state.current_action
        side = self.state.current_speaker_side
        completed_opportunity_id = (
            self.state.allocated_opportunity_id
            if self.state.free_competition_enabled
            else self.state.opportunity_id
        )
        source_speech_id = self.state.current_speech_id
        existing_selection_deadline = self.state.selection_deadline_mono
        if action is None or action.action_kind != "FREE_DEBATE" or side is None:
            raise MatchDomainError("match_state_conflict")
        consumed_ms = int(payload.get("audio_duration_ms", 0))
        if consumed_ms <= 0:
            consumed_ms = self.state.speech_remaining_ms or 0
        consumed_ms = min(consumed_ms, self._free_remaining_ms(side))
        remaining = max(0, self._free_remaining_ms(side) - consumed_ms)
        if remaining < FREE_MINIMUM_SPEECH_MS:
            remaining = 0
        next_side = self._opposite_side(side)
        next_remaining = self._free_remaining_ms(next_side)
        if next_remaining < FREE_MINIMUM_SPEECH_MS:
            next_remaining = 0
        if next_remaining <= 0 and remaining <= 0:
            self._cancel_timer()
            finished = self._event(
                event_type,
                {
                    **dict(payload),
                    "opportunity_id": str(completed_opportunity_id),
                },
            )
            self.state = replace(
                self.state,
                status="FINISHED",
                action_state="MATCH_FINISHED",
                current_speech_id=None,
                interim_text="",
                current_speaker_user_id=None,
                current_agent_profile_id=None,
                current_speaker_side=None,
                current_speaker_seat_no=None,
                speech_remaining_ms=None,
                human_start_deadline_mono=None,
                free_affirmative_remaining_ms=(
                    remaining if side == "AFFIRMATIVE" else next_remaining
                ),
                free_negative_remaining_ms=(remaining if side == "NEGATIVE" else next_remaining),
                hand_window_open=False,
                agent_hand_queue=(),
                agent_selection_mode=None,
                agent_decision_round_id=None,
                agent_decisions=(),
            )
            return finished, self._event("match.finished", {})
        self._cancel_timer()
        self.state = replace(
            self.state,
            action_state="FREE_SELECTING",
            current_speech_id=None,
            interim_text="",
            current_speaker_user_id=None,
            current_agent_profile_id=None,
            current_speaker_side=None,
            current_speaker_seat_no=None,
            speech_deadline_mono=None,
            human_start_deadline_mono=None,
            speech_remaining_ms=None,
            host_audio_remaining_ms=None,
            host_audio_deadline_mono=None,
            host_audio_deadline_ms=None,
            free_affirmative_remaining_ms=(remaining if side == "AFFIRMATIVE" else next_remaining),
            free_negative_remaining_ms=(remaining if side == "NEGATIVE" else next_remaining),
            free_holder_side=(next_side if next_remaining > 0 else side),
            hand_window_open=True,
            hand_queue=tuple(
                user_id
                for user_id in self.state.hand_queue
                if self._free_human(user_id, next_side if next_remaining > 0 else side) is not None
            ),
            agent_hand_queue=(
                self.state.agent_hand_queue if self.state.free_competition_enabled else ()
            ),
            agent_selection_mode=None,
            agent_decision_round_id=(
                self.state.agent_decision_round_id if self.state.free_competition_enabled else None
            ),
            agent_decisions=(
                self.state.agent_decisions if self.state.free_competition_enabled else ()
            ),
            allocated_opportunity_id=None,
            selection_deadline_mono=(
                existing_selection_deadline or self._clock() + 3.0
                if self.state.free_competition_enabled
                else self.state.selection_deadline_mono
            ),
            selection_remaining_ms=(
                max(
                    0,
                    int(
                        ((existing_selection_deadline or self._clock() + 3.0) - self._clock())
                        * 1000
                    ),
                )
                if self.state.free_competition_enabled
                else self.state.selection_remaining_ms
            ),
        )
        completed = self._event(
            event_type,
            {**dict(payload), "opportunity_id": str(completed_opportunity_id)},
        )
        opened = self._event(
            "hand.window_opened",
            {"side": self.state.free_holder_side, "duration_ms": 3000},
        )
        if self.state.free_competition_enabled:
            delay = max(
                0.0,
                (existing_selection_deadline or self._clock() + 3.0) - self._clock(),
            )
            self._schedule_internal(
                delay, "hand.window_closed", f"internal:free:{self.state.sequence}"
            )
            return completed, opened
        trigger_kind: Literal["HUMAN_SPEECH", "AGENT_SPEECH", "SAME_SIDE_CONTINUATION"] = (
            "SAME_SIDE_CONTINUATION"
            if self.state.free_holder_side == side
            else "AGENT_SPEECH"
            if event_type == "agent.finalized"
            else "HUMAN_SPEECH"
        )
        decision_started = self._start_free_agent_decisions(
            str(self.state.free_holder_side),
            trigger_kind=trigger_kind,
            source_speech_id=source_speech_id,
        )
        self._schedule_internal(3.0, "hand.window_closed", f"internal:free:{self.state.sequence}")
        return completed, opened, decision_started

    def _speech_reset(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.action_state not in (
            "HUMAN_SPEAKING",
            "SPEECH_FINALIZING",
            "AGENT_PREPARING",
            "AGENT_SPEAKING",
            "AGENT_FINALIZING",
        ):
            raise MatchDomainError("match_state_conflict")
        if command.actor_user_id != self.state.current_speaker_user_id and not bool(
            command.payload.get("privileged")
        ):
            raise MatchDomainError("forbidden")
        self._cancel_timer()
        action = self.state.current_action
        if action is None:
            raise MatchDomainError("match_state_conflict")
        free_action = action.action_kind == "FREE_DEBATE"
        agent_action = action.action_kind == "AGENT_SPEECH" or (
            free_action and self.state.current_agent_profile_id is not None
        )
        speaker_user_id = (
            None
            if agent_action
            else self.state.current_speaker_user_id
            if free_action
            else action.speaker_user_id
        )
        agent_profile_id = (
            self.state.current_agent_profile_id
            if free_action and agent_action
            else action.agent_profile_id
            if agent_action
            else None
        )
        side = self.state.current_speaker_side if free_action else action.side
        seat_no = self.state.current_speaker_seat_no if free_action else action.seat_no
        if free_action:
            if side is None or seat_no is None:
                raise MatchDomainError("match_state_conflict")
            if agent_action and agent_profile_id is None:
                raise MatchDomainError("match_state_conflict")
            if not agent_action and speaker_user_id is None:
                raise MatchDomainError("match_state_conflict")
            duration_ms = self._free_turn_duration_ms(action, side)
        else:
            duration_ms = action.duration_seconds * 1000
        invalidated_opportunity_id = (
            self.state.opportunity_id
            if free_action and self.state.free_competition_enabled
            else None
        )
        self.state = replace(
            self.state,
            action_state="AGENT_PREPARING" if agent_action else "HUMAN_READY_TO_START",
            current_speech_id=None,
            speech_start_sequence=None,
            current_speaker_user_id=speaker_user_id,
            current_agent_profile_id=agent_profile_id,
            current_speaker_side=side,
            current_speaker_seat_no=seat_no,
            speech_deadline_mono=None,
            human_start_deadline_mono=None,
            speech_remaining_ms=duration_ms,
            opportunity_id=(
                None if invalidated_opportunity_id is not None else self.state.opportunity_id
            ),
            selection_phase=(
                None if invalidated_opportunity_id is not None else self.state.selection_phase
            ),
            agent_effective_status=(
                None
                if invalidated_opportunity_id is not None
                else self.state.agent_effective_status
            ),
            selection_deadline_mono=None,
            selection_remaining_ms=None,
            human_wait_deadline_mono=None,
            human_wait_remaining_ms=None,
            hand_queue=() if invalidated_opportunity_id is not None else self.state.hand_queue,
            agent_hand_queue=(
                () if invalidated_opportunity_id is not None else self.state.agent_hand_queue
            ),
            agent_decision_round_id=(
                None
                if invalidated_opportunity_id is not None
                else self.state.agent_decision_round_id
            ),
            agent_decisions=(
                () if invalidated_opportunity_id is not None else self.state.agent_decisions
            ),
            hand_window_open=(
                False if invalidated_opportunity_id is not None else self.state.hand_window_open
            ),
        )
        if not agent_action:
            self._schedule_human_start_timeout()
        payload: dict[str, Any] = {
            "action_key": action.action_key,
            "duration_ms": duration_ms,
        }
        if speaker_user_id is not None:
            payload["speaker_user_id"] = str(speaker_user_id)
        if agent_profile_id is not None:
            payload["agent_profile_id"] = str(agent_profile_id)
        if side is not None:
            payload["side"] = side
        if seat_no is not None:
            payload["seat_no"] = seat_no
        ready = self._event(
            "agent.preparing" if agent_action else "speech.ready",
            payload,
        )
        if invalidated_opportunity_id is not None:
            return (
                self._event(
                    "free.opportunity_invalidated",
                    {
                        "opportunity_id": str(invalidated_opportunity_id),
                        "reason": "SPEECH_RESET",
                    },
                ),
                ready,
            )
        return (ready,)

    def _advance_action(
        self, event_type: str, payload: Mapping[str, Any]
    ) -> tuple[MatchEvent, ...]:
        finished = self._event(event_type, payload)
        self.state = replace(
            self.state,
            action_state="ACTION_FINISHED",
            current_action_index=self.state.current_action_index + 1,
            current_speech_id=None,
            interim_text="",
            current_speaker_user_id=None,
            current_agent_profile_id=None,
            speech_deadline_mono=None,
            human_start_deadline_mono=None,
            speech_remaining_ms=None,
            host_audio_remaining_ms=None,
            host_audio_deadline_mono=None,
            host_audio_deadline_ms=None,
        )
        return (finished, *self._enter_current_action())

    def _match_terminate(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if not bool(command.payload.get("privileged")):
            raise MatchDomainError("forbidden")
        self._cancel_timer()
        self.state = replace(
            self.state,
            status="TERMINATED",
            action_state="MATCH_FINISHED",
            current_speech_id=None,
            interim_text="",
            human_start_deadline_mono=None,
        )
        return (self._event("match.terminated", {}),)

    def _match_pause(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.status != "RUNNING" or not bool(command.payload.get("authorized")):
            raise MatchDomainError("forbidden")
        remaining_ms = self.state.speech_remaining_ms
        if self.state.speech_deadline_mono is not None:
            remaining_ms = max(0, int((self.state.speech_deadline_mono - self._clock()) * 1000))
        host_remaining_ms = self.state.host_audio_remaining_ms
        if self.state.host_audio_deadline_mono is not None:
            host_remaining_ms = max(
                0, int((self.state.host_audio_deadline_mono - self._clock()) * 1000)
            )
        selection_remaining_ms = self.state.selection_remaining_ms
        if self.state.selection_deadline_mono is not None:
            selection_remaining_ms = max(
                0, int((self.state.selection_deadline_mono - self._clock()) * 1000)
            )
        human_wait_remaining_ms = self.state.human_wait_remaining_ms
        if self.state.human_wait_deadline_mono is not None:
            human_wait_remaining_ms = max(
                0, int((self.state.human_wait_deadline_mono - self._clock()) * 1000)
            )
        self._cancel_timer()
        previous_action_state = self.state.action_state
        invalidate_speech_opportunity = (
            self.state.free_competition_enabled
            and self.state.opportunity_id is not None
            and self.state.selection_deadline_mono is None
            and previous_action_state in {"HUMAN_SPEAKING", "AGENT_SPEAKING"}
        )
        invalidated_opportunity_id = (
            self.state.opportunity_id if invalidate_speech_opportunity else None
        )
        self.state = replace(
            self.state,
            status="PAUSED",
            action_state="RECOVERY_REQUIRED",
            speech_deadline_mono=None,
            human_start_deadline_mono=None,
            speech_remaining_ms=remaining_ms,
            host_audio_remaining_ms=host_remaining_ms,
            host_audio_deadline_mono=None,
            host_audio_deadline_ms=None,
            selection_deadline_mono=None,
            selection_remaining_ms=selection_remaining_ms,
            human_wait_deadline_mono=None,
            human_wait_remaining_ms=human_wait_remaining_ms,
            paused_from_status="RUNNING",
            paused_from_action_state=previous_action_state,
            pause_initiator_user_id=command.actor_user_id,
            opportunity_id=(None if invalidate_speech_opportunity else self.state.opportunity_id),
            selection_phase=(None if invalidate_speech_opportunity else self.state.selection_phase),
            agent_effective_status=(
                None if invalidate_speech_opportunity else self.state.agent_effective_status
            ),
            hand_queue=() if invalidate_speech_opportunity else self.state.hand_queue,
            agent_hand_queue=(() if invalidate_speech_opportunity else self.state.agent_hand_queue),
            agent_decision_round_id=(
                None if invalidate_speech_opportunity else self.state.agent_decision_round_id
            ),
            agent_decisions=(() if invalidate_speech_opportunity else self.state.agent_decisions),
            hand_window_open=(
                False if invalidate_speech_opportunity else self.state.hand_window_open
            ),
        )
        paused = self._event(
            "match.paused",
            {
                "initiator_user_id": (
                    str(command.actor_user_id) if command.actor_user_id else None
                ),
                "reason": str(command.payload.get("reason", "MANUAL")),
            },
        )
        if invalidated_opportunity_id is not None:
            return (
                self._event(
                    "free.opportunity_invalidated",
                    {
                        "opportunity_id": str(invalidated_opportunity_id),
                        "reason": "SPEECH_INTERRUPTED",
                    },
                ),
                paused,
            )
        return (paused,)

    def _match_resume(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.status not in ("PAUSED", "SYSTEM_RECOVERY", "ERROR"):
            raise MatchDomainError("match_state_conflict")
        can_resume = bool(command.payload.get("privileged")) or (
            command.actor_user_id is not None
            and command.actor_user_id == self.state.pause_initiator_user_id
        )
        if not can_resume:
            raise MatchDomainError("forbidden")
        reasons_value = command.payload.get("reasons", [])
        reasons: list[Any] = (
            cast(list[Any], reasons_value) if isinstance(reasons_value, list) else []
        )
        if reasons:
            return (
                self._event(
                    "match.resume_check_failed",
                    {"reasons": [str(item) for item in reasons]},
                ),
            )
        self.state = replace(
            self.state,
            status="PAUSED",
            action_state="RESUME_COUNTDOWN",
            error_code=None,
            offline_user_id=None,
        )
        event = self._event("match.resume_countdown", {"duration_ms": 3000})
        self._schedule_internal(3.0, "resume.elapsed", f"internal:resume:{self.state.sequence}")
        return (event,)

    def _resume_elapsed(self, _: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.status != "PAUSED" or self.state.action_state != "RESUME_COUNTDOWN":
            return ()
        restored = self.state.paused_from_action_state or "HUMAN_READY_TO_START"
        was_agent = restored in ("AGENT_SPEAKING", "AGENT_FINALIZING", "AGENT_PREPARING")
        if (
            self.state.free_competition_enabled
            and restored in ("SPEECH_FINALIZING", "AGENT_FINALIZING")
            and self.state.selection_remaining_ms is not None
        ):
            restored = "FREE_SELECTING"
            was_agent = False
        elif restored in ("HUMAN_SPEAKING", "SPEECH_FINALIZING"):
            restored = "HUMAN_READY_TO_START"
        elif restored in ("AGENT_SPEAKING", "AGENT_FINALIZING"):
            restored = "AGENT_PREPARING"
            self.state = replace(self.state, current_speech_id=None, interim_text="")
        self.state = replace(
            self.state,
            status="RUNNING",
            action_state=restored,
            speech_deadline_mono=None,
            human_start_deadline_mono=None,
            host_audio_deadline_mono=None,
            host_audio_deadline_ms=None,
            paused_from_status=None,
            paused_from_action_state=None,
            pause_initiator_user_id=None,
        )
        resumed = self._event("match.resumed", {})
        action = self.state.current_action
        if restored == "NOT_STARTED":
            # A process restart can recover during the initial countdown. Once
            # an operator resumes, enter the current action instead of leaving
            # the match in RUNNING/NOT_STARTED with no timer or transition.
            return resumed, *self._enter_current_action()
        if restored == "HUMAN_READY_TO_START":
            self._schedule_human_start_timeout()
            return (resumed,)
        if restored == "HOST_ANNOUNCING" and self.state.host_audio_remaining_ms is not None:
            remaining = self.state.host_audio_remaining_ms
            self.state = replace(
                self.state,
                host_audio_deadline_mono=self._clock() + remaining / 1000,
                host_audio_deadline_ms=int(self._wall_clock() * 1000) + remaining,
            )
            self._schedule_internal(
                remaining / 1000, "host.elapsed", f"internal:host-resume:{self.state.sequence}"
            )
            return (resumed,)
        if restored == "PREPARING" and self.state.speech_remaining_ms is not None:
            remaining = self.state.speech_remaining_ms
            self.state = replace(
                self.state,
                speech_deadline_mono=self._clock() + remaining / 1000,
            )
            self._schedule_internal(
                remaining / 1000,
                "preparation.elapsed",
                f"internal:preparation-resume:{self.state.sequence}",
            )
            return (resumed,)
        if restored == "FREE_SELECTING" and action is not None:
            if self.state.free_competition_enabled:
                if self.state.selection_phase == "HUMAN_ONLY_WAIT":
                    self.state = replace(
                        self.state,
                        hand_window_open=True,
                        agent_effective_status="SKIP",
                        human_wait_deadline_mono=self._clock() + 60.0,
                        human_wait_remaining_ms=60_000,
                    )
                    self._schedule_internal(
                        60.0,
                        "human.wait_timeout",
                        f"internal:human-wait-resumed:{self.state.opportunity_id}",
                    )
                    return (
                        resumed,
                        self._event(
                            "free.human_wait_started",
                            {
                                "opportunity_id": str(self.state.opportunity_id),
                                "side": self.state.free_holder_side,
                                "duration_ms": 60_000,
                                "agent_status": "SKIP",
                                "resumed": True,
                            },
                        ),
                    )
                remaining = max(0, int(self.state.selection_remaining_ms or 0))
                self.state = replace(
                    self.state,
                    hand_window_open=True,
                    selection_phase="COMPETING",
                    selection_deadline_mono=self._clock() + remaining / 1000,
                )
                self._schedule_internal(
                    remaining / 1000,
                    "hand.window_closed",
                    f"internal:free-window-resumed:{self.state.opportunity_id}",
                )
                return (
                    resumed,
                    self._event(
                        "hand.window_opened",
                        {
                            "side": self.state.free_holder_side,
                            "duration_ms": remaining,
                            "resumed": True,
                        },
                    ),
                )
            self.state = replace(
                self.state,
                hand_queue=(),
                agent_hand_queue=(),
                agent_selection_mode=None,
                agent_decision_round_id=None,
                agent_decisions=(),
                hand_window_open=True,
            )
            opened = self._event(
                "hand.window_opened",
                {"side": self.state.free_holder_side, "duration_ms": 3000},
            )
            decision_started = self._start_free_agent_decisions(str(self.state.free_holder_side))
            self._schedule_internal(
                3.0,
                "hand.window_closed",
                f"internal:free-resumed:{self.state.sequence}",
            )
            return resumed, opened, decision_started
        agent_profile_id = self.state.current_agent_profile_id or (
            action.agent_profile_id if action is not None else None
        )
        if was_agent and action is not None and agent_profile_id is not None:
            return (
                resumed,
                self._event(
                    "agent.preparing",
                    {
                        "action_key": action.action_key,
                        "agent_profile_id": str(agent_profile_id),
                        "side": self.state.current_speaker_side,
                        "duration_ms": (
                            self.state.speech_remaining_ms
                            if self.state.speech_remaining_ms is not None
                            else action.duration_seconds * 1000
                        ),
                    },
                ),
            )
        return (resumed,)

    def _member_offline(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if command.actor_user_id is None:
            raise MatchDomainError("forbidden")
        epoch_value = command.payload.get("connection_epoch")
        if epoch_value is not None:
            try:
                epoch = int(epoch_value)
            except (TypeError, ValueError):
                raise MatchDomainError("validation_error") from None
            known_epoch = self._connection_epochs.get(command.actor_user_id)
            if known_epoch is not None and epoch < known_epoch:
                return ()
            self._connection_epochs[command.actor_user_id] = max(epoch, known_epoch or epoch)
        if command.actor_user_id in self._offline_users:
            self.state = replace(
                self.state,
                connection_epochs=tuple(
                    sorted(self._connection_epochs.items(), key=lambda item: str(item[0]))
                ),
            )
            return ()
        raw_offline_since_ms = command.payload.get("offline_since_ms")
        try:
            offline_since_ms = (
                int(raw_offline_since_ms)
                if isinstance(raw_offline_since_ms, (int, float, str))
                else 0
            )
        except (TypeError, ValueError):
            offline_since_ms = 0
        if offline_since_ms <= 0:
            # Older clients did not send a timestamp.  Never interpret that
            # compatibility payload as an epoch beginning at Unix time zero.
            offline_since_ms = int(self._wall_clock() * 1000)
        offline_since = dict(self.state.offline_since_ms)
        offline_since[command.actor_user_id] = offline_since_ms
        self.state = replace(
            self.state,
            offline_user_id=command.actor_user_id,
            offline_since_ms=tuple(sorted(offline_since.items(), key=lambda item: str(item[0]))),
            connection_epochs=tuple(
                sorted(self._connection_epochs.items(), key=lambda item: str(item[0]))
            ),
        )
        if (
            self.state.action_state == "HUMAN_SPEAKING"
            and self.state.current_speaker_user_id == command.actor_user_id
        ):
            current_view = self.view()
            self._cancel_timer()
            self.state = replace(
                self.state,
                speech_deadline_mono=None,
                speech_remaining_ms=current_view.speech_remaining_ms,
                free_affirmative_remaining_ms=current_view.free_affirmative_remaining_ms,
                free_negative_remaining_ms=current_view.free_negative_remaining_ms,
            )
        return (
            self._event(
                "match.offline",
                {
                    "user_id": str(command.actor_user_id),
                    "grace_ms": 60_000,
                    "offline_since_ms": offline_since_ms,
                    "connection_epoch": command.payload.get("connection_epoch"),
                },
            ),
        )

    def _member_online(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        user_id = command.actor_user_id
        if user_id is None:
            raise MatchDomainError("forbidden")
        epoch_value = command.payload.get("connection_epoch")
        if epoch_value is not None:
            try:
                epoch = int(epoch_value)
            except (TypeError, ValueError):
                raise MatchDomainError("validation_error") from None
            known_epoch = self._connection_epochs.get(user_id)
            if known_epoch is not None and epoch < known_epoch:
                return ()
            self._connection_epochs[user_id] = max(epoch, known_epoch or epoch)
        if user_id not in self._offline_users and self.state.offline_user_id != user_id:
            self.state = replace(
                self.state,
                connection_epochs=tuple(
                    sorted(self._connection_epochs.items(), key=lambda item: str(item[0]))
                ),
            )
            return ()
        remaining_offline = self._offline_users - {user_id}
        offline_since = dict(self.state.offline_since_ms)
        offline_since.pop(user_id, None)
        self.state = replace(
            self.state,
            offline_user_id=next(iter(remaining_offline), None),
            offline_since_ms=tuple(sorted(offline_since.items(), key=lambda item: str(item[0]))),
            connection_epochs=tuple(
                sorted(self._connection_epochs.items(), key=lambda item: str(item[0]))
            ),
        )
        if (
            self.state.status == "RUNNING"
            and self.state.action_state == "HUMAN_SPEAKING"
            and self.state.current_speaker_user_id == user_id
            and self.state.speech_remaining_ms is not None
        ):
            remaining_seconds = max(0.0, self.state.speech_remaining_ms / 1000)
            self.state = replace(
                self.state,
                speech_deadline_mono=self._clock() + remaining_seconds,
            )
            self._schedule_internal(
                remaining_seconds,
                "speech.deadline",
                f"internal:speech-reconnected:{self.state.current_speech_id}",
            )
        return (self._event("match.online", {"user_id": str(user_id)}),)

    def _offline_expired(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        user_id_value = command.payload.get("user_id")
        user_id = UUID(str(user_id_value)) if user_id_value else self.state.offline_user_id
        if user_id is None or user_id not in self._offline_users or self.state.status != "RUNNING":
            return ()
        offline_since = dict(self.state.offline_since_ms).get(user_id)
        elapsed_ms = (
            self._wall_clock() * 1000 - offline_since if offline_since is not None else None
        )
        if elapsed_ms is not None and elapsed_ms < 60_000:
            remaining_seconds = max(0.01, 60 - elapsed_ms / 1000)
            self._schedule_offline_expiry(user_id, remaining_seconds)
            return ()
        current_view = self.view()
        self._cancel_timer()
        previous_action_state = self.state.action_state
        self.state = replace(
            self.state,
            status="PAUSED",
            action_state="RECOVERY_REQUIRED",
            paused_from_status="RUNNING",
            paused_from_action_state=previous_action_state,
            pause_initiator_user_id=None,
            offline_since_ms=tuple(
                (key, value) for key, value in self.state.offline_since_ms if key != user_id
            ),
            speech_remaining_ms=current_view.speech_remaining_ms,
            human_start_deadline_mono=None,
            host_audio_remaining_ms=current_view.host_audio_remaining_ms,
            host_audio_deadline_mono=None,
            host_audio_deadline_ms=None,
            free_affirmative_remaining_ms=current_view.free_affirmative_remaining_ms,
            free_negative_remaining_ms=current_view.free_negative_remaining_ms,
        )
        return (
            self._event(
                "match.paused",
                {
                    "reason": "PLAYER_OFFLINE_TIMEOUT",
                    "offline_user_id": str(user_id),
                },
            ),
        )

    def _system_recover(self, _: MatchCommand) -> tuple[MatchEvent, ...]:
        current_view = self.view()
        self._cancel_timer()
        current = self.state.current_action
        restart_action_state = self.state.action_state
        # FREE_SELECTING is a queue window, not an in-progress human turn.
        # Never infer a speaker from stale current_speaker_* fields during
        # restart; doing so resurrects the previous human and makes the UI
        # demand that user speak again.
        recovering_free_selection = (
            self.state.free_competition_enabled
            and current is not None
            and current.action_kind == "FREE_DEBATE"
            and self.state.action_state == "FREE_SELECTING"
        )
        if recovering_free_selection:
            restart_action_state = "FREE_SELECTING"
        elif self.state.action_state == "HOST_ANNOUNCING":
            restart_action_state = "HOST_ANNOUNCING"
        elif self.state.current_agent_profile_id is not None or (
            current is not None and current.agent_profile_id is not None
        ):
            restart_action_state = "AGENT_PREPARING"
        elif self.state.current_speaker_user_id is not None or (
            current is not None and current.speaker_user_id is not None
        ):
            restart_action_state = "HUMAN_READY_TO_START"
        self.state = replace(
            self.state,
            status="SYSTEM_RECOVERY",
            action_state="RECOVERY_REQUIRED",
            current_speech_id=None,
            speech_start_sequence=None,
            interim_text="",
            current_speaker_user_id=(
                None
                if recovering_free_selection
                else current.speaker_user_id
                if current is not None and current.speaker_user_id is not None
                else self.state.current_speaker_user_id
            ),
            current_agent_profile_id=(
                None
                if recovering_free_selection
                else current.agent_profile_id
                if current is not None and current.agent_profile_id is not None
                else self.state.current_agent_profile_id
            ),
            current_speaker_side=(
                None if recovering_free_selection else self.state.current_speaker_side
            ),
            current_speaker_seat_no=(
                None if recovering_free_selection else self.state.current_speaker_seat_no
            ),
            speech_deadline_mono=None,
            human_start_deadline_mono=None,
            speech_remaining_ms=(
                None
                if recovering_free_selection
                else current.duration_seconds * 1000
                if current
                else None
            ),
            host_audio_remaining_ms=current_view.host_audio_remaining_ms,
            host_audio_deadline_mono=None,
            host_audio_deadline_ms=None,
            paused_from_status="RUNNING",
            paused_from_action_state=restart_action_state,
        )
        return (self._event("match.system_recovery", {}),)

    def _system_error(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        current_view = self.view()
        self._cancel_timer()
        asr_failure = bool(command.payload.get("asr_failure"))
        action = self.state.current_action
        invalidated_opportunity_id = (
            self.state.opportunity_id
            if asr_failure
            and self.state.free_competition_enabled
            and action is not None
            and action.action_kind == "FREE_DEBATE"
            else None
        )
        restart_action_state = self.state.action_state
        if self.state.action_state == "HOST_ANNOUNCING":
            restart_action_state = "HOST_ANNOUNCING"
        elif self.state.current_agent_profile_id is not None:
            restart_action_state = "AGENT_PREPARING"
        elif self.state.current_speaker_user_id is not None:
            restart_action_state = "HUMAN_READY_TO_START"
        retry_duration_ms = current_view.speech_remaining_ms
        if asr_failure and action is not None:
            retry_duration_ms = (
                self._free_turn_duration_ms(action, str(self.state.current_speaker_side))
                if action.action_kind == "FREE_DEBATE"
                and self.state.current_speaker_side is not None
                else action.duration_seconds * 1000
            )
        self.state = replace(
            self.state,
            status="ERROR",
            action_state="RECOVERY_REQUIRED",
            current_speech_id=None if asr_failure else self.state.current_speech_id,
            speech_start_sequence=(None if asr_failure else self.state.speech_start_sequence),
            interim_text="",
            speech_deadline_mono=None,
            human_start_deadline_mono=None,
            speech_remaining_ms=retry_duration_ms,
            host_audio_remaining_ms=current_view.host_audio_remaining_ms,
            host_audio_deadline_mono=None,
            host_audio_deadline_ms=None,
            error_code=str(command.payload.get("error_code", "internal_server_error")),
            paused_from_status="RUNNING",
            paused_from_action_state=restart_action_state,
            opportunity_id=(
                None if invalidated_opportunity_id is not None else self.state.opportunity_id
            ),
            selection_phase=(
                None if invalidated_opportunity_id is not None else self.state.selection_phase
            ),
            agent_effective_status=(
                None
                if invalidated_opportunity_id is not None
                else self.state.agent_effective_status
            ),
            selection_deadline_mono=(
                None
                if invalidated_opportunity_id is not None
                else self.state.selection_deadline_mono
            ),
            selection_remaining_ms=(
                None
                if invalidated_opportunity_id is not None
                else self.state.selection_remaining_ms
            ),
            hand_queue=() if invalidated_opportunity_id is not None else self.state.hand_queue,
            agent_hand_queue=(
                () if invalidated_opportunity_id is not None else self.state.agent_hand_queue
            ),
            agent_decision_round_id=(
                None
                if invalidated_opportunity_id is not None
                else self.state.agent_decision_round_id
            ),
            agent_decisions=(
                () if invalidated_opportunity_id is not None else self.state.agent_decisions
            ),
            hand_window_open=(
                False if invalidated_opportunity_id is not None else self.state.hand_window_open
            ),
        )
        if invalidated_opportunity_id is None:
            return (self._event("match.error", dict(command.payload)),)
        invalidated = self._event(
            "free.opportunity_invalidated",
            {
                "opportunity_id": str(invalidated_opportunity_id),
                "reason": "ASR_FAILURE",
            },
        )
        return (invalidated, self._event("match.error", dict(command.payload)))

    def _schedule_human_start_timeout(self) -> None:
        deadline = self._clock() + 60.0
        self.state = replace(self.state, human_start_deadline_mono=deadline)
        self._schedule_internal(
            60.0,
            "human.start_timeout",
            f"internal:human-start:{self.state.sequence}",
        )

    def _schedule_internal(
        self,
        delay_seconds: float,
        command_type: str,
        message_id: str,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        if self._processing:
            self._pending_cancel_timer = True
            self._pending_timer = (delay_seconds, command_type, message_id, payload)
            return
        self._cancel_timer_now()
        self._schedule_internal_now(delay_seconds, command_type, message_id, payload)

    def _get_pending_timer(
        self,
    ) -> tuple[float, str, str, Mapping[str, Any] | None] | None:
        return self._pending_timer

    def _schedule_internal_now(
        self,
        delay_seconds: float,
        command_type: str,
        message_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> None:

        self._timer_deadline_mono = self._clock() + delay_seconds
        self._timer_command_type = command_type

        async def submit_after_delay() -> None:
            try:
                await self._sleep(delay_seconds)
                if self._timer is asyncio.current_task():
                    self._timer = None
                    self._timer_deadline_mono = None
                    self._timer_command_type = None
                await self.submit(
                    MatchCommand(
                        type=command_type,
                        message_id=message_id,
                        payload=payload or {},
                    )
                )
            except asyncio.CancelledError:
                return
            except MatchDomainError as error:
                # A timer can legitimately become stale when a match is paused or
                # terminated.  Keep this path observable without killing the task.
                logger.info(
                    "stale internal timer ignored",
                    extra={
                        "error_code": "internal_timer_stale",
                        "command_type": command_type,
                        "exception_type": type(error).__name__,
                    },
                )
            except Exception as error:
                # Never let an internal task die with an unretrieved exception. In
                # particular, malformed callback payloads must converge to an
                # operator-visible recovery state.
                logger.error(
                    "internal timer failed",
                    extra={
                        "error_code": "internal_timer_failed",
                        "command_type": command_type,
                        "exception_type": type(error).__name__,
                    },
                )
                try:
                    await self.submit(
                        MatchCommand(
                            type="system.error",
                            message_id=f"internal-error:{command_type}:{message_id}",
                            payload={"error_code": "internal_timer_failed"},
                        )
                    )
                except Exception as recovery_error:
                    logger.error(
                        "internal timer recovery failed",
                        extra={
                            "error_code": "internal_timer_recovery_failed",
                            "command_type": command_type,
                            "exception_type": type(recovery_error).__name__,
                        },
                    )

        self._timer = asyncio.create_task(submit_after_delay())

    def _cancel_timer(self) -> None:
        if self._processing:
            self._pending_cancel_timer = True
            self._pending_timer = None
            return
        self._cancel_timer_now()

    def _cancel_timer_now(self) -> None:
        if self._timer is not None and self._timer is not asyncio.current_task():
            self._timer.cancel()
        self._timer = None
        self._timer_deadline_mono = None
        self._timer_command_type = None

    def _schedule_offline_expiry(self, user_id: UUID, delay_seconds: float | None = None) -> None:
        self._cancel_offline_expiry(user_id)
        expiry_sequence = self.state.sequence
        if delay_seconds is None:
            offline_since = dict(self.state.offline_since_ms).get(user_id)
            delay_seconds = (
                max(0.01, 60 - (self._wall_clock() * 1000 - offline_since) / 1000)
                if offline_since is not None
                else 60.0
            )

        async def submit_after_grace() -> None:
            try:
                await self._sleep(delay_seconds)
                await self.submit(
                    MatchCommand(
                        type="offline.expired",
                        message_id=f"internal:offline:{user_id}:{expiry_sequence}",
                        payload={"user_id": str(user_id)},
                    )
                )
            except asyncio.CancelledError:
                return
            except MatchDomainError as error:
                logger.info(
                    "stale offline timer ignored",
                    extra={
                        "error_code": "offline_timer_stale",
                        "exception_type": type(error).__name__,
                    },
                )
            except Exception as error:
                logger.error(
                    "offline timer failed",
                    extra={
                        "error_code": "offline_timer_failed",
                        "exception_type": type(error).__name__,
                    },
                )
                try:
                    await self.submit(
                        MatchCommand(
                            type="system.error",
                            message_id=f"offline-error:{user_id}:{expiry_sequence}",
                            payload={"error_code": "offline_timer_failed"},
                        )
                    )
                except Exception as recovery_error:
                    logger.error(
                        "offline timer recovery failed",
                        extra={
                            "error_code": "offline_timer_recovery_failed",
                            "exception_type": type(recovery_error).__name__,
                        },
                    )
            finally:
                if self._offline_timers.get(user_id) is asyncio.current_task():
                    self._offline_timers.pop(user_id, None)

        self._offline_timers[user_id] = asyncio.create_task(submit_after_grace())

    def _cancel_offline_expiry(self, user_id: UUID) -> None:
        task = self._offline_timers.pop(user_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _cancel_all_offline_expiries(self) -> None:
        for user_id in tuple(self._offline_timers):
            self._cancel_offline_expiry(user_id)


__all__ = [
    "AgentDecisionState",
    "MatchAction",
    "MatchActor",
    "MatchCommand",
    "MatchCommandResult",
    "MatchDomainError",
    "MatchEvent",
    "MatchRuntimeState",
    "MatchRuntimeView",
    "compile_linear_actions",
]

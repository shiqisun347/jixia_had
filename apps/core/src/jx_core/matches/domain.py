"""Single-queue authoritative runtime for ordinary linear match actions."""

from __future__ import annotations

import asyncio
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
    speech_deadline_mono: float | None = None
    speech_remaining_ms: int | None = None
    current_speaker_side: str | None = None
    current_speaker_seat_no: int | None = None
    free_holder_side: str | None = None
    free_affirmative_remaining_ms: int | None = None
    free_negative_remaining_ms: int | None = None
    hand_queue: tuple[UUID, ...] = ()
    agent_hand_queue: tuple[UUID, ...] = ()
    agent_selection_mode: Literal["VOLUNTEER", "FALLBACK"] | None = None
    agent_decision_round_id: UUID | None = None
    agent_decisions: tuple[AgentDecisionState, ...] = ()
    hand_window_open: bool = False
    paused_from_status: MatchStatus | None = None
    paused_from_action_state: ActionState | None = None
    pause_initiator_user_id: UUID | None = None
    offline_user_id: UUID | None = None
    offline_since_ms: tuple[tuple[UUID, int], ...] = ()
    connection_epochs: tuple[tuple[UUID, int], ...] = ()
    error_code: str | None = None

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
    host_audio: dict[str, str] = {}
    for raw_item in raw_host_audio:
        if not isinstance(raw_item, Mapping):
            raise MatchDomainError("rule_snapshot_invalid")
        item = cast(Mapping[str, Any], raw_item)
        segment_key = item.get("segment_key")
        storage_path = item.get("storage_path")
        if segment_key and storage_path:
            host_audio[str(segment_key)] = str(storage_path)
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
        host_path = host_audio.get(f"stage-{stage_position}-start")
        end_host_path = host_audio.get(f"stage-{stage_position}-end")
        if stage_kind == "END":
            if host_path:
                compiled.append(
                    MatchAction(
                        stage_position=stage_position,
                        action_position=0,
                        action_kind="HOST_AUDIO",
                        duration_seconds=0,
                        host_audio_path=host_path,
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
        self._offline_users: set[UUID] = {
            user_id for user_id, _ in state.offline_since_ms
        } or ({state.offline_user_id} if state.offline_user_id is not None else set())
        # WebSocket reconnects can make an older connection's close callback
        # arrive after the newer connection has already joined. Keep the
        # newest observed epoch so that stale lifecycle events cannot mark a
        # reconnected participant offline again.
        self._connection_epochs: dict[UUID, int] = dict(state.connection_epochs)
        self._processing = False
        self._pending_cancel_timer = False
        self._pending_timer: tuple[float, str, str] | None = None

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
        return MatchRuntimeView(
            state=self.state,
            speech_remaining_ms=speech_remaining_ms,
            countdown_remaining_ms=countdown_remaining_ms,
            free_affirmative_remaining_ms=affirmative_remaining_ms,
            free_negative_remaining_ms=negative_remaining_ms,
        )

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
            "host.finished": self._host_finished,
            "preparation.elapsed": self._preparation_elapsed,
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
            self.state = replace(self.state, status="FINISHED", action_state="MATCH_FINISHED")
            return (self._event("match.finished", {}),)
        if action.host_audio_path:
            self.state = replace(
                self.state,
                action_state="HOST_ANNOUNCING",
                current_speaker_user_id=None,
                current_agent_profile_id=None,
                current_speaker_side=None,
                current_speaker_seat_no=None,
                current_speech_id=None,
                speech_deadline_mono=None,
                speech_remaining_ms=None,
                hand_queue=(),
                agent_hand_queue=(),
                agent_selection_mode=None,
                agent_decision_round_id=None,
                agent_decisions=(),
                hand_window_open=False,
            )
            return (
                self._event(
                    "host.play",
                    {"action_key": action.action_key, "storage_path": action.host_audio_path},
                ),
            )
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

    def _activate_action(self, action: MatchAction) -> tuple[MatchEvent, ...]:
        if action.action_kind == "PREPARATION":
            self.state = replace(self.state, action_state="PREPARING")
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
            self.state = replace(
                self.state,
                action_state="FREE_SELECTING",
                current_speaker_user_id=None,
                current_agent_profile_id=None,
                current_speaker_side=None,
                current_speaker_seat_no=None,
                current_speech_id=None,
                speech_deadline_mono=None,
                speech_remaining_ms=None,
                free_holder_side=action.free_starting_side,
                free_affirmative_remaining_ms=total_ms,
                free_negative_remaining_ms=total_ms,
                hand_queue=(),
                agent_hand_queue=(),
                agent_selection_mode=None,
                agent_decision_round_id=None,
                agent_decisions=(),
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
            opened = self._event(
                "hand.window_opened",
                {"side": action.free_starting_side, "duration_ms": 3000},
            )
            decision_started = self._start_free_agent_decisions(action.free_starting_side)
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

    def _start_free_agent_decisions(self, side: str) -> MatchEvent:
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
        self.state = replace(
            self.state,
            agent_decision_round_id=round_id,
            agent_decisions=decisions,
            agent_hand_queue=(),
            agent_selection_mode=None,
        )
        return self._event(
            "agent.decision_started",
            {
                "action_key": action.action_key,
                "side": side,
                "decision_round_id": str(round_id),
                "agents": [
                    {
                        "agent_profile_id": str(item.agent_profile_id),
                        "seat_no": item.seat_no,
                    }
                    for item in decisions
                ],
            },
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

    def _sorted_agent_hands(
        self, decisions: tuple[AgentDecisionState, ...]
    ) -> tuple[UUID, ...]:
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
            else self._opposite_side(self.state.current_speaker_side)
        )
        if side is None or self._free_human(command.actor_user_id, side) is None:
            raise MatchDomainError("hand_not_eligible")
        if command.actor_user_id in self.state.hand_queue:
            raise MatchDomainError("hand_already_raised")
        queue = (*self.state.hand_queue, command.actor_user_id)
        self.state = replace(self.state, hand_queue=queue)
        return (
            self._event(
                "hand.raised",
                {
                    "user_id": str(command.actor_user_id),
                    "side": side,
                    "order": len(queue),
                },
            ),
            self._queue_snapshot_event("HUMAN_RAISED"),
        )

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
                {"user_id": str(command.actor_user_id)},
            ),
            self._queue_snapshot_event("HUMAN_CANCELLED"),
        )

    def _hand_window_closed(self, _: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.action_state != "FREE_SELECTING" or not self.state.hand_window_open:
            return ()
        self.state = replace(self.state, hand_window_open=False)
        closed = self._event("hand.window_closed", {"side": self.state.free_holder_side})
        return (closed, *self._lock_free_selection_if_ready())

    def _free_agent_decision_result(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.action_state != "FREE_SELECTING":
            raise MatchDomainError("stale_callback")
        action = self.state.current_action
        if action is None or str(command.payload.get("action_key")) != action.action_key:
            raise MatchDomainError("stale_callback")
        if str(command.payload.get("decision_round_id")) != str(self.state.agent_decision_round_id):
            raise MatchDomainError("stale_callback")
        agent_profile_id = UUID(str(command.payload["agent_profile_id"]))
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
        willingness_value = command.payload.get("willingness")
        if failed:
            should_speak = None
            willingness = None
        else:
            if not isinstance(should_speak_value, bool) or not isinstance(
                willingness_value, (int, float)
            ):
                raise MatchDomainError("stale_callback")
            should_speak = should_speak_value
            willingness = max(0.0, min(1.0, float(willingness_value)))
        result_order = 1 + max(
            (item.result_order or 0 for item in self.state.agent_decisions), default=0
        )
        updated = tuple(
            replace(
                item,
                status="HAND" if should_speak else "SKIP",
                should_speak=should_speak,
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
        )
        progress = self._event(
            "agent.decision_progress",
            {
                "action_key": action.action_key,
                "decision_round_id": str(self.state.agent_decision_round_id),
                "agent_profile_id": str(agent_profile_id),
                "status": "SKIP" if failed else "HAND" if should_speak else "SKIP",
                "should_speak": should_speak,
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
        if self.state.hand_window_open or any(
            item.status == "DECIDING" for item in self.state.agent_decisions
        ):
            return ()
        action = self.state.current_action
        side = self.state.free_holder_side
        if action is None or action.action_kind != "FREE_DEBATE" or side is None:
            raise MatchDomainError("match_state_conflict")
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
            locked = self._event(
                "free.selection_locked",
                {
                    "decision_round_id": str(self.state.agent_decision_round_id),
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
                "decision_round_id": str(self.state.agent_decision_round_id),
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

    def _speech_start(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.action_state != "HUMAN_READY_TO_START":
            raise MatchDomainError("match_state_conflict")
        if command.actor_user_id != self.state.current_speaker_user_id:
            raise MatchDomainError("not_current_speaker")
        action = self.state.current_action
        if action is None:
            raise MatchDomainError("match_state_conflict")
        speech_id = (
            UUID(str(command.payload["speech_id"])) if command.payload.get("speech_id") else uuid4()
        )
        duration_ms = self.state.speech_remaining_ms or action.duration_seconds * 1000
        deadline = self._clock() + duration_ms / 1000
        restarting_free_turn = action.action_kind == "FREE_DEBATE" and self.state.hand_window_open
        self.state = replace(
            self.state,
            action_state="HUMAN_SPEAKING",
            current_speech_id=speech_id,
            current_speaker_side=action.side or self.state.current_speaker_side,
            current_speaker_seat_no=action.seat_no or self.state.current_speaker_seat_no,
            speech_deadline_mono=deadline,
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
        )
        event = self._event(
            "speech.started",
            {
                "speech_id": str(speech_id),
                "speaker_user_id": str(command.actor_user_id),
                "duration_ms": duration_ms,
            },
        )
        self._schedule_internal(
            duration_ms / 1000,
            "speech.deadline",
            f"internal:speech-deadline:{speech_id}",
        )
        if action.action_kind != "FREE_DEBATE":
            return (event,)
        return (
            event,
            self._event(
                "hand.window_opened",
                {"side": self._opposite_side(self.state.current_speaker_side), "duration_ms": None},
            ),
        )

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
        speech_id = UUID(str(command.payload["speech_id"]))
        duration_ms = self.state.speech_remaining_ms or action.duration_seconds * 1000
        restarting_free_turn = action.action_kind == "FREE_DEBATE" and self.state.hand_window_open
        self.state = replace(
            self.state,
            action_state="AGENT_SPEAKING",
            current_speech_id=speech_id,
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
        )
        event = self._event(
            "agent.playback_started",
            {
                **dict(command.payload),
                "speech_id": str(speech_id),
                "duration_ms": duration_ms,
            },
        )
        self._schedule_internal(
            duration_ms / 1000,
            "speech.deadline",
            f"internal:agent-deadline:{speech_id}",
        )
        if action.action_kind != "FREE_DEBATE":
            return (event,)
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

    def _speech_deadline(self, _: MatchCommand) -> tuple[MatchEvent, ...]:
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
            speech_deadline_mono=None,
        )
        return (
            self._event(
                "agent.finalizing",
                {"speech_id": str(self.state.current_speech_id), "reason": reason},
            ),
        )

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
            self.state, action_state="SPEECH_FINALIZING", speech_deadline_mono=None
        )
        return (
            self._event(
                "speech.finalizing",
                {"speech_id": str(speech_id), "reason": reason},
            ),
        )

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
            finished = self._event(event_type, payload)
            self.state = replace(
                self.state,
                status="FINISHED",
                action_state="MATCH_FINISHED",
                current_speech_id=None,
                current_speaker_user_id=None,
                current_agent_profile_id=None,
                current_speaker_side=None,
                current_speaker_seat_no=None,
                speech_remaining_ms=None,
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
            current_speaker_user_id=None,
            current_agent_profile_id=None,
            current_speaker_side=None,
            current_speaker_seat_no=None,
            speech_deadline_mono=None,
            speech_remaining_ms=None,
            free_affirmative_remaining_ms=(remaining if side == "AFFIRMATIVE" else next_remaining),
            free_negative_remaining_ms=(remaining if side == "NEGATIVE" else next_remaining),
            free_holder_side=(next_side if next_remaining > 0 else side),
            hand_window_open=True,
            agent_hand_queue=(),
            agent_selection_mode=None,
            agent_decision_round_id=None,
            agent_decisions=(),
        )
        completed = self._event(event_type, payload)
        opened = self._event(
            "hand.window_opened",
            {"side": self.state.free_holder_side, "duration_ms": 3000},
        )
        decision_started = self._start_free_agent_decisions(str(self.state.free_holder_side))
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
        self.state = replace(
            self.state,
            action_state="AGENT_PREPARING" if agent_action else "HUMAN_READY_TO_START",
            current_speech_id=None,
            current_speaker_user_id=speaker_user_id,
            current_agent_profile_id=agent_profile_id,
            current_speaker_side=side,
            current_speaker_seat_no=seat_no,
            speech_deadline_mono=None,
            speech_remaining_ms=duration_ms,
        )
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
        return (
            self._event(
                "agent.preparing" if agent_action else "speech.ready",
                payload,
            ),
        )

    def _advance_action(
        self, event_type: str, payload: Mapping[str, Any]
    ) -> tuple[MatchEvent, ...]:
        finished = self._event(event_type, payload)
        self.state = replace(
            self.state,
            action_state="ACTION_FINISHED",
            current_action_index=self.state.current_action_index + 1,
            current_speech_id=None,
            current_speaker_user_id=None,
            current_agent_profile_id=None,
            speech_deadline_mono=None,
            speech_remaining_ms=None,
        )
        return (finished, *self._enter_current_action())

    def _match_terminate(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if not bool(command.payload.get("privileged")):
            raise MatchDomainError("forbidden")
        self._cancel_timer()
        self.state = replace(self.state, status="TERMINATED", action_state="MATCH_FINISHED")
        return (self._event("match.terminated", {}),)

    def _match_pause(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        if self.state.status != "RUNNING" or not bool(command.payload.get("authorized")):
            raise MatchDomainError("forbidden")
        remaining_ms = self.state.speech_remaining_ms
        if self.state.speech_deadline_mono is not None:
            remaining_ms = max(0, int((self.state.speech_deadline_mono - self._clock()) * 1000))
        self._cancel_timer()
        previous_action_state = self.state.action_state
        self.state = replace(
            self.state,
            status="PAUSED",
            action_state="RECOVERY_REQUIRED",
            speech_deadline_mono=None,
            speech_remaining_ms=remaining_ms,
            paused_from_status="RUNNING",
            paused_from_action_state=previous_action_state,
            pause_initiator_user_id=command.actor_user_id,
        )
        return (
            self._event(
                "match.paused",
                {
                    "initiator_user_id": (
                        str(command.actor_user_id) if command.actor_user_id else None
                    ),
                    "reason": str(command.payload.get("reason", "MANUAL")),
                },
            ),
        )

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
        if restored in ("HUMAN_SPEAKING", "SPEECH_FINALIZING"):
            restored = "HUMAN_READY_TO_START"
        elif restored in ("AGENT_SPEAKING", "AGENT_FINALIZING"):
            restored = "AGENT_PREPARING"
            self.state = replace(self.state, current_speech_id=None)
        self.state = replace(
            self.state,
            status="RUNNING",
            action_state=restored,
            speech_deadline_mono=None,
            paused_from_status=None,
            paused_from_action_state=None,
            pause_initiator_user_id=None,
        )
        resumed = self._event("match.resumed", {})
        action = self.state.current_action
        if restored == "FREE_SELECTING" and action is not None:
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
                        "duration_ms": self.state.speech_remaining_ms
                        or action.duration_seconds * 1000,
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
            if known_epoch == epoch and command.actor_user_id in self._offline_users:
                return ()
            self._connection_epochs[command.actor_user_id] = max(epoch, known_epoch or epoch)
        offline_since_ms = int(command.payload.get("offline_since_ms", 0))
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
        if offline_since is not None and time() * 1000 - offline_since < 60_000:
            remaining_seconds = max(0.01, 60 - (time() * 1000 - offline_since) / 1000)
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
        self._cancel_timer()
        current = self.state.current_action
        restart_action_state = self.state.action_state
        if self.state.current_agent_profile_id is not None or (
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
            current_speaker_user_id=(
                current.speaker_user_id
                if current is not None and current.speaker_user_id is not None
                else self.state.current_speaker_user_id
            ),
            current_agent_profile_id=(
                current.agent_profile_id
                if current is not None and current.agent_profile_id is not None
                else self.state.current_agent_profile_id
            ),
            speech_deadline_mono=None,
            speech_remaining_ms=(current.duration_seconds * 1000 if current else None),
            paused_from_status="RUNNING",
            paused_from_action_state=restart_action_state,
        )
        return (self._event("match.system_recovery", {}),)

    def _system_error(self, command: MatchCommand) -> tuple[MatchEvent, ...]:
        self._cancel_timer()
        restart_action_state = self.state.action_state
        if self.state.current_agent_profile_id is not None:
            restart_action_state = "AGENT_PREPARING"
        elif self.state.current_speaker_user_id is not None:
            restart_action_state = "HUMAN_READY_TO_START"
        self.state = replace(
            self.state,
            status="ERROR",
            action_state="RECOVERY_REQUIRED",
            speech_deadline_mono=None,
            error_code=str(command.payload.get("error_code", "internal_server_error")),
            paused_from_status="RUNNING",
            paused_from_action_state=restart_action_state,
        )
        return (self._event("match.error", dict(command.payload)),)

    def _schedule_internal(self, delay_seconds: float, command_type: str, message_id: str) -> None:
        if self._processing:
            self._pending_cancel_timer = True
            self._pending_timer = (delay_seconds, command_type, message_id)
            return
        self._cancel_timer_now()
        self._schedule_internal_now(delay_seconds, command_type, message_id)

    def _get_pending_timer(self) -> tuple[float, str, str] | None:
        return self._pending_timer

    def _schedule_internal_now(
        self, delay_seconds: float, command_type: str, message_id: str
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
                await self.submit(MatchCommand(type=command_type, message_id=message_id))
            except (asyncio.CancelledError, MatchDomainError):
                return

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
                max(0.01, 60 - (time() * 1000 - offline_since) / 1000)
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
            except (asyncio.CancelledError, MatchDomainError):
                return
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

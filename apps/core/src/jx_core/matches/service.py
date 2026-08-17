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
from ..models import (
    AgentFreeDebateDecision,
    AgentGeneration,
    AgentProfile,
    AsrSegment,
    BackgroundTask,
    ExternalCall,
    Match,
    MatchFile,
    MatchParticipant,
    Room,
    Seat,
    Speech,
    User,
)
from ..models import MatchEvent as MatchEventRow
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


def _file_size(path: str | None) -> int:
    if not path:
        return 0
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


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
    async def start_speech(self, match_id: UUID, speech_id: UUID, user_id: UUID) -> None: ...

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
        "speech_remaining_ms": state.speech_remaining_ms,
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
                "willingness": item.willingness,
                "result_order": item.result_order,
                "failed": item.failed,
            }
            for item in state.agent_decisions
        ],
        "hand_window_open": state.hand_window_open,
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
        speech_remaining_ms=snapshot.get("speech_remaining_ms"),
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

    def set_speech_runtime(self, runtime: SpeechRuntime) -> None:
        self._speech_runtime = runtime

    def set_agent_runtime(self, runtime: AgentRuntime) -> None:
        self._agent_runtime = runtime

    def set_postmatch_runtime(self, runtime: PostmatchRuntime) -> None:
        self._postmatch_runtime = runtime

    async def _pre_commit(
        self,
        previous: MatchRuntimeState,
        candidate: MatchRuntimeState,
        events: tuple[MatchEvent, ...],
        command: MatchCommand,
    ) -> None:
        if command.type != "speech.reset":
            return
        if self._speech_runtime is not None:
            await self._speech_runtime.reset_speech(previous.match_id)
        if self._agent_runtime is not None:
            await self._agent_runtime.reset_agent(previous.match_id)

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
                if self._agent_runtime is not None and action is not None and candidates:
                    self._spawn(
                        self._agent_runtime.decide_free_debate(
                            match_id=event.match_id,
                            action_key=action.action_key,
                            side=side,
                            agent_profile_ids=candidates,
                            decision_round_id=UUID(str(event.payload["decision_round_id"])),
                        ),
                        name=f"agent-decide-{event.match_id}-{event.payload['decision_round_id']}",
                    )
                elif candidates:
                    round_id = UUID(str(event.payload["decision_round_id"]))
                    for agent_profile_id in candidates:
                        self._spawn(
                            self._report_unavailable_decision(
                                match_id=event.match_id,
                                action_key=str(event.payload["action_key"]),
                                agent_profile_id=agent_profile_id,
                                side=side,
                                decision_round_id=round_id,
                            ),
                            name=f"agent-decision-error-{event.match_id}-{agent_profile_id}",
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
    ) -> None:
        await self.report_free_decision(
            match_id=match_id,
            action_key=action_key,
            agent_profile_id=agent_profile_id,
            side=side,
            decision_round_id=decision_round_id,
            should_speak=None,
            willingness=None,
            failed=True,
            attempt_no=1,
            duration_ms=0,
            error_code="agent_unavailable",
        )

    async def publish_agent_text_delta(
        self, match_id: UUID, generation_id: UUID, text: str
    ) -> None:
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

    async def report_free_decision(
        self,
        *,
        match_id: UUID,
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
    ) -> object:
        return await self.submit(
            match_id,
            MatchCommand(
                type="free.agent_decision_result",
                message_id=(
                    f"free-agent-decision:{match_id}:{decision_round_id}:{agent_profile_id}"
                ),
                payload={
                    "action_key": action_key,
                    "agent_profile_id": str(agent_profile_id),
                    "side": side,
                    "decision_round_id": str(decision_round_id),
                    "should_speak": should_speak,
                    "willingness": willingness,
                    "failed": failed,
                    "attempt_no": attempt_no,
                    "duration_ms": duration_ms,
                    "error_code": error_code,
                },
            ),
        )

    async def publish_agent_subtitle(
        self, match_id: UUID, speech_id: UUID, text: str, played_ms: int
    ) -> None:
        actor = self._actors.get(match_id)
        if actor is None or actor.state.current_speech_id != speech_id:
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

    async def publish_agent_retry(
        self, match_id: UUID, generation_id: UUID, error_code: str
    ) -> None:
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
        self, match_id: UUID, speech_id: UUID, segment_no: int, text: str
    ) -> None:
        actor = self._actors.get(match_id)
        if actor is None or actor.state.current_speech_id != speech_id:
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
        speech_id: UUID,
        segment_no: int,
        task_id: UUID,
        final_text: str,
        first_interim_latency_ms: int | None,
        final_latency_ms: int,
        pcm_sample_count: int,
    ) -> None:
        match_id: UUID | None = None
        async with self._session_factory() as session:
            async with session.begin():
                match_id = await session.scalar(
                    select(Speech.match_id).where(Speech.id == speech_id)
                )
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
        if match_id is not None:
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

    async def start_asr_segment(self, *, speech_id: UUID, segment_no: int, task_id: UUID) -> None:
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
                        asr_segment_id=segment.id,
                        started_at=datetime.now(UTC),
                    )
                )

    async def fail_asr_segment(
        self,
        *,
        speech_id: UUID,
        segment_no: int,
        task_id: UUID,
        error_code: str,
    ) -> None:
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

    async def start_agent_playback(
        self,
        *,
        match_id: UUID,
        speech_id: UUID,
        generation_id: UUID,
        agent_profile_id: UUID,
        audio_storage_path: str,
    ) -> object:
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

    async def finish_agent_playback(self, match_id: UUID, speech_id: UUID) -> object:
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
        match_id: UUID,
        speech_id: UUID,
        generation_id: UUID,
        final_text: str,
        llm_draft_text: str,
        audio_storage_path: str,
        audio_duration_ms: int,
        audio_truncated: bool,
    ) -> object:
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

    async def handle_agent_failure(
        self, match_id: UUID, generation_id: UUID | None, error_code: str
    ) -> None:
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
        match_id: UUID,
        speech_id: UUID,
        final_text: str,
        first_interim_latency_ms: int | None,
        final_latency_ms: int,
        audio_duration_ms: int,
        audio_storage_path: str | None,
        audio_recording_error: str | None,
    ) -> MatchCommandResult:
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

    async def handle_asr_failure(self, match_id: UUID, speech_id: UUID, code: str) -> None:
        actor = await self.get_actor(match_id)
        if (
            actor.state.status != "RUNNING"
            or actor.state.current_speech_id != speech_id
            or actor.state.action_state not in {"HUMAN_SPEAKING", "SPEECH_FINALIZING"}
        ):
            return
        async with self._session_factory() as session:
            async with session.begin():
                speech = await session.get(Speech, speech_id, with_for_update=True)
                if speech is None or speech.match_id != match_id:
                    return
                speech.status = "FAILED"
                speech.asr_error_code = code
                speech.ended_at = datetime.now(UTC)
                failures = await session.scalar(
                    select(func.count())
                    .select_from(Speech)
                    .where(
                        Speech.match_id == match_id,
                        Speech.action_key == speech.action_key,
                        Speech.status == "FAILED",
                    )
                )
        if int(failures or 0) < 2:
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
                        payload={"speech_id": str(speech_id), "error_code": code},
                    ),
                ),
            )
            return
        await actor.submit(
            MatchCommand(
                type="system.error",
                message_id=f"asr-error:{speech_id}",
                payload={"error_code": code},
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
                for event in events:
                    session.add(
                        MatchEventRow(
                            match_id=event.match_id,
                            sequence=event.sequence,
                            event_type=event.type,
                            payload=dict(event.payload),
                        )
                    )
                    if event.type == "agent.decision_started":
                        decision_round_id = UUID(str(event.payload["decision_round_id"]))
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
                                == UUID(str(event.payload["decision_round_id"])),
                                AgentFreeDebateDecision.agent_profile_id
                                == UUID(str(event.payload["agent_profile_id"])),
                            )
                            .with_for_update()
                        )
                        if decision is None:
                            raise MatchDomainError("match_state_conflict")
                        decision.status = str(event.payload["status"])
                        decision.should_speak = event.payload.get("should_speak")
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
                    elif event.type == "free.selection_locked":
                        decision_round_id = UUID(str(event.payload["decision_round_id"]))
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
                        agent_ranks = {
                            agent_id: len(candidate.hand_queue) + index + 1
                            for index, agent_id in enumerate(candidate.agent_hand_queue)
                        }
                        selected_agent_id = (
                            UUID(str(event.payload["agent_profile_id"]))
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
                for event in events:
                    speech_id = event.payload.get("speech_id")
                    if event.type in ("speech.started", "agent.playback_started") and speech_id:
                        current = candidate.current_action
                        if current is not None and (
                            current.speaker_user_id is not None
                            or current.agent_profile_id is not None
                            or candidate.current_speaker_user_id is not None
                            or candidate.current_agent_profile_id is not None
                        ):
                            previous_attempt = await session.scalar(
                                select(func.coalesce(func.max(Speech.attempt_no), 0)).where(
                                    Speech.match_id == candidate.match_id,
                                    Speech.action_key == current.action_key,
                                )
                            )
                            existing_speech = await session.get(
                                Speech, UUID(str(speech_id)), with_for_update=True
                            )
                            if existing_speech is not None:
                                existing_speech.status = "STARTED"
                                existing_speech.ended_at = None
                                existing_speech.finalized_at = None
                            else:
                                session.add(
                                    Speech(
                                        id=UUID(str(speech_id)),
                                        match_id=candidate.match_id,
                                        action_key=current.action_key,
                                        user_id=candidate.current_speaker_user_id,
                                        side=(
                                            candidate.current_speaker_side
                                            or current.side
                                            or "AFFIRMATIVE"
                                        ),
                                        seat_no=(
                                            candidate.current_speaker_seat_no
                                            or current.seat_no
                                            or 1
                                        ),
                                        speaker_kind=(
                                            "AGENT"
                                            if candidate.current_agent_profile_id is not None
                                            else "HUMAN"
                                        ),
                                        agent_profile_id=candidate.current_agent_profile_id,
                                        generation_id=(
                                            UUID(str(event.payload["generation_id"]))
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
                            session.add(
                                MatchFile(
                                    match_id=candidate.match_id,
                                    speech_id=speech.id,
                                    owner_user_id=speech.user_id,
                                    file_key=f"human-{speech.id}",
                                    file_kind="HUMAN_RAW",
                                    status="READY" if storage_path else "FAILED",
                                    storage_path=str(storage_path) if storage_path else None,
                                    codec="pcm_s16le_16000_mono" if storage_path else None,
                                    byte_count=_file_size(
                                        str(storage_path) if storage_path else None
                                    ),
                                    duration_ms=speech.audio_duration_ms,
                                    expires_at=datetime.now(UTC) + timedelta(days=30),
                                    error_code=(
                                        str(event.payload.get("audio_recording_error"))
                                        if event.payload.get("audio_recording_error")
                                        else None
                                    ),
                                )
                            )
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
                            session.add(
                                MatchFile(
                                    match_id=candidate.match_id,
                                    speech_id=speech.id,
                                    owner_user_id=None,
                                    file_key=f"agent-{speech.id}",
                                    file_kind="AGENT_RAW",
                                    status="READY" if speech.audio_storage_path else "FAILED",
                                    storage_path=speech.audio_storage_path or None,
                                    codec="ogg_opus" if speech.audio_storage_path else None,
                                    byte_count=_file_size(speech.audio_storage_path),
                                    duration_ms=speech.audio_duration_ms,
                                    expires_at=datetime.now(UTC) + timedelta(days=30),
                                    error_code=(
                                        None if speech.audio_storage_path else "agent_audio_missing"
                                    ),
                                )
                            )
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
                if actor_role != "ADMIN" and room.organizer_user_id != actor_user_id:
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
                match_id = uuid4()
                state = MatchRuntimeState(
                    match_id=match_id,
                    status="START_PENDING_RUNTIME",
                    action_state="NOT_STARTED",
                    actions=actions,
                    match_seed=match_id.int % (2**63 - 1),
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
        if command.type == "speech.start" and self._speech_runtime is not None:
            if command.actor_user_id is None:
                raise MatchDomainError("not_current_speaker")
            speech_id = actor.state.current_speech_id or uuid4()
            await self._speech_runtime.start_speech(match_id, speech_id, command.actor_user_id)
            try:
                return await actor.submit(
                    MatchCommand(
                        type=command.type,
                        message_id=command.message_id,
                        actor_user_id=command.actor_user_id,
                        payload={**command.payload, "speech_id": str(speech_id)},
                    )
                )
            except Exception:
                await self._speech_runtime.reset_speech(match_id)
                raise
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

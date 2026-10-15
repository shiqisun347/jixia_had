"""LiveKit server subscriber feeding bounded Fun-ASR speech sessions."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Any, BinaryIO, Protocol
from uuid import UUID, uuid4
from weakref import ref

from livekit import api, rtc

from ..config import Settings
from ..data_capture.provider import ProviderCallCapture
from ..data_capture.provider_writer import ProviderCaptureWriter, new_provider_capture
from ..runtime_identity import CallbackEnvelope
from .protocol import FunAsrConnection, FunAsrError, SegmentResult
from .session import AsrQueueFull, AsrSpeechSession

logger = logging.getLogger("jx-core.asr")

_LIVEKIT_HUMAN_IDENTITY_RE = re.compile(
    r"^jx-human-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"-(?P<user_id>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
    r"-(?P<epoch>[1-9][0-9]*)-[^-]+$"
)
_LEGACY_USER_IDENTITY_RE = re.compile(
    r"^user-(?P<user_id>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:-|$)"
)
LIVEKIT_CONNECT_TIMEOUT_SECONDS = 10.0
ASR_CLEANUP_TIMEOUT_SECONDS = 2.0
ASR_FAILURE_CALLBACK_TIMEOUT_SECONDS = 10.0
ASR_TASK_CANCEL_TIMEOUT_SECONDS = 2.0
ASR_CLOSED_MATCH_TOMBSTONE_LIMIT = 4096


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    """Retrieve a detached ASR task result after bounded cancellation."""

    try:
        task.exception()
    except BaseException:
        # Cancelled tasks and cleanup failures are already represented by the
        # bounded cancellation diagnostic.
        pass


async def _bounded_cleanup(awaitable: Awaitable[Any], *, resource: str, match_id: UUID) -> None:
    """Detach cancellation-resistant native cleanup after a short deadline.

    LiveKit and provider SDK close methods normally finish immediately, but a
    broken transport can ignore cancellation. Awaiting ``wait_for`` directly
    would then hold the terminal match cleanup open and retain the receiver.
    Keep the resource reference local to a detached task and consume its late
    result so the event loop remains warning-free.
    """

    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait({task}, timeout=ASR_CLEANUP_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        task.cancel()
        task.add_done_callback(_consume_task_exception)
        raise
    if not done:
        task.cancel()
        task.add_done_callback(_consume_task_exception)
        logger.warning(
            "ASR resource cleanup timed out",
            extra={
                "error_code": "asr_cleanup_timeout",
                "match_id": str(match_id),
                "resource": resource,
            },
        )
        return
    try:
        task.result()
    except BaseException:
        logger.warning(
            "ASR resource cleanup failed",
            extra={
                "error_code": "asr_cleanup_failed",
                "match_id": str(match_id),
                "resource": resource,
            },
        )


async def _bounded_cleanup_call(
    close: Callable[[], Awaitable[Any]], *, resource: str, match_id: UUID
) -> None:
    """Isolate a retired resource's factory before applying its existing budget."""

    try:
        await _bounded_cleanup(close(), resource=resource, match_id=match_id)
    except Exception as error:
        logger.warning(
            "ASR resource cleanup failed",
            extra={
                "error_code": "asr_cleanup_failed",
                "match_id": str(match_id),
                "resource": resource,
                "exception_type": type(error).__name__,
            },
        )


async def _wait_for_retired_asr_tasks(
    tasks: tuple[asyncio.Task[None], ...], *, resource: str, match_id: UUID
) -> None:
    """Wait only for an already-cancelled exact snapshot, without a receiver ref."""

    pending_tasks = {task for task in tasks if not task.done()}
    for task in tasks:
        if task.done():
            _consume_task_exception(task)
    if not pending_tasks:
        return
    try:
        done, pending = await asyncio.wait(pending_tasks, timeout=ASR_TASK_CANCEL_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        for task in pending_tasks:
            task.add_done_callback(_consume_task_exception)
        raise
    for task in done:
        _consume_task_exception(task)
    if pending:
        for task in pending:
            task.add_done_callback(_consume_task_exception)
        logger.warning(
            "ASR task cancellation timed out",
            extra={
                "error_code": "asr_task_cancel_timeout",
                "match_id": str(match_id),
                "resource": resource,
                "task_count": len(pending),
            },
        )


async def _close_retired_asr_provider(
    connection: FunAsrConnection,
    tasks: tuple[asyncio.Task[None], ...],
    *,
    match_id: UUID,
) -> None:
    """Close now, then reclaim any late socket/capture from retired producers."""

    pending = {task for task in tasks if not task.done()}
    had_pending = bool(pending)
    initial_returned = False
    final_scheduled = False

    def close_after_producers() -> None:
        nonlocal final_scheduled
        if had_pending and initial_returned and not pending and not final_scheduled:
            final_scheduled = True
            cleanup = asyncio.create_task(
                _bounded_cleanup_call(
                    lambda: connection.close(), resource="retired_provider_tail", match_id=match_id
                )
            )
            cleanup.add_done_callback(_consume_task_exception)

    def producer_finished(task: asyncio.Task[None]) -> None:
        _consume_task_exception(task)
        pending.discard(task)
        close_after_producers()

    for task in pending:
        task.add_done_callback(producer_finished)
    await _bounded_cleanup_call(
        lambda: connection.close(), resource="provider_connection", match_id=match_id
    )
    initial_returned = True
    close_after_producers()


def _schedule_asr_startup_cleanup(
    room: rtc.Room,
    connection: FunAsrConnection | None,
    *,
    match_id: UUID,
) -> list[asyncio.Task[None]]:
    """Give each local resource a close owner before awaiting rollback."""

    cleanup: list[asyncio.Task[None]] = []
    if connection is not None:
        cleanup.append(
            asyncio.create_task(
                _bounded_cleanup_call(
                    connection.close, resource="start_provider", match_id=match_id
                )
            )
        )
    cleanup.append(
        asyncio.create_task(
            _bounded_cleanup_call(room.disconnect, resource="start_room", match_id=match_id)
        )
    )
    for task in cleanup:
        task.add_done_callback(_consume_task_exception)
    return cleanup


def _retired_asr_connection_finished(
    task: asyncio.Task[None], *, room: rtc.Room, match_id: UUID
) -> None:
    # Only the old SDK task and Room survive retirement, never a receiver or
    # runtime. Let the SDK finish ready before requesting public disconnect.
    _consume_task_exception(task)
    _schedule_asr_startup_cleanup(room, None, match_id=match_id)


@dataclass(slots=True)
class _ReceiverStartAttempt:
    failed: bool = False


@dataclass(slots=True)
class _TrackConsumer:
    room: rtc.Room
    key: tuple[str, str]
    track: rtc.Track
    publication: rtc.RemoteTrackPublication
    participant: rtc.RemoteParticipant
    task: asyncio.Task[None] | None = None
    retired: bool = False


@dataclass(frozen=True, slots=True)
class PausedSpeech:
    speech_id: UUID
    final_text: str
    audio_duration_ms: int
    last_segment_no: int
    first_interim_latency_ms: int | None
    callback_envelope: CallbackEnvelope


class AsrRuntimeCallbacks(Protocol):
    async def publish_asr_interim(
        self, *, envelope: CallbackEnvelope, segment_no: int, text: str
    ) -> None: ...

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
    ) -> None: ...

    async def start_asr_segment(
        self, *, envelope: CallbackEnvelope, segment_no: int, task_id: UUID
    ) -> None: ...

    async def fail_asr_segment(
        self, *, envelope: CallbackEnvelope, segment_no: int, task_id: UUID, error_code: str
    ) -> None: ...

    async def persist_asr_capture(
        self, *, envelope: CallbackEnvelope, task_id: UUID, provider_capture: ProviderCallCapture
    ) -> None: ...

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
    ) -> object: ...

    async def handle_asr_failure(self, *, envelope: CallbackEnvelope, code: str) -> None: ...


class MatchAudioReceiver:
    def __init__(
        self,
        *,
        match_id: UUID,
        settings: Settings,
        callbacks: AsrRuntimeCallbacks,
        capture_writer: ProviderCaptureWriter | None = None,
    ) -> None:
        self.match_id = match_id
        self._settings = settings
        self._callbacks = callbacks
        self._capture_writer = capture_writer
        self._room: rtc.Room | None = None
        self._connection: FunAsrConnection | None = None
        self._track_tasks: set[asyncio.Task[None]] = set()
        self._subscribed_tracks: dict[tuple[str, str], _TrackConsumer] = {}
        self._retired_track_room: rtc.Room | None = None
        self._failed_start_attempt: _ReceiverStartAttempt | None = None
        self._speech: AsrSpeechSession | None = None
        self._speech_task: asyncio.Task[None] | None = None
        self._speaker_user_id: UUID | None = None
        self._pause_requested = False
        self._paused_speech: PausedSpeech | None = None
        self._recording_file: BinaryIO | None = None
        self._recording_speech_id: UUID | None = None
        self._recording_spool_path: Path | None = None
        self._recording_storage_path: Path | None = None
        self._recording_error: str | None = None
        self._speech_envelope: CallbackEnvelope | None = None
        self._lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._closed = False
        self._close_requested = False

    async def start(self, *, _attempt: _ReceiverStartAttempt | None = None) -> None:
        async with self._lifecycle_lock:
            if self._closed or self._close_requested:
                raise FunAsrError("asr_receiver_closed")
            try:
                await self._start()
            except BaseException:
                if _attempt is not None:
                    # Claim only failures after entering this lifecycle lock.
                    # Waiting callers cancelled before entry own no retirement.
                    _attempt.failed = True
                    self._failed_start_attempt = _attempt
                    self._closed = True
                raise

    def owns_failed_start(self, attempt: _ReceiverStartAttempt) -> bool:
        return attempt.failed and self._failed_start_attempt is attempt

    def request_close(self) -> None:
        """Retire pending handoff before close waits for the lifecycle lock."""

        self._close_requested = True

    async def _start(self) -> None:
        if self._closed or self._close_requested:
            raise FunAsrError("asr_receiver_closed")
        if self._room is not None:
            return
        if (
            self._settings.livekit_url is None
            or self._settings.livekit_api_key is None
            or self._settings.livekit_api_secret is None
        ):
            raise FunAsrError("livekit_not_configured")
        if self._settings.asr_api_key is None:
            raise FunAsrError("asr_not_configured")
        room_name = f"jx-match-{self.match_id}"
        token = (
            api.AccessToken(
                self._settings.livekit_api_key.get_secret_value(),
                self._settings.livekit_api_secret.get_secret_value(),
            )
            .with_identity(f"jx-asr-{self.match_id}-{uuid4().hex}")
            .with_ttl(timedelta(minutes=10))
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=False,
                    can_subscribe=True,
                    can_publish_data=False,
                )
            )
            .to_jwt()
        )
        room = rtc.Room()
        receiver_ref, room_ref = ref(self), ref(room)
        match_id = self.match_id
        connection: FunAsrConnection | None = None
        connection_task: asyncio.Task[None] | None = None
        previous_tasks = self._track_tasks.copy()

        def on_track_subscribed(
            track: rtc.Track,
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            receiver, subscribed_room = receiver_ref(), room_ref()
            if receiver is not None and subscribed_room is not None:
                receiver._on_track_subscribed(subscribed_room, track, publication, participant)

        def on_track_unsubscribed(
            track: rtc.Track | None,
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            receiver, subscribed_room = receiver_ref(), room_ref()
            if receiver is not None and subscribed_room is not None:
                receiver._on_track_unsubscribed(subscribed_room, track, publication, participant)

        def on_track_unpublished(
            publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant
        ) -> None:
            receiver, subscribed_room = receiver_ref(), room_ref()
            if receiver is not None and subscribed_room is not None:
                receiver._on_track_unpublished(subscribed_room, publication, participant)

        def on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
            receiver, subscribed_room = receiver_ref(), room_ref()
            if receiver is not None and subscribed_room is not None:
                receiver._on_participant_disconnected(subscribed_room, participant)

        def on_participant_connected(participant: rtc.RemoteParticipant) -> None:
            receiver, subscribed_room = receiver_ref(), room_ref()
            if receiver is not None and subscribed_room is not None:
                receiver._on_participant_connected(subscribed_room, participant)

        def on_disconnected(_: Any) -> None:
            receiver, subscribed_room = receiver_ref(), room_ref()
            if receiver is not None and subscribed_room is not None:
                receiver._on_track_room_disconnected(subscribed_room)

        def on_reconnected() -> None:
            receiver, subscribed_room = receiver_ref(), room_ref()
            if receiver is not None and subscribed_room is not None:
                receiver._on_track_room_reconnected(subscribed_room)

        try:
            room.on("track_subscribed", on_track_subscribed)  # pyright: ignore[reportUnknownMemberType]
            room.on("track_unsubscribed", on_track_unsubscribed)  # pyright: ignore[reportUnknownMemberType]
            room.on("track_unpublished", on_track_unpublished)  # pyright: ignore[reportUnknownMemberType]
            room.on("participant_disconnected", on_participant_disconnected)  # pyright: ignore[reportUnknownMemberType]
            room.on("participant_connected", on_participant_connected)  # pyright: ignore[reportUnknownMemberType]
            room.on("disconnected", on_disconnected)  # pyright: ignore[reportUnknownMemberType]
            room.on("reconnected", on_reconnected)  # pyright: ignore[reportUnknownMemberType]
            connection_task = asyncio.create_task(
                room.connect(
                    self._settings.livekit_url, token, rtc.RoomOptions(auto_subscribe=True)
                ),
                name=f"asr-connect-{match_id}",
            )
            connection_task.add_done_callback(_consume_task_exception)
            try:
                await asyncio.wait_for(
                    asyncio.shield(connection_task),
                    timeout=LIVEKIT_CONNECT_TIMEOUT_SECONDS,
                )
            except TimeoutError as error:
                raise FunAsrError("asr_start_timeout") from error
            if self._closed or self._close_requested:
                raise FunAsrError("asr_receiver_closed")
            connection = self._new_provider_connection()
            # Inventory is read before handoff. Events during join cannot consume
            # tracks, and the inventory restores every already published track.
            tracks = [
                (publication.track, publication, participant)
                for participant in room.remote_participants.values()
                for publication in participant.track_publications.values()
                if publication.track is not None
                and publication.track.kind == rtc.TrackKind.KIND_AUDIO
            ]
            self._room, self._connection = room, connection
            self._retired_track_room = None
            for track, publication, participant in tracks:
                on_track_subscribed(track, publication, participant)
        except BaseException:
            if self._room is room:
                self._room = None
            if self._connection is connection:
                self._connection = None
            if self._retired_track_room is room:
                self._retired_track_room = None
            for owner in tuple(self._subscribed_tracks.values()):
                if owner.room is room:
                    self._retire_track_consumer(owner)
            retired_tasks = tuple(self._track_tasks - previous_tasks)
            self._track_tasks.difference_update(retired_tasks)
            for task in retired_tasks:
                task.add_done_callback(_consume_task_exception)
                if not task.cancelling():
                    task.cancel()
            if connection_task is not None and not connection_task.done():
                connection_task.add_done_callback(
                    partial(_retired_asr_connection_finished, room=room, match_id=match_id)
                )
            else:
                cleanup = _schedule_asr_startup_cleanup(room, connection, match_id=match_id)
                if retired_tasks:
                    waiting = asyncio.create_task(
                        _wait_for_retired_asr_tasks(
                            retired_tasks, resource="start_track", match_id=match_id
                        )
                    )
                    waiting.add_done_callback(_consume_task_exception)
                    cleanup.append(waiting)
                with suppress(asyncio.CancelledError):
                    await asyncio.wait(cleanup)
            raise

    def _on_track_subscribed(
        self,
        room: rtc.Room,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        if (
            self._closed
            or self._close_requested
            or self._room is not room
            or self._retired_track_room is room
            or track.kind != rtc.TrackKind.KIND_AUDIO
        ):
            return
        key = (participant.identity, publication.sid)
        visible_participant = room.remote_participants.get(participant.identity)
        if visible_participant is not None:
            visible_publication = visible_participant.track_publications.get(publication.sid)
            if (
                visible_participant is not participant
                or visible_publication is None
                or visible_publication is not publication
                or visible_publication.track is not track
            ):
                return
        # Keep one track per live publication, including an event which precedes
        # inventory visibility. Finished streams do not restart on duplicate events.
        self._prune_track_consumers(room, keep_key=key)
        previous = self._subscribed_tracks.get(key)
        if (
            previous is not None
            and previous.track is track
            and previous.publication is publication
            and previous.participant is participant
        ):
            return
        if previous is not None:
            self._retire_track_consumer(previous)
        owner = _TrackConsumer(
            room=room, key=key, track=track, publication=publication, participant=participant
        )
        self._subscribed_tracks[key] = owner
        logger.info(
            "livekit audio track subscribed",
            extra={"match_id": str(self.match_id), "participant_identity": participant.identity},
        )
        task = asyncio.create_task(
            self._consume_track(track, participant.identity, owner=owner),
            name=f"asr-track-{self.match_id}-{participant.identity}",
        )
        owner.task = task
        self._track_tasks.add(task)
        receiver_ref, match_id = ref(self), self.match_id

        def track_finished(completed: asyncio.Task[None]) -> None:
            receiver = receiver_ref()
            if receiver is not None:
                receiver._track_tasks.discard(completed)
            if completed.cancelled():
                return
            error = completed.exception()
            if error is not None:
                logger.warning(
                    "LiveKit ASR track task failed",
                    extra={
                        "error_code": getattr(error, "code", "asr_track_failed"),
                        "match_id": str(match_id),
                    },
                )

        task.add_done_callback(track_finished)

    def _on_track_unsubscribed(
        self,
        room: rtc.Room,
        track: rtc.Track | None,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        if self._room is not room or track is None:
            return
        owner = self._subscribed_tracks.get((participant.identity, publication.sid))
        if (
            owner is not None
            and owner.room is room
            and owner.track is track
            and owner.publication is publication
            and owner.participant is participant
        ):
            self._retire_track_consumer(owner)

    def _on_track_unpublished(
        self,
        room: rtc.Room,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        if self._room is not room:
            return
        owner = self._subscribed_tracks.get((participant.identity, publication.sid))
        if (
            owner is not None
            and owner.room is room
            and owner.publication is publication
            and owner.participant is participant
        ):
            self._retire_track_consumer(owner)

    def _on_participant_disconnected(
        self, room: rtc.Room, participant: rtc.RemoteParticipant
    ) -> None:
        if self._room is not room:
            return
        for owner in tuple(self._subscribed_tracks.values()):
            if owner.room is room and owner.participant is participant:
                self._retire_track_consumer(owner)

    def _on_track_room_disconnected(self, room: rtc.Room) -> None:
        if self._room is not room:
            return
        self._retired_track_room = room
        for owner in tuple(self._subscribed_tracks.values()):
            if owner.room is room:
                self._retire_track_consumer(owner)

    def _on_participant_connected(self, room: rtc.Room, participant: rtc.RemoteParticipant) -> None:
        if self._room is room and room.remote_participants.get(participant.identity) is participant:
            self._prune_track_consumers(room)

    def _on_track_room_reconnected(self, room: rtc.Room) -> None:
        if self._room is room:
            self._retired_track_room = None
            self._prune_track_consumers(room)

    def _prune_track_consumers(
        self, room: rtc.Room, *, keep_key: tuple[str, str] | None = None
    ) -> None:
        if self._room is not room:
            return
        for owner in tuple(self._subscribed_tracks.values()):
            if owner.key == keep_key:
                continue
            participant = room.remote_participants.get(owner.key[0])
            publication = (
                participant.track_publications.get(owner.key[1])
                if participant is not None and participant is owner.participant
                else None
            )
            if (
                publication is None
                or publication is not owner.publication
                or publication.track is not owner.track
            ):
                self._retire_track_consumer(owner)

    def _retire_track_consumer(self, owner: _TrackConsumer) -> None:
        if owner.retired:
            return
        # Fence input synchronously; task cancellation and native EOS may be late.
        owner.retired = True
        if self._subscribed_tracks.get(owner.key) is owner:
            self._subscribed_tracks.pop(owner.key)
        task = owner.task
        if task is not None and not task.done() and not task.cancelling():
            task.cancel()

    def _track_consumer_is_current(self, owner: _TrackConsumer) -> bool:
        return (
            not owner.retired
            and not self._closed
            and not self._close_requested
            and self._room is owner.room
            and self._subscribed_tracks.get(owner.key) is owner
        )

    async def start_speech(
        self, speech_id: UUID, speaker_user_id: UUID, envelope: CallbackEnvelope
    ) -> None:
        envelope.require("speech_id", "connection_epoch")
        if envelope.match_id != self.match_id or envelope.speech_id != speech_id:
            raise FunAsrError("asr_callback_identity_mismatch")
        async with self._lifecycle_lock:
            if self._closed:
                raise FunAsrError("asr_receiver_closed")
            async with self._lock:
                await self._start()
                await self._start_speech_locked(speech_id, speaker_user_id, envelope)

    def _new_provider_connection(self) -> FunAsrConnection:
        if self._settings.asr_api_key is None:
            raise FunAsrError("asr_not_configured")
        capture_writer = self._capture_writer
        return FunAsrConnection(
            url=self._settings.asr_ws_url,
            api_key=self._settings.asr_api_key.get_secret_value(),
            model=self._settings.asr_model,
            workspace_id=self._settings.asr_workspace_id,
            capture_writer=capture_writer,
            capture_factory=lambda: new_provider_capture(capture_writer),
        )

    async def _start_speech_locked(
        self, speech_id: UUID, speaker_user_id: UUID, envelope: CallbackEnvelope
    ) -> None:
        if self._room is None:
            raise FunAsrError("asr_not_configured")
        if self._connection is None:
            self._connection = self._new_provider_connection()
        connection = self._connection
        if self._speech is not None:
            if self._speech.input_closed:
                raise FunAsrError("asr_finalization_pending")
            raise FunAsrError("asr_speech_busy")
        paused = self._paused_speech
        if paused is not None and paused.speech_id != speech_id:
            raise FunAsrError("asr_speech_busy")
        callback_envelope = (
            replace(
                paused.callback_envelope,
                connection_epoch=envelope.connection_epoch,
                context_version=envelope.context_version,
            )
            if paused
            else envelope
        )
        session: AsrSpeechSession

        def owned(
            callback: Callable[..., Awaitable[Any]], *, capture_callback: bool = False
        ) -> Callable[..., Awaitable[Any]]:
            async def dispatch(*args: Any) -> Any:
                if self._speech is not session:
                    if capture_callback:
                        cast_capture = args[1]
                        cast_capture.close()
                    raise asyncio.CancelledError
                try:
                    result = await callback(*args, envelope=callback_envelope)
                except BaseException:
                    if self._speech is not session:
                        raise asyncio.CancelledError from None
                    raise
                if self._speech is not session:
                    raise asyncio.CancelledError
                return result

            return dispatch

        session = AsrSpeechSession(
            speech_id=speech_id,
            connection=connection,
            on_interim=owned(self._on_interim),
            on_segment=owned(self._on_segment),
            on_segment_started=owned(self._on_segment_started),
            on_segment_failed=owned(self._on_segment_failed),
            on_capture=owned(self._on_capture, capture_callback=True),
            start_segment_no=paused.last_segment_no if paused else 0,
            initial_text=paused.final_text if paused else "",
            initial_sample_count=(paused.audio_duration_ms * 16 if paused else 0),
            initial_first_interim_latency_ms=(paused.first_interim_latency_ms if paused else None),
        )
        self._speech = session
        self._speaker_user_id = speaker_user_id
        self._speech_envelope = callback_envelope
        if paused is None:
            self._start_recording(speech_id, speaker_user_id)
        self._speech_task = asyncio.create_task(
            self._run_speech(session, callback_envelope), name=f"asr-speech-{speech_id}"
        )
        self._paused_speech = None

    async def finish_speech(self, speech_id: UUID) -> None:
        async with self._lifecycle_lock:
            session = self._speech
            task = self._speech_task
            if session is None or task is None or session.speech_id != speech_id:
                raise FunAsrError("asr_speech_not_found")
        await session.finish()
        await task

    async def abort_speech(self, *, expected_session: AsrSpeechSession | None = None) -> None:
        async with self._lifecycle_lock:
            if expected_session is not None and self._speech is not expected_session:
                return
            task = self._detach_speech()
            connection = self._connection
            try:
                await self._cancel_task(task)
            finally:
                if (
                    task is not None
                    and task is not asyncio.current_task()
                    and not task.done()
                    and connection is not None
                    and self._connection is connection
                ):
                    self._connection = None
                    cleanup = asyncio.create_task(
                        _close_retired_asr_provider(connection, (task,), match_id=self.match_id)
                    )
                    cleanup.add_done_callback(_consume_task_exception)

    def _detach_speech(self) -> asyncio.Task[None] | None:
        task, self._speech_task = self._speech_task, None
        self._speech = None
        self._speaker_user_id = None
        self._paused_speech = None
        self._speech_envelope = None
        self._pause_requested = False
        self._discard_recording()
        return task

    async def _cancel_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None or task is asyncio.current_task():
            return
        await self._cancel_tasks((task,), resource="speech")

    async def _cancel_tasks(self, tasks: tuple[asyncio.Task[None], ...], *, resource: str) -> None:
        current = asyncio.current_task()
        pending_tasks = {task for task in tasks if task is not current and not task.done()}
        if not pending_tasks:
            for task in tasks:
                if not task.cancelled() and task.done():
                    _consume_task_exception(task)
            return
        for task in pending_tasks:
            task.cancel()
        try:
            done, pending = await asyncio.wait(
                pending_tasks, timeout=ASR_TASK_CANCEL_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            for task in pending_tasks:
                task.add_done_callback(_consume_task_exception)
            raise
        for task in done:
            _consume_task_exception(task)
        if pending:
            for task in pending:
                task.add_done_callback(_consume_task_exception)
            logger.warning(
                "ASR task cancellation timed out",
                extra={
                    "error_code": "asr_task_cancel_timeout",
                    "match_id": str(self.match_id),
                    "resource": resource,
                    "task_count": len(pending),
                },
            )

    async def pause_speech(self, speech_id: UUID) -> None:
        async with self._lifecycle_lock:
            session = self._speech
            task = self._speech_task
            if session is None or task is None or session.speech_id != speech_id:
                return
            self._pause_requested = True
        await session.finish()
        await task

    async def close(self) -> None:
        self.request_close()
        async with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            speech_task = self._speech_task
            try:
                self._detach_speech()
            except Exception as error:
                # A failed spool unlink must not leave a published path that a
                # late retired speech could delete after a replacement records.
                self._recording_speech_id = None
                self._recording_spool_path = None
                self._recording_storage_path = None
                self._recording_error = None
                logger.warning(
                    "ASR recording cleanup failed",
                    extra={
                        "error_code": "asr_cleanup_failed",
                        "match_id": str(self.match_id),
                        "resource": "recording",
                        "exception_type": type(error).__name__,
                    },
                )
            track_tasks = tuple(self._track_tasks)
            self._track_tasks.clear()
            for owner in tuple(self._subscribed_tracks.values()):
                self._retire_track_consumer(owner)
            self._subscribed_tracks.clear()
            connection, self._connection = self._connection, None
            room, self._room = self._room, None
            self._retired_track_room = None
        current = asyncio.current_task()
        speech_tasks = (
            (speech_task,) if speech_task is not None and speech_task is not current else ()
        )
        track_tasks = tuple(task for task in track_tasks if task is not current)
        retired_tasks = speech_tasks + track_tasks
        for task in retired_tasks:
            task.add_done_callback(_consume_task_exception)
            if not task.done() and (task not in track_tasks or not task.cancelling()):
                task.cancel()
        cleanup: list[asyncio.Task[None]] = []
        for tasks, resource in ((speech_tasks, "speech"), (track_tasks, "track")):
            if tasks:
                cleanup.append(
                    asyncio.create_task(
                        _wait_for_retired_asr_tasks(
                            tasks, resource=resource, match_id=self.match_id
                        )
                    )
                )
        if connection is not None:
            cleanup.append(
                asyncio.create_task(
                    _close_retired_asr_provider(connection, retired_tasks, match_id=self.match_id)
                )
            )
        if room is not None:
            cleanup.append(
                asyncio.create_task(
                    _bounded_cleanup_call(
                        lambda: room.disconnect(), resource="room", match_id=self.match_id
                    )
                )
            )
        for task in cleanup:
            task.add_done_callback(_consume_task_exception)
        if cleanup:
            # Caller cancellation retires only its wait; all exact-resource owners
            # must remain scheduled, including factories not yet entered.
            await asyncio.wait(cleanup)

    async def _run_speech(self, session: AsrSpeechSession, envelope: CallbackEnvelope) -> None:
        try:
            result = await session.run()
            if self._speech is not session:
                return
            if self._pause_requested:
                self._paused_speech = PausedSpeech(
                    speech_id=session.speech_id,
                    final_text=result.final_text,
                    audio_duration_ms=result.audio_duration_ms,
                    last_segment_no=result.last_segment_no,
                    first_interim_latency_ms=result.first_interim_latency_ms,
                    callback_envelope=envelope,
                )
            else:
                audio_storage_path = self._publish_recording()
                await self._callbacks.finalize_asr_speech(
                    envelope=envelope,
                    final_text=result.final_text,
                    first_interim_latency_ms=result.first_interim_latency_ms,
                    final_latency_ms=max(
                        (item.final_latency_ms for item in result.segments), default=0
                    ),
                    audio_duration_ms=result.audio_duration_ms,
                    audio_storage_path=audio_storage_path,
                    audio_recording_error=self._recording_error,
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if self._speech is not session:
                return
            if self._pause_requested:
                checkpoint = session.checkpoint()
                self._paused_speech = PausedSpeech(
                    speech_id=session.speech_id,
                    final_text=checkpoint.final_text,
                    audio_duration_ms=checkpoint.audio_duration_ms,
                    last_segment_no=checkpoint.last_segment_no,
                    first_interim_latency_ms=checkpoint.first_interim_latency_ms,
                    callback_envelope=envelope,
                )
                logger.info(
                    "asr pause retained checkpoint after provider close failure",
                    extra={"match_id": str(self.match_id), "speech_id": str(session.speech_id)},
                )
            else:
                if self._recording_storage_path is not None:
                    self._recording_storage_path.unlink(missing_ok=True)
                code = error.code if isinstance(error, FunAsrError) else "asr_stream_failed"
                await self._callbacks.handle_asr_failure(
                    envelope=envelope,
                    code=code,
                )
        finally:
            if self._speech is session:
                self._pause_requested = False
                self._speech = None
                self._speech_task = None
                self._speaker_user_id = None

    async def _on_interim(
        self, speech_id: UUID, segment_no: int, text: str, *, envelope: CallbackEnvelope
    ) -> None:
        await self._callbacks.publish_asr_interim(
            envelope=envelope, segment_no=segment_no, text=text
        )

    async def _on_segment(
        self,
        speech_id: UUID,
        segment_no: int,
        result: SegmentResult,
        pcm_sample_count: int,
        *,
        envelope: CallbackEnvelope,
    ) -> None:
        await self._callbacks.persist_asr_segment(
            envelope=envelope,
            speech_id=speech_id,
            segment_no=segment_no,
            task_id=result.task_id,
            final_text=result.final_text,
            first_interim_latency_ms=result.first_interim_latency_ms,
            final_latency_ms=result.final_latency_ms,
            pcm_sample_count=pcm_sample_count,
        )

    async def _on_capture(
        self, task_id: UUID, capture: ProviderCallCapture, *, envelope: CallbackEnvelope | None
    ) -> None:
        callback = getattr(self._callbacks, "persist_asr_capture", None)
        try:
            if callback is None or envelope is None:
                return
            await callback(envelope=envelope, task_id=task_id, provider_capture=capture)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "ASR provider capture persistence failed",
                extra={
                    "error_code": "asr_capture_persist_failed",
                    "match_id": str(self.match_id),
                    "details": {"exception_type": type(error).__name__},
                },
            )
        finally:
            # Preparation may have transferred ownership to the writer; close
            # only releases a still-owned reservation, including detached speech.
            capture.close()

    async def _on_segment_started(
        self, speech_id: UUID, segment_no: int, task_id: UUID, *, envelope: CallbackEnvelope
    ) -> None:
        callback = getattr(self._callbacks, "start_asr_segment", None)
        if callback is not None:
            await callback(envelope=envelope, segment_no=segment_no, task_id=task_id)

    async def _on_segment_failed(
        self,
        speech_id: UUID,
        segment_no: int,
        task_id: UUID,
        error_code: str,
        *,
        envelope: CallbackEnvelope,
    ) -> None:
        callback = getattr(self._callbacks, "fail_asr_segment", None)
        if callback is not None:
            await callback(
                envelope=envelope,
                segment_no=segment_no,
                task_id=task_id,
                error_code=error_code,
            )

    async def _consume_track(
        self, track: rtc.Track, identity: str, *, owner: _TrackConsumer | None = None
    ) -> None:
        if owner is not None and not self._track_consumer_is_current(owner):
            return
        identity_parts = _identity_parts(identity)
        if identity_parts is None:
            return
        user_id, identity_epoch = identity_parts
        stream = rtc.AudioStream(
            track,
            capacity=10,
            sample_rate=16000,
            num_channels=1,
            frame_size_ms=100,
        )
        frame_count = 0
        try:
            async for event in stream:
                if owner is not None and not self._track_consumer_is_current(owner):
                    return
                frame_count += 1
                if frame_count == 1:
                    logger.info(
                        "livekit first audio frame received",
                        extra={
                            "match_id": str(self.match_id),
                            "participant_identity": identity,
                            "sample_rate": event.frame.sample_rate,
                            "samples_per_channel": event.frame.samples_per_channel,
                            "num_channels": event.frame.num_channels,
                        },
                    )
                session = self._speech
                envelope = self._speech_envelope
                if session is None or self._speaker_user_id != user_id:
                    continue
                if envelope is None or identity_epoch is None:
                    return
                if envelope.connection_epoch != identity_epoch:
                    return
                try:
                    pcm = bytes(event.frame.data)
                    self._write_recording(pcm)
                    session.feed_pcm(pcm)
                except AsrQueueFull:
                    try:
                        await asyncio.wait_for(
                            self._callbacks.handle_asr_failure(
                                envelope=envelope, code="asr_pcm_queue_full"
                            ),
                            timeout=ASR_FAILURE_CALLBACK_TIMEOUT_SECONDS,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        logger.warning(
                            "ASR queue-full failure callback failed",
                            extra={
                                "error_code": "asr_pcm_queue_failure_callback",
                                "exception_type": type(error).__name__,
                                "match_id": str(self.match_id),
                            },
                        )
                    # The callback may reset this attempt and release a new
                    # speech. Retire only the producer which actually overflowed,
                    # then retain this publication's audio consumer for retries.
                    await self.abort_speech(expected_session=session)
        finally:
            logger.info(
                "livekit audio stream closed",
                extra={
                    "match_id": str(self.match_id),
                    "participant_identity": identity,
                    "frame_count": frame_count,
                },
            )
            await _bounded_cleanup(stream.aclose(), resource="audio_stream", match_id=self.match_id)

    def _start_recording(self, speech_id: UUID, speaker_user_id: UUID) -> None:
        self._discard_recording()
        storage_root = getattr(self._settings, "agent_audio_storage_dir", None)
        if not storage_root:
            return
        directory = (
            Path(storage_root) / "matches" / str(self.match_id) / "human" / str(speaker_user_id)
        )
        spool = directory / f"{speech_id}.pcm.part"
        storage = directory / f"{speech_id}.pcm"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            self._recording_file = spool.open("wb")
            self._recording_speech_id = speech_id
            self._recording_spool_path = spool
            self._recording_storage_path = storage
            self._recording_error = None
        except OSError:
            self._recording_error = "audio_recording_failed"
            logger.warning(
                "human audio recording unavailable",
                extra={"match_id": str(self.match_id), "error_code": self._recording_error},
            )

    def _write_recording(self, pcm: bytes) -> None:
        if self._recording_file is None:
            return
        try:
            self._recording_file.write(pcm)
        except OSError:
            self._recording_error = "audio_recording_failed"
            self._close_recording_file()
            if self._recording_spool_path is not None:
                self._recording_spool_path.unlink(missing_ok=True)

    def _publish_recording(self) -> str | None:
        spool = self._recording_spool_path
        storage = self._recording_storage_path
        if self._recording_file is None or spool is None or storage is None:
            return None
        try:
            self._recording_file.flush()
            os.fsync(self._recording_file.fileno())
            self._close_recording_file()
            os.replace(spool, storage)
            return str(storage)
        except OSError:
            self._recording_error = "audio_recording_failed"
            self._close_recording_file()
            spool.unlink(missing_ok=True)
            storage.unlink(missing_ok=True)
            return None

    def _close_recording_file(self) -> None:
        file = self._recording_file
        self._recording_file = None
        if file is not None:
            try:
                file.close()
            except OSError:
                pass

    def _discard_recording(self) -> None:
        self._close_recording_file()
        if self._recording_spool_path is not None:
            self._recording_spool_path.unlink(missing_ok=True)
        self._recording_speech_id = None
        self._recording_spool_path = None
        self._recording_storage_path = None
        self._recording_error = None


def _user_id_from_identity(identity: str) -> UUID | None:
    parts = _identity_parts(identity)
    return parts[0] if parts is not None else None


def _identity_parts(identity: str) -> tuple[UUID, int | None] | None:
    match = _LIVEKIT_HUMAN_IDENTITY_RE.fullmatch(identity) or _LEGACY_USER_IDENTITY_RE.match(
        identity
    )
    if match is None:
        return None
    try:
        epoch = int(match.group("epoch")) if "epoch" in match.groupdict() else None
        return UUID(match.group("user_id")), epoch
    except ValueError:
        return None


class AsrRuntime:
    def __init__(
        self,
        *,
        settings: Settings,
        callbacks: AsrRuntimeCallbacks,
        capture_writer: ProviderCaptureWriter | None = None,
    ) -> None:
        self._settings = settings
        self._callbacks = callbacks
        self._capture_writer = capture_writer
        self._receivers: dict[UUID, MatchAudioReceiver] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._closed_matches: OrderedDict[UUID, None] = OrderedDict()

    def _remember_closed_match_locked(self, match_id: UUID) -> None:
        self._closed_matches[match_id] = None
        self._closed_matches.move_to_end(match_id)
        while len(self._closed_matches) > ASR_CLOSED_MATCH_TOMBSTONE_LIMIT:
            self._closed_matches.popitem(last=False)

    async def ensure_match(self, match_id: UUID) -> MatchAudioReceiver:
        async with self._lock:
            if self._closed or match_id in self._closed_matches:
                raise FunAsrError("asr_receiver_closed")
            receiver = self._receivers.get(match_id)
            if receiver is None:
                receiver = MatchAudioReceiver(
                    match_id=match_id,
                    settings=self._settings,
                    callbacks=self._callbacks,
                    capture_writer=self._capture_writer,
                )
                self._receivers[match_id] = receiver
        attempt = _ReceiverStartAttempt()
        try:
            await receiver.start(_attempt=attempt)
        except BaseException:
            if receiver.owns_failed_start(attempt):
                cleanup = asyncio.create_task(
                    self._retire_failed_start(match_id, receiver, attempt)
                )
                cleanup.add_done_callback(_consume_task_exception)
                with suppress(asyncio.CancelledError):
                    await asyncio.wait({cleanup}, timeout=ASR_CLEANUP_TIMEOUT_SECONDS)
            raise
        return receiver

    async def _retire_failed_start(
        self, match_id: UUID, receiver: MatchAudioReceiver, attempt: _ReceiverStartAttempt
    ) -> None:
        async with self._lock:
            if not receiver.owns_failed_start(attempt):
                return
            if self._receivers.get(match_id) is receiver:
                self._receivers.pop(match_id, None)
        await receiver.close()

    async def start_speech(
        self,
        match_id: UUID,
        speech_id: UUID,
        user_id: UUID,
        envelope: CallbackEnvelope,
    ) -> None:
        receiver = await self.ensure_match(match_id)
        await receiver.start_speech(speech_id, user_id, envelope)

    async def finish_speech(self, match_id: UUID, speech_id: UUID) -> None:
        receiver = self._receivers.get(match_id)
        if receiver is not None:
            await receiver.finish_speech(speech_id)

    async def pause_speech(self, match_id: UUID, speech_id: UUID) -> None:
        receiver = self._receivers.get(match_id)
        if receiver is not None:
            await receiver.pause_speech(speech_id)

    async def reset_speech(self, match_id: UUID) -> None:
        receiver = self._receivers.get(match_id)
        if receiver is not None:
            await receiver.abort_speech()

    async def close_match(self, match_id: UUID) -> None:
        async with self._lock:
            self._remember_closed_match_locked(match_id)
            receiver = self._receivers.pop(match_id, None)
            if receiver is not None:
                receiver.request_close()
        if receiver is not None:
            await receiver.close()

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            receivers = list(self._receivers.values())
            self._receivers.clear()
            for receiver in receivers:
                receiver.request_close()
        await asyncio.gather(*(receiver.close() for receiver in receivers), return_exceptions=True)


__all__ = [
    "AsrRuntime",
    "AsrRuntimeCallbacks",
    "MatchAudioReceiver",
    "_user_id_from_identity",
]

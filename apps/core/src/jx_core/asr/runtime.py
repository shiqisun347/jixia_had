"""LiveKit server subscriber feeding bounded Fun-ASR speech sessions."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import UUID

from livekit import api, rtc

from ..config import Settings
from .protocol import FunAsrConnection, FunAsrError, SegmentResult
from .session import AsrQueueFull, AsrSpeechSession

logger = logging.getLogger("jx-core.asr")


@dataclass(frozen=True, slots=True)
class PausedSpeech:
    speech_id: UUID
    final_text: str
    audio_duration_ms: int
    last_segment_no: int
    first_interim_latency_ms: int | None


class AsrRuntimeCallbacks(Protocol):
    async def publish_asr_interim(
        self, match_id: UUID, speech_id: UUID, segment_no: int, text: str
    ) -> None: ...

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
    ) -> None: ...

    async def start_asr_segment(
        self, *, speech_id: UUID, segment_no: int, task_id: UUID
    ) -> None: ...

    async def fail_asr_segment(
        self, *, speech_id: UUID, segment_no: int, task_id: UUID, error_code: str
    ) -> None: ...

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
    ) -> object: ...

    async def handle_asr_failure(self, match_id: UUID, speech_id: UUID, code: str) -> None: ...


class MatchAudioReceiver:
    def __init__(
        self,
        *,
        match_id: UUID,
        settings: Settings,
        callbacks: AsrRuntimeCallbacks,
    ) -> None:
        self.match_id = match_id
        self._settings = settings
        self._callbacks = callbacks
        self._room: rtc.Room | None = None
        self._connection: FunAsrConnection | None = None
        self._track_tasks: set[asyncio.Task[None]] = set()
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
        self._lock = asyncio.Lock()

    async def start(self) -> None:
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
            .with_identity(f"jx-asr-{self.match_id}")
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

        def on_track_subscribed(
            track: rtc.Track,
            _: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            logger.info(
                "livekit audio track subscribed",
                extra={
                    "match_id": str(self.match_id),
                    "participant_identity": participant.identity,
                },
            )
            task = asyncio.create_task(
                self._consume_track(track, participant.identity),
                name=f"asr-track-{self.match_id}-{participant.identity}",
            )
            self._track_tasks.add(task)
            task.add_done_callback(self._track_tasks.discard)

        room.on("track_subscribed", on_track_subscribed)  # pyright: ignore[reportUnknownMemberType]

        await room.connect(self._settings.livekit_url, token, rtc.RoomOptions(auto_subscribe=True))
        self._room = room
        self._connection = FunAsrConnection(
            url=self._settings.asr_ws_url,
            api_key=self._settings.asr_api_key.get_secret_value(),
            model=self._settings.asr_model,
            workspace_id=self._settings.asr_workspace_id,
        )

        # Tracks can be published before the service participant finishes connecting.
        for participant in room.remote_participants.values():
            for publication in participant.track_publications.values():
                if (
                    publication.track is not None
                    and publication.track.kind == rtc.TrackKind.KIND_AUDIO
                ):
                    on_track_subscribed(publication.track, publication, participant)

    async def start_speech(self, speech_id: UUID, speaker_user_id: UUID) -> None:
        async with self._lock:
            if self._room is None or self._connection is None:
                await self.start()
            connection = self._connection
            if connection is None:
                raise FunAsrError("asr_not_configured")
            if self._speech is not None:
                raise FunAsrError("asr_speech_busy")
            paused = self._paused_speech
            if paused is not None and paused.speech_id != speech_id:
                raise FunAsrError("asr_speech_busy")
            session = AsrSpeechSession(
                speech_id=speech_id,
                connection=connection,
                on_interim=self._on_interim,
                on_segment=self._on_segment,
                on_segment_started=self._on_segment_started,
                on_segment_failed=self._on_segment_failed,
                start_segment_no=paused.last_segment_no if paused else 0,
                initial_text=paused.final_text if paused else "",
                initial_sample_count=(paused.audio_duration_ms * 16 if paused else 0),
                initial_first_interim_latency_ms=(
                    paused.first_interim_latency_ms if paused else None
                ),
            )
            self._speech = session
            self._speaker_user_id = speaker_user_id
            if paused is None:
                self._start_recording(speech_id, speaker_user_id)
            self._speech_task = asyncio.create_task(
                self._run_speech(session), name=f"asr-speech-{speech_id}"
            )
        try:
            await session.wait_ready()
            self._paused_speech = None
        except Exception:
            task, self._speech_task = self._speech_task, None
            self._speech = None
            self._speaker_user_id = None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise FunAsrError("asr_start_timeout") from None

    async def finish_speech(self, speech_id: UUID) -> None:
        session = self._speech
        task = self._speech_task
        if session is None or task is None or session.speech_id != speech_id:
            raise FunAsrError("asr_speech_not_found")
        await session.finish()
        await task

    async def abort_speech(self) -> None:
        task, self._speech_task = self._speech_task, None
        self._speech = None
        self._speaker_user_id = None
        self._paused_speech = None
        self._discard_recording()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def pause_speech(self, speech_id: UUID) -> None:
        session = self._speech
        task = self._speech_task
        if session is None or task is None or session.speech_id != speech_id:
            return
        self._pause_requested = True
        await session.finish()
        await task

    async def close(self) -> None:
        await self.abort_speech()
        for task in tuple(self._track_tasks):
            task.cancel()
        if self._track_tasks:
            await asyncio.gather(*self._track_tasks, return_exceptions=True)
        if self._connection is not None:
            await self._connection.close()
        if self._room is not None:
            await self._room.disconnect()
        self._connection = None
        self._room = None

    async def _run_speech(self, session: AsrSpeechSession) -> None:
        try:
            result = await session.run()
            if self._pause_requested:
                self._paused_speech = PausedSpeech(
                    speech_id=session.speech_id,
                    final_text=result.final_text,
                    audio_duration_ms=result.audio_duration_ms,
                    last_segment_no=result.last_segment_no,
                    first_interim_latency_ms=result.first_interim_latency_ms,
                )
            else:
                audio_storage_path = self._publish_recording()
                await self._callbacks.finalize_asr_speech(
                    match_id=self.match_id,
                    speech_id=session.speech_id,
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
            if self._recording_storage_path is not None:
                self._recording_storage_path.unlink(missing_ok=True)
            code = error.code if isinstance(error, FunAsrError) else "asr_stream_failed"
            await self._callbacks.handle_asr_failure(self.match_id, session.speech_id, code)
        finally:
            self._pause_requested = False
            if self._speech is session:
                self._speech = None
                self._speech_task = None
                self._speaker_user_id = None

    async def _on_interim(self, speech_id: UUID, segment_no: int, text: str) -> None:
        await self._callbacks.publish_asr_interim(self.match_id, speech_id, segment_no, text)

    async def _on_segment(
        self, speech_id: UUID, segment_no: int, result: SegmentResult, pcm_sample_count: int
    ) -> None:
        await self._callbacks.persist_asr_segment(
            speech_id=speech_id,
            segment_no=segment_no,
            task_id=result.task_id,
            final_text=result.final_text,
            first_interim_latency_ms=result.first_interim_latency_ms,
            final_latency_ms=result.final_latency_ms,
            pcm_sample_count=pcm_sample_count,
        )

    async def _on_segment_started(self, speech_id: UUID, segment_no: int, task_id: UUID) -> None:
        callback = getattr(self._callbacks, "start_asr_segment", None)
        if callback is not None:
            await callback(speech_id=speech_id, segment_no=segment_no, task_id=task_id)

    async def _on_segment_failed(
        self,
        speech_id: UUID,
        segment_no: int,
        task_id: UUID,
        error_code: str,
    ) -> None:
        callback = getattr(self._callbacks, "fail_asr_segment", None)
        if callback is not None:
            await callback(
                speech_id=speech_id,
                segment_no=segment_no,
                task_id=task_id,
                error_code=error_code,
            )

    async def _consume_track(self, track: rtc.Track, identity: str) -> None:
        user_id = _user_id_from_identity(identity)
        if user_id is None:
            return
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
                if session is None or self._speaker_user_id != user_id:
                    continue
                try:
                    pcm = bytes(event.frame.data)
                    self._write_recording(pcm)
                    session.feed_pcm(pcm)
                except AsrQueueFull:
                    await self._callbacks.handle_asr_failure(
                        self.match_id, session.speech_id, "asr_pcm_queue_full"
                    )
                    return
        finally:
            logger.info(
                "livekit audio stream closed",
                extra={
                    "match_id": str(self.match_id),
                    "participant_identity": identity,
                    "frame_count": frame_count,
                },
            )
            await stream.aclose()

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
    if not identity.startswith("user-") or len(identity) < 41:
        return None
    try:
        return UUID(identity[5:41])
    except ValueError:
        return None


class AsrRuntime:
    def __init__(self, *, settings: Settings, callbacks: AsrRuntimeCallbacks) -> None:
        self._settings = settings
        self._callbacks = callbacks
        self._receivers: dict[UUID, MatchAudioReceiver] = {}
        self._lock = asyncio.Lock()

    async def ensure_match(self, match_id: UUID) -> None:
        async with self._lock:
            receiver = self._receivers.get(match_id)
            if receiver is None:
                receiver = MatchAudioReceiver(
                    match_id=match_id, settings=self._settings, callbacks=self._callbacks
                )
                self._receivers[match_id] = receiver
        try:
            await receiver.start()
        except Exception:
            async with self._lock:
                self._receivers.pop(match_id, None)
            await receiver.close()
            raise

    async def start_speech(self, match_id: UUID, speech_id: UUID, user_id: UUID) -> None:
        await self.ensure_match(match_id)
        receiver = self._receivers[match_id]
        await receiver.start_speech(speech_id, user_id)

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
            receiver = self._receivers.pop(match_id, None)
        if receiver is not None:
            await receiver.close()

    async def close(self) -> None:
        async with self._lock:
            receivers = list(self._receivers.values())
            self._receivers.clear()
        await asyncio.gather(*(receiver.close() for receiver in receivers), return_exceptions=True)


__all__ = ["AsrRuntime", "AsrRuntimeCallbacks", "MatchAudioReceiver"]

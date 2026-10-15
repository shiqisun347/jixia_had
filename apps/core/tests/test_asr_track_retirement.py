"""Exact publication retirement with original receiver; RTC frames only are synthetic."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from livekit import rtc

from jx_core.asr.runtime import MatchAudioReceiver
from jx_core.runtime_identity import CallbackEnvelope
from test_asr_startup_identity import Boundary, receiver

TrackItem = tuple[Any, Any, Any, asyncio.Task[None]]


class Stream:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=10)
        self.waiting = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.late_release = asyncio.Event()
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_release.set()
        self.deliver_after_cancel = False
        self.close_count = 0
        self.close_cancelled = 0

    def __aiter__(self) -> Stream:
        return self

    async def __anext__(self) -> Any:
        self.waiting.set()
        try:
            return await self.queue.get()
        except asyncio.CancelledError:
            self.cancelled.set()
            if not self.deliver_after_cancel:
                raise
            await self.late_release.wait()
            return self.frame()

    @staticmethod
    def frame() -> Any:
        return SimpleNamespace(
            frame=SimpleNamespace(
                data=b"\x01" * 3200,
                sample_rate=16000,
                samples_per_channel=1600,
                num_channels=1,
            )
        )

    async def aclose(self) -> None:
        self.close_count += 1
        self.close_entered.set()
        try:
            await self.close_release.wait()
        except asyncio.CancelledError:
            self.close_cancelled += 1
            raise


class Speech:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def feed_pcm(self, pcm: bytes) -> None:
        self.frames.append(pcm)


class Case:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.callbacks = AsyncMock()
        self.receiver = MatchAudioReceiver(
            match_id=uuid4(), settings=cast(Any, SimpleNamespace()), callbacks=self.callbacks
        )
        self.room: Any = SimpleNamespace(remote_participants={}, disconnect=AsyncMock())
        self.receiver._room = self.room
        self.user = uuid4()
        self.identity = f"jx-human-{self.receiver.match_id}-{self.user}-1-owned"
        self.streams: dict[int, Stream] = {}
        monkeypatch.setattr(rtc, "AudioStream", self.stream)
        self.speech = Speech()
        self.envelope = CallbackEnvelope(
            match_id=self.receiver.match_id,
            speech_id=uuid4(),
            attempt_no=1,
            generation_id=None,
            connection_epoch=1,
            context_version=8,
            opportunity_id=None,
            opportunity_generation=None,
        )
        self.receiver._speech = cast(Any, self.speech)
        self.receiver._speaker_user_id = self.user
        self.receiver._speech_envelope = self.envelope

    def stream(self, track: Any, **options: Any) -> Any:
        assert options == {
            "capacity": 10,
            "sample_rate": 16000,
            "num_channels": 1,
            "frame_size_ms": 100,
        }
        result = Stream()
        self.streams[id(track)] = result
        return result

    def subscribe(self, sid: str = "same-sid", *, identity: str | None = None) -> TrackItem:
        track: Any = SimpleNamespace(kind=rtc.TrackKind.KIND_AUDIO)
        publication: Any = SimpleNamespace(sid=sid, track=track)
        name = identity or self.identity
        participant = self.room.remote_participants.setdefault(
            name, SimpleNamespace(identity=name, track_publications={})
        )
        participant.track_publications[sid] = publication
        before = set(self.receiver._track_tasks)
        self.receiver._on_track_subscribed(self.room, track, publication, participant)
        task = next(iter(self.receiver._track_tasks - before))
        return track, publication, participant, task

    def unsubscribe(self, item: TrackItem, *, room: Any = None) -> None:
        track, publication, participant, _ = item
        self.receiver._on_track_unsubscribed(room or self.room, track, publication, participant)

    async def started(self, item: TrackItem) -> Stream:
        async with asyncio.timeout(0.5):
            while id(item[0]) not in self.streams:
                await asyncio.sleep(0)
            stream = self.streams[id(item[0])]
            await stream.waiting.wait()
        return stream

    async def finish(self) -> None:
        for stream in self.streams.values():
            stream.deliver_after_cancel = False
            stream.late_release.set()
            stream.close_release.set()
        await asyncio.wait_for(self.receiver.close(), timeout=0.5)
        assert not self.receiver._track_tasks and not self.receiver._subscribed_tracks


@pytest.mark.asyncio
async def test_start_registers_exact_unsubscribe_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    boundary = Boundary()
    boundary.install(monkeypatch)
    target = receiver(uuid4())
    try:
        await target.start()
        assert len(boundary.rooms[0].listeners.get("track_subscribed", [])) == 1
        assert len(boundary.rooms[0].listeners.get("track_unsubscribed", [])) == 1
        for event in (
            "track_unpublished",
            "participant_disconnected",
            "participant_connected",
            "disconnected",
            "reconnected",
        ):
            assert len(boundary.rooms[0].listeners.get(event, [])) == 1
    finally:
        await boundary.finish_fixture((target,))


@pytest.mark.asyncio
async def test_unsubscribed_late_pcm_cannot_enter_current_speech(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = Case(monkeypatch)
    try:
        old = case.subscribe()
        stream = await case.started(old)
        stream.deliver_after_cancel = True
        case.unsubscribe(old)
        await asyncio.wait_for(stream.cancelled.wait(), 0.5)
        stream.late_release.set()
        await asyncio.wait_for(asyncio.gather(old[3], return_exceptions=True), 0.5)
        assert not case.speech.frames and stream.close_count == 1
        assert case.receiver._speech is case.speech
        assert case.receiver._speech_envelope is case.envelope
        case.callbacks.handle_asr_failure.assert_not_called()
    finally:
        await case.finish()


@pytest.mark.asyncio
async def test_prestart_unsubscribe_creates_no_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    case = Case(monkeypatch)
    try:
        old = case.subscribe()
        case.unsubscribe(old)
        await asyncio.gather(old[3], return_exceptions=True)
        assert not case.streams and not case.receiver._subscribed_tracks
    finally:
        await case.finish()


@pytest.mark.asyncio
async def test_same_sid_replacement_old_events_and_done_cannot_retire_new_track(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = Case(monkeypatch)
    try:
        old = case.subscribe()
        old_stream = await case.started(old)
        old_stream.deliver_after_cancel = True
        new = case.subscribe()
        new_stream = await case.started(new)
        await asyncio.wait_for(old_stream.cancelled.wait(), 0.5)
        case.unsubscribe(old)
        case.unsubscribe(old)
        old_stream.late_release.set()
        await asyncio.gather(old[3], return_exceptions=True)
        assert old_stream.close_count == 1 and not new[3].done()
        case.receiver._on_track_subscribed(case.room, *new[:3])
        assert len(case.streams) == 2
        await new_stream.queue.put(new_stream.frame())
        async with asyncio.timeout(0.5):
            while not case.speech.frames:
                await asyncio.sleep(0)
        assert case.speech.frames == [b"\x01" * 3200]
        assert case.receiver._speech_envelope is case.envelope
    finally:
        await case.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("other", ["room", "identity", "sid", "track", "missing_track"])
async def test_unsubscribe_requires_exact_owner(
    monkeypatch: pytest.MonkeyPatch, other: str
) -> None:
    case = Case(monkeypatch)
    try:
        item = case.subscribe()
        stream = await case.started(item)
        track, publication, participant, task = item
        room = case.room
        if other == "room":
            room = SimpleNamespace()
        elif other == "identity":
            participant = SimpleNamespace(identity=case.identity + "-old")
        elif other == "sid":
            publication = SimpleNamespace(sid="old-sid")
        elif other == "track":
            track = SimpleNamespace(kind=rtc.TrackKind.KIND_AUDIO)
        else:
            track = None
        case.receiver._on_track_unsubscribed(
            cast(Any, room), cast(Any, track), cast(Any, publication), cast(Any, participant)
        )
        await asyncio.sleep(0)
        assert not task.done() and stream.close_count == 0
    finally:
        await case.finish()


@pytest.mark.asyncio
async def test_inventory_pruning_retires_only_removed_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = Case(monkeypatch)
    try:
        old = case.subscribe("old-sid")
        old_stream = await case.started(old)
        old[2].track_publications.pop("old-sid")
        new = case.subscribe("new-sid")
        await case.started(new)
        await asyncio.wait_for(asyncio.gather(old[3], return_exceptions=True), 0.5)
        assert old_stream.close_count == 1 and not new[3].done()
    finally:
        await case.finish()


@pytest.mark.asyncio
async def test_duplicate_unsubscribe_and_close_do_not_cancel_in_progress_stream_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = Case(monkeypatch)
    try:
        old = case.subscribe()
        stream = await case.started(old)
        stream.close_release.clear()
        case.unsubscribe(old)
        await asyncio.wait_for(stream.close_entered.wait(), 0.5)
        case.unsubscribe(old)
        closing = asyncio.create_task(case.receiver.close())
        await asyncio.sleep(0)
        assert old[3].cancelling() == 1
        assert stream.close_count == 1 and stream.close_cancelled == 0
        stream.close_release.set()
        await asyncio.wait_for(closing, 0.5)
        assert stream.close_count == 1 and stream.close_cancelled == 0
    finally:
        await case.finish()


@pytest.mark.asyncio
async def test_new_epoch_same_sid_survives_late_old_pcm_and_duplicate_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = Case(monkeypatch)
    try:
        old = case.subscribe()
        old_stream = await case.started(old)
        old_stream.deliver_after_cancel = True
        new_identity = f"jx-human-{case.receiver.match_id}-{case.user}-2-owned"
        new = case.subscribe(identity=new_identity)
        new_stream = await case.started(new)
        current = replace(case.envelope, connection_epoch=2)
        case.receiver._speech_envelope = current
        case.unsubscribe(old)
        await asyncio.wait_for(old_stream.cancelled.wait(), 0.5)
        old_stream.late_release.set()
        await asyncio.gather(old[3], return_exceptions=True)
        case.unsubscribe(old)
        assert not case.speech.frames and not new[3].done()
        await new_stream.queue.put(new_stream.frame())
        async with asyncio.timeout(0.5):
            while not case.speech.frames:
                await asyncio.sleep(0)
        assert case.speech.frames == [b"\x01" * 3200]
        assert case.receiver._speech_envelope is current
    finally:
        await case.finish()


@pytest.mark.asyncio
async def test_stream_cleanup_failure_does_not_fail_or_replace_current_speech(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = Case(monkeypatch)
    try:
        old = case.subscribe()
        stream = await case.started(old)

        async def failed_close() -> None:
            stream.close_count += 1
            raise RuntimeError("synthetic native cleanup failure")

        monkeypatch.setattr(stream, "aclose", failed_close)
        case.unsubscribe(old)
        await asyncio.wait_for(asyncio.gather(old[3], return_exceptions=True), 0.5)
        assert stream.close_count == 1 and not case.receiver._subscribed_tracks
        assert case.receiver._speech is case.speech
        assert case.receiver._speech_envelope is case.envelope
        case.callbacks.handle_asr_failure.assert_not_called()
    finally:
        await case.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["unpublished", "participant", "room"])
async def test_sdk_terminal_fallback_retires_exact_old_consumer(
    monkeypatch: pytest.MonkeyPatch,
    event: str,
) -> None:
    case = Case(monkeypatch)
    try:
        old = case.subscribe()
        stream = await case.started(old)
        stream.deliver_after_cancel = True
        if event == "unpublished":
            old[2].track_publications.pop(old[1].sid)
            case.receiver._on_track_unpublished(case.room, old[1], old[2])
        elif event == "participant":
            case.room.remote_participants.pop(old[2].identity)
            case.receiver._on_participant_disconnected(case.room, old[2])
        else:
            case.receiver._on_track_room_disconnected(case.room)
        await asyncio.wait_for(stream.cancelled.wait(), 0.5)
        stream.late_release.set()
        await asyncio.gather(old[3], return_exceptions=True)
        assert not case.speech.frames and stream.close_count == 1
        assert (
            case.receiver._speech is case.speech and case.receiver._speech_envelope is case.envelope
        )
        case.callbacks.handle_asr_failure.assert_not_called()
    finally:
        await case.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["unsubscribed", "unpublished", "participant"])
async def test_late_same_key_metadata_cannot_retire_reused_track_new_owner(
    monkeypatch: pytest.MonkeyPatch,
    event: str,
) -> None:
    case = Case(monkeypatch)
    try:
        old = case.subscribe()
        stream = await case.started(old)
        new_publication: Any = SimpleNamespace(sid=old[1].sid, track=old[0])
        new_participant: Any = SimpleNamespace(
            identity=case.identity, track_publications={new_publication.sid: new_publication}
        )
        case.room.remote_participants[case.identity] = new_participant
        case.receiver._on_track_subscribed(case.room, old[0], new_publication, new_participant)
        await asyncio.gather(old[3], return_exceptions=True)
        current = next(iter(case.receiver._track_tasks))
        assert current is not old[3] and stream.close_count == 1
        if event == "unsubscribed":
            case.unsubscribe(old)
        elif event == "unpublished":
            case.receiver._on_track_unpublished(case.room, old[1], old[2])
        else:
            case.receiver._on_participant_disconnected(case.room, old[2])
        # A delayed subscribed callback from old metadata must also be rejected.
        case.receiver._on_track_subscribed(case.room, *old[:3])
        await asyncio.sleep(0)
        assert not current.done() and case.receiver._track_tasks == {current}
    finally:
        await case.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["participant", "publication", "track"])
async def test_reconnected_inventory_retires_missing_or_replaced_owners(
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    case = Case(monkeypatch)
    try:
        old = case.subscribe()
        stream = await case.started(old)
        if missing == "participant":
            case.room.remote_participants.pop(case.identity)
        elif missing == "publication":
            old[2].track_publications.pop(old[1].sid)
        else:
            old[1].track = SimpleNamespace(kind=rtc.TrackKind.KIND_AUDIO)
        case.receiver._on_track_room_reconnected(case.room)
        await asyncio.wait_for(asyncio.gather(old[3], return_exceptions=True), 0.5)
        assert stream.close_count == 1 and not case.receiver._subscribed_tracks
    finally:
        await case.finish()


@pytest.mark.asyncio
async def test_disconnected_room_rejects_queued_input_and_reconnected_accepts_current_track(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = Case(monkeypatch)
    try:
        old = case.subscribe()
        await case.started(old)
        case.receiver._on_track_room_disconnected(case.room)
        await asyncio.gather(old[3], return_exceptions=True)
        case.receiver._on_track_subscribed(case.room, *old[:3])
        assert not case.receiver._track_tasks
        case.receiver._on_track_room_reconnected(case.room)
        new = case.subscribe()
        stream = await case.started(new)
        await stream.queue.put(stream.frame())
        async with asyncio.timeout(0.5):
            while not case.speech.frames:
                await asyncio.sleep(0)
        assert not new[3].done() and case.speech.frames == [b"\x01" * 3200]
    finally:
        await case.finish()


@pytest.mark.asyncio
async def test_valid_inventory_and_old_room_terminal_events_preserve_live_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = Case(monkeypatch)
    try:
        old = case.subscribe()
        stream = await case.started(old)
        old[1].muted = True
        case.receiver._on_track_room_reconnected(case.room)
        retired_room: Any = SimpleNamespace()
        case.receiver._on_track_room_disconnected(retired_room)
        case.receiver._on_track_unpublished(retired_room, old[1], old[2])
        case.receiver._on_participant_disconnected(retired_room, old[2])
        case.receiver._on_track_room_reconnected(retired_room)
        await asyncio.sleep(0)
        assert not old[3].done() and stream.close_count == 0
    finally:
        await case.finish()


@pytest.mark.asyncio
async def test_new_participant_inventory_retires_old_identity_owner_without_touching_speech(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = Case(monkeypatch)
    try:
        old = case.subscribe()
        stream = await case.started(old)
        current: Any = SimpleNamespace(identity=case.identity, track_publications={})
        case.room.remote_participants[case.identity] = current
        # A delayed old connected callback cannot change ownership.
        case.receiver._on_participant_connected(case.room, old[2])
        assert not old[3].done()
        case.receiver._on_participant_connected(case.room, current)
        await asyncio.gather(old[3], return_exceptions=True)
        assert stream.close_count == 1 and not case.receiver._subscribed_tracks
        assert case.receiver._speech is case.speech
        assert case.receiver._speech_envelope is case.envelope
    finally:
        await case.finish()

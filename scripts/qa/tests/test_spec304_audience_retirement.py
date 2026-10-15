"""Exact-owned QA consumer controls; silent old tracks deliberately emit no EOS."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr
from test_spec304_match_probe import probe as probe


@pytest.fixture
def owned(probe: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    full = probe.full
    streams: list[Any] = []

    class Stream:
        def __init__(self, track: Any, **_: Any):
            self.track = track
            self.queue: asyncio.Queue[Any] = asyncio.Queue()
            self.close_calls = 0
            self.closed = False
            self.close_entered = asyncio.Event()
            self.close_release = asyncio.Event()
            self.close_release.set()
            self.close_cancelled = False
            streams.append(self)

        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> Any:
            frame = await self.queue.get()
            if frame is None:
                raise StopAsyncIteration
            return frame

        async def aclose(self) -> None:
            self.close_calls += 1
            self.close_entered.set()
            try:
                await self.close_release.wait()
            except asyncio.CancelledError:
                self.close_cancelled = True
                raise
            self.closed = True

    class Room:
        def __init__(self):
            self.handlers: dict[str, list[Any]] = {}
            self._remote_participants: dict[str, Any] = {}

        @property
        def remote_participants(self) -> dict[str, Any]:
            return self._remote_participants

        def on(self, event: str, callback: Any) -> None:
            self.handlers.setdefault(event, []).append(callback)

        def emit(
            self, event: str, track: Any, participant: Any = None, *,
            identity: str = "jx-agent-owned-synthetic"
        ) -> None:
            if event not in {"track_subscribed", "track_unsubscribed"}:
                for callback in self.handlers.get(event, []):
                    callback(track, participant) if participant is not None else callback(track)
                return
            participant = self._remote_participants.setdefault(
                identity,
                SimpleNamespace(identity=identity, track_publications={}, _info=SimpleNamespace()),
            )
            participant._track_publications = participant.track_publications
            publication = participant.track_publications.setdefault(
                "owned-publication", SimpleNamespace(sid="owned-publication", track=None)
            )
            if event == "track_subscribed":
                publication.track = track
                publication._track = track
            for callback in self.handlers.get(event, []):
                callback(track, publication, participant)

        async def connect(self, *_: Any) -> None:
            pass

        async def disconnect(self) -> None:
            pass

    native_dispatch = full.rtc.Room._on_room_event
    monkeypatch.setattr(full.rtc, "Room", Room)
    monkeypatch.setattr(full.rtc, "AudioStream", Stream)
    settings = SimpleNamespace(
        livekit_api_key=SecretStr("owned-qa-key"),
        livekit_api_secret=SecretStr("owned-qa-secret-not-a-real-key-000000000000"),
        livekit_url="wss://synthetic.invalid",
    )
    return SimpleNamespace(
        streams=streams, settings=settings, full=full, native_dispatch=native_dispatch
    )


async def connected(owned: Any) -> Any:
    audience = owned.full.AudienceAudio()
    await audience.connect(owned.settings, uuid4())
    return audience


@pytest.mark.asyncio
async def test_unsubscribe_retires_only_owned_silent_track_without_waiting_for_eos(owned: Any):
    audience = await connected(owned)
    track = SimpleNamespace(sid="owned-track")
    try:
        audience.room.emit("track_subscribed", track)
        await asyncio.sleep(0.01)
        assert len(owned.streams) == 1 and audience.tasks
        audience.room.emit("track_unsubscribed", track)
        await asyncio.sleep(0.01)
        assert owned.streams[0].closed and not audience.tasks
        assert owned.streams[0].close_calls == 1
    finally:
        await audience.close()
        assert all(stream.closed for stream in owned.streams)


@pytest.mark.asyncio
async def test_unsubscribe_before_consumer_start_creates_no_native_stream(owned: Any):
    audience = await connected(owned)
    track = SimpleNamespace(sid="owned-track")
    try:
        audience.room.emit("track_subscribed", track)
        audience.room.emit("track_unsubscribed", track)
        await asyncio.sleep(0.01)
        assert not owned.streams and not audience.tasks
    finally:
        await audience.close()


@pytest.mark.asyncio
async def test_same_sid_replacement_and_late_old_unsubscribe_preserve_new_consumer(owned: Any):
    audience = await connected(owned)
    old, replacement = SimpleNamespace(sid="same"), SimpleNamespace(sid="same")
    try:
        audience.room.emit("track_subscribed", old)
        await asyncio.sleep(0.01)
        audience.room.emit("track_subscribed", replacement)
        await asyncio.sleep(0.01)
        audience.room.emit("track_unsubscribed", old)
        await asyncio.sleep(0.01)
        assert len(owned.streams) == 2
        assert owned.streams[0].closed and not owned.streams[1].closed
        assert len(audience.tasks) == 1
    finally:
        await audience.close()
        assert all(stream.closed and stream.close_calls == 1 for stream in owned.streams)


@pytest.mark.asyncio
async def test_duplicate_unsubscribe_and_global_close_do_not_cancel_retirement_twice(owned: Any):
    audience = await connected(owned)
    track = SimpleNamespace(sid="owned-track")
    closing: asyncio.Task[Any] | None = None
    try:
        audience.room.emit("track_subscribed", track)
        await asyncio.sleep(0.01)
        stream = owned.streams[0]
        stream.close_release.clear()
        audience.room.emit("track_unsubscribed", track)
        audience.room.emit("track_unsubscribed", track)
        await asyncio.wait_for(stream.close_entered.wait(), 0.1)
        closing = asyncio.create_task(audience.close())
        await asyncio.sleep(0.01)
        assert not stream.close_cancelled and not closing.done()
        stream.close_release.set()
        await asyncio.wait_for(closing, 0.1)
        assert stream.close_calls == 1 and stream.closed and not audience.tasks
    finally:
        for stream in owned.streams:
            stream.close_release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await audience.close()


@pytest.mark.asyncio
async def test_native_eos_still_completes_original_consumer_normally(owned: Any):
    audience = await connected(owned)
    track = SimpleNamespace(sid="owned-track")
    try:
        audience.room.emit("track_subscribed", track)
        await asyncio.sleep(0.01)
        await owned.streams[0].queue.put(None)
        await asyncio.sleep(0.01)
        assert owned.streams[0].closed and not audience.tasks
    finally:
        await audience.close()


@pytest.mark.asyncio
async def test_retired_room_late_subscription_cannot_create_new_consumer(owned: Any):
    audience = await connected(owned)
    retired_room = audience.room
    await audience.close()
    try:
        retired_room.emit("track_subscribed", SimpleNamespace(sid="late-old-track"))
        await asyncio.sleep(0.01)
        assert not owned.streams and not audience.tasks
    finally:
        await audience.close()


@pytest.mark.asyncio
async def test_duplicate_current_subscription_does_not_create_second_stream(owned: Any):
    audience = await connected(owned)
    track = SimpleNamespace(sid="owned-track")
    try:
        audience.room.emit("track_subscribed", track)
        audience.room.emit("track_subscribed", track)
        await asyncio.sleep(0.01)
        assert len(owned.streams) == len(audience.tasks) == audience.tracks["agent"] == 1
    finally:
        await audience.close()


@pytest.mark.asyncio
async def test_same_publication_sid_from_another_identity_does_not_retire_current_owner(owned: Any):
    audience = await connected(owned)
    first, second = SimpleNamespace(sid="same"), SimpleNamespace(sid="same")
    try:
        audience.room.emit("track_subscribed", first)
        audience.room.emit("track_subscribed", second, identity="jx-agent-another-synthetic")
        await asyncio.sleep(0.01)
        assert len(owned.streams) == len(audience.tasks) == 2
        audience.room.emit("track_unsubscribed", first)
        await asyncio.sleep(0.01)
        assert owned.streams[0].closed and not owned.streams[1].closed
        assert len(audience.tasks) == 1
    finally:
        await audience.close()


@pytest.mark.asyncio
async def test_original_sdk_unpublish_first_without_unsubscribe_retires_stream(owned: Any):
    from livekit.rtc._proto import room_pb2

    audience = await connected(owned)
    try:
        audience.room.emit("track_subscribed", SimpleNamespace(sid="owned-track"))
        await asyncio.sleep(0.01)
        event = room_pb2.RoomEvent()
        event.track_unpublished.participant_identity = "jx-agent-owned-synthetic"
        event.track_unpublished.publication_sid = "owned-publication"
        owned.native_dispatch(audience.room, event)
        event = room_pb2.RoomEvent()
        event.track_unsubscribed.participant_identity = "jx-agent-owned-synthetic"
        event.track_unsubscribed.track_sid = "owned-publication"
        owned.native_dispatch(audience.room, event)
        await asyncio.sleep(0.01)
        assert owned.streams[0].closed and not audience.tasks
    finally:
        await audience.close()


@pytest.mark.asyncio
async def test_original_sdk_participant_disconnect_first_retires_stream(owned: Any):
    from livekit.rtc._proto import room_pb2

    audience = await connected(owned)
    try:
        audience.room.emit("track_subscribed", SimpleNamespace(sid="owned-track"))
        await asyncio.sleep(0.01)
        event = room_pb2.RoomEvent()
        event.participant_disconnected.participant_identity = "jx-agent-owned-synthetic"
        owned.native_dispatch(audience.room, event)
        await asyncio.sleep(0.01)
        assert owned.streams[0].closed and not audience.tasks
    finally:
        await audience.close()


@pytest.mark.asyncio
async def test_late_old_publication_and_participant_events_preserve_replacement(owned: Any):
    audience = await connected(owned)
    try:
        audience.room.emit("track_subscribed", SimpleNamespace(sid="old-track"))
        await asyncio.sleep(0.01)
        old_participant = audience.room.remote_participants.pop("jx-agent-owned-synthetic")
        old_publication = old_participant.track_publications["owned-publication"]
        audience.room.emit("track_subscribed", SimpleNamespace(sid="new-track"))
        await asyncio.sleep(0.01)
        audience.room.emit("track_unpublished", old_publication, old_participant)
        audience.room.emit("participant_disconnected", old_participant)
        await asyncio.sleep(0.01)
        assert owned.streams[0].closed and not owned.streams[1].closed
        assert len(audience.tasks) == 1
    finally:
        await audience.close()


@pytest.mark.asyncio
async def test_room_disconnected_retires_all_consumers_and_rejects_late_subscription(owned: Any):
    audience = await connected(owned)
    try:
        audience.room.emit("track_subscribed", SimpleNamespace(sid="first"))
        audience.room.emit("track_subscribed", SimpleNamespace(sid="second"), identity="other")
        await asyncio.sleep(0.01)
        audience.room.emit("disconnected", 0)
        audience.room.emit("track_subscribed", SimpleNamespace(sid="late"), identity="late")
        await asyncio.sleep(0.01)
        assert len(owned.streams) == 2
        assert all(stream.closed for stream in owned.streams) and not audience.tasks
    finally:
        await audience.close()


@pytest.mark.asyncio
async def test_reconnected_inventory_prunes_only_missing_track(owned: Any):
    audience = await connected(owned)
    try:
        audience.room.emit("track_subscribed", SimpleNamespace(sid="old-track"))
        audience.room.emit("track_subscribed", SimpleNamespace(sid="other-track"), identity="other")
        await asyncio.sleep(0.01)
        audience.room.remote_participants["jx-agent-owned-synthetic"].track_publications.clear()
        for callback in audience.room.handlers.get("reconnected", []):
            callback()
        await asyncio.sleep(0.01)
        assert owned.streams[0].closed and not owned.streams[1].closed
        assert len(audience.tasks) == 1
    finally:
        await audience.close()


@pytest.mark.asyncio
async def test_transient_reconnecting_and_mute_do_not_retire_current_track(owned: Any):
    audience = await connected(owned)
    try:
        audience.room.emit("track_subscribed", SimpleNamespace(sid="current"))
        await asyncio.sleep(0.01)
        for callback in audience.room.handlers.get("reconnecting", []):
            callback()
        participant = audience.room.remote_participants["jx-agent-owned-synthetic"]
        publication = participant.track_publications["owned-publication"]
        audience.room.emit("track_muted", publication, participant)
        for callback in audience.room.handlers.get("reconnected", []):
            callback()
        await asyncio.sleep(0.01)
        assert len(audience.tasks) == 1 and not owned.streams[0].closed
    finally:
        await audience.close()


@pytest.mark.asyncio
async def test_late_old_subscribe_cannot_replace_current_inventory_owner(owned: Any):
    audience = await connected(owned)
    try:
        old_track = SimpleNamespace(sid="old-track")
        audience.room.emit("track_subscribed", old_track)
        await asyncio.sleep(0.01)
        old_participant = audience.room.remote_participants.pop("jx-agent-owned-synthetic")
        old_publication = old_participant.track_publications["owned-publication"]
        audience.room.emit("track_subscribed", SimpleNamespace(sid="new-track"))
        await asyncio.sleep(0.01)
        for callback in audience.room.handlers["track_subscribed"]:
            callback(old_track, old_publication, old_participant)
        await asyncio.sleep(0.01)
        assert len(owned.streams) == 2
        assert owned.streams[0].closed and not owned.streams[1].closed
        assert len(audience.tasks) == 1 and audience.tracks["agent"] == 2
    finally:
        await audience.close()


@pytest.mark.asyncio
async def test_late_old_publication_from_same_participant_preserves_new_stream(owned: Any):
    audience = await connected(owned)
    try:
        audience.room.emit("track_subscribed", SimpleNamespace(sid="old-track"))
        await asyncio.sleep(0.01)
        participant = audience.room.remote_participants["jx-agent-owned-synthetic"]
        old_publication = participant.track_publications.pop("owned-publication")
        audience.room.emit("track_subscribed", SimpleNamespace(sid="new-track"))
        await asyncio.sleep(0.01)
        audience.room.emit("track_unpublished", old_publication, participant)
        await asyncio.sleep(0.01)
        assert owned.streams[0].closed and not owned.streams[1].closed
        assert len(audience.tasks) == 1
    finally:
        await audience.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal", ["track_unpublished", "participant_disconnected", "disconnected"]
)
async def test_fallback_before_consumer_start_creates_no_stream(owned: Any, terminal: str):
    audience = await connected(owned)
    try:
        audience.room.emit("track_subscribed", SimpleNamespace(sid="current-track"))
        participant = audience.room.remote_participants["jx-agent-owned-synthetic"]
        publication = participant.track_publications["owned-publication"]
        if terminal == "track_unpublished":
            participant.track_publications.clear()
            audience.room.emit(terminal, publication, participant)
        elif terminal == "participant_disconnected":
            audience.room.remote_participants.clear()
            audience.room.emit(terminal, participant)
        else:
            audience.room.emit(terminal, 0)
        await asyncio.sleep(0.01)
        assert not owned.streams and not audience.tasks
    finally:
        await audience.close()


@pytest.mark.asyncio
async def test_overlapping_terminal_callbacks_do_not_interrupt_native_close(owned: Any):
    audience = await connected(owned)
    closing: asyncio.Task[Any] | None = None
    try:
        audience.room.emit("track_subscribed", SimpleNamespace(sid="current-track"))
        await asyncio.sleep(0.01)
        participant = audience.room.remote_participants["jx-agent-owned-synthetic"]
        publication = participant.track_publications["owned-publication"]
        stream = owned.streams[0]
        stream.close_release.clear()
        audience.room.emit("track_unpublished", publication, participant)
        await asyncio.wait_for(stream.close_entered.wait(), 0.1)
        audience.room.emit("participant_disconnected", participant)
        audience.room.emit("disconnected", 0)
        closing = asyncio.create_task(audience.close())
        await asyncio.sleep(0.01)
        assert not stream.close_cancelled and stream.close_calls == 1
        stream.close_release.set()
        await asyncio.wait_for(closing, 0.1)
        assert stream.closed and not audience.tasks
    finally:
        for stream in owned.streams:
            stream.close_release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await audience.close()

from __future__ import annotations

import asyncio
import importlib
import json
from threading import RLock
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr
from test_spec304_match_probe import probe as probe


@pytest.fixture
def rtc_probe(probe: Any) -> Any:
    return importlib.import_module("server_spec304_rtc_observer_probe")


def test_comparison_has_same_process_balanced_abba_order(rtc_probe: Any) -> None:
    modes = rtc_probe.comparison_modes()
    assert len(modes) == 24
    assert modes.count("plain") == modes.count("observed") == 12
    assert all(modes[i:i + 4] == ["plain", "observed", "observed", "plain"]
               for i in range(0, 24, 4))


@pytest.mark.parametrize("rounds", [0, 3, 5, 36])
def test_comparison_refuses_unbounded_or_unbalanced_round_counts(
    rtc_probe: Any, rounds: int
) -> None:
    with pytest.raises(ValueError):
        rtc_probe.comparison_modes(rounds)


def test_ffi_observation_reads_only_existing_queue_counts(rtc_probe: Any) -> None:
    queues: list[asyncio.Queue[str]] = [asyncio.Queue(), asyncio.Queue()]
    queues[0].put_nowait("never-export-event-token")
    client = SimpleNamespace(queue=SimpleNamespace(
        _lock=RLock(), _subscribers=[(queue, None, None) for queue in queues],
    ))
    value = rtc_probe.ffi_counts(client)
    assert value == {"subscribers": 2, "queued_events": 1, "largest_queue": 1}
    assert queues[0].qsize() == 1 and "never-export" not in json.dumps(value)
    assert not hasattr(client.queue, "subscribe")


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_sampling_forwards_original_result_or_error_and_drains_monitor(
    rtc_probe: Any, raises: bool
) -> None:
    baseline = set(asyncio.all_tasks())
    row: dict[str, Any] = {}

    async def original() -> str:
        await asyncio.sleep(0.01)
        if raises:
            raise RuntimeError("owned-original-error")
        return "owned-original-result"

    if raises:
        with pytest.raises(RuntimeError, match="owned-original-error"):
            await rtc_probe.observed_await(original(), row, 0.001)
    else:
        assert await rtc_probe.observed_await(original(), row, 0.001) == "owned-original-result"
    assert row["samples"] > 0 and len(row["last_await_frames"]) <= 12
    assert not (asyncio.all_tasks() - baseline)


@pytest.mark.asyncio
async def test_budget_exceedance_does_not_cancel_or_retry_original_cleanup(rtc_probe: Any) -> None:
    entered = 0

    async def close() -> None:
        nonlocal entered
        entered += 1
        await asyncio.sleep(0.015)

    task, row = await rtc_probe.close_checkpoint(close(), budget=0.001)
    assert not row["done_at_business_budget"] and not task.cancelled() and not task.done()
    await task
    await asyncio.sleep(0)
    assert entered == 1 and row["completed_ms"] >= 10
    assert row["completed_cancelled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("slow_disconnect", [False, True])
async def test_owned_rtc_run_balances_modes_or_stops_on_original_budget_and_cleans_exact_room(
    rtc_probe: Any, monkeypatch: pytest.MonkeyPatch, slow_disconnect: bool
) -> None:
    room_names: set[str] = set()
    calls: list[str] = []
    disconnects = 0

    class API:
        def __init__(self, *_: Any) -> None:
            self.room = self

        async def list_rooms(self, request: Any) -> Any:
            return SimpleNamespace(rooms=[name for name in request.names if name in room_names])

        async def create_room(self, request: Any) -> Any:
            assert request.name.startswith("jx-qa-rtc-")
            room_names.add(request.name)
            calls.append("create")
            return request

        async def delete_room(self, request: Any) -> None:
            assert request.room in room_names
            room_names.remove(request.room)
            calls.append("delete")

        async def aclose(self) -> None:
            calls.append("api_close")

    class Token:
        def __init__(self, *_: Any) -> None:
            pass

        def with_identity(self, _: str) -> Any:
            return self

        def with_ttl(self, _: Any) -> Any:
            return self

        def with_grants(self, _: Any) -> Any:
            return self

        def to_jwt(self) -> str:
            return "never-export-token"

    class Room:
        def __init__(self) -> None:
            self.local_participant = SimpleNamespace(publish_track=self.publish)

        async def connect(self, url: str, token: str) -> None:
            assert url == "wss://synthetic.invalid" and token == "never-export-token"
            calls.append("connect")

        async def publish(self, *_: Any) -> None:
            calls.append("publish")

        async def disconnect(self) -> None:
            nonlocal disconnects
            disconnects += 1
            if slow_disconnect and disconnects == 1:
                await asyncio.sleep(0.6)
            calls.append("disconnect")

    class Source:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def capture_frame(self, _: Any) -> None:
            calls.append("frame")

        async def wait_for_playout(self) -> None:
            pass

        async def aclose(self) -> None:
            calls.append("source_close")

    def namespace(**values: Any) -> SimpleNamespace:
        return SimpleNamespace(**values)

    def track(*_: Any) -> SimpleNamespace:
        return SimpleNamespace()

    def version(_: str) -> str:
        return "1.1.20"

    for name in ["ListRoomsRequest", "CreateRoomRequest", "DeleteRoomRequest", "VideoGrants"]:
        monkeypatch.setattr(rtc_probe.api, name, namespace)
    monkeypatch.setattr(rtc_probe.api, "LiveKitAPI", API)
    monkeypatch.setattr(rtc_probe.api, "AccessToken", Token)
    monkeypatch.setattr(rtc_probe.rtc, "Room", Room)
    monkeypatch.setattr(rtc_probe.rtc, "AudioSource", Source)
    monkeypatch.setattr(rtc_probe.rtc, "AudioFrame", namespace)
    monkeypatch.setattr(rtc_probe.rtc, "LocalAudioTrack", SimpleNamespace(
        create_audio_track=track,
    ))
    monkeypatch.setattr(rtc_probe.rtc, "TrackPublishOptions", SimpleNamespace)
    monkeypatch.setattr(rtc_probe.importlib.metadata, "version", version)
    monkeypatch.setattr(rtc_probe, "FfiClient", SimpleNamespace(instance=SimpleNamespace(
        queue=SimpleNamespace(_lock=RLock(), _subscribers=[]),
    )))
    settings = SimpleNamespace(
        livekit_url="wss://synthetic.invalid",
        livekit_api_key=SecretStr("never-export-api-key"),
        livekit_api_secret=SecretStr("never-export-secret"),
    )
    value = await rtc_probe.run(settings, rounds=4)
    assert value["status"] == ("failed" if slow_disconnect else "passed")
    assert len(value["rows"]) == (1 if slow_disconnect else 4)
    assert calls.count("connect") == (1 if slow_disconnect else 4)
    assert calls.count("frame") == (3 if slow_disconnect else 12)
    assert calls.count("create") == calls.count("delete") == calls.count("api_close") == 1
    assert not room_names and value["owned_room_deleted"] is True
    assert value["new_pending_tasks"] == 0 and value["after"]["subscribers"] == 0
    assert value["observer_ffi_subscriptions_created"] == 0
    assert "never-export" not in json.dumps(value)
    if slow_disconnect:
        assert value["rows"][0]["disconnect"]["done_at_business_budget"] is False
        assert value["rows"][0]["disconnect"]["completed_ms"] >= 500

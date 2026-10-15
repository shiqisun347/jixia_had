"""Owned RTC-only ABBA comparison; no Core database or match operations."""

from __future__ import annotations

import asyncio
import gc
import hashlib
import importlib.metadata
import json
from datetime import timedelta
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4
from weakref import WeakKeyDictionary

from livekit import api, rtc
from livekit.rtc._ffi_client import FfiClient
from server_full_match_probe import NativeDisconnectLedger

from jx_core.agent.runtime import AGENT_RESOURCE_CLEANUP_TIMEOUT_SECONDS
from jx_core.config import Settings, load_settings
from jx_core.host_audio_timing import host_audio_attempt_timeout_seconds

# This QA probe inspects counts only, without subscribing to SDK events.
# pyright: reportPrivateUsage=false


def comparison_modes(rounds: int = 24) -> list[str]:
    if rounds < 4 or rounds > 32 or rounds % 4:
        raise ValueError("qa_rtc_comparison_requires_4_to_32_rounds_in_blocks_of_four")
    return ["plain", "observed", "observed", "plain"] * (rounds // 4)


def ffi_counts(client: Any) -> dict[str, int]:
    queue = client.queue
    with queue._lock:
        sizes = [subscriber.qsize() for subscriber, _, _ in queue._subscribers]
    return {
        "subscribers": len(sizes),
        "queued_events": sum(sizes),
        "largest_queue": max(sizes, default=0),
    }


def process_counts() -> dict[str, int | None]:
    status = Path("/proc/self/status")
    values: dict[str, int | None] = {"rss_kib": None, "threads": None, "fds": None}
    if status.is_file():
        for line in status.read_text().splitlines():
            if line.startswith("VmRSS:"):
                values["rss_kib"] = int(line.split()[1])
            elif line.startswith("Threads:"):
                values["threads"] = int(line.split()[1])
    fds = Path("/proc/self/fd")
    if fds.is_dir():
        values["fds"] = sum(1 for _ in fds.iterdir())
    return values


async def observed_await(
    operation: Any, row: dict[str, Any], period: float, snapshot: Any = None
) -> Any:
    """The same bounded coroutine-chain sampling used by the full probe."""
    async def sample() -> None:
        while True:
            child = operation
            frames: list[dict[str, Any]] = []
            for _ in range(12):
                code = getattr(child, "cr_code", None)
                if code is None:
                    break
                frame = getattr(child, "cr_frame", None)
                frames.append({"function": code.co_name, "line": frame.f_lineno if frame else None})
                child = getattr(child, "cr_await", None)
            row["last_await_frames"] = frames
            if snapshot is not None:
                row["sdk_counts"] = snapshot()
            row["samples"] = row.get("samples", 0) + 1
            await asyncio.sleep(period)

    monitor = asyncio.create_task(sample(), name="qa-rtc-comparison-observer")
    try:
        return await operation
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


async def close_checkpoint(
    operation: Any, *, budget: float = AGENT_RESOURCE_CLEANUP_TIMEOUT_SECONDS
) -> tuple[asyncio.Task[Any], dict[str, Any]]:
    """Observe the original budget without losing the single cleanup owner."""
    started = monotonic()
    task = asyncio.create_task(operation, name="qa-rtc-owned-cleanup")
    row = {
        "business_budget_ms": round(budget * 1000),
    }

    def completed(result: asyncio.Task[Any]) -> None:
        row["completed_ms"] = round((monotonic() - started) * 1000)
        row["completed_cancelled"] = result.cancelled()

    task.add_done_callback(completed)
    done, _ = await asyncio.wait({task}, timeout=budget)
    row["done_at_business_budget"] = bool(done)
    row["checkpoint_ms"] = round((monotonic() - started) * 1000)
    if done:
        await task
    return task, row


async def run(settings: Settings, *, rounds: int = 24) -> dict[str, Any]:
    modes = comparison_modes(rounds)
    assert importlib.metadata.version("livekit") == "1.1.20", "qa_fixed_sdk_required"
    assert settings.livekit_api_key is not None and settings.livekit_api_secret is not None
    url = settings.livekit_url
    assert isinstance(url, str) and url, "qa_livekit_route_required"
    baseline = set(asyncio.all_tasks())
    client = FfiClient.instance
    room_name = "jx-qa-rtc-" + uuid4().hex
    api_client = api.LiveKitAPI(
        url,
        settings.livekit_api_key.get_secret_value(),
        settings.livekit_api_secret.get_secret_value(),
    )
    created = False
    pending: set[asyncio.Task[Any]] = set()
    cleanup_shapes: dict[asyncio.Task[Any], tuple[dict[str, Any], float]] = {}
    rooms: list[Any] = []
    sources: list[Any] = []
    known_rooms: WeakKeyDictionary[Any, int] = WeakKeyDictionary()
    receipt: dict[str, Any] = {
        "probe": "spec304-rtc-observer-abba-v1",
        "status": "failed",
        "sdk": "1.1.20",
        "rounds_requested": rounds,
        "owned_room_name": room_name,
        "rows": [],
        "route_sha256": hashlib.sha256(url.encode()).hexdigest(),
        "core_database_accessed": False,
        "production_matches_created": 0,
        "observer_ffi_subscriptions_created": 0,
        "scope": (
            "SDK publisher connect/cleanup and full-probe ledger/task sampling; "
            "no match, ASR, TTS or audience"
        ),
        "before": {**process_counts(), **ffi_counts(client)},
    }
    lag = {"samples": 0, "sum_lag_ms": 0.0, "max_lag_ms": 0.0}

    async def measure_lag() -> None:
        while True:
            started = monotonic()
            await asyncio.sleep(0.05)
            elapsed = max(0.0, (monotonic() - started - 0.05) * 1000)
            lag["samples"] += 1
            lag["sum_lag_ms"] += elapsed
            lag["max_lag_ms"] = max(lag["max_lag_ms"], elapsed)

    lag_task = asyncio.create_task(measure_lag(), name="qa-rtc-loop-lag")
    started_all = monotonic()
    try:
        existing = await asyncio.wait_for(
            api_client.room.list_rooms(api.ListRoomsRequest(names=[room_name])), 5
        )
        assert not existing.rooms, "qa_owned_room_already_exists"
        own = await asyncio.wait_for(
            api_client.room.create_room(api.CreateRoomRequest(name=room_name)), 5
        )
        assert own.name == room_name
        created = True
        for index, mode in enumerate(modes):
            row: dict[str, Any] = {"round": index + 1, "mode": mode, "status": "failed"}
            receipt["rows"].append(row)
            ledger = NativeDisconnectLedger() if mode == "observed" else None
            room = rtc.Room()
            rooms.append(room)
            known_rooms[room] = index
            token = (
                api.AccessToken(
                    settings.livekit_api_key.get_secret_value(),
                    settings.livekit_api_secret.get_secret_value(),
                )
                .with_identity(f"jx-qa-rtc-{index}-{uuid4().hex}")
                .with_ttl(timedelta(minutes=5))
                .with_grants(api.VideoGrants(
                    room_join=True, room=room_name, can_publish=True,
                    can_subscribe=False, can_publish_data=False,
                ))
                .to_jwt()
            )
            if ledger is not None:
                ledger.install()
            connection_box: dict[str, asyncio.Task[Any]] = {}

            def sdk_snapshot(
                owned_room: Any = room,
                owned_box: dict[str, asyncio.Task[Any]] = connection_box,
                owned_index: int = index,
            ) -> dict[str, int | None]:
                connecting_queue = None
                connect_task = owned_box.get("task")
                if connect_task is not None:
                    coroutine = connect_task.get_coro()
                    frame = getattr(coroutine, "cr_frame", None)
                    connecting_queue = frame.f_locals.get("queue") if frame else None
                room_queue = getattr(owned_room, "_ffi_queue", None)
                older_listeners = sum(
                    1 for old, number in list(known_rooms.items())
                    if number < owned_index and getattr(old, "_task", None) is not None
                    and not old._task.done()
                )
                return {
                    **ffi_counts(client),
                    "connect_callback_queue": (
                        connecting_queue.qsize() if connecting_queue else None
                    ),
                    "room_event_queue": room_queue.qsize() if room_queue else None,
                    "older_live_listeners": older_listeners,
                }

            async def publisher(
                owned_room: Any = room,
                owned_row: dict[str, Any] = row,
                owned_token: str = token,
                owned_box: dict[str, asyncio.Task[Any]] = connection_box,
            ) -> None:
                began = monotonic()
                owned_row["phase"] = "connect"
                connect_task = asyncio.create_task(
                    owned_room.connect(url, owned_token),
                    name="qa-rtc-owned-connect",
                )
                owned_box["task"] = connect_task
                pending.add(connect_task)
                await asyncio.shield(connect_task)
                pending.discard(connect_task)
                owned_row["join_ms"] = round((monotonic() - began) * 1000)
                source = rtc.AudioSource(48_000, 1, queue_size_ms=200)
                sources.append(source)
                owned_row["phase"] = "publish_track"
                track = rtc.LocalAudioTrack.create_audio_track("qa-silent-probe", source)
                options = rtc.TrackPublishOptions()
                options.source = rtc.TrackSource.SOURCE_MICROPHONE
                await owned_room.local_participant.publish_track(track, options)
                owned_row["join_publish_ms"] = round((monotonic() - began) * 1000)
                owned_row["phase"] = "synthetic_audio"
                for _ in range(3):
                    await source.capture_frame(rtc.AudioFrame(
                        data=bytes(960), sample_rate=48_000, num_channels=1,
                        samples_per_channel=480,
                    ))
                await source.wait_for_playout()

            try:
                operation = publisher()
                if ledger is not None:
                    # Full flow concurrently samples host at100ms and join at50ms.
                    operation = observed_await(
                        operation, row.setdefault("join_observer", {}), 0.05, sdk_snapshot
                    )
                    operation = observed_await(operation, row.setdefault("host_observer", {}), 0.1)
                budget = host_audio_attempt_timeout_seconds(5690)
                row["original_closing_attempt_budget_ms"] = round(budget * 1000)
                await asyncio.wait_for(operation, timeout=budget)
                for source in sources[-1:]:
                    row["phase"] = "source_cleanup"
                    close_started = monotonic()
                    task, shape = await close_checkpoint(source.aclose())
                    pending.add(task)
                    cleanup_shapes[task] = shape, close_started
                    row["source_cleanup"] = shape
                    if not shape["done_at_business_budget"]:
                        raise TimeoutError("qa_original_source_cleanup_budget_exceeded")
                    pending.discard(task)
                    cleanup_shapes.pop(task, None)
                    sources.remove(source)
                began = monotonic()
                row["phase"] = "disconnect"
                task, shape = await close_checkpoint(room.disconnect())
                pending.add(task)
                cleanup_shapes[task] = shape, began
                row["disconnect"] = shape
                if not shape["done_at_business_budget"]:
                    # Stop comparison, while final cleanup keeps this exact task.
                    raise TimeoutError("qa_original_disconnect_budget_exceeded")
                pending.discard(task)
                cleanup_shapes.pop(task, None)
                rooms.remove(room)
                row["disconnect_ms"] = round((monotonic() - began) * 1000)
                row["status"] = "passed"
            except BaseException as error:
                row["exception_class"] = type(error).__name__ if type(error).__name__ in {
                    "TimeoutError", "CancelledError", "RuntimeError", "ConnectError"
                } else None
                row["sdk_counts_at_failure"] = sdk_snapshot()
                break
            finally:
                if ledger is not None:
                    ledger.restore()
                    row["disconnect_observer"] = list(ledger.rows)
                row["after"] = {**process_counts(), **ffi_counts(client)}
                row["loop_lag"] = dict(lag)
            if monotonic() - started_all > 150:
                receipt["watchdog_exceeded"] = True
                break
        receipt["status"] = "passed" if len(receipt["rows"]) == rounds and all(
            row["status"] == "passed" for row in receipt["rows"]
        ) else "failed"
    except BaseException as error:
        receipt["exception_class"] = type(error).__name__ if type(error).__name__ in {
            "TimeoutError", "CancelledError", "RuntimeError", "AssertionError"
        } else None
    finally:
        # These are independent fallback cleanup attempts for owned resources,
        # never retries of a failed diagnostic round or business model calls.
        async def cleanup_source(source: Any) -> None:
            await source.aclose()

        for source in sources:
            try:
                await asyncio.wait_for(cleanup_source(source), 3)
            except BaseException:
                receipt["fallback_source_cleanup_failed"] = True
        if pending:
            done, _ = await asyncio.wait(pending, timeout=3)
            for task in done:
                if not task.cancelled():
                    task.exception()
        if created:
            try:
                await asyncio.wait_for(
                    api_client.room.delete_room(api.DeleteRoomRequest(room=room_name)), 5
                )
                check = await asyncio.wait_for(
                    api_client.room.list_rooms(api.ListRoomsRequest(names=[room_name])), 5
                )
                receipt["owned_room_deleted"] = not check.rooms
            except BaseException:
                receipt["owned_room_deleted"] = False
        for room in rooms:
            try:
                await asyncio.wait_for(room.disconnect(), 3)
            except BaseException:
                receipt["fallback_room_cleanup_failed"] = True
        if pending:
            done, remaining = await asyncio.wait(pending, timeout=3)
            for task in done:
                if not task.cancelled():
                    task.exception()
            for task in remaining:
                task.cancel()
            await asyncio.gather(*remaining, return_exceptions=True)
            receipt["tasks_cancelled_after_owned_room_deletion"] = len(remaining)
        for task, (shape, began) in cleanup_shapes.items():
            shape["done_after_fallback"] = task.done() and not task.cancelled()
            if task.done() and not task.cancelled() and "completed_ms" not in shape:
                shape["completed_observed_ms"] = round((monotonic() - began) * 1000)
        await api_client.aclose()
        lag_task.cancel()
        await asyncio.gather(lag_task, return_exceptions=True)
        rooms.clear()
        sources.clear()
        gc.collect()
        await asyncio.sleep(0.5)
        receipt["after"] = {**process_counts(), **ffi_counts(client)}
        receipt["new_pending_tasks"] = len(asyncio.all_tasks() - baseline)
        receipt["elapsed_ms"] = round((monotonic() - started_all) * 1000)
        receipt["loop_lag"] = dict(lag)
        if receipt.get("owned_room_deleted") is not True or receipt["new_pending_tasks"]:
            receipt["status"] = "failed"
        if receipt["after"]["subscribers"] != receipt["before"]["subscribers"]:
            receipt["status"] = "failed"
    return receipt


async def main() -> int:
    try:
        receipt = await run(load_settings())
    except BaseException as error:
        receipt = {"status": "failed", "exception_class": type(error).__name__}
    print(json.dumps(receipt, sort_keys=True), flush=True)
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

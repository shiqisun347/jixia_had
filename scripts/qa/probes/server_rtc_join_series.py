"""One owned media room, 20 serial real publisher joins; no match/model/database writes."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from livekit import api, rtc
from server_full_match_probe import AudienceAudio
from server_provider_media_probe import connect_human, error_receipt

from jx_core.agent.runtime import AgentRuntime, _bounded_agent_cleanup_call
from jx_core.asr.runtime import MatchAudioReceiver
from jx_core.config import load_settings


async def run() -> dict[str, Any]:
    settings = load_settings()
    match_id = uuid4()
    receiver = MatchAudioReceiver(match_id=match_id, settings=settings, callbacks=SimpleNamespace())
    publisher = AgentRuntime(settings=settings, session_factory=None, callbacks=SimpleNamespace())
    audience = AudienceAudio()
    humans = []
    rows: list[dict[str, Any]] = []
    baseline = set(asyncio.all_tasks())
    started = asyncio.get_running_loop().time()
    try:
        await receiver.start()
        await audience.connect(settings, match_id)
        for _ in range(4):
            humans.append(await asyncio.wait_for(connect_human(settings, match_id, uuid4()), 15))
        for index in range(1, 21):
            room = source = None
            join_started = asyncio.get_running_loop().time()
            try:
                room, source = await asyncio.wait_for(
                    publisher._connect_publisher(
                        match_id,
                        identity_prefix="jx-host" if index % 2 else "jx-agent",
                        track_name="host-announcement" if index % 2 else "agent-speech",
                    ),
                    15,
                )
                for _ in range(10):
                    await source.capture_frame(
                        rtc.AudioFrame(
                            data=bytes(1920),
                            sample_rate=48000,
                            num_channels=1,
                            samples_per_channel=960,
                        )
                    )
                await source.wait_for_playout()
                row = {
                    "index": index,
                    "status": "passed",
                    "join_and_play_ms": round(
                        (asyncio.get_running_loop().time() - join_started) * 1000
                    ),
                }
            except Exception as error:
                row = {
                    "index": index,
                    **error_receipt(error),
                    "join_and_play_ms": round(
                        (asyncio.get_running_loop().time() - join_started) * 1000
                    ),
                }
            finally:
                if source is not None:
                    await _bounded_agent_cleanup_call(source.aclose, resource="qa-series-source")
                if room is not None:
                    await _bounded_agent_cleanup_call(room.disconnect, resource="qa-series-room")
                del source, room
                gc.collect()
                await asyncio.sleep(0.05)
            rows.append(row)
            print(json.dumps({"join": row}, sort_keys=True), flush=True)
            if row["status"] != "passed":
                break
        return {
            "probe": "serial_owned_rtc_join_series",
            "joins": rows,
            "status": "passed"
            if len(rows) == 20 and all(row["status"] == "passed" for row in rows)
            else "failed",
            "elapsed_ms": round((asyncio.get_running_loop().time() - started) * 1000),
        }
    finally:
        await receiver.close()
        await audience.close()
        for room, source in humans:
            await source.aclose()
            await room.disconnect()
        humans.clear()
        await publisher.close()
        client = api.LiveKitAPI(
            settings.livekit_url,
            settings.livekit_api_key.get_secret_value(),
            settings.livekit_api_secret.get_secret_value(),
        )
        try:
            await client.room.delete_room(api.DeleteRoomRequest(room=f"jx-match-{match_id}"))
            rooms = await client.room.list_rooms(
                api.ListRoomsRequest(names=[f"jx-match-{match_id}"])
            )
            print(json.dumps({"cleanup": {"owned_rtc_room_deleted": not rooms.rooms}}), flush=True)
        finally:
            await client.aclose()
        del receiver, audience, publisher
        gc.collect()
        await asyncio.sleep(0.5)
        print(
            json.dumps({"cleanup": {"new_pending_tasks": len(asyncio.all_tasks() - baseline)}}),
            flush=True,
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    try:
        result = asyncio.run(run())
    except Exception as error:
        result = error_receipt(error)
    print(json.dumps({"receipt": result}, sort_keys=True), flush=True)

"""Explicit server-only real provider/RTC QA; print aggregate receipts only.

Run from the deployed checkout with its normal load_settings context. This
creates normal OPS_PROBE audit captures, a precisely owned temporary RTC room,
and temporary synthetic audio. It never creates a production match or user.
It proves a short synthetic speech chain, not a full match or physical device.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import re
import sys
import tempfile
from array import array
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

from livekit import api, rtc
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ops"))
from provider_chain_probe import (  # noqa: E402
    load_material,
    persist_probe_capture,
    probe_asr,
    probe_judge,
    probe_llm,
    probe_tts,
)

from jx_core.agent.runtime import (  # noqa: E402
    _consume_detached_cleanup,
    _retired_publisher_connection_finished,
    _schedule_publisher_startup_cleanup,
)
from jx_core.asr.runtime import MatchAudioReceiver  # noqa: E402
from jx_core.config import load_settings  # noqa: E402
from jx_core.database import Database  # noqa: E402
from jx_core.models import ExternalCall  # noqa: E402
from jx_core.runtime_identity import CallbackEnvelope  # noqa: E402


def error_receipt(error: BaseException) -> dict[str, str]:
    code = str(getattr(error, "code", str(error)))
    if not re.fullmatch(r"[A-Za-z0-9_]{1,100}", code):
        code = type(error).__name__
    return {"status": "failed", "error_class": type(error).__name__, "error_code": code}


class SpeechReceipts:
    def __init__(self, database: Database, envelope: CallbackEnvelope) -> None:
        self.database = database
        self.envelope = envelope
        self.started_at = datetime.now(UTC)
        self.final: dict[str, Any] | None = None
        self.failures: list[str] = []
        self.segments = 0
        self.interims = 0
        self.captures = 0

    def check(self, values: dict[str, Any]) -> None:
        if values["envelope"] != self.envelope:
            raise RuntimeError("qa_callback_identity_mismatch")

    async def publish_asr_interim(self, **values: Any) -> None:
        self.check(values)
        self.interims += 1

    async def persist_asr_segment(self, **values: Any) -> None:
        self.check(values)
        self.segments += 1

    async def start_asr_segment(self, **values: Any) -> None:
        self.check(values)

    async def fail_asr_segment(self, **values: Any) -> None:
        self.check(values)
        self.failures.append(values["error_code"])

    async def persist_asr_capture(self, **values: Any) -> None:
        self.check(values)
        settings = load_settings()
        await persist_probe_capture(
            self.database,
            call_kind="ASR",
            provider="BAILIAN",
            operation="duplex.transcribe",
            model=settings.asr_model,
            voice=None,
            capture=values["provider_capture"],
            started_at=self.started_at,
            error=RuntimeError("qa_asr_segment_failed") if self.failures else None,
        )
        self.captures += 1

    async def finalize_asr_speech(self, **values: Any) -> None:
        self.check(values)
        self.final = {
            "transcript_chars": len(values["final_text"].strip()),
            "audio_duration_ms": values["audio_duration_ms"],
            "first_interim_latency_ms": values["first_interim_latency_ms"],
            "final_latency_ms": values["final_latency_ms"],
            "recording_saved": values["audio_storage_path"] is not None,
            "recording_error": values["audio_recording_error"],
        }

    async def handle_asr_failure(self, **values: Any) -> None:
        self.check(values)
        self.failures.append(values["code"])


async def connect_human(settings: Any, match_id: UUID, user_id: UUID) -> tuple[Any, Any]:
    """Use current publisher ownership helpers with a valid synthetic human identity."""
    token = (
        api.AccessToken(
            settings.livekit_api_key.get_secret_value(),
            settings.livekit_api_secret.get_secret_value(),
        )
        .with_identity(f"jx-human-{match_id}-{user_id}-1-{uuid4().hex[:8]}")
        .with_ttl(timedelta(minutes=10))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=f"jx-match-{match_id}",
                can_publish=True,
                can_subscribe=False,
                can_publish_data=False,
            )
        )
        .to_jwt()
    )
    room = rtc.Room()
    source = None
    connection_task = None
    try:
        connection_task = asyncio.create_task(
            room.connect(settings.livekit_url, token), name="jx-qa-human-connect"
        )
        connection_task.add_done_callback(_consume_detached_cleanup)
        await asyncio.shield(connection_task)
        source = rtc.AudioSource(48_000, 1, queue_size_ms=200)
        track = rtc.LocalAudioTrack.create_audio_track("synthetic-microphone", source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_MICROPHONE
        await room.local_participant.publish_track(track, options)
        return room, source
    except BaseException:
        if connection_task is not None and not connection_task.done():
            connection_task.add_done_callback(
                lambda completed: _retired_publisher_connection_finished(completed, room=room)
            )
        else:
            await asyncio.wait(_schedule_publisher_startup_cleanup(room, source))
        raise


async def rtc_speech(pcm: bytes, database: Database) -> dict[str, Any]:
    settings = load_settings()
    match_id, user_id, speech_id = uuid4(), uuid4(), uuid4()
    envelope = CallbackEnvelope(match_id, speech_id, 1, None, 1, 0, None, None)
    callbacks = SpeechReceipts(database, envelope)
    room_name = f"jx-match-{match_id}"
    temporary = tempfile.TemporaryDirectory(prefix="jx-media-qa-")
    qa_settings = settings.model_copy(update={"match_audio_storage_dir": temporary.name})
    receiver = MatchAudioReceiver(match_id=match_id, settings=qa_settings, callbacks=callbacks)
    room = source = None
    started = monotonic()
    baseline = set(asyncio.all_tasks())
    receipt: dict[str, Any] = {"status": "failed"}
    cleanup_errors: list[str] = []
    try:
        await receiver.start()
        room, source = await asyncio.wait_for(connect_human(qa_settings, match_id, user_id), 15)
        # Publisher's own join task is shielded; timeout never cancels SDK join.
        deadline = monotonic() + 5
        while not receiver._subscribed_tracks and monotonic() < deadline:
            await asyncio.sleep(0.02)
        if not receiver._subscribed_tracks:
            raise RuntimeError("qa_rtc_track_subscription_missing")
        await receiver.start_speech(speech_id, user_id, envelope)
        samples = array("h", pcm)
        expanded = array("h", (value for sample in samples for value in (sample,) * 3))
        frame_samples = 4800
        sent = 0
        for offset in range(0, len(expanded), frame_samples):
            frame = expanded[offset : offset + frame_samples]
            await source.capture_frame(
                rtc.AudioFrame(
                    data=frame.tobytes(),
                    sample_rate=48_000,
                    num_channels=1,
                    samples_per_channel=len(frame),
                )
            )
            sent += len(frame)
        # Add real silence to make the final audible sentence available before finish.
        for _ in range(7):
            await source.capture_frame(
                rtc.AudioFrame(
                    data=bytes(frame_samples * 2),
                    sample_rate=48_000,
                    num_channels=1,
                    samples_per_channel=frame_samples,
                )
            )
        await source.wait_for_playout()
        await asyncio.sleep(0.2)
        await asyncio.wait_for(receiver.finish_speech(speech_id), timeout=12)
        if callbacks.failures:
            raise RuntimeError("qa_rtc_asr_failure")
        if callbacks.final is None or callbacks.final["transcript_chars"] == 0:
            raise RuntimeError("qa_rtc_asr_final_missing")
        receipt = {
            "status": "passed",
            "transport": "real_livekit_audio_to_real_asr",
            "sent_audio_duration_ms": sent // 48,
            "wall_elapsed_ms": round((monotonic() - started) * 1000),
            "segments": callbacks.segments,
            "interims": callbacks.interims,
            "captures": callbacks.captures,
            **callbacks.final,
        }
    except Exception as error:
        receipt = error_receipt(error)
        receipt["provider_error_codes"] = callbacks.failures
    finally:
        # Native resources are closed sequentially while their asyncio/FFI loop lives.
        for cleanup in (
            receiver.close,
            source.aclose if source is not None else None,
            room.disconnect if room is not None else None,
        ):
            if cleanup is None:
                continue
            try:
                await asyncio.wait_for(cleanup(), timeout=12)
            except Exception as error:
                cleanup_errors.append(error_receipt(error)["error_code"])
        if settings.livekit_api_key is not None and settings.livekit_api_secret is not None:
            client = api.LiveKitAPI(
                settings.livekit_url,
                settings.livekit_api_key.get_secret_value(),
                settings.livekit_api_secret.get_secret_value(),
            )
            try:
                await client.room.delete_room(api.DeleteRoomRequest(room=room_name))
                remaining = await client.room.list_rooms(api.ListRoomsRequest(names=[room_name]))
                receipt["owned_rtc_room_deleted"] = len(remaining.rooms) == 0
            except Exception as error:
                cleanup_errors.append(error_receipt(error)["error_code"])
            finally:
                await client.aclose()
        receipt["receiver_closed"] = receiver._closed and receiver._room is None
        receipt["receiver_track_tasks"] = len(receiver._track_tasks)
        receipt["receiver_provider_detached"] = receiver._connection is None
        del receiver, source, room
        gc.collect()
        await asyncio.sleep(0.5)
        owned_pending = [task for task in asyncio.all_tasks() - baseline if not task.done()]
        receipt["new_pending_tasks_after_cleanup"] = len(owned_pending)
        temporary.cleanup()
        receipt["temporary_audio_removed"] = not Path(temporary.name).exists()
        receipt["cleanup_errors"] = cleanup_errors
        if (
            cleanup_errors
            or owned_pending
            or not receipt.get("owned_rtc_room_deleted")
            or not receipt["receiver_closed"]
            or not receipt["receiver_provider_detached"]
            or receipt["receiver_track_tasks"]
            or not receipt["temporary_audio_removed"]
        ):
            receipt["status"] = "failed"
            receipt["error_code"] = "qa_cleanup_incomplete"
    return receipt


async def run() -> dict[str, Any]:
    logging.basicConfig(level=logging.CRITICAL)
    settings = load_settings()
    database = Database(settings.database_url_value)
    started_at = datetime.now(UTC)
    receipt: dict[str, Any] = {
        "probe": "server_real_provider_media_short_chain",
        "livekit_sdk": version("livekit"),
        "production_matches_created": 0,
        "physical_microphone": "not_tested",
        "complete_4v4_match": "not_tested",
        "parallel_audio_streams": 1,
        "started_at": started_at.isoformat(),
    }
    try:
        material = await load_material(database)
        receipt["configuration"] = {
            "llm_model": material.model_id,
            "tts_model": settings.tts_model,
            "asr_model": settings.asr_model,
            "voice": material.voice,
            "voice_rate": material.voice_rate,
        }
        try:
            receipt["tts"], pcm = await probe_tts(material, database)
        except Exception as error:
            receipt["tts"] = error_receipt(error)
            return receipt
        for key, operation in (
            ("direct_asr", lambda: probe_asr(pcm, database)),
            ("llm", lambda: probe_llm(material, database)),
            ("judge", lambda: probe_judge(material, database)),
            ("rtc_asr", lambda: rtc_speech(pcm, database)),
        ):
            try:
                receipt[key] = await operation()
            except Exception as error:
                receipt[key] = error_receipt(error)
        async with database.session_factory() as audit:
            counts = await audit.execute(
                select(ExternalCall.call_kind, ExternalCall.status, func.count())
                .where(
                    ExternalCall.source_kind == "OPS_PROBE",
                    ExternalCall.started_at >= started_at,
                )
                .group_by(ExternalCall.call_kind, ExternalCall.status)
            )
            receipt["ops_probe_calls_in_window"] = [
                {"kind": kind, "status": status, "count": count}
                for kind, status, count in counts.all()
            ]
        receipt["completed_at"] = datetime.now(UTC).isoformat()
        return receipt
    finally:
        await database.dispose()


if __name__ == "__main__":
    result = asyncio.run(run())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    required = ("tts", "direct_asr", "llm", "judge", "rtc_asr")
    passed = all(result.get(key, {}).get("status") == "passed" for key in required)
    raise SystemExit(0 if passed else 1)

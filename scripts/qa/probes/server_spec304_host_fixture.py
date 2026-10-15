"""Explicit Spec 304 host QA inputs and read-only owned-asset acceptance.

The full match tool creates resources only in its separate owned QA database.
This helper never updates production resources or loads credentials/settings.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import av
from sqlalchemy import select, text

from jx_core.models import HostAudioAsset, Room, Rule, RuleAudioGeneration, VoiceProfile
from jx_core.rules.host_voice import (
    CONFERENCE_HOST_NAME,
    CONFERENCE_HOST_VOICE,
    is_host_reserved_voice,
)

HOST_FIXTURE_VERSION = "spec304-conference-host-fixture-v2"
HOST_WELCOME = "欢迎来到稷下辩论现场。以理相辩，以言会友。现在开始本场正式辩论。"
HOST_CLOSING = "本场辩论到此结束。感谢辩手的交锋，也感谢各位的聆听。"
TTS_MODEL = "qwen-audio-3.1-tts-flash"
RESERVED_VOICES = (CONFERENCE_HOST_VOICE, "longyixuanjun_v3.1")
MAX_ASSET_BYTES = 2 * 1024 * 1024
MAX_DECODED_SAMPLES = 30 * 48_000
VOICE_FIELDS = (
    "name",
    "kind",
    "provider_voice",
    "rate",
    "chars_per_second",
    "playback_gain",
    "avatar_key",
)


class HostFixtureError(RuntimeError):
    pass


async def load_resources(database: Any, original: Any = None) -> tuple[Any, list[dict[str, Any]]]:
    """Keep current model/judge material, choose eight non-host Agent voices."""
    if original is None:
        from server_full_match_probe import load_resources as original

    material, _ = await original(database)
    async with database.session_factory() as session:
        agents = list(
            (
                await session.scalars(
                    select(VoiceProfile)
                    .where(
                        VoiceProfile.status == "ENABLED",
                        VoiceProfile.kind == "AGENT",
                        VoiceProfile.provider_voice.not_in(RESERVED_VOICES),
                    )
                    .order_by(VoiceProfile.name, VoiceProfile.id)
                    .limit(8)
                )
            ).all()
        )
    if len(agents) != 8 or any(is_host_reserved_voice(voice.provider_voice) for voice in agents):
        raise HostFixtureError("qa_conference_agent_voice_resources_incomplete")
    host = {
        "name": CONFERENCE_HOST_NAME,
        "kind": "HOST",
        "provider_voice": CONFERENCE_HOST_VOICE,
        "rate": 1.0,
        "chars_per_second": 4.0,
        "playback_gain": 1.0,
        "avatar_key": None,
    }
    return material, [
        host,
        *[{field: getattr(voice, field) for field in VOICE_FIELDS} for voice in agents],
    ]


def _stage_boundaries(stages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Use the same stage/start/end order as the authoritative compiler."""
    boundaries: list[tuple[str, str]] = []
    for index, stage in enumerate(stages, 1):
        position = int(stage.get("position", index))
        for boundary in ("start", "end"):
            value = str(stage.get(f"{boundary}_host_text") or "")
            if value.strip():
                boundaries.append((f"stage-{position}-{boundary}", value))
        if stage.get("stage_kind") == "END":
            break
    return boundaries


def short_draft(
    original: Any,
    tag: str,
    *flags: Any,
    boundary_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prefix the original first cue; replace only the actual final farewell."""
    draft = deepcopy(original(tag, *flags))
    stages = cast(list[dict[str, Any]], draft["stages"])
    non_end = [stage for stage in stages if stage.get("stage_kind") != "END"]
    if not non_end:
        raise HostFixtureError("qa_conference_host_boundaries_missing")
    end = next((stage for stage in stages if stage.get("stage_kind") == "END"), None)
    closing_target = next(
        (
            (end, field)
            for field in ("end_host_text", "start_host_text")
            if end is not None and str(end.get(field) or "").strip()
        ),
        (non_end[-1], "end_host_text"),
    )
    first_target = next(
        (
            (stage, field)
            for stage in stages
            for field in ("start_host_text", "end_host_text")
            if str(stage.get(field) or "").strip()
        ),
        (non_end[0], "start_host_text"),
    )
    if first_target[0] is closing_target[0] and first_target[1] == closing_target[1]:
        first_target = non_end[0], "start_host_text"
    first_stage, first_field = first_target
    original_prompt = str(first_stage.get(first_field) or "")
    prefix = HOST_WELCOME + "\n"
    if original_prompt.startswith(prefix):
        original_prompt = original_prompt[len(prefix) :]
    first_stage[first_field] = prefix + original_prompt
    closing_target[0][closing_target[1]] = HOST_CLOSING
    boundaries = _stage_boundaries(stages)
    if (
        len(boundaries) < 2
        or boundaries[0][1] != prefix + original_prompt
        or boundaries[-1][1] != HOST_CLOSING
        or sum(value.count(HOST_WELCOME) for _, value in boundaries) != 1
        or sum(value.count(HOST_CLOSING) for _, value in boundaries) != 1
    ):
        raise HostFixtureError("qa_conference_host_boundary_order_or_duplicate")
    if boundary_receipt is not None:
        boundary_receipt.clear()
        boundary_receipt.update(
            welcome_segment_key=boundaries[0][0],
            closing_segment_key=boundaries[-1][0],
            original_first_prompt_sha256=hashlib.sha256(original_prompt.encode()).hexdigest(),
            original_first_prompt_present=bool(original_prompt.strip()),
            host_text_sha256={
                key: hashlib.sha256(value.encode()).hexdigest() for key, value in boundaries
            },
        )
    return draft


def decoded_duration_ms(path: Path) -> int:
    """Count actual 48 kHz mono decoded samples; do not trust Ogg metadata."""
    samples = 0
    resampler = av.AudioResampler(format="s16", layout="mono", rate=48_000)
    with av.open(str(path)) as container:
        if len(container.streams.audio) != 1:
            raise HostFixtureError("qa_conference_audio_stream_count_invalid")
        for frame in container.decode(audio=0):
            for normalized in resampler.resample(frame):
                samples += normalized.samples
            if samples > MAX_DECODED_SAMPLES:
                raise HostFixtureError("qa_conference_host_audio_too_long")
        for normalized in resampler.resample(None):
            samples += normalized.samples
    if not 0 < samples <= MAX_DECODED_SAMPLES:
        raise HostFixtureError("qa_conference_host_audio_empty_or_too_long")
    return samples // 48


def verify_boundary_durations(welcome_ms: int, closing_ms: int) -> None:
    if not 5_000 <= welcome_ms <= 8_000 or not 5_000 <= closing_ms <= 8_000:
        raise HostFixtureError("qa_conference_host_boundary_duration_out_of_range")


async def measure_welcome_preface(settings: Any, host_root: Path) -> dict[str, Any]:
    """Measure only the added preface via the same real Jobs adapter, no DB."""
    from jx_jobs.tts import DashScopeTTSClient  # pyright: ignore[reportMissingTypeStubs]

    temporary_root = Path(tempfile.mkdtemp(prefix=".spec304-welcome-", dir=host_root.parent))
    temporary_root.chmod(0o700)
    output = temporary_root / "welcome.ogg"
    client: DashScopeTTSClient | None = None
    try:
        client = DashScopeTTSClient(
            websocket_url=settings.tts_ws_url,
            api_key=settings.tts_api_key.get_secret_value(),
            workspace=settings.tts_workspace_id or "",
            model=settings.tts_model,
            task_timeout_seconds=30,
            max_audio_bytes=MAX_ASSET_BYTES,
        )
        async with asyncio.timeout(45):
            await client.synthesize_to_file(
                text=HOST_WELCOME,
                voice=CONFERENCE_HOST_VOICE,
                rate=1.0,
                output_path=output,
            )
            if not output.is_file() or not 0 < output.stat().st_size <= MAX_ASSET_BYTES:
                raise HostFixtureError("qa_conference_welcome_preface_audio_invalid")
            measured_ms = await asyncio.wait_for(
                asyncio.to_thread(decoded_duration_ms, output), timeout=10
            )
            receipt = {
                "status": "passed",
                "actual_provider": True,
                "adapter": "jx_jobs.tts.DashScopeTTSClient",
                "provider_voice": CONFERENCE_HOST_VOICE,
                "model": TTS_MODEL,
                "rate": 1.0,
                "text_sha256": hashlib.sha256(HOST_WELCOME.encode()).hexdigest(),
                "audio_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "decoded_ms": measured_ms,
            }
    except Exception as error:
        raise HostFixtureError("qa_conference_welcome_preface_measurement_failed") from error
    finally:
        try:
            if client is not None:
                await asyncio.wait_for(client.close(), timeout=3)
        finally:
            shutil.rmtree(temporary_root)
    receipt["temporary_audio_removed"] = not temporary_root.exists()
    return receipt


async def verify_prepared(factory: Any, settings: Any, values: dict[str, Any]) -> dict[str, Any]:
    """Verify identity, frozen asset input, actual files and actual durations."""
    host_root = Path(settings.host_audio_storage_dir).resolve()
    if (
        not host_root.parent.name.startswith("jx-full-match-qa-")
        or host_root == host_root.parent
        or settings.tts_model != TTS_MODEL
    ):
        raise HostFixtureError("qa_conference_storage_or_model_not_owned")
    async with factory() as session:
        identity = (
            (
                await session.execute(
                    text("SELECT current_database() AS name, host(inet_server_addr()) AS address")
                )
            )
            .mappings()
            .one()
        )
        if identity["name"] != "jx_debate_qa_agent_faults" or identity["address"] not in {
            "127.0.0.1",
            "::1",
        }:
            raise HostFixtureError("qa_conference_database_not_owned")
        room = await session.get(Room, values["room_id"])
        rule = await session.get(Rule, room.rule_id) if room else None
        generation = (
            await session.get(RuleAudioGeneration, rule.current_audio_generation_id)
            if rule and rule.current_audio_generation_id
            else None
        )
        host = await session.get(VoiceProfile, rule.host_voice_profile_id) if rule else None
        if (
            room is None
            or not room.title.startswith("真实短场QA-")
            or room.label != "服务器隔离合成QA"
            or rule is None
            or not rule.name.startswith("真实短场QA-")
            or generation is None
            or generation.status != "SUCCEEDED"
            or generation.review_status != "APPROVED"
            or not generation.is_current
            or host is None
            or host.kind != "HOST"
            or host.status != "ENABLED"
            or host.name != CONFERENCE_HOST_NAME
            or host.provider_voice != CONFERENCE_HOST_VOICE
            or host.rate != 1.0
        ):
            raise HostFixtureError("qa_conference_host_fixture_identity_invalid")
        assets = list(
            (
                await session.scalars(
                    select(HostAudioAsset)
                    .where(
                        HostAudioAsset.rule_id == rule.id,
                        HostAudioAsset.generation_id == generation.id,
                    )
                    .order_by(HostAudioAsset.segment_key)
                    .limit(13)
                )
            ).all()
        )
    expected = int(values["host_assets"])
    raw_boundaries = values.get("conference_host_boundary_inputs")
    if not isinstance(raw_boundaries, dict):
        raise HostFixtureError("qa_conference_original_host_boundary_inputs_missing")
    expected_boundaries = cast(dict[str, Any], raw_boundaries)
    raw_text_hashes = expected_boundaries.get("host_text_sha256")
    if not isinstance(raw_text_hashes, dict):
        raise HostFixtureError("qa_conference_original_host_boundary_inputs_missing")
    expected_text_hashes = cast(dict[str, str], raw_text_hashes)
    frozen = room.rule_snapshot
    boundaries = _stage_boundaries(frozen.get("stages", []))
    prefix = HOST_WELCOME + "\n"
    if (
        len(boundaries) < 2
        or boundaries[0][0] != expected_boundaries.get("welcome_segment_key")
        or boundaries[-1][0] != expected_boundaries.get("closing_segment_key")
        or not boundaries[0][1].startswith(prefix)
        or boundaries[-1][1] != HOST_CLOSING
        or hashlib.sha256(boundaries[0][1][len(prefix) :].encode()).hexdigest()
        != expected_boundaries.get("original_first_prompt_sha256")
        or {key: hashlib.sha256(value.encode()).hexdigest() for key, value in boundaries}
        != expected_text_hashes
        or sum(value.count(HOST_WELCOME) for _, value in boundaries) != 1
        or sum(value.count(HOST_CLOSING) for _, value in boundaries) != 1
    ):
        raise HostFixtureError("qa_conference_original_host_cue_not_preserved")
    frozen_identity = frozen.get("host_identity", {})
    frozen_assets = {item["segment_key"]: item for item in frozen.get("host_audio", [])}
    if (
        not 2 <= expected <= 12
        or len(assets) != expected
        or len(frozen_assets) != expected
        or set(frozen_assets) != set(expected_text_hashes)
        or generation.total_count != expected
        or generation.ready_count != expected
        or generation.failed_count != 0
        or frozen_identity.get("voice_profile_id") != str(host.id)
        or frozen_identity.get("name") != CONFERENCE_HOST_NAME
        or frozen_identity.get("provider_voice") != CONFERENCE_HOST_VOICE
        or frozen_identity.get("rate") != 1.0
        or frozen_identity.get("model") != TTS_MODEL
        or frozen_identity.get("generation_id") != str(generation.id)
    ):
        raise HostFixtureError("qa_conference_frozen_host_identity_mismatch")
    format_snapshot = cast(dict[str, Any], room.format_snapshot or {})
    frozen_agents = cast(list[dict[str, Any]], format_snapshot.get("agents", []))
    if len(frozen_agents) != 8 or any(
        is_host_reserved_voice(item.get("voice", {}).get("provider_voice", ""))
        for item in frozen_agents
    ):
        raise HostFixtureError("qa_conference_frozen_agent_pool_contains_host")
    receipts: list[dict[str, Any]] = []
    welcome_ms, closing_ms = None, None
    for asset in assets:
        snapshot = asset.input_snapshot
        frozen_asset = frozen_assets.get(asset.segment_key)
        if (
            asset.status != "READY"
            or asset.voice_profile_id != host.id
            or asset.generation_id != generation.id
            or not asset.storage_path
            or not asset.checksum
            or snapshot.get("voice_profile_id") != str(host.id)
            or snapshot.get("provider_voice") != CONFERENCE_HOST_VOICE
            or snapshot.get("rate") != 1.0
            or snapshot.get("model") != TTS_MODEL
            or snapshot.get("text") != asset.text
            or snapshot.get("text_hash") != asset.text_hash
            or hashlib.sha256(asset.text.encode()).hexdigest() != asset.text_hash
            or asset.text_hash != expected_text_hashes.get(asset.segment_key)
            or frozen_asset is None
            or frozen_asset.get("voice_profile_id") != str(host.id)
            or frozen_asset.get("provider_voice") != CONFERENCE_HOST_VOICE
            or frozen_asset.get("rate") != 1.0
            or frozen_asset.get("model") != TTS_MODEL
            or frozen_asset.get("generation_id") != str(generation.id)
            or frozen_asset.get("input_hash") != asset.input_hash
            or frozen_asset.get("storage_path") != asset.storage_path
            or frozen_asset.get("duration_ms") != asset.duration_ms
            or frozen_asset.get("text_hash") != asset.text_hash
        ):
            raise HostFixtureError("qa_conference_frozen_host_asset_mismatch")
        path = (host_root / asset.storage_path).resolve()
        if (
            not path.is_relative_to(host_root)
            or not path.is_file()
            or not 0 < path.stat().st_size <= MAX_ASSET_BYTES
        ):
            raise HostFixtureError("qa_conference_host_asset_not_owned_or_invalid")
        if hashlib.sha256(path.read_bytes()).hexdigest() != asset.checksum:
            raise HostFixtureError("qa_conference_host_asset_checksum_mismatch")
        actual_ms = await asyncio.wait_for(asyncio.to_thread(decoded_duration_ms, path), timeout=10)
        if abs(actual_ms - int(asset.duration_ms or 0)) > 100:
            raise HostFixtureError("qa_conference_host_asset_duration_mismatch")
        if asset.segment_key == boundaries[0][0]:
            if welcome_ms is not None:
                raise HostFixtureError("qa_conference_welcome_not_unique")
            if asset.text != boundaries[0][1]:
                raise HostFixtureError("qa_conference_welcome_text_mismatch")
            welcome_ms = actual_ms
        if asset.segment_key == boundaries[-1][0]:
            if closing_ms is not None:
                raise HostFixtureError("qa_conference_closing_not_unique")
            if asset.text != HOST_CLOSING:
                raise HostFixtureError("qa_conference_closing_text_mismatch")
            closing_ms = actual_ms
        receipts.append({"segment_key": asset.segment_key, "whole_asset_decoded_ms": actual_ms})
    if welcome_ms is None or closing_ms is None:
        raise HostFixtureError("qa_conference_host_boundaries_missing")
    preface = await measure_welcome_preface(settings, host_root)
    if (
        preface.get("status") != "passed"
        or preface.get("actual_provider") is not True
        or preface.get("provider_voice") != CONFERENCE_HOST_VOICE
        or preface.get("model") != TTS_MODEL
        or preface.get("rate") != 1.0
        or preface.get("text_sha256") != hashlib.sha256(HOST_WELCOME.encode()).hexdigest()
        or preface.get("temporary_audio_removed") is not True
        or not isinstance(preface.get("decoded_ms"), int)
    ):
        raise HostFixtureError("qa_conference_welcome_preface_identity_invalid")
    verify_boundary_durations(preface["decoded_ms"], closing_ms)
    receipt: dict[str, Any] = {
        "status": "passed",
        "fixture_version": HOST_FIXTURE_VERSION,
        "explicit_qa_configuration": True,
        "production_host_modified": False,
        "host_name": CONFERENCE_HOST_NAME,
        "provider_voice": CONFERENCE_HOST_VOICE,
        "rate": 1.0,
        "model": TTS_MODEL,
        "voice_profile_id": str(host.id),
        "generation_id": str(generation.id),
        "all_assets_frozen": True,
        "normal_agent_voices": 8,
        "host_reserved_absent_from_agents": True,
        "welcome_segment_key": boundaries[0][0],
        "closing_segment_key": boundaries[-1][0],
        "original_first_host_prompt_preserved": True,
        "original_first_host_prompt_present": expected_boundaries.get(
            "original_first_prompt_present"
        ),
        "welcome_preface_ms": preface["decoded_ms"],
        "welcome_whole_asset_decoded_ms": welcome_ms,
        "welcome_preface_measurement": preface,
        "closing_decoded_ms": closing_ms,
        "assets": receipts,
    }
    values["conference_host_fixture"] = receipt
    return receipt

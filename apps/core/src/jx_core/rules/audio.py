"""Host-audio manifests, immutable batches, and transactional state convergence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.errors import AuthError
from ..models import (
    BackgroundTask,
    HostAudioAsset,
    Rule,
    RuleAudioGeneration,
    RuleStage,
    VoiceProfile,
)


@dataclass(frozen=True)
class AudioConfiguration:
    model: str = "qwen-audio-3.1-tts-flash"
    workspace_id: str = ""
    websocket_url: str = ""
    storage_root: str | None = None


def fingerprint(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def audio_file(root: str | Path, relative: str) -> Path:
    base = Path(root).resolve()
    path = (base / relative).resolve()
    if path == base or not path.is_relative_to(base):
        raise AuthError("host_audio_unavailable")
    return path


def project(rule: Rule, generation: RuleAudioGeneration) -> None:
    if rule.lifecycle_status == "ARCHIVED":
        rule.status = "ARCHIVED"
    elif generation.status == "FAILED":
        rule.status = "GENERATING_AUDIO_FAILED"
    elif generation.status in {"QUEUED", "RUNNING"}:
        rule.status = "GENERATING_AUDIO"
    elif generation.review_status in {"APPROVED", "NOT_REQUIRED"}:
        # A draft with no host segments is immediately ready for review-free
        # enablement.  Keep lifecycle_status as the authoritative published
        # switch; status describes whether the configuration is ready to be
        # enabled while it remains unpublished.
        rule.status = "ENABLED" if rule.lifecycle_status == "ENABLED" else "READY"
    else:
        rule.status = "READY"
    rule.audio_reviewed_at = (
        generation.approved_at or generation.requested_at
        if generation.review_status in {"APPROVED", "NOT_REQUIRED"}
        else None
    )


async def aggregate(session: AsyncSession, rule: Rule, generation: RuleAudioGeneration) -> None:
    if rule.current_audio_generation_id != generation.id or not generation.is_current:
        return
    await session.flush()
    assets = list(
        (
            await session.scalars(
                select(HostAudioAsset).where(HostAudioAsset.generation_id == generation.id)
            )
        ).all()
    )
    generation.total_count = len(generation.manifest.get("segments", []))
    generation.ready_count = sum(a.status == "READY" for a in assets)
    generation.failed_count = sum(a.status == "FAILED" for a in assets)
    active = any(a.status in {"PENDING", "RUNNING"} for a in assets)
    if not generation.total_count:
        generation.status, generation.review_status = "NOT_REQUIRED", "NOT_REQUIRED"
    elif len(assets) != generation.total_count:
        generation.status = "FAILED"
        generation.last_error_code = "tts_assets_missing"
    elif generation.ready_count == generation.total_count:
        generation.status = "SUCCEEDED"
        generation.last_error_code = None
    elif active:
        generation.status = "RUNNING" if any(a.status == "RUNNING" for a in assets) else "QUEUED"
    else:
        generation.status = "FAILED"
        generation.last_error_code = next(
            (a.error_code for a in assets if a.error_code), "tts_asset_failed"
        )
    generation.completed_at = (
        (generation.completed_at or datetime.now(UTC))
        if generation.status in {"NOT_REQUIRED", "SUCCEEDED", "FAILED"}
        else None
    )
    project(rule, generation)


async def queue_asset(
    session: AsyncSession, generation: RuleAudioGeneration, asset: HostAudioAsset
) -> None:
    task = BackgroundTask(
        task_type="HOST_TTS",
        payload={
            "rule_id": str(generation.rule_id),
            "generation_id": str(generation.id),
            "asset_id": str(asset.id),
            "manifest_hash": generation.manifest_hash,
            "config_revision": generation.config_revision,
        },
        available_at=datetime.now(UTC),
        max_attempts=2,
    )
    session.add(task)
    await session.flush()
    asset.active_task_id = task.id
    asset.status, asset.error_code = "PENDING", None


async def build_manifest(
    session: AsyncSession, *, rule: Rule, configuration: AudioConfiguration
) -> dict[str, Any]:
    stages = list(
        (
            await session.scalars(
                select(RuleStage).where(RuleStage.rule_id == rule.id).order_by(RuleStage.position)
            )
        ).all()
    )
    voice = (
        await session.get(VoiceProfile, rule.host_voice_profile_id)
        if rule.host_voice_profile_id
        else None
    )
    segments: list[dict[str, Any]] = []
    for stage in stages:
        for boundary, text_value in (
            ("start", stage.start_host_text),
            ("end", stage.end_host_text),
        ):
            if not text_value.strip():
                continue
            if voice is None or voice.kind != "HOST" or voice.status != "ENABLED":
                raise AuthError("host_voice_unavailable")
            inputs = {
                "text": text_value,
                "text_hash": sha256(text_value.encode()).hexdigest(),
                "voice_profile_id": str(voice.id),
                "provider_voice": voice.provider_voice,
                "rate": voice.rate,
                "model": configuration.model,
                "workspace_id": configuration.workspace_id,
                "websocket_url": configuration.websocket_url,
                "format": "ogg_opus",
                "sample_rate": 24000,
                "bit_rate": 32,
            }
            segments.append(
                {
                    "stage_id": str(stage.id),
                    "boundary": boundary,
                    "position": stage.position,
                    "segment_key": f"stage-{stage.position}-{boundary}",
                    "input_hash": fingerprint(inputs),
                    "input": inputs,
                }
            )
    return {"schema": "host-audio-v1", "segments": segments}


async def create_generation(
    session: AsyncSession,
    *,
    rule: Rule,
    actor_user_id: UUID | None,
    configuration: AudioConfiguration | None = None,
    force: bool = False,
) -> RuleAudioGeneration:
    configuration = configuration or AudioConfiguration()
    manifest = await build_manifest(session, rule=rule, configuration=configuration)
    segments = manifest["segments"]
    manifest_hash = fingerprint(manifest)
    previous = (
        await session.get(
            RuleAudioGeneration, rule.current_audio_generation_id, with_for_update=True
        )
        if rule.current_audio_generation_id
        else None
    )
    if previous and previous.manifest_hash == manifest_hash and not force:
        return previous
    reusable: dict[str, HostAudioAsset] = {}
    if previous:
        for asset in (
            await session.scalars(
                select(HostAudioAsset).where(HostAudioAsset.generation_id == previous.id)
            )
        ).all():
            if (
                asset.status == "READY"
                and asset.input_snapshot.get("provenance") != "legacy_unknown"
                and asset.input_hash
                and asset.storage_path
                and (asset.duration_ms or 0) > 0
            ):
                if (
                    configuration.storage_root is None
                    or audio_file(configuration.storage_root, asset.storage_path).is_file()
                ):
                    reusable[asset.input_hash] = asset
        previous.is_current, previous.status = False, "SUPERSEDED"
        tasks = list(
            (
                await session.scalars(
                    select(BackgroundTask).where(
                        BackgroundTask.task_type == "HOST_TTS",
                        BackgroundTask.payload["generation_id"].astext == str(previous.id),
                        BackgroundTask.status.in_(["PENDING", "RUNNING"]),
                    )
                )
            ).all()
        )
        for task in tasks:
            task.status, task.error_code, task.lease_until = (
                "FAILED",
                "tts_generation_superseded",
                None,
            )
    generation = RuleAudioGeneration(
        rule_id=rule.id,
        config_revision=rule.config_revision,
        manifest_hash=manifest_hash,
        manifest=manifest,
        requested_by=actor_user_id,
        requested_at=datetime.now(UTC),
        is_current=True,
        total_count=len(segments),
        ready_count=0,
        failed_count=0,
        status="QUEUED" if segments else "NOT_REQUIRED",
        review_status="PENDING" if segments else "NOT_REQUIRED",
    )
    session.add(generation)
    await session.flush()
    rule.current_audio_generation_id = generation.id
    for segment in segments:
        inputs = segment["input"]
        asset = HostAudioAsset(
            rule_id=rule.id,
            generation_id=generation.id,
            segment_key=segment["segment_key"],
            text=inputs["text"],
            text_hash=inputs["text_hash"],
            input_hash=segment["input_hash"],
            input_snapshot=inputs,
            voice_profile_id=UUID(inputs["voice_profile_id"]),
        )
        session.add(asset)
        await session.flush()
        cached = reusable.get(asset.input_hash or "") if not force else None
        if cached:
            asset.status, asset.storage_path, asset.duration_ms = (
                "READY",
                cached.storage_path,
                cached.duration_ms,
            )
            asset.byte_count, asset.checksum = cached.byte_count, cached.checksum
        else:
            await queue_asset(session, generation, asset)
    await aggregate(session, rule, generation)
    return generation


async def repair_generation(
    session: AsyncSession, *, rule: Rule, retry: bool = False
) -> RuleAudioGeneration:
    if rule.current_audio_generation_id is None:
        raise AuthError("rule_audio_not_ready")
    generation = await session.get(
        RuleAudioGeneration, rule.current_audio_generation_id, with_for_update=True
    )
    if generation is None:
        raise AuthError("rule_audio_not_ready")
    now = datetime.now(UTC)
    assets = list(
        (
            await session.scalars(
                select(HostAudioAsset)
                .where(HostAudioAsset.generation_id == generation.id)
                .with_for_update()
            )
        ).all()
    )
    by_key = {asset.segment_key: asset for asset in assets}
    for segment in generation.manifest.get("segments", []):
        if segment["segment_key"] in by_key or not retry:
            continue
        inputs = segment["input"]
        asset = HostAudioAsset(
            rule_id=rule.id,
            generation_id=generation.id,
            segment_key=segment["segment_key"],
            text=inputs["text"],
            text_hash=inputs["text_hash"],
            voice_profile_id=UUID(inputs["voice_profile_id"]),
            input_hash=segment["input_hash"],
            input_snapshot=inputs,
            status="FAILED",
            error_code="tts_assets_missing",
        )
        session.add(asset)
        await session.flush()
        assets.append(asset)
    for asset in assets:
        if asset.status == "READY":
            continue
        task = (
            await session.get(BackgroundTask, asset.active_task_id, with_for_update=True)
            if asset.active_task_id
            else None
        )
        if task and task.status == "RUNNING" and task.lease_until and task.lease_until > now:
            continue
        if task and task.status == "RUNNING":
            task.status = "FAILED" if task.attempts >= task.max_attempts else "PENDING"
            task.error_code, task.lease_until = "tts_worker_lease_expired", None
        if task is None or task.status in {"FAILED", "SUCCEEDED"}:
            asset.status, asset.error_code = (
                "FAILED",
                task.error_code if task else "tts_task_missing",
            )
            if retry:
                await queue_asset(session, generation, asset)
        elif asset.status == "RUNNING":
            asset.status = "PENDING"
    await aggregate(session, rule, generation)
    return generation

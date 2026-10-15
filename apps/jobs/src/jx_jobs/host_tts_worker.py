"""Bounded host TTS with immutable inputs and fenced, atomic DB completion."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

import av
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jx_core.data_capture.provider import PROVIDER_CAPTURE_VERSION, ProviderCallCapture
from jx_core.data_capture.provider_writer import ProviderCaptureWriter, prepare_capture
from jx_core.models import BackgroundTask, ExternalCall, HostAudioAsset, Rule, RuleAudioGeneration
from jx_core.rules.audio import aggregate, repair_generation

from .checksums import sha256_file
from .task_queue import TaskClaim, claim_next
from .tts import TTSProviderError


class TTSClient(Protocol):
    async def synthesize_to_file(
        self, *, text: str, voice: str, rate: float, output_path: Path
    ) -> None: ...


@runtime_checkable
class CapturedTTSClient(Protocol):
    def take_capture(self) -> ProviderCallCapture | None: ...


def audio_duration_ms(path: Path) -> int:
    """Count decoded samples; an Ogg container duration may describe only its tail."""
    seconds = 0.0
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            seconds += frame.samples / frame.sample_rate
    duration = round(seconds * 1000)
    if duration <= 0:
        raise TTSProviderError("tts_audio_duration_invalid")
    return duration


async def _fenced_rows(
    session: AsyncSession, claim: TaskClaim
) -> tuple[Rule, RuleAudioGeneration, HostAudioAsset, BackgroundTask] | None:
    try:
        rule_id = UUID(str(claim.payload["rule_id"]))
        generation_id = UUID(str(claim.payload["generation_id"]))
        asset_id = UUID(str(claim.payload["asset_id"]))
    except (ValueError, KeyError):
        return None
    rule = await session.get(Rule, rule_id, with_for_update=True)
    generation = await session.get(RuleAudioGeneration, generation_id, with_for_update=True)
    asset = await session.get(HostAudioAsset, asset_id, with_for_update=True)
    task = await session.get(BackgroundTask, claim.task_id, with_for_update=True)
    if (
        rule is None
        or generation is None
        or asset is None
        or task is None
        or rule.lifecycle_status == "ARCHIVED"
        or rule.current_audio_generation_id != generation_id
        or not generation.is_current
        or generation.rule_id != rule.id
        or asset.rule_id != rule.id
        or asset.generation_id != generation.id
        or asset.active_task_id != task.id
        or generation.manifest_hash != claim.payload.get("manifest_hash")
        or generation.config_revision != claim.payload.get("config_revision")
        or task.status != "RUNNING"
        or task.attempts != claim.attempt_no
        or task.lease_until is None
        or task.lease_until <= datetime.now(UTC)
    ):
        return None
    return rule, generation, asset, task


async def reconcile_host_tasks(factory: async_sessionmaker[AsyncSession]) -> None:
    """Bounded recovery also settles an exhausted attempt abandoned by a crash."""
    async with factory() as session:
        async with session.begin():
            ids = list(
                (
                    await session.scalars(
                        select(Rule.id)
                        .join(
                            RuleAudioGeneration,
                            RuleAudioGeneration.id == Rule.current_audio_generation_id,
                        )
                        .where(RuleAudioGeneration.status.in_(["QUEUED", "RUNNING"]))
                        .order_by(Rule.updated_at)
                        .limit(20)
                    )
                ).all()
            )
            for rule_id in ids:
                rule = await session.get(Rule, rule_id, with_for_update=True)
                if rule is not None:
                    await repair_generation(session, rule=rule)


async def process_one_host_tts(
    factory: async_sessionmaker[AsyncSession],
    *,
    client: TTSClient,
    storage_root: Path,
    capture_writer: ProviderCaptureWriter | None = None,
) -> bool:
    await reconcile_host_tasks(factory)
    async with factory() as session:
        claim = await claim_next(session, task_type="HOST_TTS", lease_seconds=210)
    if claim is None:
        return False
    call_id = uuid4()
    async with factory() as session:
        async with session.begin():
            rows = await _fenced_rows(session, claim)
            if rows is None:
                task = await session.get(BackgroundTask, claim.task_id, with_for_update=True)
                if task and task.attempts == claim.attempt_no and task.status == "RUNNING":
                    task.status, task.error_code, task.lease_until = (
                        "FAILED",
                        "tts_generation_stale",
                        None,
                    )
                return True
            rule, generation, asset, task = rows
            inputs = dict(asset.input_snapshot)
            asset.status = "RUNNING"
            generation.started_at = generation.started_at or datetime.now(UTC)
            await aggregate(session, rule, generation)
            call = ExternalCall(
                id=call_id,
                call_kind="TTS",
                provider="BAILIAN",
                operation="duplex.synthesize",
                model=str(inputs.get("model", "")),
                voice=str(inputs.get("provider_voice", "")),
                attempt_no=claim.attempt_no,
                status="STARTED",
                capture_version=PROVIDER_CAPTURE_VERSION,
                capture_storage_mode="ASYNC_V1",
                captured_at=datetime.now(UTC),
                source_kind="HOST_AUDIO",
                source_resource_id=str(asset.id),
                logical_call_id=task.id,
                started_at=datetime.now(UTC),
            )
            session.add(call)
    output_path = (
        storage_root
        / "rules"
        / str(claim.payload["rule_id"])
        / str(claim.payload["generation_id"])
        / f"{claim.task_id}-{claim.attempt_no}.ogg"
    )
    error_code: str | None = None
    duration_ms, byte_count, checksum = 0, 0, ""
    try:
        if not inputs or getattr(client, "model", inputs.get("model")) != inputs.get("model"):
            raise TTSProviderError("tts_config_changed")
        async with asyncio.timeout(180):
            await client.synthesize_to_file(
                text=str(inputs["text"]),
                voice=str(inputs["provider_voice"]),
                rate=float(inputs["rate"]),
                output_path=output_path,
            )
            duration_ms = await asyncio.to_thread(audio_duration_ms, output_path)
            byte_count = output_path.stat().st_size
            checksum = await asyncio.to_thread(sha256_file, output_path)
    except TTSProviderError as error:
        error_code = error.code
    except TimeoutError:
        error_code = "tts_task_timeout"
    except Exception:
        error_code = "tts_internal_failed"
    capture = client.take_capture() if isinstance(client, CapturedTTSClient) else None
    submission = None
    try:
        async with factory() as session:
            async with session.begin():
                rows = await _fenced_rows(session, claim)
                call = await session.get(ExternalCall, call_id, with_for_update=True)
                if call is not None:
                    call.status = "FAILED" if error_code or rows is None else "SUCCEEDED"
                    call.error_code = error_code or (
                        "tts_generation_stale" if rows is None else None
                    )
                    call.completed_at = datetime.now(UTC)
                    call.audio_bytes, call.audio_duration_ms = byte_count, duration_ms
                if rows is None:
                    return True
                # The successful claim check is the acceptance proof. Build its
                # scalar snapshot before retiring the lease, never recheck the
                # now-completed task as though it were still an active claim.
                if call is not None and capture is not None:
                    submission = prepare_capture(capture_writer, call, capture)
                rule, generation, asset, task = rows
                task.lease_until = None
                task.updated_at = datetime.now(UTC)
                if error_code:
                    terminal = task.attempts >= task.max_attempts
                    task.status, task.error_code = ("FAILED" if terminal else "PENDING"), error_code
                    task.available_at = datetime.now(UTC)
                    asset.status, asset.error_code = (
                        ("FAILED" if terminal else "PENDING"),
                        error_code,
                    )
                else:
                    task.status, task.error_code = "SUCCEEDED", None
                    asset.status, asset.error_code = "READY", None
                    asset.storage_path = str(output_path.relative_to(storage_root))
                    asset.duration_ms, asset.byte_count, asset.checksum = (
                        duration_ms,
                        byte_count,
                        checksum,
                    )
                await aggregate(session, rule, generation)
        if submission is not None and capture_writer is not None:
            capture_writer.submit(submission)
        return True
    finally:
        if capture is not None:
            capture.close()
        if submission is not None:
            submission.close()


__all__ = ["process_one_host_tts", "audio_duration_ms"]

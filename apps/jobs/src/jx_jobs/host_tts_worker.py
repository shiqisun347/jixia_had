"""HOST_TTS task handler with bounded retries and atomic file publication."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

import av
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jx_core.data_capture.provider import PROVIDER_CAPTURE_VERSION, ProviderCallCapture
from jx_core.data_capture.provider_persistence import persist_provider_capture
from jx_core.models import ExternalCall

from .task_queue import claim_next, complete, fail
from .tts import TTSProviderError


class TTSClient(Protocol):
    async def synthesize_to_file(
        self, *, text: str, voice: str, rate: float, output_path: Path
    ) -> None: ...


@runtime_checkable
class CapturedTTSClient(Protocol):
    def take_capture(self) -> ProviderCallCapture | None: ...


def audio_duration_ms(path: Path) -> int:
    """Read the published container duration; never trust browser playback time."""
    with av.open(str(path)) as container:
        if container.duration is not None:
            duration = int(round(container.duration * 1000 / av.time_base))
        else:
            durations: list[int] = []
            for stream in container.streams:
                stream_duration = stream.duration
                time_base = stream.time_base
                if stream_duration is not None and time_base is not None:
                    durations.append(int(round(float(stream_duration * time_base) * 1000)))
            duration = max(durations, default=0)
    if duration <= 0:
        raise TTSProviderError("tts_audio_duration_invalid")
    return duration


async def process_one_host_tts(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    client: TTSClient,
    storage_root: Path,
) -> bool:
    async with session_factory() as session:
        claim = await claim_next(session, task_type="HOST_TTS")
    if claim is None:
        return False

    try:
        asset_id = UUID(str(claim.payload.get("asset_id", "")))
    except ValueError:
        async with session_factory() as session:
            await fail(session, task_id=claim.task_id, error_code="tts_asset_id_invalid")
        return True
    call_id: UUID | None = None
    try:
        async with session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT a.text, a.rule_id, v.provider_voice, v.rate "
                            "FROM host_audio_assets a "
                            "JOIN voice_profiles v ON v.id = a.voice_profile_id "
                            "WHERE a.id = :asset_id"
                        ),
                        {"asset_id": asset_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise TTSProviderError("tts_asset_not_found")
        call_id = uuid4()
        started_at = datetime.now(UTC)
        async with session_factory() as session:
            async with session.begin():
                session.add(
                    ExternalCall(
                        id=call_id,
                        call_kind="TTS",
                        provider="BAILIAN",
                        operation="duplex.server_commit",
                        voice=str(row["provider_voice"]),
                        attempt_no=claim.attempt_no,
                        status="STARTED",
                        capture_version=PROVIDER_CAPTURE_VERSION,
                        captured_at=started_at,
                        source_kind="HOST_AUDIO",
                        source_resource_id=str(asset_id),
                        logical_call_id=claim.task_id,
                        started_at=started_at,
                    )
                )
        output_path = storage_root / "rules" / str(row["rule_id"]) / f"{asset_id}.ogg"
        await client.synthesize_to_file(
            text=str(row["text"]),
            voice=str(row["provider_voice"]),
            rate=float(row["rate"]),
            output_path=output_path,
        )
        duration_ms = audio_duration_ms(output_path)
        async with session_factory() as session:
            async with session.begin():
                call = await session.get(ExternalCall, call_id, with_for_update=True)
                if call is not None:
                    capture = (
                        client.take_capture() if isinstance(client, CapturedTTSClient) else None
                    )
                    if capture is not None:
                        await persist_provider_capture(session, call, capture)
                    call.status = "SUCCEEDED"
                    call.audio_bytes = output_path.stat().st_size
                    call.audio_duration_ms = duration_ms
                    call.completed_latency_ms = max(
                        0, int((datetime.now(UTC) - call.started_at).total_seconds() * 1000)
                    )
                    call.completed_at = datetime.now(UTC)
                await session.execute(
                    text(
                        "UPDATE host_audio_assets SET status = 'READY', storage_path = :path, "
                        "duration_ms = :duration_ms, error_code = NULL, updated_at = now() "
                        "WHERE id = :asset_id"
                    ),
                    {
                        "path": str(output_path.relative_to(storage_root)),
                        "duration_ms": duration_ms,
                        "asset_id": asset_id,
                    },
                )
            await complete(session, task_id=claim.task_id)
    except TTSProviderError as error:
        async with session_factory() as session:
            if call_id is not None:
                async with session.begin():
                    call = await session.get(ExternalCall, call_id, with_for_update=True)
                    if call is not None:
                        capture = (
                            client.take_capture() if isinstance(client, CapturedTTSClient) else None
                        )
                        if capture is not None:
                            await persist_provider_capture(session, call, capture)
                        call.status = "FAILED"
                        call.error_code = error.code
                        call.completed_at = datetime.now(UTC)
            terminal = await fail(session, task_id=claim.task_id, error_code=error.code)
            if terminal:
                async with session.begin():
                    await session.execute(
                        text(
                            "UPDATE host_audio_assets SET status = 'FAILED', error_code = :code, "
                            "updated_at = now() WHERE id = :asset_id"
                        ),
                        {"code": error.code, "asset_id": asset_id},
                    )
                    await session.execute(
                        text(
                            "UPDATE rules SET status = 'GENERATING_AUDIO_FAILED', "
                            "updated_at = now() WHERE id = "
                            "(SELECT rule_id FROM host_audio_assets WHERE id = :asset_id)"
                        ),
                        {"asset_id": asset_id},
                    )
    except Exception:
        async with session_factory() as session:
            await fail(session, task_id=claim.task_id, error_code="tts_internal_failed")
    return True


__all__ = ["process_one_host_tts"]

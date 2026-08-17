"""HOST_TTS task handler with bounded retries and atomic file publication."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .task_queue import claim_next, complete, fail
from .tts import TTSProviderError


class TTSClient(Protocol):
    async def synthesize_to_file(
        self, *, text: str, voice: str, rate: float, output_path: Path
    ) -> None: ...


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
        output_path = storage_root / "rules" / str(row["rule_id"]) / f"{asset_id}.ogg"
        await client.synthesize_to_file(
            text=str(row["text"]),
            voice=str(row["provider_voice"]),
            rate=float(row["rate"]),
            output_path=output_path,
        )
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE host_audio_assets SET status = 'READY', storage_path = :path, "
                        "error_code = NULL, updated_at = now() WHERE id = :asset_id"
                    ),
                    {"path": str(output_path.relative_to(storage_root)), "asset_id": asset_id},
                )
            await complete(session, task_id=claim.task_id)
    except TTSProviderError as error:
        async with session_factory() as session:
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

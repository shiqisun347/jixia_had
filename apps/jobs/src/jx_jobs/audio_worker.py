"""Post-match FFmpeg replay generation and expired-file cleanup."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .task_queue import claim_next, complete, fail


class AudioProcessingError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AudioSource:
    path: Path
    codec: str


def build_ffmpeg_command(sources: list[AudioSource], output_path: Path) -> list[str]:
    if not sources:
        raise AudioProcessingError("audio_sources_missing")
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    filters: list[str] = []
    labels: list[str] = []
    for index, source in enumerate(sources):
        if source.codec == "pcm_s16le_16000_mono":
            command.extend(["-f", "s16le", "-ar", "16000", "-ac", "1"])
        command.extend(["-i", str(source.path)])
        label = f"a{index}"
        labels.append(f"[{label}]")
        filters.append(
            f"[{index}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono,"
            f"asetpts=N/SR/TB[{label}]"
        )
    filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[out]")
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-c:a",
            "libopus",
            "-b:a",
            "32k",
            "-vbr",
            "on",
            str(output_path),
        ]
    )
    return command


async def _run_ffmpeg(command: list[str], timeout_seconds: float = 900) -> None:
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except FileNotFoundError as error:
        raise AudioProcessingError("ffmpeg_unavailable") from error
    except TimeoutError as error:
        if process is not None:
            process.kill()
            await process.wait()
        raise AudioProcessingError("audio_processing_timeout") from error
    except OSError as error:
        raise AudioProcessingError(f"audio_ffmpeg_oserror_{error.errno or errno.EIO}") from error
    if process.returncode != 0:
        # Provider/process details stay out of the task error and public API.
        del stderr
        raise AudioProcessingError(f"audio_processing_failed_{process.returncode}")


def _resolve_host_path(path_value: str, host_root: Path) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else host_root / path


def resolve_cleanup_path(path_value: str, storage_roots: list[Path]) -> Path:
    path = Path(path_value).resolve()
    roots = [root.resolve() for root in storage_roots]
    if not any(path.is_relative_to(root) for root in roots):
        raise AudioProcessingError("file_cleanup_path_invalid")
    return path


async def _load_sources(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    match_id: UUID,
    host_storage_root: Path,
) -> list[AudioSource]:
    async with session_factory() as session:
        match_row = (
            (
                await session.execute(
                    text("SELECT runtime_snapshot FROM matches WHERE id = :match_id"),
                    {"match_id": match_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        speech_rows = list(
            (
                await session.execute(
                    text(
                        "SELECT action_key, audio_storage_path, speaker_kind, created_at "
                        "FROM speeches WHERE match_id = :match_id AND status = 'FINALIZED' "
                        "AND audio_storage_path IS NOT NULL ORDER BY created_at"
                    ),
                    {"match_id": match_id},
                )
            ).mappings()
        )
    if match_row is None:
        raise AudioProcessingError("match_not_found")
    snapshot_value = match_row["runtime_snapshot"]
    snapshot = cast(dict[str, Any], snapshot_value) if isinstance(snapshot_value, dict) else {}
    actions_value = snapshot.get("actions", [])
    actions = cast(list[Any], actions_value) if isinstance(actions_value, list) else []
    speeches_by_action: dict[str, list[Any]] = {}
    for row in speech_rows:
        speeches_by_action.setdefault(str(row["action_key"]), []).append(row)
    sources: list[AudioSource] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_map = cast(dict[str, Any], action)
        host_path = action_map.get("host_audio_path")
        if host_path:
            resolved = _resolve_host_path(str(host_path), host_storage_root)
            if resolved.is_file():
                sources.append(AudioSource(resolved, "ogg_opus"))
        action_key = f"{action_map.get('stage_position')}:{action_map.get('action_position')}"
        for speech in speeches_by_action.get(action_key, []):
            path = Path(str(speech["audio_storage_path"]))
            if path.is_file():
                codec = (
                    "pcm_s16le_16000_mono" if str(speech["speaker_kind"]) == "HUMAN" else "ogg_opus"
                )
                sources.append(AudioSource(path, codec))
    return sources


async def process_one_postmatch_audio(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    storage_root: Path,
    host_storage_root: Path,
) -> bool:
    async with session_factory() as session:
        active_count = await session.scalar(
            text(
                "SELECT count(*) FROM matches WHERE status IN "
                "('START_COUNTDOWN', 'RUNNING', 'PAUSED', 'SYSTEM_RECOVERY', 'ERROR')"
            )
        )
    if int(active_count or 0) >= 4:
        return False
    async with session_factory() as session:
        claim = await claim_next(session, task_type="POSTMATCH_AUDIO", lease_seconds=900)
    if claim is None:
        return False
    try:
        match_id = UUID(str(claim.payload.get("match_id", "")))
    except ValueError:
        async with session_factory() as session:
            await fail(session, task_id=claim.task_id, error_code="match_id_invalid")
        return True

    output_dir = storage_root / "matches" / str(match_id) / "replay"
    output_path = output_dir / "match.opus"
    temporary = output_dir / f"match.attempt-{claim.attempt_no}.opus"
    try:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise AudioProcessingError(f"audio_output_dir_{error.errno or 'failed'}") from error
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO match_files "
                        "(id, match_id, file_key, file_kind, status, byte_count, "
                        "created_at, updated_at) "
                        "VALUES (gen_random_uuid(), :match_id, 'replay', 'MATCH_REPLAY', "
                        "'PROCESSING', 0, now(), now()) "
                        "ON CONFLICT (match_id, file_key) DO UPDATE SET status = 'PROCESSING', "
                        "error_code = NULL, updated_at = now()"
                    ),
                    {"match_id": match_id},
                )
        sources = await _load_sources(
            session_factory, match_id=match_id, host_storage_root=host_storage_root
        )
        await _run_ffmpeg(build_ffmpeg_command(sources, temporary))
        try:
            os.replace(temporary, output_path)
            size = output_path.stat().st_size
        except OSError as error:
            raise AudioProcessingError(f"audio_output_publish_{error.errno or 'failed'}") from error
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE match_files SET status = 'READY', storage_path = :path, "
                        "codec = 'opus', byte_count = :size, expires_at = :expires_at, "
                        "error_code = NULL, updated_at = now() "
                        "WHERE match_id = :match_id AND file_key = 'replay'"
                    ),
                    {
                        "match_id": match_id,
                        "path": str(output_path),
                        "size": size,
                        "expires_at": datetime.now(UTC) + timedelta(days=90),
                    },
                )
            await complete(session, task_id=claim.task_id)
    except AudioProcessingError as error:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
        async with session_factory() as session:
            terminal = await fail(session, task_id=claim.task_id, error_code=error.code)
            if terminal:
                async with session.begin():
                    await session.execute(
                        text(
                            "UPDATE match_files SET status = 'FAILED', error_code = :code, "
                            "updated_at = now() WHERE match_id = :match_id AND file_key = 'replay'"
                        ),
                        {"match_id": match_id, "code": error.code},
                    )
    except Exception as error:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
        error_code = f"audio_internal_{type(error).__name__.lower()}"
        async with session_factory() as session:
            terminal = await fail(session, task_id=claim.task_id, error_code=error_code)
            if terminal:
                async with session.begin():
                    await session.execute(
                        text(
                            "UPDATE match_files SET status = 'FAILED', error_code = :code, "
                            "updated_at = now() WHERE match_id = :match_id AND file_key = 'replay'"
                        ),
                        {"match_id": match_id, "code": error_code},
                    )
    return True


async def process_one_file_cleanup(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    storage_roots: list[Path],
) -> bool:
    async with session_factory() as session:
        claim = await claim_next(session, task_type="FILE_CLEANUP")
    if claim is None:
        return False
    try:
        async with session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        text(
                            "SELECT id, storage_path FROM match_files WHERE status = 'READY' "
                            "AND permanent = false AND expires_at <= now() LIMIT 500"
                        )
                    )
                ).mappings()
            )
        expired_ids: list[UUID] = []
        invalid_ids: list[UUID] = []
        for row in rows:
            path_value = row["storage_path"]
            if path_value:
                try:
                    path = resolve_cleanup_path(str(path_value), storage_roots)
                except AudioProcessingError:
                    invalid_ids.append(UUID(str(row["id"])))
                    continue
                path.unlink(missing_ok=True)
            expired_ids.append(UUID(str(row["id"])))
        async with session_factory() as session:
            async with session.begin():
                if expired_ids:
                    await session.execute(
                        text(
                            "UPDATE match_files SET status = 'EXPIRED', storage_path = NULL, "
                            "updated_at = now() WHERE id = ANY(:ids)"
                        ),
                        {"ids": expired_ids},
                    )
                if invalid_ids:
                    await session.execute(
                        text(
                            "UPDATE match_files SET status = 'FAILED', "
                            "error_code = 'file_cleanup_path_invalid', updated_at = now() "
                            "WHERE id = ANY(:ids)"
                        ),
                        {"ids": invalid_ids},
                    )
            await complete(session, task_id=claim.task_id)
    except Exception:
        async with session_factory() as session:
            await fail(session, task_id=claim.task_id, error_code="file_cleanup_failed")
    return True


__all__ = [
    "AudioProcessingError",
    "AudioSource",
    "build_ffmpeg_command",
    "process_one_file_cleanup",
    "process_one_postmatch_audio",
    "resolve_cleanup_path",
]

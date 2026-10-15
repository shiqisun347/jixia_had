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

# A malformed or historically very large match must not turn the background
# worker into an unbounded ffmpeg command.  These limits are deliberately
# shared by source loading and command construction so callers cannot bypass
# the guard by constructing a command directly.
POSTMATCH_AUDIO_MAX_ACTIONS = 512
POSTMATCH_AUDIO_MAX_SPEECH_ROWS = 512
POSTMATCH_AUDIO_MAX_SOURCES = 1_024
POSTMATCH_AUDIO_MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
POSTMATCH_AUDIO_MAX_INPUT_FILE_BYTES = 16 * 1024 * 1024
# Rule validation permits up to 90 minutes per match. Human recordings are
# raw 16 kHz/16-bit/mono PCM (32,000 bytes/sec), so that legal upper bound is
# 172,800,000 bytes before adding agent/host Opus inputs. Keep the replay
# guard above that product ceiling while retaining a finite ffmpeg budget.
POSTMATCH_AUDIO_MAX_INPUT_BYTES = 256 * 1024 * 1024
POSTMATCH_AUDIO_MAX_OUTPUT_BYTES = 64 * 1024 * 1024
POSTMATCH_AUDIO_MAX_PATH_CHARS = 4_096


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
    if len(sources) > POSTMATCH_AUDIO_MAX_SOURCES:
        raise AudioProcessingError("audio_sources_too_many")
    if len(str(output_path)) > POSTMATCH_AUDIO_MAX_PATH_CHARS:
        raise AudioProcessingError("audio_output_path_too_long")
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    filters: list[str] = []
    labels: list[str] = []
    for index, source in enumerate(sources):
        if len(str(source.path)) > POSTMATCH_AUDIO_MAX_PATH_CHARS:
            raise AudioProcessingError("audio_source_path_too_long")
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
            # Keep a malformed/very long source from filling the worker disk
            # before the post-process size check can run.
            "-fs",
            str(POSTMATCH_AUDIO_MAX_OUTPUT_BYTES),
            str(output_path),
        ]
    )
    return command


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """Kill and reap a child process without leaving a cancelled task behind."""

    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    try:
        await asyncio.wait_for(asyncio.shield(process.wait()), timeout=5)
    except TimeoutError:
        # A second kill handles a process that raced with the first signal. Do
        # not wait forever in the worker if a broken subprocess transport does
        # not report completion.
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(process.wait()), timeout=5)


async def _run_ffmpeg(command: list[str], timeout_seconds: float = 900) -> None:
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            # Diagnostics are represented by a stable return-code error. Do
            # not retain an unbounded ffmpeg stderr pipe in Jobs memory.
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except FileNotFoundError as error:
        raise AudioProcessingError("ffmpeg_unavailable") from error
    except asyncio.CancelledError:
        if process is not None:
            # Shield the reap so the cancellation cannot interrupt kill+wait;
            # preserve the original CancelledError for the task owner.
            with contextlib.suppress(BaseException):
                await asyncio.shield(_terminate_process(process))
        raise
    except TimeoutError as error:
        if process is not None:
            await _terminate_process(process)
        raise AudioProcessingError("audio_processing_timeout") from error
    except OSError as error:
        raise AudioProcessingError(f"audio_ffmpeg_oserror_{error.errno or errno.EIO}") from error
    if process.returncode != 0:
        # Provider/process details stay out of the task error and public API.
        raise AudioProcessingError(f"audio_processing_failed_{process.returncode}")


def _resolve_audio_source_path(
    path_value: str,
    storage_root: Path,
    *,
    error_code: str,
) -> Path:
    if len(path_value) > POSTMATCH_AUDIO_MAX_PATH_CHARS:
        raise AudioProcessingError("audio_source_path_too_long")
    try:
        root = storage_root.resolve()
        candidate = Path(path_value)
        if not candidate.is_absolute():
            candidate = root / candidate
        path = candidate.resolve()
    except (OSError, RuntimeError) as error:
        raise AudioProcessingError(error_code) from error
    if path == root or not path.is_relative_to(root):
        raise AudioProcessingError(error_code)
    if len(str(path)) > POSTMATCH_AUDIO_MAX_PATH_CHARS:
        raise AudioProcessingError("audio_source_path_too_long")
    return path


def _resolve_host_path(path_value: str, host_root: Path) -> Path:
    return _resolve_audio_source_path(
        path_value,
        host_root,
        error_code="audio_host_path_invalid",
    )


def resolve_cleanup_path(path_value: str, storage_roots: list[Path]) -> Path:
    if len(path_value) > POSTMATCH_AUDIO_MAX_PATH_CHARS:
        raise AudioProcessingError("file_cleanup_path_too_long")
    try:
        path = Path(path_value).resolve()
        roots = [root.resolve() for root in storage_roots]
    except (OSError, RuntimeError) as error:
        raise AudioProcessingError("file_cleanup_path_invalid") from error
    if any(path == root for root in roots) or not any(path.is_relative_to(root) for root in roots):
        raise AudioProcessingError("file_cleanup_path_invalid")
    if len(str(path)) > POSTMATCH_AUDIO_MAX_PATH_CHARS:
        raise AudioProcessingError("file_cleanup_path_too_long")
    return path


async def _load_sources(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    match_id: UUID,
    host_storage_root: Path,
    speech_storage_root: Path | None = None,
) -> list[AudioSource]:
    async with session_factory() as session:
        snapshot_meta = (
            await session.execute(
                text(
                    "SELECT octet_length(runtime_snapshot::text) AS snapshot_bytes FROM matches "
                    "WHERE id = :match_id"
                ),
                {"match_id": match_id},
            )
        ).mappings().one_or_none()
        if snapshot_meta is None:
            raise AudioProcessingError("match_not_found")
        snapshot_size = int(snapshot_meta["snapshot_bytes"] or 0)
        if snapshot_size > POSTMATCH_AUDIO_MAX_SNAPSHOT_BYTES:
            raise AudioProcessingError("audio_runtime_snapshot_too_large")
        match_row = (
            (
                await session.execute(
                    text(
                        "SELECT runtime_snapshot FROM matches "
                        "WHERE id = :match_id "
                        "AND octet_length(runtime_snapshot::text) <= :max_snapshot_bytes"
                    ),
                    {
                        "match_id": match_id,
                        "max_snapshot_bytes": POSTMATCH_AUDIO_MAX_SNAPSHOT_BYTES,
                    },
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
                        "AND origin = 'ORIGINAL' "
                        "AND audio_storage_path IS NOT NULL ORDER BY created_at, id "
                        "LIMIT :row_limit"
                    ),
                    {
                        "match_id": match_id,
                        "row_limit": POSTMATCH_AUDIO_MAX_SPEECH_ROWS + 1,
                    },
                )
            ).mappings()
        )
    if match_row is None:
        # The first scalar query established that the match exists. If the
        # row grew between statements, fail closed without transferring the
        # oversized JSONB value to Python.
        raise AudioProcessingError("audio_runtime_snapshot_too_large")
    if len(speech_rows) > POSTMATCH_AUDIO_MAX_SPEECH_ROWS:
        raise AudioProcessingError("audio_speech_rows_too_many")
    snapshot_value = match_row["runtime_snapshot"]
    snapshot = cast(dict[str, Any], snapshot_value) if isinstance(snapshot_value, dict) else {}
    actions_value = snapshot.get("actions", [])
    actions = cast(list[Any], actions_value) if isinstance(actions_value, list) else []
    if len(actions) > POSTMATCH_AUDIO_MAX_ACTIONS:
        raise AudioProcessingError("audio_actions_too_many")
    speeches_by_action: dict[str, list[Any]] = {}
    for row in speech_rows:
        speeches_by_action.setdefault(str(row["action_key"]), []).append(row)
    sources: list[AudioSource] = []
    total_input_bytes = 0

    def append_source(path: Path, codec: str) -> None:
        nonlocal total_input_bytes
        try:
            stat = path.stat()
        except OSError:
            return
        if not path.is_file():
            return
        if stat.st_size > POSTMATCH_AUDIO_MAX_INPUT_FILE_BYTES:
            raise AudioProcessingError("audio_input_file_too_large")
        total_input_bytes += stat.st_size
        if total_input_bytes > POSTMATCH_AUDIO_MAX_INPUT_BYTES:
            raise AudioProcessingError("audio_input_total_too_large")
        sources.append(AudioSource(path, codec))
        if len(sources) > POSTMATCH_AUDIO_MAX_SOURCES:
            raise AudioProcessingError("audio_sources_too_many")

    for action in actions:
        if not isinstance(action, dict):
            continue
        action_map = cast(dict[str, Any], action)
        host_path = action_map.get("host_audio_path")
        if host_path:
            resolved = _resolve_host_path(str(host_path), host_storage_root)
            append_source(resolved, "ogg_opus")
        action_key = f"{action_map.get('stage_position')}:{action_map.get('action_position')}"
        for speech in speeches_by_action.get(action_key, []):
            path = _resolve_audio_source_path(
                str(speech["audio_storage_path"]),
                speech_storage_root or host_storage_root,
                error_code="audio_speech_path_invalid",
            )
            codec = (
                "pcm_s16le_16000_mono" if str(speech["speaker_kind"]) == "HUMAN" else "ogg_opus"
            )
            append_source(path, codec)
    return sources


async def process_one_postmatch_audio(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    storage_root: Path,
    host_storage_root: Path,
    speech_storage_root: Path | None = None,
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
            session_factory,
            match_id=match_id,
            host_storage_root=host_storage_root,
            speech_storage_root=speech_storage_root,
        )
        await _run_ffmpeg(build_ffmpeg_command(sources, temporary))
        try:
            size = temporary.stat().st_size
            # ``-fs`` bounds ffmpeg's growth while this final check rejects a
            # truncated file that reached the hard limit.
            if size >= POSTMATCH_AUDIO_MAX_OUTPUT_BYTES:
                raise AudioProcessingError("audio_output_too_large")
            os.replace(temporary, output_path)
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

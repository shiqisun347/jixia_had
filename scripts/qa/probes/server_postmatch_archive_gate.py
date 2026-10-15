"""Execute the normal Jobs replay task only against an owned full-match QA DB."""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import UUID

import av
from sqlalchemy import text

from jx_jobs.audio_worker import AudioSource, _load_sources, process_one_postmatch_audio


class ArchiveGateError(RuntimeError):
    pass


def decoded_samples(source: AudioSource) -> int:
    """Count decoded 48 kHz mono samples, never trust Ogg duration metadata."""
    if source.codec == "pcm_s16le_16000_mono":
        size = source.path.stat().st_size
        if size == 0 or size % 2:
            raise ArchiveGateError("qa_archive_pcm_size_invalid")
        return size // 2 * 3
    count = 0
    resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
    with av.open(str(source.path)) as container:
        if len(container.streams.audio) != 1:
            raise ArchiveGateError("qa_archive_audio_stream_count_invalid")
        for frame in container.decode(audio=0):
            for normalized in resampler.resample(frame):
                count += normalized.samples
        for normalized in resampler.resample(None):
            count += normalized.samples
    if count <= 0:
        raise ArchiveGateError("qa_archive_decoded_audio_empty")
    return count


def verify_lengths(source_samples: int, replay_samples: int, source_count: int) -> int:
    # Opus padding and resampling rounding may differ slightly between FFmpeg
    # and PyAV. This finite tolerance is far shorter than any QA host segment.
    tolerance = max(4800, source_count * 48)
    delta = abs(replay_samples - source_samples)
    if delta > tolerance:
        raise ArchiveGateError("qa_archive_duration_mismatch")
    return delta


async def task_state(factory: Any, match_id: UUID) -> dict[str, Any]:
    async with factory() as session:
        tasks = list(
            (
                await session.execute(
                    text(
                        "SELECT status, attempts, max_attempts, error_code, lease_until "
                        "FROM background_tasks WHERE task_type = 'POSTMATCH_AUDIO' "
                        "AND payload->>'match_id' = :match_id ORDER BY created_at"
                    ),
                    {"match_id": str(match_id)},
                )
            ).mappings()
        )
        file = (
            (
                await session.execute(
                    text(
                        "SELECT status, byte_count, codec, error_code, storage_path "
                        "FROM match_files WHERE match_id = :match_id AND file_key = 'replay'"
                    ),
                    {"match_id": match_id},
                )
            )
            .mappings()
            .one_or_none()
        )
    return {
        "task_count": len(tasks),
        "tasks": [
            {
                "status": task["status"],
                "attempts": task["attempts"],
                "max_attempts": task["max_attempts"],
                "error_code": task["error_code"],
                "lease_released": task["lease_until"] is None,
            }
            for task in tasks
        ],
        "file_status": file["status"] if file else None,
        "file_byte_count": file["byte_count"] if file else None,
        "file_codec": file["codec"] if file else None,
        "file_error_code": file["error_code"] if file else None,
        "storage_path": file["storage_path"] if file else None,
    }


async def verify_owned_archive(
    factory: Any, settings: Any, match_id: UUID, *, owned_root: Path
) -> dict[str, Any]:
    receipt: dict[str, Any] = {"status": "failed", "worker_calls": 0}
    started = monotonic()
    root = owned_root.resolve()
    host_root = Path(settings.host_audio_storage_dir).resolve()
    speech_root = Path(settings.agent_audio_storage_dir).resolve()
    output_root = Path(settings.match_audio_storage_dir).resolve()
    try:
        if not root.name.startswith("jx-full-match-qa-") or not all(
            path != root and path.is_relative_to(root)
            for path in (host_root, speech_root, output_root)
        ):
            raise ArchiveGateError("qa_archive_storage_not_owned")
        async with factory() as session:
            identity = (
                (
                    await session.execute(
                        text(
                            "SELECT current_database() AS name, host(inet_server_addr()) AS address"
                        )
                    )
                )
                .mappings()
                .one()
            )
            if identity["name"] != "jx_debate_qa_agent_faults" or identity["address"] not in {
                "127.0.0.1",
                "::1",
            }:
                raise ArchiveGateError("qa_archive_database_not_owned")
            match = (
                (
                    await session.execute(
                        text("SELECT status, runtime_snapshot FROM matches WHERE id = :match_id"),
                        {"match_id": match_id},
                    )
                )
                .mappings()
                .one()
            )
            if match["status"] != "FINISHED":
                raise ArchiveGateError("qa_archive_match_not_finished")
            tasks = list(
                (
                    await session.execute(
                        text(
                            "SELECT payload->>'match_id' AS match_id, status, attempts, "
                            "max_attempts FROM background_tasks WHERE task_type = 'POSTMATCH_AUDIO'"
                        )
                    )
                ).mappings()
            )
            if (
                len(tasks) != 1
                or tasks[0]["match_id"] != str(match_id)
                or tasks[0]["status"] != "PENDING"
                or tasks[0]["attempts"] != 0
                or tasks[0]["max_attempts"] != 2
            ):
                raise ArchiveGateError("qa_archive_task_scope_invalid")
            speech_count = await session.scalar(
                text(
                    "SELECT count(*) FROM speeches WHERE match_id = :match_id "
                    "AND status = 'FINALIZED' AND audio_storage_path IS NOT NULL"
                ),
                {"match_id": match_id},
            )
        actions = match["runtime_snapshot"].get("actions", [])
        host_count = sum(bool(action.get("host_audio_path")) for action in actions)
        sources = await _load_sources(
            factory,
            match_id=match_id,
            host_storage_root=host_root,
            speech_storage_root=speech_root,
        )
        receipt.update(
            host_source_count=host_count,
            speech_source_count=speech_count,
            loaded_source_count=len(sources),
            source_codecs=dict(Counter(source.codec for source in sources)),
        )
        if len(sources) != host_count + int(speech_count or 0) or not sources:
            raise ArchiveGateError("qa_archive_sources_incomplete")
        # Run the genuine bounded claim/FFmpeg/publish/complete path; retry only
        # when that worker itself leaves the existing task PENDING.
        for _ in range(2):
            worked = await process_one_postmatch_audio(
                factory,
                storage_root=output_root,
                host_storage_root=host_root,
                speech_storage_root=speech_root,
            )
            receipt["worker_calls"] += 1
            state = await task_state(factory, match_id)
            receipt.update({key: value for key, value in state.items() if key != "storage_path"})
            if not worked or state["tasks"][0]["status"] != "PENDING":
                break
        if not worked or state["tasks"][0]["status"] != "SUCCEEDED":
            raise ArchiveGateError("qa_archive_worker_not_succeeded")
        output = output_root / "matches" / str(match_id) / "replay" / "match.opus"
        if (
            state["file_status"] != "READY"
            or state["file_codec"] != "opus"
            or not state["tasks"][0]["lease_released"]
            or state["storage_path"] != str(output)
            or output.stat().st_size != state["file_byte_count"]
            or output.stat().st_size <= 0
        ):
            raise ArchiveGateError("qa_archive_published_file_invalid")
        source_samples = sum(
            await asyncio.to_thread(lambda: [decoded_samples(source) for source in sources])
        )
        replay_samples = await asyncio.to_thread(decoded_samples, AudioSource(output, "ogg_opus"))
        delta = verify_lengths(source_samples, replay_samples, len(sources))
        receipt.update(
            status="passed",
            source_decoded_samples_48000=source_samples,
            replay_decoded_samples_48000=replay_samples,
            source_decoded_duration_ms=source_samples // 48,
            replay_decoded_duration_ms=replay_samples // 48,
            duration_delta_samples=delta,
            decoded_audio_streams=1,
            archive_exists=True,
        )
    except Exception as error:
        receipt["error_code"] = (
            str(error) if isinstance(error, ArchiveGateError) else "qa_archive_error"
        )
        receipt["exception_class"] = type(error).__name__
    finally:
        receipt["elapsed_ms"] = round((monotonic() - started) * 1000)
    return receipt

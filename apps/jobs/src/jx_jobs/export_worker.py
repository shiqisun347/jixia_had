"""Build bounded, self-describing match research exports."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jx_core.data_capture.content import load_content_blob
from jx_core.data_capture.export_snapshot import (
    CAPTURE_SNAPSHOT_VERSION,
    EXPORT_CALL_FIELDS,
    PROVIDER_EXPORT_MAX_CALL_ROWS,
    PROVIDER_EXPORT_MAX_ITEMS,
)
from jx_core.data_capture.provider import PROVIDER_CAPTURE_VERSION
from jx_core.data_capture.provider_projection import CAPTURE_RAW_FIELDS, capture_projections
from jx_core.models import ExternalCall

from .checksums import sha256_file
from .task_queue import claim_next, complete, fail


class ExportError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# Audio is collected into the per-match ``files`` mapping before it is written
# to the archive. Keep that in-memory collection bounded even when a match has
# many recorded speeches. The per-file limit matches the TTS provider output
# budget; the total leaves enough room for a normal 4v4 match while preventing
# an unexpectedly large export from exhausting the worker process.
MATCH_EXPORT_MAX_AUDIO_FILE_BYTES = 16 * 1024 * 1024
MATCH_EXPORT_MAX_AUDIO_BYTES = 64 * 1024 * 1024
# A normal 4v4 match produces a few hundred rows at most.  Keep the
# research-export worker from materialising an accidentally unbounded match
# (for example after a reconnect loop) into several Python lists.  Queries
# request one sentinel row so an over-limit export fails closed before any
# archive bytes are written.
MATCH_EXPORT_MAX_SPEECH_ROWS = 4_096
MATCH_EXPORT_MAX_EVENT_ROWS = 8_192
MATCH_EXPORT_MAX_CALL_ROWS = PROVIDER_EXPORT_MAX_CALL_ROWS
MATCH_EXPORT_MAX_ITEMS = PROVIDER_EXPORT_MAX_ITEMS
MATCH_EXPORT_MAX_PROVIDER_PAYLOAD_BYTES = 128 * 1024 * 1024


def _bounded_rows(rows: Any, *, max_rows: int) -> list[dict[str, Any]]:
    collected = list(rows)
    if len(collected) > max_rows:
        raise ExportError("export_match_rows_too_large")
    return collected


def _read_audio_file(
    path: Path, *, max_bytes: int, overflow_code: str = "export_audio_file_too_large"
) -> bytes:
    """Read at most ``max_bytes`` + 1 bytes and reject oversized files.

    Reading one extra byte makes the size check safe when a file grows after
    its initial ``stat`` call. ``ExportError`` keeps the failure code stable
    for task records and callers without exposing local paths.
    """

    try:
        with path.open("rb") as stream:
            payload = stream.read(max_bytes + 1)
    except OSError as error:
        raise ExportError("export_audio_unreadable") from error
    if len(payload) > max_bytes:
        raise ExportError(overflow_code)
    return payload


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _safe_text(value: Any) -> str:
    text_value = "" if value is None else str(value)
    if text_value[:1] in {"=", "+", "-", "@"}:
        return "'" + text_value
    return text_value


async def _match_package(
    session: AsyncSession,
    *,
    match_id: UUID,
    cutoff_sequence: int,
    cutoff_context_version: int,
    cutoff_at: datetime,
    include_audio: bool,
    audio_roots: list[Path],
    export_item_id: UUID | None = None,
    capture_snapshot_version: int | None = None,
) -> dict[str, bytes]:
    match = (
        (
            await session.execute(
                text(
                    "SELECT m.id, m.status, m.sequence, m.context_version, m.created_at, "
                    "r.label, r.topic_snapshot FROM matches m JOIN rooms r ON r.id=m.room_id "
                    "WHERE m.id=:id"
                ),
                {"id": match_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if match is None:
        raise ExportError("match_not_found")
    speeches = _bounded_rows(
        (
            await session.execute(
                text(
                    "SELECT id, action_key, speaker_kind, user_id, agent_profile_id, side, "
                    "seat_no, status, asr_raw_final_text, display_text, finalized_at, "
                    "audio_duration_ms, audio_storage_path FROM speeches "
                    "WHERE match_id=:id AND created_at <= :cutoff ORDER BY created_at "
                    f"LIMIT {MATCH_EXPORT_MAX_SPEECH_ROWS + 1}"
                ),
                {"id": match_id, "cutoff": cutoff_at},
            )
        ).mappings(),
        max_rows=MATCH_EXPORT_MAX_SPEECH_ROWS,
    )
    events = _bounded_rows(
        (
            await session.execute(
                text(
                    "SELECT sequence, event_type, payload, created_at FROM match_events "
                    "WHERE match_id=:id AND sequence <= :sequence ORDER BY sequence "
                    f"LIMIT {MATCH_EXPORT_MAX_EVENT_ROWS + 1}"
                ),
                {"id": match_id, "sequence": cutoff_sequence},
            )
        ).mappings(),
        max_rows=MATCH_EXPORT_MAX_EVENT_ROWS,
    )
    fields = ", ".join((*EXPORT_CALL_FIELDS, *CAPTURE_RAW_FIELDS))
    if capture_snapshot_version is not None:
        if capture_snapshot_version != CAPTURE_SNAPSHOT_VERSION or export_item_id is None:
            raise ExportError("export_capture_snapshot_unsupported")
        calls = _bounded_rows(
            (
                await session.execute(
                    text(
                        f"SELECT external_call_id AS id, {fields}, capture_revision "
                        "FROM export_provider_capture_snapshots WHERE export_item_id=:item_id "
                        "ORDER BY started_at, external_call_id "
                        f"LIMIT {MATCH_EXPORT_MAX_CALL_ROWS + 1}"
                    ),
                    {"item_id": export_item_id},
                )
            ).mappings(),
            max_rows=MATCH_EXPORT_MAX_CALL_ROWS,
        )
    else:
        # Historical tasks retain their original live cutoff reader, with the
        # shared projection preventing an ASYNC gap from exposing business blobs.
        calls = _bounded_rows(
            (
                await session.execute(
                    text(
                        f"SELECT id, {fields}, capture_storage_mode "
                        "FROM external_calls WHERE match_id=:id AND started_at <= :cutoff "
                        "AND (capture_version IS NOT NULL OR capture_storage_mode='ASYNC_V1') "
                        "AND source_kind='MATCH' ORDER BY started_at, id "
                        f"LIMIT {MATCH_EXPORT_MAX_CALL_ROWS + 1}"
                    ),
                    {"id": match_id, "cutoff": cutoff_at},
                )
            ).mappings(),
            max_rows=MATCH_EXPORT_MAX_CALL_ROWS,
        )
        projected = await capture_projections(session, [ExternalCall(**dict(row)) for row in calls])
        calls = [
            {
                **{k: v for k, v in row.items() if k != "capture_storage_mode"},
                **projected[row["id"]],
            }
            for row in calls
        ]
    provider_payload_bytes = 0
    for row in calls:
        for side in ("request", "response"):
            raw_bytes = row.get(f"{side}_original_bytes") or row.get(f"{side}_stored_bytes")
            try:
                provider_payload_bytes += max(0, int(raw_bytes or 0))
            except (TypeError, ValueError):
                continue
    if provider_payload_bytes > MATCH_EXPORT_MAX_PROVIDER_PAYLOAD_BYTES:
        raise ExportError("export_provider_payload_too_large")
    transcript_rows = [
        {
            "speech_id": str(row["id"]),
            "action_key": row["action_key"],
            "speaker_kind": row["speaker_kind"],
            "user_id": str(row["user_id"]) if row["user_id"] else None,
            "agent_profile_id": str(row["agent_profile_id"]) if row["agent_profile_id"] else None,
            "side": row["side"],
            "seat_no": row["seat_no"],
            "status": row["status"],
            "asr_raw_final_text": row["asr_raw_final_text"],
            "display_text": row["display_text"],
            "finalized_at": row["finalized_at"],
            "audio_duration_ms": row["audio_duration_ms"],
        }
        for row in speeches
    ]
    transcript_md = "\n\n".join(
        "### {action_key} · {speaker_kind}\n\n{text}".format(
            action_key=row["action_key"],
            speaker_kind=row["speaker_kind"],
            text=row["display_text"] or row["asr_raw_final_text"] or "（无文字）",
        )
        for row in transcript_rows
    )
    speech_buffer = io.StringIO()
    speech_writer = csv.DictWriter(
        speech_buffer,
        fieldnames=[
            "speech_id",
            "action_key",
            "speaker_kind",
            "user_id",
            "agent_profile_id",
            "side",
            "seat_no",
            "status",
            "asr_raw_final_text",
            "display_text",
            "finalized_at",
            "audio_duration_ms",
        ],
    )
    speech_writer.writeheader()
    speech_writer.writerows(
        {key: _safe_text(value) for key, value in row.items()} for row in transcript_rows
    )
    call_rows: list[dict[str, Any]] = []
    for row in calls:
        item = dict(row)
        content_errors: list[str] = []
        for side in ("request", "response"):
            blob_id = item.pop(f"{side}_blob_id")
            payload: Any = None
            if blob_id is not None:
                try:
                    payload = await load_content_blob(session, UUID(str(blob_id)))
                except (LookupError, ValueError, OSError):
                    content_errors.append(side)
            item[side] = payload
        item["content_errors"] = content_errors
        call_rows.append(item)
    event_rows = [dict(row) for row in events]
    manifest = {
        "export_schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "match_id": str(match_id),
        "status_at_cutoff": match["status"],
        "cutoff_sequence": cutoff_sequence,
        "cutoff_context_version": cutoff_context_version,
        "incomplete": match["status"] not in {"FINISHED", "TERMINATED"},
        "scope": "single_match",
        "provider_call_capture_schema_version": PROVIDER_CAPTURE_VERSION,
        "provider_capture_snapshot_version": capture_snapshot_version,
    }
    files: dict[str, bytes] = {
        "manifest.json": (_json(manifest) + "\n").encode(),
        "transcript.md": transcript_md.encode(),
        "speeches.csv": ("\ufeff" + speech_buffer.getvalue()).encode(),
        "agent-calls.jsonl": ("\n".join(_json(row) for row in call_rows) + "\n").encode(),
        "events.jsonl": ("\n".join(_json(row) for row in event_rows) + "\n").encode(),
        "judge.json": b"{}\n",
    }
    if include_audio:
        audio_bytes = 0
        for row in speeches:
            path_value = row["audio_storage_path"]
            if not path_value:
                continue
            path = Path(str(path_value)).resolve()
            if not any(path.is_relative_to(root.resolve()) for root in audio_roots):
                continue
            if not path.is_file():
                continue
            try:
                file_size = path.stat().st_size
            except OSError as error:
                raise ExportError("export_audio_unreadable") from error
            if file_size > MATCH_EXPORT_MAX_AUDIO_FILE_BYTES:
                raise ExportError("export_audio_file_too_large")
            remaining_bytes = MATCH_EXPORT_MAX_AUDIO_BYTES - audio_bytes
            if file_size > remaining_bytes:
                raise ExportError("export_audio_total_too_large")
            read_limit = min(MATCH_EXPORT_MAX_AUDIO_FILE_BYTES, remaining_bytes)
            payload = _read_audio_file(
                path,
                max_bytes=read_limit,
                overflow_code=(
                    "export_audio_total_too_large"
                    if read_limit < MATCH_EXPORT_MAX_AUDIO_FILE_BYTES
                    else "export_audio_file_too_large"
                ),
            )
            files[f"audio/{path.name}"] = payload
            audio_bytes += len(payload)
    return files


async def process_one_match_export(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    storage_root: Path,
    audio_roots: list[Path],
) -> bool:
    async with session_factory() as session:
        claim = await claim_next(session, task_type="MATCH_EXPORT", lease_seconds=900)
    if claim is None:
        return False
    export_id = UUID(str(claim.payload.get("export_id", "")))
    temporary = storage_root / f"{export_id}.attempt-{claim.attempt_no}.zip"
    output = storage_root / f"{export_id}.zip"
    try:
        storage_root.mkdir(parents=True, exist_ok=True)
        async with session_factory() as session:
            await session.execute(
                text(
                    "UPDATE match_exports SET status='RUNNING', "
                    "started_at=COALESCE(started_at, now()), updated_at=now() WHERE id=:id"
                ),
                {"id": export_id},
            )
            await session.commit()
            rows = _bounded_rows(
                (
                    await session.execute(
                        text(
                            "SELECT * FROM match_export_items WHERE export_id=:id ORDER BY id "
                            f"LIMIT {MATCH_EXPORT_MAX_ITEMS + 1}"
                        ),
                        {"id": export_id},
                    )
                ).mappings(),
                max_rows=MATCH_EXPORT_MAX_ITEMS,
            )
            export_row = (
                (
                    await session.execute(
                        text("SELECT include_audio, created_at FROM match_exports WHERE id=:id"),
                        {"id": export_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if export_row is None:
            raise ExportError("export_not_found")
        succeeded = 0
        failed = 0
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in rows:
                try:
                    async with session_factory() as session:
                        files = await _match_package(
                            session,
                            match_id=UUID(str(item["match_id"])),
                            cutoff_sequence=int(item["cutoff_sequence"]),
                            cutoff_context_version=int(item["cutoff_context_version"]),
                            cutoff_at=export_row["created_at"],
                            include_audio=bool(export_row["include_audio"]),
                            audio_roots=audio_roots,
                            export_item_id=UUID(str(item["id"])),
                            capture_snapshot_version=item["capture_snapshot_version"],
                        )
                    prefix = f"matches/{item['match_id']}/"
                    for name, data in files.items():
                        archive.writestr(prefix + name, data)
                    succeeded += 1
                    async with session_factory() as session:
                        await session.execute(
                            text(
                                "UPDATE match_export_items SET status='SUCCEEDED', "
                                "completed_at=now() WHERE id=:id"
                            ),
                            {"id": item["id"]},
                        )
                        await session.commit()
                except Exception as error:
                    failed += 1
                    async with session_factory() as session:
                        await session.execute(
                            text(
                                "UPDATE match_export_items SET status='FAILED', "
                                "error_code=:error WHERE id=:id"
                            ),
                            {
                                "id": item["id"],
                                "error": getattr(error, "code", "export_item_failed"),
                            },
                        )
                        await session.commit()
        os.replace(temporary, output)
        digest = await asyncio.to_thread(sha256_file, output)
        status = "PARTIAL" if failed else "SUCCEEDED"
        async with session_factory() as session:
            await session.execute(
                text(
                    "UPDATE match_exports SET status=:status, processed_items=:processed, "
                    "storage_path=:path, byte_count=:bytes, sha256=:sha256, "
                    "expires_at=now()+interval '7 days', completed_at=now(), "
                    "updated_at=now() WHERE id=:id"
                ),
                {
                    "id": export_id,
                    "status": status,
                    "processed": succeeded + failed,
                    "path": str(output),
                    "bytes": output.stat().st_size,
                    "sha256": digest,
                },
            )
            await session.commit()
            await complete(session, task_id=claim.task_id)
    except Exception as error:
        temporary.unlink(missing_ok=True)
        async with session_factory() as session:
            terminal = await fail(
                session, task_id=claim.task_id, error_code=getattr(error, "code", "export_failed")
            )
            if terminal:
                await session.execute(
                    text(
                        "UPDATE match_exports SET status='FAILED', error_code=:error, "
                        "updated_at=now() WHERE id=:id"
                    ),
                    {"id": export_id, "error": getattr(error, "code", "export_failed")},
                )
                await session.commit()
    return True

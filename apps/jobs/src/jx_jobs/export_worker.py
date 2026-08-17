"""Build bounded, self-describing match research exports."""

from __future__ import annotations

import csv
import hashlib
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

from .task_queue import claim_next, complete, fail


class ExportError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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
    speeches = list(
        (
            await session.execute(
                text(
                    "SELECT id, action_key, speaker_kind, user_id, agent_profile_id, side, "
                    "seat_no, status, asr_raw_final_text, display_text, finalized_at, "
                    "audio_duration_ms, audio_storage_path FROM speeches "
                    "WHERE match_id=:id AND created_at <= :cutoff ORDER BY created_at"
                ),
                {"id": match_id, "cutoff": cutoff_at},
            )
        ).mappings()
    )
    events = list(
        (
            await session.execute(
                text(
                    "SELECT sequence, event_type, payload, created_at FROM match_events "
                    "WHERE match_id=:id AND sequence <= :sequence ORDER BY sequence"
                ),
                {"id": match_id, "sequence": cutoff_sequence},
            )
        ).mappings()
    )
    calls = list(
        (
            await session.execute(
                text(
                    "SELECT id, call_kind, provider, operation, model, voice, attempt_no, "
                    "status, speech_id, generation_id, decision_round_id, context_version, "
                    "started_at, first_result_latency_ms, completed_latency_ms, error_code "
                    "FROM external_calls WHERE match_id=:id AND started_at <= :cutoff "
                    "ORDER BY started_at"
                ),
                {"id": match_id, "cutoff": cutoff_at},
            )
        ).mappings()
    )
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
    call_rows = [dict(row) for row in calls]
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
        for row in speeches:
            path_value = row["audio_storage_path"]
            if not path_value:
                continue
            path = Path(str(path_value)).resolve()
            if not any(path.is_relative_to(root.resolve()) for root in audio_roots):
                continue
            if path.is_file():
                files[f"audio/{path.name}"] = path.read_bytes()
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
            rows = list(
                (
                    await session.execute(
                        text("SELECT * FROM match_export_items WHERE export_id=:id ORDER BY id"),
                        {"id": export_id},
                    )
                ).mappings()
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
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
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

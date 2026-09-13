"""Backfill authoritative host-audio durations (dry-run unless --apply)."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import av
from sqlalchemy import text

from jx_core.config import load_settings
from jx_core.database import Database


def duration_ms(path: Path) -> int:
    with av.open(str(path)) as container:
        if container.duration is None:
            raise ValueError("container duration missing")
        value = int(round(container.duration * 1000 / av.time_base))
    if value <= 0:
        raise ValueError("duration is not positive")
    return value


def resolve_path(root: Path, storage_path: str) -> Path:
    candidate = Path(storage_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if root.resolve() not in resolved.parents and resolved != root.resolve():
        raise ValueError("audio path outside configured storage root")
    return resolved


def update_snapshot(
    snapshot: Any,
    *,
    segment_durations: dict[str, int] | None = None,
    path_durations: dict[str, int] | None = None,
) -> tuple[Any, int]:
    if not isinstance(snapshot, dict):
        return snapshot, 0
    changed = 0
    host_audio = snapshot.get("host_audio")
    if isinstance(host_audio, list):
        for item in host_audio:
            if (
                isinstance(item, dict)
                and segment_durations is not None
                and item.get("segment_key") in segment_durations
            ):
                value = segment_durations[str(item["segment_key"])]
                if item.get("duration_ms") != value:
                    item["duration_ms"] = value
                    changed += 1
    actions = snapshot.get("actions")
    if isinstance(actions, list):
        for action in actions:
            if isinstance(action, dict) and action.get("host_audio_path"):
                path = str(action["host_audio_path"])
                value = (path_durations or {}).get(path)
                if value is not None and action.get("host_audio_duration_ms") != value:
                    action["host_audio_duration_ms"] = value
                    changed += 1
    return snapshot, changed


async def run(apply: bool) -> int:
    settings = load_settings()
    database = Database(settings.database_url_value)
    root = Path(settings.host_audio_storage_dir)
    durations_by_rule: dict[UUID, dict[str, int]] = {}
    durations_by_path: dict[str, int] = {}
    async with database.session_factory() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT id, rule_id, segment_key, storage_path, duration_ms "
                        "FROM host_audio_assets "
                        "WHERE status = 'READY' ORDER BY segment_key"
                    )
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            if row["duration_ms"] and int(row["duration_ms"]) > 0:
                value = int(row["duration_ms"])
                rule_id = UUID(str(row["rule_id"]))
                durations_by_rule.setdefault(rule_id, {})[str(row["segment_key"])] = value
                durations_by_path[str(row["storage_path"])] = value
                continue
            try:
                value = duration_ms(resolve_path(root, str(row["storage_path"])))
            except (OSError, ValueError, av.error.FFmpegError) as error:
                print(f"invalid asset {row['id']}: {type(error).__name__}")
                return 2
            rule_id = UUID(str(row["rule_id"]))
            durations_by_rule.setdefault(rule_id, {})[str(row["segment_key"])] = value
            durations_by_path[str(row["storage_path"])] = value
            if apply:
                await session.execute(
                    text(
                        "UPDATE host_audio_assets SET duration_ms = :duration_ms, "
                        "updated_at = now() WHERE id = :asset_id"
                    ),
                    {"duration_ms": value, "asset_id": UUID(str(row["id"]))},
                )
        room_rows = (
            (await session.execute(text("SELECT id, rule_id, rule_snapshot FROM rooms")))
            .mappings()
            .all()
        )
        match_rows = (
            (
                await session.execute(
                    text(
                        "SELECT id, runtime_snapshot FROM matches "
                        "WHERE status NOT IN ('FINISHED', 'TERMINATED')"
                    )
                )
            )
            .mappings()
            .all()
        )
        updates = 0
        for row in room_rows:
            snapshot, changed = update_snapshot(
                row["rule_snapshot"],
                segment_durations=durations_by_rule.get(UUID(str(row["rule_id"])), {}),
            )
            updates += changed
            if apply and changed:
                await session.execute(
                    text(
                        "UPDATE rooms SET rule_snapshot = CAST(:snapshot AS jsonb), "
                        "updated_at = now() "
                        "WHERE id = :id"
                    ),
                    {"snapshot": json.dumps(snapshot), "id": row["id"]},
                )
        for row in match_rows:
            snapshot, changed = update_snapshot(
                row["runtime_snapshot"], path_durations=durations_by_path
            )
            updates += changed
            if apply and changed:
                await session.execute(
                    text(
                        "UPDATE matches SET runtime_snapshot = CAST(:snapshot AS jsonb), "
                        "updated_at = now() "
                        "WHERE id = :id"
                    ),
                    {"snapshot": json.dumps(snapshot), "id": row["id"]},
                )
        if apply:
            await session.commit()
        print(
            f"assets={len(rows)} durations={len(durations_by_path)} "
            f"snapshot_fields={updates} apply={apply}"
        )
    await database.dispose()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.apply)))


if __name__ == "__main__":
    main()

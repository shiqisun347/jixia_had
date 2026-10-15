"""Dry-run legacy host audio against the expected RuleStage manifest.

--apply attaches validated legacy files without claiming their historical model;
only a valid existing approval is retained. Unreviewed batches remain PENDING.
Old unfinished task evidence is retained and replacements receive fenced identity.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import select

from jx_core.config import load_settings
from jx_core.database import Database
from jx_core.models import BackgroundTask, HostAudioAsset, Rule, RuleAudioGeneration
from jx_core.rules.audio import (
    AudioConfiguration,
    aggregate,
    audio_file,
    build_manifest,
    fingerprint,
    queue_asset,
)
from jx_jobs.host_tts_worker import audio_duration_ms


async def run(*, apply: bool) -> dict[str, object]:
    settings = load_settings()
    configuration = AudioConfiguration(
        model=settings.tts_model,
        workspace_id=settings.tts_workspace_id or "",
        websocket_url=settings.tts_ws_url,
        storage_root=settings.host_audio_storage_dir,
    )
    database = Database(settings.database_url_value)
    report: dict[str, Any] = {"apply": apply, "rules": [], "attached": 0}
    try:
        async with database.session_factory() as session:
            rule_ids = list(
                (
                    await session.scalars(
                        select(Rule.id)
                        .where(
                            Rule.current_audio_generation_id.is_(None),
                            Rule.lifecycle_status != "ARCHIVED",
                        )
                        .order_by(Rule.id)
                    )
                ).all()
            )
        for rule_id in rule_ids:
            async with database.session_factory() as session:
                async with session.begin():
                    rule = await session.get(Rule, rule_id, with_for_update=True)
                    if rule is None or rule.current_audio_generation_id is not None:
                        continue
                    manifest = await build_manifest(session, rule=rule, configuration=configuration)
                    assets = list(
                        (
                            await session.scalars(
                                select(HostAudioAsset).where(
                                    HostAudioAsset.rule_id == rule.id,
                                    HostAudioAsset.generation_id.is_(None),
                                )
                            )
                        ).all()
                    )
                    by_key = {asset.segment_key: asset for asset in assets}
                    checks: dict[str, tuple[HostAudioAsset | None, str | None, int, int, str]] = {}
                    for segment in manifest["segments"]:
                        key = segment["segment_key"]
                        inputs = segment["input"]
                        asset = by_key.get(key)
                        error: str | None = None
                        duration, size, checksum = 0, 0, ""
                        if asset is None:
                            error = "tts_assets_missing"
                        elif (
                            asset.text != inputs["text"]
                            or str(asset.voice_profile_id) != inputs["voice_profile_id"]
                        ):
                            error = "tts_legacy_input_mismatch"
                        elif asset.status == "READY":
                            try:
                                if not asset.storage_path:
                                    raise ValueError("missing path")
                                path = audio_file(
                                    settings.host_audio_storage_dir, asset.storage_path
                                )
                                size = path.stat().st_size
                                duration = await asyncio.to_thread(audio_duration_ms, path)
                                checksum = await asyncio.to_thread(
                                    lambda path=path: sha256(path.read_bytes()).hexdigest()
                                )
                                if size <= 0:
                                    error = "tts_asset_file_empty"
                            except Exception:
                                error = "tts_asset_file_invalid"
                        elif asset.status not in {"PENDING", "RUNNING"}:
                            error = asset.error_code or "tts_legacy_failed"
                        checks[key] = asset, error, duration, size, checksum
                    unexpected = sorted(set(by_key) - set(checks))
                    valid_ready = sum(
                        asset is not None and asset.status == "READY" and error is None
                        for asset, error, _, _, _ in checks.values()
                    )
                    previous_approved = rule.audio_reviewed_at
                    preserve_approval = bool(previous_approved and valid_ready == len(checks))
                    report["rules"].append(
                        {
                            "rule_id": str(rule.id),
                            "rule_name": rule.name,
                            "expected": len(checks),
                            "assets": len(assets),
                            "ready_validated": valid_ready,
                            "anomalies": sum(
                                error is not None for _, error, _, _, _ in checks.values()
                            ),
                            "unexpected_assets": unexpected,
                            "preserve_historical_approval": preserve_approval,
                            "legacy_provenance": "model_and_rate_unknown",
                        }
                    )
                    if not apply:
                        continue
                    generation = RuleAudioGeneration(
                        rule_id=rule.id,
                        config_revision=rule.config_revision,
                        manifest=manifest,
                        manifest_hash=fingerprint(manifest),
                        requested_at=datetime.now(UTC),
                        requested_by=rule.created_by,
                        is_current=True,
                        status="QUEUED",
                        review_status="PENDING",
                        total_count=len(checks),
                    )
                    session.add(generation)
                    await session.flush()
                    rule.current_audio_generation_id = generation.id
                    for segment in manifest["segments"]:
                        inputs = segment["input"]
                        asset, error, duration, size, checksum = checks[segment["segment_key"]]
                        if asset is None:
                            asset = HostAudioAsset(
                                rule_id=rule.id,
                                segment_key=segment["segment_key"],
                                text=inputs["text"],
                                text_hash=inputs["text_hash"],
                                voice_profile_id=UUID(inputs["voice_profile_id"]),
                                status="FAILED",
                                error_code=error,
                            )
                            session.add(asset)
                            await session.flush()
                        asset.generation_id = generation.id
                        asset.input_hash = segment["input_hash"]
                        asset.input_snapshot = {
                            **inputs,
                            "provenance": "legacy_unknown",
                            "actual_model": None,
                        }
                        if error:
                            asset.status, asset.error_code = "FAILED", error
                        elif asset.status == "READY":
                            asset.duration_ms, asset.byte_count, asset.checksum = (
                                duration,
                                size,
                                checksum,
                            )
                        old_tasks = list(
                            (
                                await session.scalars(
                                    select(BackgroundTask).where(
                                        BackgroundTask.task_type == "HOST_TTS",
                                        BackgroundTask.payload["asset_id"].astext == str(asset.id),
                                        BackgroundTask.status.in_(["PENDING", "RUNNING"]),
                                    )
                                )
                            ).all()
                        )
                        for task in old_tasks:
                            task.status, task.error_code, task.lease_until = (
                                "FAILED",
                                "legacy_audio_repaired",
                                None,
                            )
                        if error is None and asset.status in {"PENDING", "RUNNING"}:
                            asset.input_snapshot = inputs
                            await queue_asset(session, generation, asset)
                    if preserve_approval:
                        generation.review_status, generation.approved_at = (
                            "APPROVED",
                            previous_approved,
                        )
                    await aggregate(session, rule, generation)
                    report["attached"] += len(checks)
        return report
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit the reviewed repair")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(apply=args.apply)), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"failed": type(error).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

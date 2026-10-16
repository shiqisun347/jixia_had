"""Copy one exact rule's catalog into an owned QA database, never live match data.

Credentials remain in process memory/encrypted model columns. This module has no
CLI and never serializes the material bundle to logs or artifacts.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, text, update
from sqlalchemy.engine import make_url

from jx_core.config import Settings
from jx_core.database import Database
from jx_core.models import (
    AgentProfile,
    AgentPromptOverride,
    HostAudioAsset,
    Match,
    ModelProfile,
    Room,
    Rule,
    RuleAudioGeneration,
    RuleJudgeConfig,
    RuleStage,
    StageAction,
    StagePromptTemplate,
    User,
    VoiceProfile,
)
from jx_core.rooms.roster import CONFERENCE_ROLES

CATALOG_TABLES = (
    ModelProfile,
    VoiceProfile,
    Rule,
    RuleStage,
    StageAction,
    AgentProfile,
    StagePromptTemplate,
    AgentPromptOverride,
    RuleJudgeConfig,
    RuleAudioGeneration,
    HostAudioAsset,
)
ACTIVE_DECISION_POLICY = (
    "当你能提出有依据的反驳、澄清、例证或新的论证时，应积极争取发言，不必等待队友先行。\n"
    "仅在没有可贡献的新内容、只能重复既有发言时选择不发言；不要因对方观点复杂而默认退让。"
)


def catalog_fingerprint(tables: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    """Hash public catalog settings without including credential material."""
    excluded = {"api_key_ciphertext", "api_key_nonce", "api_key_last4"}
    return {
        name: hashlib.sha256(
            json.dumps(
                [
                    {key: value for key, value in row.items() if key not in excluded}
                    for row in sorted(rows, key=lambda item: str(item.get("id", "")))
                ],
                sort_keys=True,
                default=str,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        for name, rows in tables.items()
    }


def row_values(model: Any, row: Any) -> dict[str, Any]:
    return {column.key: getattr(row, column.key) for column in model.__table__.columns}


async def read_material(settings: Settings, *, source_match_id: UUID) -> dict[str, Any]:
    database = Database(settings.database_url_value)
    tables: dict[str, list[dict[str, Any]]] = {}
    try:
        async with database.session_factory() as session:
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            await session.execute(text("SET LOCAL statement_timeout='5s'"))
            assert await session.scalar(text("SHOW transaction_read_only")) == "on"
            room = await session.scalar(
                select(Room)
                .join(Match, Match.room_id == Room.id)
                .where(Match.id == source_match_id)
            )
            if room is None:
                raise RuntimeError("qa_exact_source_match_missing")
            rule = await session.get(Rule, room.rule_id)
            if rule is None or rule.status != "ENABLED" or rule.historical_read_only:
                raise RuntimeError("qa_exact_source_rule_not_enabled")
            stages = list(
                (
                    await session.scalars(
                        select(RuleStage)
                        .where(RuleStage.rule_id == rule.id)
                        .order_by(RuleStage.position)
                    )
                ).all()
            )
            stage_ids = [stage.id for stage in stages]
            agents = list(
                (
                    await session.scalars(
                        select(AgentProfile).where(AgentProfile.rule_id == rule.id)
                    )
                ).all()
            )
            agent_ids = [agent.id for agent in agents]
            judge = await session.get(RuleJudgeConfig, rule.id)
            model_ids = {agent.model_profile_id for agent in agents}
            if judge is not None and judge.model_profile_id is not None:
                model_ids.add(judge.model_profile_id)
            if rule.default_agent_model_profile_id:
                model_ids.add(rule.default_agent_model_profile_id)
            voice_ids = {agent.voice_profile_id for agent in agents}
            if rule.host_voice_profile_id:
                voice_ids.add(rule.host_voice_profile_id)
            generation = await session.get(RuleAudioGeneration, rule.current_audio_generation_id)
            if generation is None or generation.status != "SUCCEEDED":
                raise RuntimeError("qa_exact_host_generation_missing")
            queries = {
                ModelProfile: select(ModelProfile).where(ModelProfile.id.in_(model_ids)),
                VoiceProfile: select(VoiceProfile).where(VoiceProfile.id.in_(voice_ids)),
                StageAction: select(StageAction).where(StageAction.stage_id.in_(stage_ids)),
                StagePromptTemplate: select(StagePromptTemplate).where(
                    StagePromptTemplate.stage_id.in_(stage_ids)
                ),
                AgentPromptOverride: select(AgentPromptOverride).where(
                    AgentPromptOverride.agent_profile_id.in_(agent_ids)
                ),
                HostAudioAsset: select(HostAudioAsset).where(
                    HostAudioAsset.generation_id == generation.id
                ),
            }
            for model, query in queries.items():
                rows = list((await session.scalars(query)).all())
                if len(rows) > 1024:
                    raise RuntimeError("qa_catalog_row_budget")
                tables[model.__tablename__] = [row_values(model, row) for row in rows]
            for model, rows in (
                (Rule, [rule]),
                (RuleStage, stages),
                (AgentProfile, agents),
                (RuleJudgeConfig, [judge] if judge is not None else []),
                (RuleAudioGeneration, [generation]),
            ):
                tables[model.__tablename__] = [row_values(model, row) for row in rows]
            root = Path(settings.host_audio_storage_dir).resolve()
            audio: dict[str, bytes] = {}
            for asset in tables[HostAudioAsset.__tablename__]:
                relative = asset["storage_path"]
                if asset["status"] != "READY" or not relative:
                    raise RuntimeError("qa_host_asset_not_ready")
                path = (root / relative).resolve()
                if not path.is_relative_to(root):
                    raise RuntimeError("qa_host_asset_outside_root")
                body = path.read_bytes()
                audio[relative] = body
                if sum(len(value) for value in audio.values()) > 32 * 1024 * 1024:
                    raise RuntimeError("qa_host_asset_byte_budget")
            return {
                "tables": tables,
                "audio": audio,
                "rule_id": rule.id,
                "rule_version": rule.version,
                "source_revision": rule.config_revision,
            }
    finally:
        await database.dispose()


async def seed_material(factory: Any, material: dict[str, Any], host_root: Path) -> dict[str, Any]:
    """Use only an empty owned schema; source credentials are never decrypted here."""
    tables = deepcopy(material["tables"])
    source_fingerprint = catalog_fingerprint(tables)
    async with factory() as session:
        target = make_url(session.bind.url)
        if (
            target.host != "127.0.0.1"
            or target.database != "jx_debate_qa_agent_faults"
            or await session.scalar(text("SELECT current_database()")) != target.database
        ):
            raise RuntimeError("qa_catalog_target_database_not_owned")
        await session.rollback()
        async with session.begin():
            if await session.scalar(select(Rule.id).limit(1)) is not None:
                raise RuntimeError("qa_catalog_target_not_empty")
            creators = {row["created_by"] for row in tables[Rule.__tablename__]}
            for row in tables[RuleAudioGeneration.__tablename__]:
                creators.update(
                    value for value in (row["requested_by"], row["approved_by"]) if value
                )
            for creator in creators:
                session.add(
                    User(
                        id=creator,
                        username=f"qas-{creator.hex[:28]}",
                        username_normalized=f"qas-{creator.hex[:28]}",
                        real_name="QA catalog owner",
                        password_hash="unusable-qa-catalog-owner",
                        role="USER",
                    )
                )
            await session.flush()
            names = {voice: name for _, _, name, voice in CONFERENCE_ROLES}
            avatars = {
                row["name"]: row["avatar_key"]
                for row in tables[VoiceProfile.__tablename__]
                if row["name"] in names.values() and row["avatar_key"]
            }
            for row in tables[VoiceProfile.__tablename__]:
                if row["provider_voice"] in names:
                    row["name"] = names[row["provider_voice"]]
                    if row["name"] in avatars:
                        row["avatar_key"] = avatars[row["name"]]
                elif row["name"] in names.values():
                    row["name"] = f"备用 · {row['provider_voice']}"
            voices = {row["id"]: row for row in tables[VoiceProfile.__tablename__]}
            for row in tables[AgentProfile.__tablename__]:
                voice = voices[row["voice_profile_id"]]
                row["name"] = voice["name"]
                if voice["provider_voice"] in names:
                    row["status"] = "ENABLED"
            for model in (StagePromptTemplate, AgentPromptOverride):
                for row in tables[model.__tablename__]:
                    if (
                        row["purpose"] == "DECISION"
                        and ACTIVE_DECISION_POLICY not in row["template_text"]
                    ):
                        row["template_text"] += "\n" + ACTIVE_DECISION_POLICY
            generation_id = tables[Rule.__tablename__][0]["current_audio_generation_id"]
            for model in CATALOG_TABLES:
                rows = tables[model.__tablename__]
                if model is Rule:
                    rows = [{**row, "current_audio_generation_id": None} for row in rows]
                if rows:
                    await session.execute(insert(model), rows)
            await session.execute(
                update(Rule)
                .where(Rule.id == material["rule_id"])
                .values(current_audio_generation_id=generation_id)
            )
    host_root.mkdir(parents=True, exist_ok=True)
    assets: list[dict[str, Any]] = []
    for relative, body in material["audio"].items():
        target = (host_root / relative).resolve()
        if not target.is_relative_to(host_root.resolve()):
            raise RuntimeError("qa_target_asset_outside_root")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        assets.append({"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)})
    return {
        "rule_id": str(material["rule_id"]),
        "rule_version": material["rule_version"],
        "source_revision": material["source_revision"],
        "copied_host_assets": assets,
        "catalog_rows": {name: len(rows) for name, rows in tables.items()},
        "source_catalog_sha256": source_fingerprint,
        "candidate_catalog_sha256": catalog_fingerprint(tables),
        "candidate_overrides": ["approved_conference_roster", "approved_decision_policy"],
        "credentials": "memory_and_encrypted_model_columns_only",
        "source_business_mutations": 0,
    }

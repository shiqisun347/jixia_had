"""Apply only the reviewed public Spec308 configuration in an empty owned QA DB."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from scripts.ops.spec308_voice_swap import swap_voices
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from jx_core.models import (
    AgentProfile,
    AgentPromptOverride,
    Match,
    Room,
    Rule,
    RuleStage,
    StagePromptTemplate,
    VoiceProfile,
)
from jx_core.rooms.roster import CONFERENCE_ROLES
from jx_core.rules.schemas import RulePromptUpdate, VoiceProfileUpdate
from jx_core.rules.service import CatalogService, RuleService

PROVIDERS = frozenset(
    ("longwan_v3.1", "longhua_v3.1", "anmingyuan_v3.1", "huozhuoshi_v3.1", "xiaxiaochen_v3.1")
)


def require(condition: object, code: str) -> None:
    if not condition:
        raise RuntimeError(code)


def _fingerprint(rows: list[AgentProfile]) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                {column.key: getattr(row, column.key) for column in AgentProfile.__table__.columns}
                for row in rows
            ],
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()


PromptSlot = tuple[str, str, str]


def _slot_key(row: dict[str, Any]) -> PromptSlot:
    return row["agent_profile_id"], row["stage_id"], row["purpose"]


def validate_prompt_operations(
    slots: list[dict[str, Any]], operations: list[dict[str, Any]], expected: set[PromptSlot]
) -> dict[PromptSlot, str]:
    slot_keys = {_slot_key(item) for item in slots}
    require(
        bool(expected) and slot_keys == expected and len(slots) == len(expected),
        "qa_spec308_prompt_coverage",
    )
    require(
        all(type(item.get("requires_write")) is bool for item in slots),
        "qa_spec308_prompt_write_flag",
    )
    targets = {_slot_key(item): item["template_sha256"] for item in slots}
    require(
        all(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
            for value in targets.values()
        ),
        "qa_spec308_prompt_slot_hash_invalid",
    )
    required_writes = {_slot_key(item) for item in slots if item["requires_write"]}
    operation_keys = [_slot_key(item) for item in operations]
    require(
        len(set(operation_keys)) == len(operation_keys) and set(operation_keys) == required_writes,
        "qa_spec308_prompt_operations",
    )
    for operation in operations:
        require(
            operation["method"] == "PUT" and set(operation["body"]) == {"template_text"},
            "qa_spec308_prompt_operation_invalid",
        )
        template = operation["body"]["template_text"]
        require(isinstance(template, str), "qa_spec308_prompt_operation_invalid")
        digest = hashlib.sha256(template.encode()).hexdigest()
        require(
            digest == operation["template_sha256"] == targets[_slot_key(operation)],
            "qa_spec308_prompt_hash_mismatch",
        )
    return targets


def verify_effective_prompt_templates(
    targets: dict[PromptSlot, str],
    defaults: dict[tuple[str, str], str],
    overrides: dict[PromptSlot, str],
) -> None:
    for key, expected in targets.items():
        template = overrides.get(key, defaults.get((key[1], key[2])))
        require(isinstance(template, str), "qa_spec308_effective_prompt_missing")
        require(
            hashlib.sha256(template.encode()).hexdigest() == expected,
            "qa_spec308_effective_prompt_mismatch",
        )


def read_configuration(root: Path) -> tuple[dict[str, Any], str]:
    root = root.resolve()
    path = root / "scripts/qa/probes/spec308-public-config.json"
    require(path.is_file() and not path.is_symlink(), "qa_spec308_configuration_missing")
    require(path.resolve().is_relative_to(root), "qa_spec308_configuration_outside_root")
    require(path.stat().st_size <= 4 * 1024 * 1024, "qa_spec308_configuration_budget")
    body = path.read_bytes()
    value = json.loads(body)
    require(
        isinstance(value, dict) and set(value) == {"plan", "calibrations"},
        "qa_spec308_configuration_shape",
    )
    plan, calibrations = value["plan"], value["calibrations"]
    require(
        isinstance(plan, dict) and plan.get("schema") == "spec308-prompt-override-plan-v1",
        "qa_spec308_plan_schema",
    )
    require(
        isinstance(calibrations, list) and len(calibrations) == 5, "qa_spec308_calibration_count"
    )
    ids, providers = set(), set()
    for row in calibrations:
        require(isinstance(row, dict), "qa_spec308_calibration_shape")
        voice_id = UUID(row["voice_profile_id"])
        require(
            voice_id not in ids and row["provider_voice"] in PROVIDERS,
            "qa_spec308_calibration_identity",
        )
        cps = row["chars_per_second"]
        rate = row["rate"]
        require(
            type(cps) in (int, float) and math.isfinite(cps) and 0 < cps <= 20,
            "qa_spec308_calibration_cps",
        )
        require(
            type(rate) in (int, float) and math.isfinite(rate) and 0.5 <= rate <= 2,
            "qa_spec308_calibration_rate",
        )
        require(
            isinstance(row["evidence_sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", row["evidence_sha256"]),
            "qa_spec308_calibration_evidence",
        )
        ids.add(voice_id)
        providers.add(row["provider_voice"])
    require(providers == PROVIDERS, "qa_spec308_calibration_provider_set")
    return value, hashlib.sha256(body).hexdigest()


async def prepare_configuration(factory: Any, rule_id: UUID, root: Path) -> dict[str, Any]:
    configuration, configuration_sha256 = read_configuration(root)
    plan = configuration["plan"]
    async with factory() as session:
        target = make_url(session.bind.url)
        require(
            target.host == "127.0.0.1" and target.database == "jx_debate_qa_agent_faults",
            "qa_spec308_database_not_owned",
        )
        require(
            await session.scalar(text("SELECT current_database()")) == target.database,
            "qa_spec308_database_identity",
        )
        require(
            not await session.scalar(select(func.count()).select_from(Room))
            and not await session.scalar(select(func.count()).select_from(Match)),
            "qa_spec308_requires_empty_matches",
        )
        rule = await session.get(Rule, rule_id)
        require(
            rule is not None
            and rule.side_size == 4
            and not rule.historical_read_only
            and rule.status != "ARCHIVED",
            "qa_spec308_rule_not_editable",
        )
        actor_id, revision = rule.created_by, rule.config_revision
        preconditions = [
            item for item in plan["rule_preconditions"] if item["rule_id"] == str(rule_id)
        ]
        require(
            len(preconditions) == 1 and preconditions[0]["config_revision"] == revision,
            "qa_spec308_rule_revision_drift",
        )
        agents = list((await session.scalars(select(AgentProfile).order_by(AgentProfile.id))).all())
        agent_before = _fingerprint(agents)
        enabled_ids = {
            str(agent.id)
            for agent in agents
            if agent.rule_id == rule_id and agent.status == "ENABLED"
        }
        stages = list(
            (await session.scalars(select(RuleStage).where(RuleStage.rule_id == rule_id))).all()
        )
        expected = {
            (agent_id, str(stage.id), purpose)
            for agent_id in enabled_ids
            for stage in stages
            if stage.stage_kind in {"FIXED_SPEECH", "FREE_DEBATE"}
            for purpose in (
                ("SPEECH", "DECISION") if stage.stage_kind == "FREE_DEBATE" else ("SPEECH",)
            )
        }
        slots = [item for item in plan["slots"] if item["rule_id"] == str(rule_id)]
        operations = [item for item in plan["operations"] if item["rule_id"] == str(rule_id)]
        targets = validate_prompt_operations(slots, operations, expected)
        await session.rollback()
        async with session.begin():
            await swap_voices(session, actor_user_id=actor_id)

    for calibration in configuration["calibrations"]:
        async with factory() as session:
            voice_id = UUID(calibration["voice_profile_id"])
            voice = await session.get(VoiceProfile, voice_id)
            require(
                voice is not None
                and voice.kind == "AGENT"
                and voice.provider_voice == calibration["provider_voice"]
                and voice.rate == calibration["rate"],
                "qa_spec308_voice_binding_drift",
            )
            payload = VoiceProfileUpdate(
                name=voice.name,
                kind=voice.kind,
                provider_voice=voice.provider_voice,
                rate=voice.rate,
                chars_per_second=calibration["chars_per_second"],
                playback_gain=voice.playback_gain,
                avatar_key=voice.avatar_key,
            )
            await session.rollback()
            await CatalogService().update_voice(
                session, voice_id=voice_id, payload=payload, actor_user_id=actor_id
            )

    hashes = []
    for operation in operations:
        template = operation["body"]["template_text"]
        digest = hashlib.sha256(template.encode()).hexdigest()
        async with factory() as session:
            await RuleService().save_prompt(
                session,
                rule_id=rule_id,
                stage_id=UUID(operation["stage_id"]),
                purpose=operation["purpose"],
                agent_id=UUID(operation["agent_profile_id"]),
                payload=RulePromptUpdate(template_text=template),
                actor_user_id=actor_id,
            )
        hashes.append(digest)

    async with factory() as session:
        agents = list((await session.scalars(select(AgentProfile).order_by(AgentProfile.id))).all())
        require(_fingerprint(agents) == agent_before, "qa_spec308_agent_rows_changed")
        conference_names = {row[2] for row in CONFERENCE_ROLES}
        conference_agents = [
            agent
            for agent in agents
            if agent.rule_id == rule_id
            and agent.status == "ENABLED"
            and agent.name in conference_names
        ]
        require(
            len(conference_agents) == 4
            and {agent.name for agent in conference_agents} == conference_names,
            "qa_spec308_conference_identity_ambiguous",
        )
        conference_agent_ids = sorted(str(agent.id) for agent in conference_agents)
        stage_ids = {UUID(key[1]) for key in targets}
        defaults = {
            (str(row.stage_id), row.purpose): row.template_text
            for row in await session.scalars(
                select(StagePromptTemplate).where(StagePromptTemplate.stage_id.in_(stage_ids))
            )
        }
        overrides = {
            (str(row.agent_profile_id), str(row.stage_id), row.purpose): row.template_text
            for row in await session.scalars(
                select(AgentPromptOverride).where(
                    AgentPromptOverride.stage_id.in_(stage_ids),
                    AgentPromptOverride.agent_profile_id.in_({UUID(key[0]) for key in targets}),
                )
            )
        }
        verify_effective_prompt_templates(targets, defaults, overrides)
        for calibration in configuration["calibrations"]:
            voice = await session.get(VoiceProfile, UUID(calibration["voice_profile_id"]))
            require(
                voice is not None
                and voice.provider_voice == calibration["provider_voice"]
                and voice.rate == calibration["rate"]
                and voice.chars_per_second == calibration["chars_per_second"],
                "qa_spec308_calibration_readback_mismatch",
            )
    return {
        "status": "applied",
        "applied_slots": len(operations),
        "covered_slots": len(slots),
        "effective_slots_verified": len(targets),
        "calibrations_readback_verified": True,
        "conference_agent_ids": conference_agent_ids,
        "voices": len(configuration["calibrations"]),
        "agent_rows_unchanged": True,
        "configuration_sha256": configuration_sha256,
        "template_sha256": hashes,
        "calibration_evidence_sha256": sorted(
            {row["evidence_sha256"] for row in configuration["calibrations"]}
        ),
    }

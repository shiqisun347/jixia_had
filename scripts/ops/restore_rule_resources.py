"""Restore only approved global resources from an isolated pre-reset database.

The source database must be a disposable database restored from the protected
custom dump. The command is dry-run by default and never prints secret or
Prompt contents.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import select, text

from jx_core.database import Database
from jx_core.models import (
    AgentProfile,
    BackgroundTask,
    HostAudioAsset,
    JudgeProfile,
    ModelProfile,
    Rule,
    RuleJudgeConfig,
    RuleStage,
    StageAction,
    StagePromptTemplate,
    Topic,
    User,
    VoiceProfile,
)
from jx_core.rules.prompts import (
    DEFAULT_FIXED_SPEECH_PROMPT,
    DEFAULT_FREE_DECISION_PROMPT,
    DEFAULT_FREE_SPEECH_PROMPT,
    FIXED_SPEECH_VARIABLES,
    FREE_DECISION_VARIABLES,
    FREE_SPEECH_VARIABLES,
    validate_prompt_template,
)

EXPECTED_COUNTS = {"models": 1, "voices": 12, "topics": 10, "rules": 10, "judges": 1}
C1_C6: dict[str, tuple[str, str, str, str]] = {
    "过程还是结果更能体现奋斗的价值": (
        "C1",
        "过程比结果更能体现奋斗的价值",
        "结果比过程更能体现奋斗的价值",
        "2019 华语辩论世界杯，山东大学 vs 马来亚大学",
    ),
    "网络语言丰富还是污染我们的语言": (
        "C2",
        "网络语言丰富我们的语言",
        "网络语言污染我们的语言",
        "2011 大专，台湾大学 vs 浙江大学",
    ),
    "当今社会，青年人学习应更注重广度还是深度": (
        "C3",
        "青年人学习应更注重广度",
        "青年人学习应更注重深度",
        "2023 华语辩论世界杯附加赛，兰州大学 vs 四川大学",
    ),
    "痛彻心扉的感情更应该淡忘还是铭记": (
        "C4",
        "痛彻心扉的感情更应该淡忘",
        "痛彻心扉的感情更应该铭记",
        "2018 国辩，重庆大学 vs 华侨大学",
    ),
    "金钱是不是万恶之源": (
        "C5",
        "金钱是万恶之源",
        "金钱不是万恶之源",
        "2001 大专，武汉大学 vs 马来亚大学",
    ),
    "先天遗传还是后天环境更重要": (
        "C6",
        "先天遗传比后天环境重要",
        "后天环境比先天遗传重要",
        "1997 大专，马来亚大学 vs 香港大学",
    ),
}


def _database_url(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name}_missing")
    return value


async def _source_rows(database: Database) -> dict[str, list[dict[str, Any]]]:
    queries = {
        "models": """
            SELECT id,name,config_ref,base_url,model_id,api_key_ciphertext,api_key_nonce,
                   api_key_last4,max_concurrency,token_per_char,generation_params,
                   capability_schema,status
            FROM model_profiles ORDER BY name
        """,
        "voices": """
            SELECT id,name,kind,provider_voice,rate,chars_per_second,playback_gain,
                   avatar_key,calibration_status,calibrated_at,status
            FROM voice_profiles ORDER BY name
        """,
        "topics": """
            SELECT id,topic_key,version,title,affirmative_text,negative_text,source_text,
                   cedar_id,status
            FROM topics ORDER BY created_at,id
        """,
        "rules": """
            SELECT id,rule_key,version,name,description,side_size,status
            FROM rules ORDER BY created_at,id
        """,
        "stages": """
            SELECT id,rule_id,position,name,stage_kind,duration_seconds,start_host_text,
                   end_host_text,parameters
            FROM rule_stages ORDER BY rule_id,position
        """,
        "actions": """
            SELECT stage_id,position,action_kind,side,seat_no,duration_seconds,parameters
            FROM stage_actions ORDER BY stage_id,position
        """,
        "judges": """
            SELECT id,model_profile_id,system_prompt,judge_prompt,generation_params,status
            FROM judge_profiles ORDER BY updated_at DESC,id LIMIT 1
        """,
    }
    async with database.session_factory() as session:
        return {
            name: [dict(row) for row in (await session.execute(text(query))).mappings().all()]
            for name, query in queries.items()
        }


def _report(rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    counts = {name: len(rows[name]) for name in EXPECTED_COUNTS}
    duplicate_topics = len(rows["topics"]) - len(
        {str(row["title"]).strip() for row in rows["topics"]}
    )
    missing_confirmed_topics = sorted(
        set(C1_C6) - {str(row["title"]).strip() for row in rows["topics"]}
    )
    return {
        "mode": "dry-run",
        "counts": counts,
        "expected_counts": EXPECTED_COUNTS,
        "count_mismatches": {
            name: {"expected": expected, "actual": counts[name]}
            for name, expected in EXPECTED_COUNTS.items()
            if counts[name] != expected
        },
        "conflicts": {
            "duplicate_normalized_topics": duplicate_topics,
            "missing_confirmed_topics": missing_confirmed_topics,
        },
        "excluded": [
            "agents",
            "users",
            "sessions",
            "rooms",
            "matches",
            "experiments",
            "logs",
            "rankings",
        ],
    }


def _normalized_topic_values(source: dict[str, Any]) -> dict[str, Any]:
    values = {key: value for key, value in source.items() if key != "id"}
    confirmed = C1_C6.get(str(source["title"]).strip())
    if confirmed is not None:
        code, affirmative, negative, provenance = confirmed
        values.update(
            topic_key=code,
            version=1,
            affirmative_text=affirmative,
            negative_text=negative,
            source_text=provenance,
        )
    return values


async def _apply(
    target: Database,
    *,
    rows: dict[str, list[dict[str, Any]]],
    admin_username: str,
) -> dict[str, int]:
    imported = defaultdict(int)
    async with target.session_factory() as session:
        async with session.begin():
            admin = await session.scalar(
                select(User).where(User.username_normalized == admin_username.strip().lower())
            )
            if admin is None or admin.role != "ADMIN" or admin.status != "ACTIVE":
                raise RuntimeError("active_admin_unavailable")

            model_map: dict[UUID, ModelProfile] = {}
            for source in rows["models"]:
                model = await session.scalar(
                    select(ModelProfile).where(ModelProfile.name == source["name"])
                )
                if model is None:
                    model = ModelProfile(
                        **{key: value for key, value in source.items() if key != "id"}
                    )
                    session.add(model)
                    imported["models"] += 1
                    await session.flush()
                model_map[UUID(str(source["id"]))] = model

            voice_map: dict[UUID, VoiceProfile] = {}
            for source in rows["voices"]:
                voice = await session.scalar(
                    select(VoiceProfile).where(
                        VoiceProfile.provider_voice == source["provider_voice"]
                    )
                )
                if voice is None:
                    voice = VoiceProfile(
                        **{key: value for key, value in source.items() if key != "id"}
                    )
                    session.add(voice)
                    imported["voices"] += 1
                    await session.flush()
                voice_map[UUID(str(source["id"]))] = voice

            existing_topics = list((await session.scalars(select(Topic))).all())
            topic_by_title = {item.title.strip(): item for item in existing_topics}
            for source in rows["topics"]:
                title = str(source["title"]).strip()
                confirmed = C1_C6.get(title)
                topic = topic_by_title.get(title)
                if topic is None:
                    values = _normalized_topic_values(source)
                    values["created_by"] = admin.id
                    topic = Topic(**values)
                    session.add(topic)
                    topic_by_title[title] = topic
                    imported["topics"] += 1
                elif confirmed:
                    code, affirmative, negative, provenance = confirmed
                    topic.topic_key = code
                    topic.version = 1
                    topic.affirmative_text = affirmative
                    topic.negative_text = negative
                    topic.source_text = provenance

            stages_by_rule: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
            actions_by_stage: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
            for row in rows["stages"]:
                stages_by_rule[UUID(str(row["rule_id"]))].append(row)
            for row in rows["actions"]:
                actions_by_stage[UUID(str(row["stage_id"]))].append(row)
            host_voice = next(
                (
                    voice
                    for voice in voice_map.values()
                    if voice.kind == "HOST" and voice.status == "ENABLED"
                ),
                None,
            )
            default_model = next(
                (model for model in model_map.values() if model.status == "ENABLED"), None
            )
            agent_voices = [
                voice
                for voice in voice_map.values()
                if voice.kind == "AGENT" and voice.status == "ENABLED"
            ]
            if host_voice is None or default_model is None:
                raise RuntimeError("restored_default_resources_unavailable")

            for source in rows["rules"]:
                existing = await session.scalar(
                    select(Rule).where(
                        Rule.rule_key == source["rule_key"], Rule.version == source["version"]
                    )
                )
                if existing is not None:
                    continue
                rule = Rule(
                    rule_key=source["rule_key"],
                    version=source["version"],
                    name=source["name"],
                    description=source["description"],
                    side_size=source["side_size"],
                    estimated_seconds=0,
                    status="DRAFT",
                    host_voice_profile_id=host_voice.id,
                    default_agent_model_profile_id=default_model.id,
                    topic_policy="BOTH",
                    historical_read_only=source["side_size"] != 4,
                    created_by=admin.id,
                )
                session.add(rule)
                await session.flush()
                estimated_seconds = 0
                has_host_audio = False
                for stage_source in stages_by_rule[UUID(str(source["id"]))]:
                    stage = RuleStage(
                        rule_id=rule.id,
                        **{
                            key: value
                            for key, value in stage_source.items()
                            if key not in {"id", "rule_id"}
                        },
                    )
                    session.add(stage)
                    await session.flush()
                    source_actions = actions_by_stage[UUID(str(stage_source["id"]))]
                    for action_source in source_actions:
                        session.add(
                            StageAction(
                                stage_id=stage.id,
                                **{
                                    key: value
                                    for key, value in action_source.items()
                                    if key != "stage_id"
                                },
                            )
                        )
                    estimated_seconds += (
                        stage.duration_seconds * 2
                        if stage.stage_kind == "FREE_DEBATE"
                        else sum(int(action["duration_seconds"]) for action in source_actions)
                        if stage.stage_kind == "FIXED_SPEECH"
                        else stage.duration_seconds
                    )
                    prompt_specs = []
                    if rule.side_size == 4 and stage.stage_kind == "FIXED_SPEECH":
                        prompt_specs = [
                            ("SPEECH", DEFAULT_FIXED_SPEECH_PROMPT, FIXED_SPEECH_VARIABLES, "TEXT")
                        ]
                    elif rule.side_size == 4 and stage.stage_kind == "FREE_DEBATE":
                        prompt_specs = [
                            ("SPEECH", DEFAULT_FREE_SPEECH_PROMPT, FREE_SPEECH_VARIABLES, "TEXT"),
                            (
                                "DECISION",
                                DEFAULT_FREE_DECISION_PROMPT,
                                FREE_DECISION_VARIABLES,
                                "SHOULD_SPEAK_V1",
                            ),
                        ]
                    for purpose, template, required, contract in prompt_specs:
                        session.add(
                            StagePromptTemplate(
                                stage_id=stage.id,
                                purpose=purpose,
                                template_text=template,
                                variables=sorted(
                                    validate_prompt_template(template, required_variables=required)
                                ),
                                output_contract=contract,
                            )
                        )
                    for suffix, host_text in (
                        ("start", stage.start_host_text),
                        ("end", stage.end_host_text),
                    ):
                        if not host_text:
                            continue
                        has_host_audio = True
                        asset = HostAudioAsset(
                            rule_id=rule.id,
                            segment_key=f"stage-{stage.position}-{suffix}",
                            text=host_text,
                            text_hash=sha256(host_text.encode()).hexdigest(),
                            voice_profile_id=host_voice.id,
                        )
                        session.add(asset)
                        await session.flush()
                        session.add(
                            BackgroundTask(
                                task_type="HOST_TTS",
                                payload={"asset_id": str(asset.id), "rule_id": str(rule.id)},
                                available_at=datetime.now(UTC),
                            )
                        )
                rule.estimated_seconds = estimated_seconds
                rule.status = "GENERATING_AUDIO" if has_host_audio else "READY"
                if not has_host_audio:
                    rule.audio_reviewed_at = datetime.now(UTC)
                if rule.side_size == 4:
                    for voice in agent_voices:
                        session.add(
                            AgentProfile(
                                rule_id=rule.id,
                                name=voice.name,
                                model_profile_id=default_model.id,
                                voice_profile_id=voice.id,
                                status="ENABLED",
                            )
                        )
                session.add(RuleJudgeConfig(rule_id=rule.id))
                imported["rules"] += 1

            for source in rows["judges"]:
                model = model_map.get(UUID(str(source["model_profile_id"])))
                if model is None:
                    raise RuntimeError("judge_model_mapping_missing")
                existing = await session.scalar(
                    select(JudgeProfile).order_by(JudgeProfile.updated_at.desc()).limit(1)
                )
                if existing is None:
                    session.add(
                        JudgeProfile(
                            model_profile_id=model.id,
                            system_prompt=source["system_prompt"],
                            judge_prompt=source["judge_prompt"],
                            generation_params=source["generation_params"],
                            status=source["status"],
                        )
                    )
                    imported["judges"] += 1
    return dict(imported)


async def run(*, apply: bool, admin_username: str) -> dict[str, Any]:
    source = Database(_database_url("RESOURCE_SOURCE_DATABASE_URL"))
    target = Database(_database_url("DATABASE_URL"))
    try:
        rows = await _source_rows(source)
        report = _report(rows)
        if report["count_mismatches"] or report["conflicts"]["missing_confirmed_topics"]:
            raise RuntimeError(json.dumps(report, ensure_ascii=False))
        if apply:
            report["mode"] = "apply"
            report["imported"] = await _apply(target, rows=rows, admin_username=admin_username)
        return report
    finally:
        await source.dispose()
        await target.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-username", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(run(apply=args.apply, admin_username=args.admin_username)),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

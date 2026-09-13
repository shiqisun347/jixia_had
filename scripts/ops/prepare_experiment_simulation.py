"""Prepare an idempotent draft experiment batch without exposing credentials."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from jx_core.config import load_settings
from jx_core.database import Database
from jx_core.experiments.schemas import (
    ExperimentBatchCreateRequest,
    ExperimentBatchUpdateRequest,
    ExperimentExpertBinding,
    ExperimentMemberBinding,
    ExperimentRosterPutRequest,
    ExperimentTeamBinding,
)
from jx_core.experiments.service import ExperimentService
from jx_core.models import (
    AgentProfile,
    ExperimentBatch,
    ExperimentMatchAttempt,
    ModelProfile,
    Rule,
    RuleStage,
    ScheduledMatch,
    StageAction,
    Topic,
    User,
    VoiceProfile,
)
from jx_core.rules.paper_experiment_4v4 import build_paper_experiment_4v4_draft
from jx_core.rules.schemas import TopicCreate
from jx_core.rules.service import CatalogService
from jx_core.rules.validation import RuleDraft

EXPECTED_REVISION = "0038_experiment_rule_snapshot"
RULE_KEY = "paper-experiment-4v4"
ACCOUNT_CODES = tuple(f"P{index:02d}" for index in range(1, 19)) + tuple(
    f"E{index:02d}" for index in range(1, 4)
)
FORMAL_TOPIC_TITLES = (
    "过程还是结果更能体现奋斗的价值",
    "网络语言丰富还是污染我们的语言",
    "当今社会，青年人学习应更注重广度还是深度",
    "痛彻心扉的感情更应该淡忘还是铭记",
    "金钱是不是万恶之源",
    "先天遗传还是后天环境更重要",
)
AGENT_NAMES = ("乾元", "坤元", "慎思", "破阵", "见微", "观澜")
C7 = TopicCreate(
    title="知识付费能不能缓解年轻人的压力",
    affirmative_text="知识付费能缓解年轻人的压力",
    negative_text="知识付费不能缓解年轻人的压力",
    source_text=("[P49]2019华语辩论世界杯完整赛事视频_P49_52-八分之一-麦吉尔大学vs香港中文大学"),
    cedar_id=None,
)


def validate_batch_code(code: str) -> str:
    normalized = code.strip().upper()
    if not normalized.startswith("SIM_"):
        raise ValueError("simulation batch code must start with SIM_")
    return normalized


def validate_root_credentials_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if os.geteuid() != 0:
        raise RuntimeError("experiment_preparation_requires_root")
    if not resolved.is_relative_to(Path("/root")):
        raise RuntimeError("credentials_file_must_be_under_root")
    return resolved


def _secure_mode(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise RuntimeError("credentials_file_permissions_must_be_0600")


def load_existing_credentials(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("credentials_path_must_be_a_regular_file")
    _secure_mode(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("credentials_file_invalid") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("accounts"), list):
        raise RuntimeError("credentials_file_invalid")
    return payload


def write_credentials(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _secure_mode(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _credential_codes(payload: dict[str, Any] | None) -> set[str]:
    if payload is None:
        return set()
    return {
        item.get("code")
        for item in payload["accounts"]
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }


async def _paper_rule_matches(session: Any, rule: Rule) -> bool:
    """Reject a rule unless its persisted stages exactly match the paper schedule."""

    draft = RuleDraft.model_validate(build_paper_experiment_4v4_draft())
    stages = list(
        (
            await session.scalars(
                select(RuleStage).where(RuleStage.rule_id == rule.id).order_by(RuleStage.position)
            )
        ).all()
    )
    expected_stages = draft.stages
    if len(stages) != len(expected_stages):
        return False
    for persisted, expected in zip(stages, expected_stages, strict=True):
        if (
            persisted.name != expected.name
            or persisted.stage_kind != expected.stage_kind
            or persisted.duration_seconds != expected.duration_seconds
            or persisted.parameters != expected.parameters
        ):
            return False
        if persisted.start_host_text != expected.start_host_text:
            return False
        actions = list(
            (
                await session.scalars(
                    select(StageAction)
                    .where(StageAction.stage_id == persisted.id)
                    .order_by(StageAction.position)
                )
            ).all()
        )
        expected_actions = expected.actions
        if len(actions) != len(expected_actions):
            return False
        for action, expected_action in zip(actions, expected_actions, strict=True):
            if any(
                getattr(action, field) != getattr(expected_action, field)
                for field in ("action_kind", "side", "seat_no", "duration_seconds")
            ):
                return False
    return True


def build_credential_payload(accounts: list[dict[str, str]]) -> dict[str, Any]:
    team_by_participant = {
        f"P{index:02d}": f"T{((index - 1) // 3) + 1:02d}" for index in range(1, 19)
    }
    return {
        "purpose": "Jixia paper experiment initial account handoff",
        "accounts": [
            {
                **item,
                "team_code": team_by_participant.get(item["code"]),
                "role": "participant" if item["code"].startswith("P") else "expert",
            }
            for item in accounts
        ],
    }


async def prepare(
    *, admin_username: str, batch_code: str, credentials_path: Path
) -> dict[str, Any]:
    code = validate_batch_code(batch_code)
    existing_credentials = load_existing_credentials(credentials_path)

    settings = load_settings()
    database = Database(settings.database_url_value)
    experiment_service = ExperimentService()
    try:
        async with database.session_factory() as session:
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != EXPECTED_REVISION:
                raise RuntimeError("database_revision_mismatch")
            admin = await session.scalar(
                select(User).where(User.username_normalized == admin_username.strip().lower())
            )
            if admin is None or admin.role != "ADMIN" or admin.status != "ACTIVE":
                raise RuntimeError("active_admin_unavailable")
            admin_id = admin.id

            rule = await session.scalar(
                select(Rule)
                .where(
                    Rule.rule_key == RULE_KEY,
                    Rule.status == "ENABLED",
                    Rule.side_size == 4,
                    Rule.audio_reviewed_at.is_not(None),
                )
                .order_by(Rule.version.desc())
                .limit(1)
            )
            if rule is None:
                raise RuntimeError("paper_experiment_rule_unavailable")
            if not await _paper_rule_matches(session, rule):
                raise RuntimeError("paper_experiment_rule_invalid")
            rule_id = rule.id

            formal_topics = list(
                (
                    await session.scalars(
                        select(Topic).where(
                            Topic.title.in_(FORMAL_TOPIC_TITLES), Topic.status == "ENABLED"
                        )
                    )
                ).all()
            )
            topics_by_title = {topic.title: topic for topic in formal_topics}
            if set(topics_by_title) != set(FORMAL_TOPIC_TITLES):
                raise RuntimeError("formal_topics_incomplete")
            if any(not (topic.source_text or "").strip() for topic in formal_topics):
                raise RuntimeError("formal_topic_source_missing")

            agents = list(
                (
                    await session.scalars(
                        select(AgentProfile).where(AgentProfile.name.in_(AGENT_NAMES))
                    )
                ).all()
            )
            agents_by_name = {agent.name: agent for agent in agents}
            if set(agents_by_name) != set(AGENT_NAMES) or any(
                agent.status != "ENABLED" for agent in agents
            ):
                raise RuntimeError("fixed_agents_unavailable")
            agent_ids_by_name = {name: agent.id for name, agent in agents_by_name.items()}
            if len({agent.model_profile_id for agent in agents}) != 1:
                raise RuntimeError("fixed_agents_must_share_model")
            if len({agent.voice_profile_id for agent in agents}) != len(agents):
                raise RuntimeError("fixed_agents_must_use_unique_voices")
            model = await session.get(ModelProfile, agents[0].model_profile_id)
            voices = list(
                (
                    await session.scalars(
                        select(VoiceProfile).where(
                            VoiceProfile.id.in_([agent.voice_profile_id for agent in agents])
                        )
                    )
                ).all()
            )
            if model is None or model.status != "ENABLED" or len(voices) != 6:
                raise RuntimeError("fixed_agent_dependencies_unavailable")
            if any(voice.status != "ENABLED" or voice.kind != "AGENT" for voice in voices):
                raise RuntimeError("fixed_agent_voice_unavailable")
            if len({voice.provider_voice for voice in voices}) != 6:
                raise RuntimeError("fixed_agent_provider_voices_must_be_unique")
            formal_topic_ids = {title: topics_by_title[title].id for title in FORMAL_TOPIC_TITLES}

            existing_users = list(
                (
                    await session.scalars(
                        select(User).where(
                            User.username_normalized.in_([item.lower() for item in ACCOUNT_CODES])
                        )
                    )
                ).all()
            )
            missing_credential_codes = {
                user.username.upper() for user in existing_users
            } - _credential_codes(existing_credentials)
            if missing_credential_codes:
                raise RuntimeError("existing_accounts_require_original_credentials_file")
            await session.rollback()

            training_topic = await session.scalar(
                select(Topic)
                .where(Topic.title == C7.title, Topic.status == "ENABLED")
                .order_by(Topic.version.desc())
                .limit(1)
            )
            if training_topic is None:
                await session.rollback()
                training_topic = await CatalogService().create_topic(
                    session, creator_user_id=admin_id, payload=C7
                )
            elif (
                training_topic.affirmative_text != C7.affirmative_text
                or training_topic.negative_text != C7.negative_text
                or training_topic.source_text != C7.source_text
                or training_topic.cedar_id is not None
            ):
                raise RuntimeError("existing_c7_conflicts_with_fixed_definition")
            training_topic_id = training_topic.id
            await session.rollback()

            generated = await experiment_service.generate_anonymous_accounts(
                session, actor_user_id=admin_id
            )
            new_accounts = [
                {
                    "code": item.code,
                    "username": item.username,
                    "temporary_password": item.temporary_password,
                }
                for item in generated.accounts
                if item.created and item.temporary_password is not None
            ]
            if new_accounts:
                merged = {
                    item["code"]: item
                    for item in (existing_credentials or {"accounts": []})["accounts"]
                }
                merged.update({item["code"]: item for item in new_accounts})
                write_credentials(
                    credentials_path,
                    build_credential_payload([merged[item] for item in ACCOUNT_CODES]),
                )
            elif existing_credentials is None:
                raise RuntimeError("credentials_file_required_for_existing_accounts")

            account_users = list(
                (
                    await session.scalars(
                        select(User).where(
                            User.username_normalized.in_([item.lower() for item in ACCOUNT_CODES])
                        )
                    )
                ).all()
            )
            users_by_code = {user.username.upper(): user for user in account_users}
            if set(users_by_code) != set(ACCOUNT_CODES):
                raise RuntimeError("anonymous_account_generation_incomplete")
            user_ids_by_code = {code: user.id for code, user in users_by_code.items()}
            await session.rollback()

            batch = await session.scalar(
                select(ExperimentBatch).where(ExperimentBatch.code == code)
            )
            if batch is None:
                await session.rollback()
                batch = await experiment_service.create_batch(
                    session,
                    actor_user_id=admin_id,
                    payload=ExperimentBatchCreateRequest(
                        code=code,
                        title="论文实验 v2.1 生产形态模拟",
                        rule_id=rule_id,
                    ),
                )
            elif batch.status != "DRAFT":
                raise RuntimeError("simulation_batch_not_reusable")
            elif batch.rule_id != rule_id:
                attempt = await session.scalar(
                    select(ExperimentMatchAttempt.id)
                    .join(
                        ScheduledMatch,
                        ScheduledMatch.id == ExperimentMatchAttempt.scheduled_match_id,
                    )
                    .where(ScheduledMatch.batch_id == batch.id)
                    .limit(1)
                )
                if attempt is not None:
                    raise RuntimeError("simulation_batch_rule_change_has_attempts")
                existing_batch_id = batch.id
                existing_batch_code = batch.code
                existing_batch_title = batch.title
                await session.rollback()
                batch = await experiment_service.update_batch(
                    session,
                    batch_id=existing_batch_id,
                    actor_user_id=admin_id,
                    payload=ExperimentBatchUpdateRequest(
                        code=existing_batch_code,
                        title=existing_batch_title,
                        rule_id=rule_id,
                    ),
                )
            batch_id = batch.id
            batch_status = batch.status
            await session.rollback()

            roster = ExperimentRosterPutRequest(
                teams=[
                    ExperimentTeamBinding(
                        team_code=f"T{team_no:02d}",
                        agent_profile_id=agent_ids_by_name[agent_name],
                        members=[
                            ExperimentMemberBinding(
                                user_id=user_ids_by_code[f"P{participant_no:02d}"],
                                participant_code=f"P{participant_no:02d}",
                            )
                            for participant_no in range((team_no - 1) * 3 + 1, team_no * 3 + 1)
                        ],
                    )
                    for team_no, agent_name in enumerate(AGENT_NAMES, start=1)
                ],
                experts=[
                    ExperimentExpertBinding(
                        user_id=user_ids_by_code[f"E{index:02d}"],
                        expert_code=f"E{index:02d}",
                    )
                    for index in range(1, 4)
                ],
            )
            await experiment_service.replace_roster(session, batch_id=batch_id, payload=roster)
            schedule = await experiment_service.generate_schedule(
                session,
                batch_id=batch_id,
                topic_ids=tuple(formal_topic_ids[title] for title in FORMAL_TOPIC_TITLES),
                training_topic_id=training_topic_id,
            )
            return {
                "batch_id": str(batch_id),
                "batch_code": code,
                "batch_status": batch_status,
                "credentials_path": str(credentials_path),
                "created_account_count": generated.created_count,
                "account_count": len(generated.accounts),
                "team_count": 6,
                "match_count": len(schedule.matches),
                "formal_match_count": sum(item.kind == "FORMAL" for item in schedule.matches),
                "training_match_count": sum(item.kind == "TRAINING" for item in schedule.matches),
            }
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-username", required=True)
    parser.add_argument("--batch-code", required=True)
    parser.add_argument("--credentials-file", required=True, type=Path)
    args = parser.parse_args()
    credentials_path = validate_root_credentials_path(args.credentials_file)
    result = asyncio.run(
        prepare(
            admin_username=args.admin_username,
            batch_code=args.batch_code,
            credentials_path=credentials_path,
        )
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Create and finalize the dedicated paper experiment debate rule."""

from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jx_core.config import load_settings
from jx_core.database import Database
from jx_core.models import (
    HostAudioAsset,
    JudgeProfile,
    ModelProfile,
    Rule,
    RuleJudgeConfig,
    RuleStage,
    StageAction,
    StagePromptTemplate,
    User,
    VoiceProfile,
)
from jx_core.rules.paper_experiment_4v4 import build_paper_experiment_4v4_draft
from jx_core.rules.schemas import RuleCreate
from jx_core.rules.service import RuleService
from jx_core.rules.validation import RuleDraft

RULE_KEY = "paper-experiment-4v4"


async def _archive_previous_rules(session: AsyncSession, current_rule_id: UUID) -> None:
    previous = list(
        (
            await session.scalars(
                select(Rule).where(
                    Rule.rule_key == RULE_KEY,
                    Rule.id != current_rule_id,
                )
            )
        ).all()
    )
    changed = False
    for item in previous:
        if item.status != "ARCHIVED":
            item.status = "ARCHIVED"
            changed = True
    if changed:
        await session.commit()


def _prompt_templates_match(
    prompts: list[StagePromptTemplate], *, speech_prompt: str | None, decision_prompt: str | None
) -> bool:
    expected = {
        purpose: template
        for purpose, template in (("SPEECH", speech_prompt), ("DECISION", decision_prompt))
        if template is not None
    }
    return {prompt.purpose: prompt.template_text for prompt in prompts} == expected


async def _rule_matches(session: AsyncSession, rule_id: UUID) -> bool:
    expected = RuleDraft.model_validate(build_paper_experiment_4v4_draft()).stages
    stages = list(
        (
            await session.scalars(
                select(RuleStage).where(RuleStage.rule_id == rule_id).order_by(RuleStage.position)
            )
        ).all()
    )
    if len(stages) != len(expected):
        return False
    for stage, expected_stage in zip(stages, expected, strict=True):
        if (
            stage.name != expected_stage.name
            or stage.stage_kind != expected_stage.stage_kind
            or stage.duration_seconds != expected_stage.duration_seconds
            or stage.parameters != expected_stage.parameters
            or stage.start_host_text != expected_stage.start_host_text
        ):
            return False
        actions = list(
            (
                await session.scalars(
                    select(StageAction)
                    .where(StageAction.stage_id == stage.id)
                    .order_by(StageAction.position)
                )
            ).all()
        )
        expected_actions = expected_stage.actions
        if len(actions) != len(expected_actions):
            return False
        for action, expected_action in zip(actions, expected_actions, strict=True):
            if any(
                getattr(action, field) != getattr(expected_action, field)
                for field in ("action_kind", "side", "seat_no", "duration_seconds")
            ):
                return False
        prompts = list(
            (
                await session.scalars(
                    select(StagePromptTemplate).where(
                        StagePromptTemplate.stage_id == stage.id
                    )
                )
            ).all()
        )
        if not _prompt_templates_match(
            prompts,
            speech_prompt=expected_stage.speech_prompt,
            decision_prompt=expected_stage.decision_prompt,
        ):
            return False
    return True


async def _sync_rule_judge(session: AsyncSession, rule_id: UUID) -> bool:
    profile = await session.scalar(
        select(JudgeProfile)
        .where(JudgeProfile.status == "ENABLED")
        .order_by(JudgeProfile.updated_at.desc())
        .limit(1)
    )
    if profile is None:
        return False
    rule = await session.get(Rule, rule_id, with_for_update=True)
    judge = await session.get(RuleJudgeConfig, rule_id, with_for_update=True)
    if rule is None or judge is None:
        raise RuntimeError("paper_rule_judge_unavailable")
    changed = (
        not judge.enabled
        or judge.model_profile_id != profile.model_profile_id
        or judge.judge_prompt != profile.judge_prompt
        or not judge.include_in_leaderboard
    )
    judge.enabled = True
    judge.model_profile_id = profile.model_profile_id
    judge.judge_prompt = profile.judge_prompt
    judge.include_in_leaderboard = True
    if changed:
        rule.config_revision += 1
    await session.commit()
    return True


async def ensure(*, admin_username: str, finalize: bool) -> dict[str, object]:
    settings = load_settings()
    database = Database(settings.database_url_value)
    try:
        async with database.session_factory() as session:
            admin = await session.scalar(
                select(User).where(User.username_normalized == admin_username.strip().lower())
            )
            if admin is None or admin.role != "ADMIN" or admin.status != "ACTIVE":
                raise RuntimeError("active_admin_unavailable")
            admin_id = admin.id
            existing = await session.scalar(
                select(Rule).where(Rule.rule_key == RULE_KEY).order_by(Rule.version.desc())
            )
            matches = existing is not None and await _rule_matches(session, existing.id)
            if existing is None or not matches:
                host_voice = await session.scalar(
                    select(VoiceProfile).where(
                        VoiceProfile.kind == "HOST", VoiceProfile.status == "ENABLED"
                    )
                )
                if host_voice is None:
                    raise RuntimeError("host_voice_unavailable")
                default_model = await session.scalar(
                    select(ModelProfile).where(ModelProfile.status == "ENABLED")
                )
                if default_model is None:
                    raise RuntimeError("model_profile_unavailable")
                host_voice_id = host_voice.id
                default_model_id = default_model.id
                await session.rollback()
                rule = await RuleService().create_rule(
                    session,
                    creator_user_id=admin_id,
                    payload=RuleCreate(
                        rule_key=RULE_KEY,
                        host_voice_profile_id=host_voice_id,
                        default_agent_model_profile_id=default_model_id,
                        draft=RuleDraft.model_validate(build_paper_experiment_4v4_draft()),
                    ),
                )
                await _archive_previous_rules(session, rule.id)
                judge_enabled = await _sync_rule_judge(session, rule.id)
                return {
                    "id": str(rule.id),
                    "version": rule.version,
                    "status": rule.status,
                    "action": "created",
                    "judge_enabled": judge_enabled,
                    "finalize": "waiting_for_host_audio" if finalize else "not_requested",
                }

            result: dict[str, object] = {
                "id": str(existing.id),
                "version": existing.version,
                "status": existing.status,
                "action": "existing",
            }
            await _archive_previous_rules(session, existing.id)
            result["judge_enabled"] = await _sync_rule_judge(session, existing.id)
            if not finalize:
                return result
            if existing.status == "ENABLED":
                result["finalize"] = "already_enabled"
                return result
            pending = await session.scalar(
                select(HostAudioAsset.id)
                .where(
                    HostAudioAsset.rule_id == existing.id,
                    HostAudioAsset.status != "READY",
                )
                .limit(1)
            )
            if pending is not None:
                result["finalize"] = "waiting_for_host_audio"
                return result
            rule_id = existing.id
            status = existing.status
            await session.rollback()
            if status == "GENERATING_AUDIO":
                await RuleService().review_audio(session, rule_id=rule_id, actor_user_id=admin_id)
            enabled = await RuleService().enable_rule(
                session, rule_id=rule_id, actor_user_id=admin_id
            )
            result["status"] = enabled.status
            result["finalize"] = "enabled"
            return result
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-username", required=True)
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(ensure(admin_username=args.admin_username, finalize=args.finalize)),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

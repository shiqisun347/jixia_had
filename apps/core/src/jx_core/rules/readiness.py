"""Shared, read-only rule eligibility for management, rooms, and experiments."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.errors import AuthError
from ..models import (
    AgentProfile,
    AgentPromptOverride,
    HostAudioAsset,
    ModelProfile,
    Rule,
    RuleAudioGeneration,
    RuleJudgeConfig,
    RuleStage,
    StagePromptTemplate,
    VoiceProfile,
)
from .audio import audio_file
from .host_voice import CONFERENCE_HOST_VOICE
from .structure import rule_draft
from .validation import RuleValidationError, validate_rule_draft


@dataclass(frozen=True)
class Blocker:
    code: str
    message: str
    target: str
    action: str


@dataclass(frozen=True)
class Readiness:
    blockers: list[Blocker]
    available_agent_count: int
    stage_count: int

    @property
    def ready(self) -> bool:
        return not self.blockers


async def inspect_rule(
    session: AsyncSession, rule: Rule, *, storage_root: str | Path | None = None
) -> Readiness:
    blockers: list[Blocker] = []

    def block(code: str, message: str, target: str, action: str) -> None:
        blockers.append(Blocker(code, message, target, action))

    if rule.side_size != 4 or rule.historical_read_only:
        block(
            "FORMAL_4V4_REQUIRED",
            "历史赛制仅可查看，新房间只支持正式 4v4",
            "basic",
            "新建正式 4v4 赛制",
        )
    draft = await rule_draft(session, rule)
    try:
        validate_rule_draft(draft)
    except (RuleValidationError, ValueError, TypeError):
        block("RULE_STRUCTURE_INVALID", "阶段、动作或时长配置不合法", "stages", "检查阶段流程")
    available_count = len(await eligible_agent_ids(session, rule.id))
    host = (
        await session.get(VoiceProfile, rule.host_voice_profile_id)
        if rule.host_voice_profile_id
        else None
    )
    expected = {
        f"stage-{position}-{boundary}": text_value
        for position, stage in enumerate(draft["stages"], start=1)
        for boundary, text_value in (
            ("start", stage["start_host_text"]),
            ("end", stage["end_host_text"]),
        )
        if text_value.strip()
    }
    if expected and (host is None or host.kind != "HOST" or host.status != "ENABLED"):
        block("HOST_VOICE_UNAVAILABLE", "主持音色不存在或已停用", "basic", "选择启用中的主持音色")
    generation = (
        await session.get(RuleAudioGeneration, rule.current_audio_generation_id)
        if rule.current_audio_generation_id
        else None
    )
    assets = list(
        (
            await session.scalars(
                select(HostAudioAsset).where(
                    HostAudioAsset.rule_id == rule.id,
                    HostAudioAsset.generation_id == rule.current_audio_generation_id,
                )
            )
        ).all()
    )
    actual = {asset.segment_key: asset for asset in assets}
    valid = set(expected) == set(actual)
    if generation is not None:
        valid = (
            valid and generation.is_current and generation.status in {"SUCCEEDED", "NOT_REQUIRED"}
        )
    for key, text_value in expected.items():
        asset = actual.get(key)
        if (
            asset is None
            or asset.status != "READY"
            or asset.text != text_value
            or not asset.storage_path
            or (asset.duration_ms or 0) <= 0
            or (host is not None and asset.voice_profile_id != host.id)
        ):
            valid = False
            continue
        if asset.input_snapshot and host is not None:
            valid = (
                valid
                and asset.input_snapshot.get("provider_voice") == host.provider_voice
                and asset.input_snapshot.get("rate") == host.rate
            )
        if storage_root is not None:
            try:
                path = audio_file(storage_root, asset.storage_path)
                valid = valid and path.is_file() and path.stat().st_size > 0
            except (OSError, ValueError, AuthError):
                valid = False
    if not valid:
        block(
            "HOST_AUDIO_NOT_READY",
            "主持音频尚未全部生成或与当前主持词不一致",
            "audio",
            "查看进度或重试失败片段",
        )
    elif expected and not (
        generation.review_status == "APPROVED"
        if generation is not None
        else rule.audio_reviewed_at is not None
    ):
        block(
            "HOST_AUDIO_REVIEW_REQUIRED",
            "主持音频已全部生成，请试听并批准",
            "audio",
            "试听并批准当前批次",
        )
    judge = await session.get(RuleJudgeConfig, rule.id)
    if judge is not None and judge.enabled:
        judge_model = (
            await session.get(ModelProfile, judge.model_profile_id)
            if judge.model_profile_id
            else None
        )
        if judge_model is None or judge_model.status != "ENABLED" or not judge.judge_prompt.strip():
            block("JUDGE_UNAVAILABLE", "启用的裁判模型或 Prompt 不可用", "judge", "修复裁判配置")
    return Readiness(blockers, available_count, len(draft["stages"]))


async def eligible_agent_ids(session: AsyncSession, rule_id: UUID) -> list[UUID]:
    """Return only Agents with usable resources and every required Prompt slot."""
    agents = list(
        (
            await session.scalars(
                select(AgentProfile)
                .join(ModelProfile)
                .join(VoiceProfile)
                .where(
                    AgentProfile.rule_id == rule_id,
                    AgentProfile.status == "ENABLED",
                    ModelProfile.status == "ENABLED",
                    VoiceProfile.status == "ENABLED",
                    VoiceProfile.kind == "AGENT",
                    VoiceProfile.provider_voice.not_in(
                        (CONFERENCE_HOST_VOICE, "longyixuanjun_v3.1")
                    ),
                )
            )
        ).all()
    )
    stages = list(
        (await session.scalars(select(RuleStage).where(RuleStage.rule_id == rule_id))).all()
    )
    required = {
        (stage.id, purpose)
        for stage in stages
        for purpose in (
            ("SPEECH", "DECISION")
            if stage.stage_kind == "FREE_DEBATE"
            else ("SPEECH",)
            if stage.stage_kind == "FIXED_SPEECH"
            else ()
        )
    }
    defaults = {
        (p.stage_id, p.purpose)
        for p in (
            await session.scalars(
                select(StagePromptTemplate).where(
                    StagePromptTemplate.stage_id.in_([s.id for s in stages])
                )
            )
        ).all()
        if p.template_text.strip()
    }
    overrides = list(
        (
            await session.scalars(
                select(AgentPromptOverride).where(
                    AgentPromptOverride.agent_profile_id.in_([a.id for a in agents])
                )
            )
        ).all()
    )
    return [
        a.id
        for a in agents
        if required.issubset(
            defaults
            | {
                (p.stage_id, p.purpose)
                for p in overrides
                if p.agent_profile_id == a.id and p.template_text.strip()
            }
        )
    ]

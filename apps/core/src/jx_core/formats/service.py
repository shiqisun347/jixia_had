from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit.service import AuditService
from ..auth.errors import AuthError
from ..models import (
    AgentProfile,
    FormatJudgeProfile,
    FormatModule,
    FormatTopicReference,
    FormatVersion,
    ModelProfile,
    PromptTemplate,
    Rule,
    Topic,
    VoiceProfile,
)
from .schemas import (
    FormatAgentPayload,
    FormatDraftCreate,
    FormatDraftUpdate,
    FormatJudgePayload,
    FormatModulePayload,
    FormatPublishRequest,
    FormatTopicPayload,
    PromptTemplatePayload,
)


def build_published_snapshot(
    *,
    version: FormatVersion,
    agents: list[AgentProfile],
    models: list[ModelProfile],
    voices: list[VoiceProfile],
    prompts: list[PromptTemplate],
    modules: list[FormatModule],
    judge: FormatJudgeProfile | None,
    topic_references: list[FormatTopicReference],
    published_at: datetime,
) -> dict[str, object]:
    model_by_id = {model.id: model for model in models}
    voice_by_id = {voice.id: voice for voice in voices}
    return {
        "format_version_id": str(version.id),
        "format_key": version.format_key,
        "version": version.version,
        "rule_id": str(version.rule_id),
        "agents": [
            {
                "id": str(agent.id),
                "name": agent.name,
                "model": {
                    "id": str(model_by_id[agent.model_profile_id].id),
                    "name": model_by_id[agent.model_profile_id].name,
                    "config_ref": model_by_id[agent.model_profile_id].config_ref,
                    "base_url": model_by_id[agent.model_profile_id].base_url,
                    "model_id": model_by_id[agent.model_profile_id].model_id,
                    "max_concurrency": model_by_id[agent.model_profile_id].max_concurrency,
                    "token_per_char": model_by_id[agent.model_profile_id].token_per_char,
                    "generation_params": deepcopy(
                        model_by_id[agent.model_profile_id].generation_params
                    ),
                },
                "voice": {
                    "id": str(voice_by_id[agent.voice_profile_id].id),
                    "name": voice_by_id[agent.voice_profile_id].name,
                    "provider_voice": voice_by_id[agent.voice_profile_id].provider_voice,
                    "rate": voice_by_id[agent.voice_profile_id].rate,
                    "chars_per_second": voice_by_id[agent.voice_profile_id].chars_per_second,
                    "playback_gain": voice_by_id[agent.voice_profile_id].playback_gain,
                    "avatar_key": voice_by_id[agent.voice_profile_id].avatar_key,
                },
                "generation_params": deepcopy(agent.generation_params),
            }
            for agent in agents
        ],
        "prompts": [
            {
                "agent_profile_id": (
                    str(prompt.agent_profile_id) if prompt.agent_profile_id is not None else None
                ),
                "stage_key": prompt.stage_key,
                "purpose": prompt.purpose,
                "template_text": prompt.template_text,
                "variables": deepcopy(prompt.variables),
                "output_contract": prompt.output_contract,
            }
            for prompt in prompts
        ],
        "modules": [
            {
                "module_key": module.module_key,
                "enabled": module.enabled,
                "config": deepcopy(module.config),
            }
            for module in modules
        ],
        "judge": (
            {
                "model": {
                    "id": str(model_by_id[judge.model_profile_id].id),
                    "name": model_by_id[judge.model_profile_id].name,
                    "config_ref": model_by_id[judge.model_profile_id].config_ref,
                    "base_url": model_by_id[judge.model_profile_id].base_url,
                    "model_id": model_by_id[judge.model_profile_id].model_id,
                    "max_concurrency": model_by_id[judge.model_profile_id].max_concurrency,
                    "generation_params": deepcopy(
                        model_by_id[judge.model_profile_id].generation_params
                    ),
                },
                "system_prompt": judge.system_prompt,
                "judge_prompt": judge.judge_prompt,
                "generation_params": deepcopy(judge.generation_params),
                "dimensions": deepcopy(judge.dimensions),
                "output_contract": judge.output_contract,
            }
            if judge is not None
            else None
        ),
        "topic_references": [
            {
                "position": reference.position,
                "topic_id": str(reference.topic_id) if reference.topic_id is not None else None,
                "private_snapshot": deepcopy(reference.private_snapshot),
            }
            for reference in topic_references
        ],
        "published_at": published_at.isoformat(),
    }


class FormatService:
    async def _editable_version(self, session: AsyncSession, version_id: UUID) -> FormatVersion:
        version = await session.get(FormatVersion, version_id)
        if version is None or version.status != "DRAFT":
            raise AuthError("format_immutable")
        return version

    async def list_versions(self, session: AsyncSession) -> list[FormatVersion]:
        return list(
            (
                await session.scalars(
                    select(FormatVersion).order_by(
                        FormatVersion.format_key, FormatVersion.version.desc()
                    )
                )
            ).all()
        )

    async def create_draft(
        self, session: AsyncSession, *, payload: FormatDraftCreate, actor_user_id: UUID
    ) -> FormatVersion:
        async with session.begin():
            existing = await session.scalar(
                select(FormatVersion.id).where(
                    FormatVersion.format_key == payload.format_key,
                    FormatVersion.status == "DRAFT",
                )
            )
            if existing is not None:
                raise AuthError("format_draft_exists")
            current = int(
                await session.scalar(
                    select(FormatVersion.version)
                    .where(FormatVersion.format_key == payload.format_key)
                    .order_by(FormatVersion.version.desc())
                    .limit(1)
                )
                or 0
            )
            rule = await session.get(Rule, payload.rule_id)
            if rule is None or rule.status not in {"READY", "ENABLED"}:
                raise AuthError("rule_not_ready")
            version = FormatVersion(
                format_key=payload.format_key,
                version=current + 1,
                name=payload.name,
                description=payload.description,
                rule_id=payload.rule_id,
                created_by=actor_user_id,
            )
            session.add(version)
            await session.flush()
            for module_key in (
                "ROOMS",
                "BATCHES",
                "ANNOTATIONS",
                "QUESTIONNAIRE",
                "JUDGE",
                "LEADERBOARD",
            ):
                session.add(FormatModule(format_version_id=version.id, module_key=module_key))
            AuditService().record(
                session,
                actor_user_id=actor_user_id,
                action="admin.format.draft_created",
                target_type="format_version",
                target_id=str(version.id),
            )
        return version

    async def clone_published(
        self, session: AsyncSession, *, format_key: str, actor_user_id: UUID
    ) -> FormatVersion:
        async with session.begin():
            published = await session.scalar(
                select(FormatVersion)
                .where(
                    FormatVersion.format_key == format_key,
                    FormatVersion.status == "PUBLISHED",
                )
                .order_by(FormatVersion.version.desc())
                .with_for_update()
            )
            if published is None:
                raise AuthError("format_not_found")
            draft = await session.scalar(
                select(FormatVersion.id).where(
                    FormatVersion.format_key == format_key,
                    FormatVersion.status == "DRAFT",
                )
            )
            if draft is not None:
                raise AuthError("format_draft_exists")
            version = FormatVersion(
                format_key=format_key,
                version=published.version + 1,
                name=published.name,
                description=published.description,
                rule_id=published.rule_id,
                source_version_id=published.id,
                created_by=actor_user_id,
            )
            session.add(version)
            await session.flush()
            agents = list(
                (
                    await session.scalars(
                        select(AgentProfile).where(AgentProfile.format_version_id == published.id)
                    )
                ).all()
            )
            agent_id_map: dict[UUID, UUID] = {}
            for agent in agents:
                cloned_id = uuid4()
                agent_id_map[agent.id] = cloned_id
                session.add(
                    AgentProfile(
                        id=cloned_id,
                        name=agent.name,
                        format_version_id=version.id,
                        model_profile_id=agent.model_profile_id,
                        voice_profile_id=agent.voice_profile_id,
                        system_prompt=agent.system_prompt,
                        debater_prompt=agent.debater_prompt,
                        generation_params=deepcopy(agent.generation_params),
                    )
                )
            prompts = list(
                (
                    await session.scalars(
                        select(PromptTemplate).where(
                            PromptTemplate.format_version_id == published.id
                        )
                    )
                ).all()
            )
            for prompt in prompts:
                session.add(
                    PromptTemplate(
                        format_version_id=version.id,
                        agent_profile_id=(
                            agent_id_map[prompt.agent_profile_id]
                            if prompt.agent_profile_id is not None
                            else None
                        ),
                        stage_key=prompt.stage_key,
                        purpose=prompt.purpose,
                        template_text=prompt.template_text,
                        variables=deepcopy(prompt.variables),
                        output_contract=prompt.output_contract,
                    )
                )
            modules = list(
                (
                    await session.scalars(
                        select(FormatModule).where(FormatModule.format_version_id == published.id)
                    )
                ).all()
            )
            for module in modules:
                session.add(
                    FormatModule(
                        format_version_id=version.id,
                        module_key=module.module_key,
                        enabled=module.enabled,
                        config=deepcopy(module.config),
                    )
                )
            judge = await session.scalar(
                select(FormatJudgeProfile).where(
                    FormatJudgeProfile.format_version_id == published.id
                )
            )
            if judge is not None:
                session.add(
                    FormatJudgeProfile(
                        format_version_id=version.id,
                        model_profile_id=judge.model_profile_id,
                        system_prompt=judge.system_prompt,
                        judge_prompt=judge.judge_prompt,
                        generation_params=deepcopy(judge.generation_params),
                        dimensions=deepcopy(judge.dimensions),
                        output_contract=judge.output_contract,
                    )
                )
            refs = list(
                (
                    await session.scalars(
                        select(FormatTopicReference).where(
                            FormatTopicReference.format_version_id == published.id
                        )
                    )
                ).all()
            )
            for ref in refs:
                session.add(
                    FormatTopicReference(
                        format_version_id=version.id,
                        position=ref.position,
                        topic_id=ref.topic_id,
                        private_snapshot=deepcopy(ref.private_snapshot),
                    )
                )
            AuditService().record(
                session,
                actor_user_id=actor_user_id,
                action="admin.format.draft_cloned",
                target_type="format_version",
                target_id=str(version.id),
                details={"source_version_id": str(published.id)},
            )
        return version

    async def update_draft(
        self,
        session: AsyncSession,
        *,
        version_id: UUID,
        payload: FormatDraftUpdate,
        actor_user_id: UUID,
    ) -> FormatVersion:
        async with session.begin():
            version = await session.get(FormatVersion, version_id, with_for_update=True)
            if version is None:
                raise AuthError("format_not_found")
            if version.status != "DRAFT":
                raise AuthError("format_immutable")
            if version.revision != payload.revision:
                raise AuthError("format_revision_conflict")
            rule = await session.get(Rule, payload.rule_id)
            if rule is None or rule.status not in {"READY", "ENABLED"}:
                raise AuthError("rule_not_ready")
            version.name = payload.name
            version.description = payload.description
            version.rule_id = payload.rule_id
            version.change_note = payload.change_note
            version.revision += 1
            AuditService().record(
                session,
                actor_user_id=actor_user_id,
                action="admin.format.draft_updated",
                target_type="format_version",
                target_id=str(version_id),
                details={"revision": version.revision},
            )
        return version

    async def publish(
        self,
        session: AsyncSession,
        *,
        version_id: UUID,
        payload: FormatPublishRequest,
        actor_user_id: UUID,
    ) -> FormatVersion:
        async with session.begin():
            version = await session.get(FormatVersion, version_id, with_for_update=True)
            if version is None:
                raise AuthError("format_not_found")
            if version.status != "DRAFT":
                raise AuthError("format_immutable")
            if version.revision != payload.revision:
                raise AuthError("format_revision_conflict")
            rule = await session.get(Rule, version.rule_id)
            if (
                rule is None
                or rule.status not in {"READY", "ENABLED"}
                or rule.audio_reviewed_at is None
            ):
                raise AuthError("format_publish_dependencies_missing")
            agents = list(
                (
                    await session.scalars(
                        select(AgentProfile).where(AgentProfile.format_version_id == version.id)
                    )
                ).all()
            )
            if not agents:
                raise AuthError("format_agents_missing")
            model_ids = {agent.model_profile_id for agent in agents}
            judge = await session.scalar(
                select(FormatJudgeProfile).where(FormatJudgeProfile.format_version_id == version.id)
            )
            if judge is not None:
                model_ids.add(judge.model_profile_id)
            voice_ids = {agent.voice_profile_id for agent in agents}
            models = list(
                (
                    await session.scalars(
                        select(ModelProfile).where(ModelProfile.id.in_(model_ids))
                    )
                ).all()
            )
            voices = list(
                (
                    await session.scalars(
                        select(VoiceProfile).where(VoiceProfile.id.in_(voice_ids))
                    )
                ).all()
            )
            if len(models) != len(model_ids) or any(model.status != "ENABLED" for model in models):
                raise AuthError("format_publish_dependencies_missing")
            if len(voices) != len(voice_ids) or any(voice.status != "ENABLED" for voice in voices):
                raise AuthError("format_publish_dependencies_missing")
            prompts = list(
                (
                    await session.scalars(
                        select(PromptTemplate).where(PromptTemplate.format_version_id == version.id)
                    )
                ).all()
            )
            for prompt in prompts:
                if any(variable not in prompt.template_text for variable in prompt.variables):
                    raise AuthError("format_prompt_variable_missing")
            for stage_key, purpose in (
                ("FREE_DEBATE", "DECISION"),
                ("FREE_DEBATE", "SPEECH"),
            ):
                slot_prompts = [
                    prompt
                    for prompt in prompts
                    if prompt.stage_key == stage_key and prompt.purpose == purpose
                ]
                has_default = any(prompt.agent_profile_id is None for prompt in slot_prompts)
                covered_agent_ids = {
                    prompt.agent_profile_id
                    for prompt in slot_prompts
                    if prompt.agent_profile_id is not None
                }
                if not has_default and covered_agent_ids != {agent.id for agent in agents}:
                    raise AuthError("format_prompt_missing")
            modules = list(
                (
                    await session.scalars(
                        select(FormatModule).where(FormatModule.format_version_id == version.id)
                    )
                ).all()
            )
            if (
                any(module.module_key == "JUDGE" and module.enabled for module in modules)
                and judge is None
            ):
                raise AuthError("format_publish_dependencies_missing")
            topic_references = list(
                (
                    await session.scalars(
                        select(FormatTopicReference).where(
                            FormatTopicReference.format_version_id == version.id
                        )
                    )
                ).all()
            )
            previous = await session.scalar(
                select(FormatVersion)
                .where(
                    FormatVersion.format_key == version.format_key,
                    FormatVersion.status == "PUBLISHED",
                )
                .with_for_update()
            )
            if previous is not None:
                previous.status = "ARCHIVED"
            version.status = "PUBLISHED"
            version.change_note = payload.change_note
            version.published_snapshot = build_published_snapshot(
                version=version,
                agents=agents,
                models=models,
                voices=voices,
                prompts=prompts,
                modules=modules,
                judge=judge,
                topic_references=topic_references,
                published_at=datetime.now(UTC),
            )
            version.revision += 1
            AuditService().record(
                session,
                actor_user_id=actor_user_id,
                action="admin.format.published",
                target_type="format_version",
                target_id=str(version.id),
                details={"change_note": payload.change_note},
            )
        return version

    async def save_prompt(
        self,
        session: AsyncSession,
        *,
        version_id: UUID,
        agent_profile_id: UUID | None,
        payload: PromptTemplatePayload,
        actor_user_id: UUID,
    ) -> PromptTemplate:
        async with session.begin():
            version = await session.get(FormatVersion, version_id)
            if version is None or version.status != "DRAFT":
                raise AuthError("format_immutable")
            if agent_profile_id is not None:
                agent = await session.get(AgentProfile, agent_profile_id)
                if agent is None or agent.format_version_id != version_id:
                    raise AuthError("format_agent_not_found")
            prompt = await session.scalar(
                select(PromptTemplate)
                .where(
                    PromptTemplate.format_version_id == version_id,
                    PromptTemplate.agent_profile_id == agent_profile_id,
                    PromptTemplate.stage_key == payload.stage_key,
                    PromptTemplate.purpose == payload.purpose,
                )
                .with_for_update()
            )
            if prompt is None:
                prompt = PromptTemplate(
                    format_version_id=version_id,
                    agent_profile_id=agent_profile_id,
                    **payload.model_dump(),
                )
                session.add(prompt)
            else:
                for key, value in payload.model_dump().items():
                    setattr(prompt, key, value)
            AuditService().record(
                session,
                actor_user_id=actor_user_id,
                action="admin.format.prompt_saved",
                target_type="prompt_template",
                target_id=str(prompt.id),
                details={"format_version_id": str(version_id)},
            )
        return prompt

    async def create_agent(
        self,
        session: AsyncSession,
        *,
        version_id: UUID,
        payload: FormatAgentPayload,
        actor_user_id: UUID,
    ) -> AgentProfile:
        async with session.begin():
            await self._editable_version(session, version_id)
            await self._validate_agent_payload(session, version_id=version_id, payload=payload)
            agent = AgentProfile(
                format_version_id=version_id,
                name=payload.name.strip(),
                model_profile_id=payload.model_profile_id,
                voice_profile_id=payload.voice_profile_id,
                generation_params=payload.generation_params,
            )
            session.add(agent)
            await session.flush()
            AuditService().record(
                session,
                actor_user_id=actor_user_id,
                action="admin.format.agent_created",
                target_type="agent_profile",
                target_id=str(agent.id),
                details={"format_version_id": str(version_id)},
            )
        return agent

    async def update_agent(
        self,
        session: AsyncSession,
        *,
        version_id: UUID,
        agent_id: UUID,
        payload: FormatAgentPayload,
        actor_user_id: UUID,
    ) -> AgentProfile:
        async with session.begin():
            await self._editable_version(session, version_id)
            agent = await session.get(AgentProfile, agent_id, with_for_update=True)
            if agent is None or agent.format_version_id != version_id:
                raise AuthError("format_agent_not_found")
            await self._validate_agent_payload(
                session, version_id=version_id, payload=payload, excluded_agent_id=agent_id
            )
            agent.name = payload.name.strip()
            agent.model_profile_id = payload.model_profile_id
            agent.voice_profile_id = payload.voice_profile_id
            agent.generation_params = payload.generation_params
            AuditService().record(
                session,
                actor_user_id=actor_user_id,
                action="admin.format.agent_updated",
                target_type="agent_profile",
                target_id=str(agent.id),
                details={"format_version_id": str(version_id)},
            )
        return agent

    async def delete_agent(
        self,
        session: AsyncSession,
        *,
        version_id: UUID,
        agent_id: UUID,
        actor_user_id: UUID,
    ) -> None:
        async with session.begin():
            await self._editable_version(session, version_id)
            agent = await session.get(AgentProfile, agent_id, with_for_update=True)
            if agent is None or agent.format_version_id != version_id:
                raise AuthError("format_agent_not_found")
            await session.delete(agent)
            AuditService().record(
                session,
                actor_user_id=actor_user_id,
                action="admin.format.agent_deleted",
                target_type="agent_profile",
                target_id=str(agent.id),
                details={"format_version_id": str(version_id)},
            )

    async def _validate_agent_payload(
        self,
        session: AsyncSession,
        *,
        version_id: UUID,
        payload: FormatAgentPayload,
        excluded_agent_id: UUID | None = None,
    ) -> None:
        model = await session.get(ModelProfile, payload.model_profile_id)
        voice = await session.get(VoiceProfile, payload.voice_profile_id)
        if model is None or model.status != "ENABLED":
            raise AuthError("model_profile_unavailable")
        if voice is None or voice.status != "ENABLED" or voice.kind != "AGENT":
            raise AuthError("voice_profile_unavailable")
        duplicate_query = select(AgentProfile.id).where(
            AgentProfile.format_version_id == version_id,
            AgentProfile.voice_profile_id == payload.voice_profile_id,
        )
        if excluded_agent_id is not None:
            duplicate_query = duplicate_query.where(AgentProfile.id != excluded_agent_id)
        if await session.scalar(duplicate_query) is not None:
            raise AuthError("format_agent_voice_duplicate")
        capabilities = model.capability_schema
        for key, value in payload.generation_params.items():
            capability = capabilities.get(key)
            if not isinstance(capability, dict):
                raise AuthError("format_agent_parameter_invalid")
            typed_capability = cast(dict[str, object], capability)
            minimum = typed_capability.get("minimum")
            maximum = typed_capability.get("maximum")
            if (
                not isinstance(value, int | float)
                or isinstance(value, bool)
                or not isinstance(minimum, int | float)
                or not isinstance(maximum, int | float)
                or not minimum <= value <= maximum
            ):
                raise AuthError("format_agent_parameter_invalid")

    async def save_module(
        self,
        session: AsyncSession,
        *,
        version_id: UUID,
        module_key: str,
        payload: FormatModulePayload,
        actor_user_id: UUID,
    ) -> FormatModule:
        async with session.begin():
            await self._editable_version(session, version_id)
            module = await session.scalar(
                select(FormatModule)
                .where(
                    FormatModule.format_version_id == version_id,
                    FormatModule.module_key == module_key,
                )
                .with_for_update()
            )
            if module is None:
                raise AuthError("format_module_not_found")
            module.enabled = payload.enabled
            module.config = payload.config
            AuditService().record(
                session,
                actor_user_id=actor_user_id,
                action="admin.format.module_updated",
                target_type="format_module",
                target_id=str(module.id),
                details={"enabled": payload.enabled},
            )
        return module

    async def save_judge(
        self,
        session: AsyncSession,
        *,
        version_id: UUID,
        payload: FormatJudgePayload,
        actor_user_id: UUID,
    ) -> FormatJudgeProfile:
        async with session.begin():
            await self._editable_version(session, version_id)
            model = await session.get(ModelProfile, payload.model_profile_id)
            if model is None or model.status != "ENABLED":
                raise AuthError("model_profile_unavailable")
            judge = await session.scalar(
                select(FormatJudgeProfile)
                .where(FormatJudgeProfile.format_version_id == version_id)
                .with_for_update()
            )
            if judge is None:
                judge = FormatJudgeProfile(format_version_id=version_id, **payload.model_dump())
                session.add(judge)
            else:
                for key, value in payload.model_dump().items():
                    setattr(judge, key, value)
            await session.flush()
            AuditService().record(
                session,
                actor_user_id=actor_user_id,
                action="admin.format.judge_saved",
                target_type="format_judge_profile",
                target_id=str(judge.id),
            )
        return judge

    async def save_topic_reference(
        self,
        session: AsyncSession,
        *,
        version_id: UUID,
        payload: FormatTopicPayload,
        actor_user_id: UUID,
    ) -> FormatTopicReference:
        async with session.begin():
            await self._editable_version(session, version_id)
            if payload.topic_id is not None:
                topic = await session.get(Topic, payload.topic_id)
                if topic is None or topic.status != "ENABLED":
                    raise AuthError("topic_unavailable")
            reference = await session.scalar(
                select(FormatTopicReference)
                .where(
                    FormatTopicReference.format_version_id == version_id,
                    FormatTopicReference.position == payload.position,
                )
                .with_for_update()
            )
            if reference is None:
                reference = FormatTopicReference(
                    format_version_id=version_id, **payload.model_dump()
                )
                session.add(reference)
            else:
                reference.topic_id = payload.topic_id
                reference.private_snapshot = payload.private_snapshot
            await session.flush()
            AuditService().record(
                session,
                actor_user_id=actor_user_id,
                action="admin.format.topic_saved",
                target_type="format_topic_reference",
                target_id=str(reference.id),
            )
        return reference

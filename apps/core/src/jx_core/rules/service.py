"""Transactional catalog and finite rule services for the 004 slice."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit.service import AuditService
from ..auth.errors import AuthError
from ..models import (
    AgentProfile,
    AgentPromptOverride,
    BackgroundTask,
    ExperimentBatch,
    FormatVersion,
    HostAudioAsset,
    JudgeProfile,
    Match,
    MatchParticipant,
    ModelProfile,
    Room,
    Rule,
    RuleJudgeConfig,
    RuleStage,
    Seat,
    StageAction,
    StagePromptTemplate,
    Topic,
    VoiceProfile,
)
from ..security.crypto import encrypt_secret
from .prompts import (
    DEFAULT_FIXED_SPEECH_PROMPT,
    DEFAULT_FREE_DECISION_PROMPT,
    DEFAULT_FREE_SPEECH_PROMPT,
    FIXED_SPEECH_VARIABLES,
    FREE_DECISION_VARIABLES,
    FREE_SPEECH_VARIABLES,
    PromptTemplateError,
    validate_prompt_template,
)
from .schemas import (
    AgentProfileCreate,
    AgentProfileUpdate,
    ModelProfileCreate,
    ModelProfileUpdate,
    RuleAgentUpdate,
    RuleBasicUpdate,
    RuleCreate,
    RuleJudgeUpdate,
    RulePromptUpdate,
    RuleStageUpdate,
    TopicCreate,
    TopicUpdate,
    VoiceProfileCreate,
    VoiceProfileUpdate,
)
from .validation import RuleValidationError, validate_rule_draft


def _stable_key(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


class CatalogService:
    _ACTIVE_MATCH_STATUSES = {
        "START_PENDING_RUNTIME",
        "START_COUNTDOWN",
        "RUNNING",
        "PAUSED",
        "SYSTEM_RECOVERY",
    }

    async def status_change_block(
        self,
        database_session: AsyncSession,
        *,
        kind: str,
        item_id: UUID,
        status: str,
    ) -> str | None:
        if status != "DISABLED":
            return None
        if kind == "models":
            judge_reference = await database_session.scalar(
                select(JudgeProfile.id)
                .where(
                    JudgeProfile.model_profile_id == item_id,
                    JudgeProfile.status == "ENABLED",
                )
                .limit(1)
            )
            agent_reference = await database_session.scalar(
                select(Match.id)
                .join(Room, Room.id == Match.room_id)
                .join(Seat, Seat.room_id == Room.id)
                .join(
                    AgentProfile,
                    or_(
                        AgentProfile.id == Seat.agent_profile_id,
                        AgentProfile.id == Seat.configured_agent_profile_id,
                    ),
                )
                .where(
                    Match.status.in_(self._ACTIVE_MATCH_STATUSES),
                    AgentProfile.model_profile_id == item_id,
                )
                .limit(1)
            )
            return "model_in_use" if judge_reference or agent_reference else None
        if kind == "voices":
            reference = await database_session.scalar(
                select(Match.id)
                .join(Room, Room.id == Match.room_id)
                .join(Seat, Seat.room_id == Room.id)
                .join(
                    AgentProfile,
                    or_(
                        AgentProfile.id == Seat.agent_profile_id,
                        AgentProfile.id == Seat.configured_agent_profile_id,
                    ),
                )
                .where(
                    Match.status.in_(self._ACTIVE_MATCH_STATUSES),
                    AgentProfile.voice_profile_id == item_id,
                )
                .limit(1)
            )
            return "voice_in_use" if reference else None
        if kind == "agents":
            reference = await database_session.scalar(
                select(Match.id)
                .join(Room, Room.id == Match.room_id)
                .join(Seat, Seat.room_id == Room.id)
                .where(
                    Match.status.in_(self._ACTIVE_MATCH_STATUSES),
                    or_(
                        Seat.agent_profile_id == item_id,
                        Seat.configured_agent_profile_id == item_id,
                    ),
                )
                .limit(1)
            )
            return "agent_in_use" if reference else None
        return None

    async def _validate_agent_dependencies(
        self,
        database_session: AsyncSession,
        *,
        model_profile_id: UUID,
        voice_profile_id: UUID,
    ) -> VoiceProfile:
        model = await database_session.get(ModelProfile, model_profile_id)
        voice = await database_session.get(VoiceProfile, voice_profile_id)
        if model is None or model.status != "ENABLED":
            raise AuthError("model_profile_unavailable")
        if voice is None or voice.status != "ENABLED" or voice.kind != "AGENT":
            raise AuthError("voice_profile_unavailable")
        return voice

    async def create_voice(
        self,
        database_session: AsyncSession,
        *,
        payload: VoiceProfileCreate,
        actor_user_id: UUID | None = None,
    ) -> VoiceProfile:
        async with database_session.begin():
            if payload.kind == "HOST":
                existing = await database_session.scalar(
                    select(VoiceProfile.id).where(
                        VoiceProfile.kind == "HOST", VoiceProfile.status == "ENABLED"
                    )
                )
                if existing is not None:
                    raise AuthError("host_voice_already_configured")
            voice = VoiceProfile(**payload.model_dump())
            database_session.add(voice)
            await database_session.flush()
            if voice.kind == "AGENT" and voice.status == "ENABLED":
                rules = list(
                    (
                        await database_session.scalars(
                            select(Rule).where(
                                Rule.side_size == 4,
                                Rule.historical_read_only.is_(False),
                                Rule.default_agent_model_profile_id.is_not(None),
                                Rule.status != "ARCHIVED",
                            )
                        )
                    ).all()
                )
                for rule in rules:
                    assert rule.default_agent_model_profile_id is not None
                    database_session.add(
                        AgentProfile(
                            rule_id=rule.id,
                            name=voice.name,
                            model_profile_id=rule.default_agent_model_profile_id,
                            voice_profile_id=voice.id,
                            status="ENABLED",
                        )
                    )
                    rule.config_revision += 1
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.voice.created",
                target_type="voice_profile",
                target_id=str(voice.id),
            )
        return voice

    async def create_model(
        self,
        database_session: AsyncSession,
        *,
        payload: ModelProfileCreate,
        master_key: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> ModelProfile:
        values = payload.model_dump(exclude={"api_key"})
        if payload.api_key is not None:
            if master_key is None:
                raise AuthError("model_encryption_not_configured")
            ciphertext, nonce, last4 = encrypt_secret(
                payload.api_key.get_secret_value(), master_key
            )
            values.update(
                api_key_ciphertext=ciphertext,
                api_key_nonce=nonce,
                api_key_last4=last4,
            )
        async with database_session.begin():
            model = ModelProfile(**values)
            database_session.add(model)
            await database_session.flush()
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.model.created",
                target_type="model_profile",
                target_id=str(model.id),
                details={"api_key_last4": model.api_key_last4},
            )
        return model

    async def update_model(
        self,
        database_session: AsyncSession,
        *,
        model_id: UUID,
        payload: ModelProfileUpdate,
        actor_user_id: UUID | None = None,
    ) -> ModelProfile:
        values = payload.model_dump()
        async with database_session.begin():
            model = await database_session.get(ModelProfile, model_id, with_for_update=True)
            if model is None:
                raise AuthError("catalog_item_not_found")
            active_reference = await database_session.scalar(
                select(Match.id)
                .join(Room, Room.id == Match.room_id)
                .join(Seat, Seat.room_id == Room.id)
                .join(
                    AgentProfile,
                    or_(
                        AgentProfile.id == Seat.agent_profile_id,
                        AgentProfile.id == Seat.configured_agent_profile_id,
                    ),
                )
                .where(
                    Match.status.in_(self._ACTIVE_MATCH_STATUSES),
                    AgentProfile.model_profile_id == model_id,
                )
                .limit(1)
            )
            if active_reference is not None:
                raise AuthError("model_in_use")
            for field, value in values.items():
                setattr(model, field, value)
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.model.updated",
                target_type="model_profile",
                target_id=str(model_id),
            )
        return model

    async def rotate_model_api_key(
        self,
        database_session: AsyncSession,
        *,
        model_id: UUID,
        api_key: str,
        master_key: str | None,
        actor_user_id: UUID | None = None,
    ) -> ModelProfile:
        if master_key is None:
            raise AuthError("model_encryption_not_configured")
        ciphertext, nonce, last4 = encrypt_secret(api_key, master_key)
        async with database_session.begin():
            model = await database_session.get(ModelProfile, model_id, with_for_update=True)
            if model is None:
                raise AuthError("catalog_item_not_found")
            model.api_key_ciphertext = ciphertext
            model.api_key_nonce = nonce
            model.api_key_last4 = last4
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.model.api_key_rotated",
                target_type="model_profile",
                target_id=str(model_id),
                details={"api_key_last4": last4},
            )
        return model

    async def create_agent(
        self,
        database_session: AsyncSession,
        *,
        payload: AgentProfileCreate,
        actor_user_id: UUID | None = None,
    ) -> AgentProfile:
        duplicate_name = False
        agent: AgentProfile | None = None
        async with database_session.begin():
            await self._validate_agent_dependencies(
                database_session,
                model_profile_id=payload.model_profile_id,
                voice_profile_id=payload.voice_profile_id,
            )
            existing = await database_session.scalar(
                select(AgentProfile.id).where(AgentProfile.name == payload.name)
            )
            if existing is not None:
                duplicate_name = True
            else:
                agent = AgentProfile(**payload.model_dump())
                try:
                    async with database_session.begin_nested():
                        database_session.add(agent)
                        await database_session.flush()
                except IntegrityError:
                    duplicate_name = True
                else:
                    AuditService().record(
                        database_session,
                        actor_user_id=actor_user_id,
                        action="admin.agent.created",
                        target_type="agent_profile",
                        target_id=str(agent.id),
                    )
        if duplicate_name or agent is None:
            raise AuthError("agent_name_taken")
        return agent

    async def update_agent(
        self,
        database_session: AsyncSession,
        *,
        agent_id: UUID,
        payload: AgentProfileUpdate,
        actor_user_id: UUID | None = None,
    ) -> AgentProfile:
        duplicate_name = False
        async with database_session.begin():
            agent = await database_session.get(AgentProfile, agent_id, with_for_update=True)
            if agent is None:
                raise AuthError("catalog_item_not_found")
            active_reference = await database_session.scalar(
                select(Match.id)
                .join(Room, Room.id == Match.room_id)
                .join(Seat, Seat.room_id == Room.id)
                .where(
                    Match.status.in_(
                        {
                            "START_PENDING_RUNTIME",
                            "START_COUNTDOWN",
                            "RUNNING",
                            "PAUSED",
                            "SYSTEM_RECOVERY",
                        }
                    ),
                    or_(
                        Seat.agent_profile_id == agent_id,
                        Seat.configured_agent_profile_id == agent_id,
                    ),
                )
                .limit(1)
            )
            if active_reference is not None:
                raise AuthError("agent_in_use")
            duplicate = await database_session.scalar(
                select(AgentProfile.id).where(
                    AgentProfile.name == payload.name,
                    AgentProfile.id != agent_id,
                )
            )
            if duplicate is not None:
                duplicate_name = True
            else:
                await self._validate_agent_dependencies(
                    database_session,
                    model_profile_id=payload.model_profile_id,
                    voice_profile_id=payload.voice_profile_id,
                )
                values = payload.model_dump()
                for field, value in values.items():
                    setattr(agent, field, value)
                try:
                    async with database_session.begin_nested():
                        await database_session.flush()
                except IntegrityError:
                    duplicate_name = True
                else:
                    AuditService().record(
                        database_session,
                        actor_user_id=actor_user_id,
                        action="admin.agent.updated",
                        target_type="agent_profile",
                        target_id=str(agent.id),
                    )
        if duplicate_name:
            raise AuthError("agent_name_taken")
        return agent

    async def create_topic(
        self,
        database_session: AsyncSession,
        *,
        creator_user_id: UUID,
        payload: TopicCreate,
    ) -> Topic:
        async with database_session.begin():
            topic = Topic(
                topic_key=_stable_key("topic"),
                version=1,
                created_by=creator_user_id,
                **payload.model_dump(),
            )
            database_session.add(topic)
            await database_session.flush()
            AuditService().record(
                database_session,
                actor_user_id=creator_user_id,
                action="admin.topic.created",
                target_type="topic",
                target_id=str(topic.id),
            )
        return topic

    async def update_voice(
        self,
        database_session: AsyncSession,
        *,
        voice_id: UUID,
        payload: VoiceProfileUpdate,
        actor_user_id: UUID | None = None,
    ) -> VoiceProfile:
        async with database_session.begin():
            voice = await database_session.get(VoiceProfile, voice_id, with_for_update=True)
            if voice is None:
                raise AuthError("catalog_item_not_found")
            active_reference = await database_session.scalar(
                select(Match.id)
                .join(Room, Room.id == Match.room_id)
                .join(Seat, Seat.room_id == Room.id)
                .join(
                    AgentProfile,
                    or_(
                        AgentProfile.id == Seat.agent_profile_id,
                        AgentProfile.id == Seat.configured_agent_profile_id,
                    ),
                )
                .where(
                    Match.status.in_(self._ACTIVE_MATCH_STATUSES),
                    AgentProfile.voice_profile_id == voice_id,
                )
                .limit(1)
            )
            if active_reference is not None:
                raise AuthError("voice_in_use")
            if payload.kind == "HOST" and voice.kind != "HOST":
                raise AuthError("voice_kind_immutable")
            values = payload.model_dump(exclude_unset=True)
            if any(
                values.get(field) != getattr(voice, field)
                for field in ("provider_voice", "rate", "chars_per_second", "playback_gain")
                if field in values
            ):
                voice.calibration_status = "STALE"
                voice.calibrated_at = None
            for field, value in values.items():
                setattr(voice, field, value)
            if voice.kind == "AGENT":
                agents = list(
                    (
                        await database_session.scalars(
                            select(AgentProfile)
                            .where(AgentProfile.voice_profile_id == voice_id)
                            .where(AgentProfile.rule_id.is_not(None))
                            .with_for_update()
                        )
                    ).all()
                )
                for agent in agents:
                    agent.name = voice.name
                    agent.status = voice.status
                rule_ids = {agent.rule_id for agent in agents if agent.rule_id is not None}
                if rule_ids:
                    rules = list(
                        (
                            await database_session.scalars(
                                select(Rule).where(Rule.id.in_(rule_ids)).with_for_update()
                            )
                        ).all()
                    )
                    for rule in rules:
                        rule.config_revision += 1
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.voice.updated",
                target_type="voice_profile",
                target_id=str(voice_id),
            )
        return voice

    async def delete_voice(
        self,
        database_session: AsyncSession,
        *,
        voice_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> str:
        voice = await database_session.get(VoiceProfile, voice_id, with_for_update=True)
        if voice is None:
            raise AuthError("catalog_item_not_found")
        agents = list(
            (
                await database_session.scalars(
                    select(AgentProfile)
                    .where(AgentProfile.voice_profile_id == voice_id)
                    .with_for_update()
                )
            ).all()
        )
        agent_ids = [agent.id for agent in agents]
        historical_reference = None
        if agent_ids:
            historical_reference = await database_session.scalar(
                select(MatchParticipant.id)
                .where(MatchParticipant.agent_profile_id.in_(agent_ids))
                .limit(1)
            )
            if historical_reference is None:
                historical_reference = await database_session.scalar(
                    select(Seat.id)
                    .where(
                        or_(
                            Seat.agent_profile_id.in_(agent_ids),
                            Seat.configured_agent_profile_id.in_(agent_ids),
                        )
                    )
                    .limit(1)
                )
        rule_reference = await database_session.scalar(
            select(Rule.id).where(Rule.host_voice_profile_id == voice_id).limit(1)
        )
        if historical_reference is not None or rule_reference is not None:
            voice.status = "DISABLED"
            for agent in agents:
                agent.status = "DISABLED"
            outcome = "ARCHIVED"
        else:
            for agent in agents:
                await database_session.delete(agent)
            await database_session.delete(voice)
            outcome = "DELETED"
        rule_ids = {agent.rule_id for agent in agents if agent.rule_id is not None}
        if rule_ids:
            rules = list(
                (
                    await database_session.scalars(
                        select(Rule).where(Rule.id.in_(rule_ids)).with_for_update()
                    )
                ).all()
            )
            for rule in rules:
                rule.config_revision += 1
        AuditService().record(
            database_session,
            actor_user_id=actor_user_id,
            action="admin.voice.deleted" if outcome == "DELETED" else "admin.voice.archived",
            target_type="voice_profile",
            target_id=str(voice_id),
            details={"affected_agents": len(agents)},
        )
        return outcome

    async def update_topic(
        self,
        database_session: AsyncSession,
        *,
        topic_id: UUID,
        payload: TopicUpdate,
        actor_user_id: UUID | None = None,
    ) -> Topic:
        async with database_session.begin():
            topic = await database_session.get(Topic, topic_id, with_for_update=True)
            if topic is None:
                raise AuthError("catalog_item_not_found")
            active_reference = await database_session.scalar(
                select(Match.id)
                .join(Room, Room.id == Match.room_id)
                .where(Match.status.in_(self._ACTIVE_MATCH_STATUSES), Room.topic_id == topic_id)
                .limit(1)
            )
            if active_reference is not None:
                raise AuthError("topic_in_use")
            for field, value in payload.model_dump().items():
                setattr(topic, field, value)
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.topic.updated",
                target_type="topic",
                target_id=str(topic_id),
            )
        return topic

    async def list_catalog(
        self, database_session: AsyncSession
    ) -> tuple[list[VoiceProfile], list[ModelProfile], list[AgentProfile], list[Topic], list[Rule]]:
        voices = list(
            (await database_session.scalars(select(VoiceProfile).order_by(VoiceProfile.name))).all()
        )
        models = list(
            (await database_session.scalars(select(ModelProfile).order_by(ModelProfile.name))).all()
        )
        agents = list(
            (await database_session.scalars(select(AgentProfile).order_by(AgentProfile.name))).all()
        )
        topics = list(
            (await database_session.scalars(select(Topic).order_by(Topic.created_at.desc()))).all()
        )
        rules = list(
            (await database_session.scalars(select(Rule).order_by(Rule.created_at.desc()))).all()
        )
        return voices, models, agents, topics, rules


class RuleService:
    @staticmethod
    def _validated_prompt(
        template_text: str,
        *,
        required_variables: frozenset[str],
        field: str,
    ) -> list[str]:
        try:
            return sorted(
                validate_prompt_template(template_text, required_variables=required_variables)
            )
        except PromptTemplateError as error:
            raise AuthError("rule_invalid", {field: str(error)}) from error

    async def create_rule(
        self,
        database_session: AsyncSession,
        *,
        creator_user_id: UUID,
        payload: RuleCreate,
    ) -> Rule:
        try:
            snapshot = validate_rule_draft(payload.draft.model_dump(mode="json"))
        except RuleValidationError as error:
            raise AuthError("rule_invalid", {"draft": str(error)}) from error
        current = datetime.now(UTC)
        rule_key = payload.rule_key or _stable_key("rule")
        async with database_session.begin():
            host_voice = await database_session.get(VoiceProfile, payload.host_voice_profile_id)
            if host_voice is None or host_voice.status != "ENABLED" or host_voice.kind != "HOST":
                raise AuthError("host_voice_unavailable")
            default_model = None
            if payload.default_agent_model_profile_id is not None:
                default_model = await database_session.get(
                    ModelProfile, payload.default_agent_model_profile_id
                )
            if payload.draft.side_size == 4 and (
                default_model is None or default_model.status != "ENABLED"
            ):
                raise AuthError(
                    "rule_invalid",
                    {"default_agent_model_profile_id": "请选择已启用的默认 Agent 模型"},
                )
            current_version = await database_session.scalar(
                select(func.coalesce(func.max(Rule.version), 0)).where(Rule.rule_key == rule_key)
            )
            version = int(current_version or 0) + 1
            host_segments: list[tuple[str, str]] = []
            rule = Rule(
                rule_key=rule_key,
                version=version,
                name=payload.draft.name,
                description=payload.draft.description,
                side_size=payload.draft.side_size,
                estimated_seconds=int(snapshot["estimated_seconds"]),
                status="DRAFT",
                host_voice_profile_id=host_voice.id,
                default_agent_model_profile_id=(
                    default_model.id if default_model is not None else None
                ),
                topic_policy=payload.topic_policy,
                historical_read_only=payload.draft.side_size != 4,
                created_by=creator_user_id,
            )
            database_session.add(rule)
            await database_session.flush()
            for stage_position, stage_draft in enumerate(payload.draft.stages, start=1):
                stage = RuleStage(
                    rule_id=rule.id,
                    position=stage_position,
                    name=stage_draft.name,
                    stage_kind=stage_draft.stage_kind,
                    duration_seconds=stage_draft.duration_seconds,
                    start_host_text=stage_draft.start_host_text,
                    end_host_text=stage_draft.end_host_text,
                    parameters=stage_draft.parameters,
                )
                database_session.add(stage)
                await database_session.flush()
                speech_prompt = stage_draft.speech_prompt
                decision_prompt = stage_draft.decision_prompt
                if payload.draft.side_size == 4 and stage_draft.stage_kind == "FIXED_SPEECH":
                    speech_prompt = speech_prompt or DEFAULT_FIXED_SPEECH_PROMPT
                if payload.draft.side_size == 4 and stage_draft.stage_kind == "FREE_DEBATE":
                    speech_prompt = speech_prompt or DEFAULT_FREE_SPEECH_PROMPT
                    decision_prompt = decision_prompt or DEFAULT_FREE_DECISION_PROMPT
                if speech_prompt is not None:
                    required = (
                        FREE_SPEECH_VARIABLES
                        if stage_draft.stage_kind == "FREE_DEBATE"
                        else FIXED_SPEECH_VARIABLES
                    )
                    variables = self._validated_prompt(
                        speech_prompt,
                        required_variables=required,
                        field=f"draft.stages.{stage_position - 1}.speech_prompt",
                    )
                    database_session.add(
                        StagePromptTemplate(
                            stage_id=stage.id,
                            purpose="SPEECH",
                            template_text=speech_prompt,
                            variables=variables,
                            output_contract="TEXT",
                        )
                    )
                if decision_prompt is not None:
                    variables = self._validated_prompt(
                        decision_prompt,
                        required_variables=FREE_DECISION_VARIABLES,
                        field=f"draft.stages.{stage_position - 1}.decision_prompt",
                    )
                    database_session.add(
                        StagePromptTemplate(
                            stage_id=stage.id,
                            purpose="DECISION",
                            template_text=decision_prompt,
                            variables=variables,
                            output_contract="SHOULD_SPEAK_V1",
                        )
                    )
                for action_position, action_draft in enumerate(stage_draft.actions, start=1):
                    database_session.add(
                        StageAction(
                            stage_id=stage.id,
                            position=action_position,
                            **action_draft.model_dump(),
                        )
                    )
                if stage_draft.start_host_text:
                    host_segments.append(
                        (f"stage-{stage_position}-start", stage_draft.start_host_text)
                    )
                if stage_draft.end_host_text:
                    host_segments.append((f"stage-{stage_position}-end", stage_draft.end_host_text))
            for segment_key, text_value in host_segments:
                asset = HostAudioAsset(
                    rule_id=rule.id,
                    segment_key=segment_key,
                    text=text_value,
                    text_hash=sha256(text_value.encode("utf-8")).hexdigest(),
                    voice_profile_id=host_voice.id,
                )
                database_session.add(asset)
                await database_session.flush()
                database_session.add(
                    BackgroundTask(
                        task_type="HOST_TTS",
                        payload={"asset_id": str(asset.id), "rule_id": str(rule.id)},
                        available_at=current,
                    )
                )
            rule.status = "GENERATING_AUDIO" if host_segments else "READY"
            if default_model is not None:
                agent_voices = list(
                    (
                        await database_session.scalars(
                            select(VoiceProfile)
                            .where(
                                VoiceProfile.kind == "AGENT",
                                VoiceProfile.status == "ENABLED",
                            )
                            .order_by(VoiceProfile.name, VoiceProfile.id)
                        )
                    ).all()
                )
                for voice in agent_voices:
                    database_session.add(
                        AgentProfile(
                            rule_id=rule.id,
                            name=voice.name,
                            model_profile_id=default_model.id,
                            voice_profile_id=voice.id,
                            status="ENABLED",
                        )
                    )
            database_session.add(RuleJudgeConfig(rule_id=rule.id))
            AuditService().record(
                database_session,
                actor_user_id=creator_user_id,
                action="admin.rule.created",
                target_type="rule",
                target_id=str(rule.id),
            )
            await database_session.flush()
        return rule

    async def update_rule_agent(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        agent_id: UUID,
        payload: RuleAgentUpdate,
        actor_user_id: UUID,
    ) -> AgentProfile:
        async with database_session.begin():
            agent = await database_session.get(AgentProfile, agent_id, with_for_update=True)
            if agent is None or agent.rule_id != rule_id:
                raise AuthError("catalog_item_not_found")
            model = await database_session.get(ModelProfile, payload.model_profile_id)
            if model is None or model.status != "ENABLED":
                raise AuthError("model_profile_unavailable")
            agent.model_profile_id = model.id
            agent.generation_params = dict(payload.generation_params)
            agent.status = payload.status
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            assert rule is not None
            rule.config_revision += 1
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule_agent.updated",
                target_type="agent_profile",
                target_id=str(agent.id),
                details={"rule_id": str(rule_id), "status": agent.status},
            )
        return agent

    async def update_rule_basic(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        payload: RuleBasicUpdate,
        actor_user_id: UUID,
    ) -> Rule:
        current = datetime.now(UTC)
        async with database_session.begin():
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            if rule is None:
                raise AuthError("rule_not_found")
            if rule.historical_read_only or rule.status == "ARCHIVED":
                raise AuthError("rule_invalid", {"rule_id": "历史规则不可编辑"})
            host_voice = await database_session.get(VoiceProfile, payload.host_voice_profile_id)
            if host_voice is None or host_voice.kind != "HOST" or host_voice.status != "ENABLED":
                raise AuthError("host_voice_unavailable")
            model = await database_session.get(ModelProfile, payload.default_agent_model_profile_id)
            if model is None or model.status != "ENABLED":
                raise AuthError("model_profile_unavailable")
            host_changed = rule.host_voice_profile_id != host_voice.id
            rule.name = payload.name.strip()
            rule.description = payload.description.strip()
            rule.host_voice_profile_id = host_voice.id
            rule.default_agent_model_profile_id = model.id
            rule.topic_policy = payload.topic_policy
            rule.postmatch_questionnaire_enabled = payload.postmatch_questionnaire_enabled
            rule.config_revision += 1
            if host_changed:
                assets = list(
                    (
                        await database_session.scalars(
                            select(HostAudioAsset)
                            .where(HostAudioAsset.rule_id == rule_id)
                            .with_for_update()
                        )
                    ).all()
                )
                old_tasks = list(
                    (
                        await database_session.scalars(
                            select(BackgroundTask).where(BackgroundTask.task_type == "HOST_TTS")
                        )
                    ).all()
                )
                for asset in assets:
                    for task in old_tasks:
                        if str(task.payload.get("asset_id", "")) == str(asset.id):
                            await database_session.delete(task)
                    segment_key, text_value = asset.segment_key, asset.text
                    await database_session.delete(asset)
                    replacement = HostAudioAsset(
                        rule_id=rule_id,
                        segment_key=segment_key,
                        text=text_value,
                        text_hash=sha256(text_value.encode("utf-8")).hexdigest(),
                        voice_profile_id=host_voice.id,
                    )
                    database_session.add(replacement)
                    await database_session.flush()
                    database_session.add(
                        BackgroundTask(
                            task_type="HOST_TTS",
                            payload={"asset_id": str(replacement.id), "rule_id": str(rule_id)},
                            available_at=current,
                        )
                    )
                rule.status = "GENERATING_AUDIO" if assets else "READY"
                rule.audio_reviewed_at = None if assets else current
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule.basic_updated",
                target_type="rule",
                target_id=str(rule_id),
            )
            await database_session.flush()
        return rule

    async def update_rule_stage(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        stage_id: UUID,
        payload: RuleStageUpdate,
        actor_user_id: UUID,
    ) -> RuleStage:
        current = datetime.now(UTC)
        async with database_session.begin():
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            stage = await database_session.get(RuleStage, stage_id, with_for_update=True)
            if rule is None or stage is None or stage.rule_id != rule_id:
                raise AuthError("rule_not_found")
            if rule.historical_read_only or rule.status == "ARCHIVED":
                raise AuthError("rule_invalid", {"rule_id": "历史规则不可编辑"})
            stages = list(
                (
                    await database_session.scalars(
                        select(RuleStage)
                        .where(RuleStage.rule_id == rule_id)
                        .order_by(RuleStage.position)
                    )
                ).all()
            )
            draft_stages: list[dict[str, object]] = []
            for item in stages:
                actions = list(
                    (
                        await database_session.scalars(
                            select(StageAction)
                            .where(StageAction.stage_id == item.id)
                            .order_by(StageAction.position)
                        )
                    ).all()
                )
                values: dict[str, object] = {
                    "name": item.name,
                    "stage_kind": item.stage_kind,
                    "duration_seconds": item.duration_seconds,
                    "start_host_text": item.start_host_text,
                    "end_host_text": item.end_host_text,
                    "parameters": item.parameters,
                    "actions": [
                        {
                            "action_kind": action.action_kind,
                            "side": action.side,
                            "seat_no": action.seat_no,
                            "duration_seconds": action.duration_seconds,
                            "parameters": action.parameters,
                        }
                        for action in actions
                    ],
                }
                if item.id == stage_id:
                    values.update(payload.model_dump(mode="json"))
                draft_stages.append(values)
            try:
                snapshot = validate_rule_draft(
                    {
                        "name": rule.name,
                        "description": rule.description,
                        "side_size": rule.side_size,
                        "stages": draft_stages,
                    }
                )
            except RuleValidationError as error:
                raise AuthError("rule_invalid", {"stage": str(error)}) from error
            host_changed = (
                stage.start_host_text != payload.start_host_text
                or stage.end_host_text != payload.end_host_text
            )
            stage.name = payload.name.strip()
            stage.duration_seconds = payload.duration_seconds
            stage.start_host_text = payload.start_host_text
            stage.end_host_text = payload.end_host_text
            stage.parameters = dict(payload.parameters)
            await database_session.execute(
                delete(StageAction).where(StageAction.stage_id == stage_id)
            )
            for position, action in enumerate(payload.actions, start=1):
                database_session.add(
                    StageAction(
                        stage_id=stage_id,
                        position=position,
                        **action.model_dump(),
                    )
                )
            rule.estimated_seconds = int(snapshot["estimated_seconds"])
            rule.config_revision += 1
            if host_changed:
                for suffix, text_value in (
                    ("start", payload.start_host_text),
                    ("end", payload.end_host_text),
                ):
                    segment_key = f"stage-{stage.position}-{suffix}"
                    old_asset = await database_session.scalar(
                        select(HostAudioAsset)
                        .where(
                            HostAudioAsset.rule_id == rule_id,
                            HostAudioAsset.segment_key == segment_key,
                        )
                        .with_for_update()
                    )
                    if old_asset is not None:
                        old_tasks = list(
                            (
                                await database_session.scalars(
                                    select(BackgroundTask).where(
                                        BackgroundTask.task_type == "HOST_TTS"
                                    )
                                )
                            ).all()
                        )
                        for task in old_tasks:
                            if str(task.payload.get("asset_id", "")) == str(old_asset.id):
                                await database_session.delete(task)
                        await database_session.delete(old_asset)
                        await database_session.flush()
                    if text_value:
                        replacement = HostAudioAsset(
                            rule_id=rule_id,
                            segment_key=segment_key,
                            text=text_value,
                            text_hash=sha256(text_value.encode("utf-8")).hexdigest(),
                            voice_profile_id=rule.host_voice_profile_id,
                        )
                        database_session.add(replacement)
                        await database_session.flush()
                        database_session.add(
                            BackgroundTask(
                                task_type="HOST_TTS",
                                payload={"asset_id": str(replacement.id), "rule_id": str(rule_id)},
                                available_at=current,
                            )
                        )
                remaining_assets = int(
                    await database_session.scalar(
                        select(func.count())
                        .select_from(HostAudioAsset)
                        .where(HostAudioAsset.rule_id == rule_id)
                    )
                    or 0
                )
                rule.status = "GENERATING_AUDIO" if remaining_assets else "READY"
                rule.audio_reviewed_at = None if remaining_assets else current
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule.stage_updated",
                target_type="rule_stage",
                target_id=str(stage_id),
                details={"rule_id": str(rule_id)},
            )
            await database_session.flush()
        return stage

    async def save_prompt(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        stage_id: UUID,
        purpose: str,
        payload: RulePromptUpdate,
        actor_user_id: UUID,
        agent_id: UUID | None = None,
    ) -> StagePromptTemplate | AgentPromptOverride:
        if purpose not in {"SPEECH", "DECISION"}:
            raise AuthError("rule_invalid", {"purpose": "Prompt 类型无效"})
        async with database_session.begin():
            stage = await database_session.get(RuleStage, stage_id, with_for_update=True)
            if stage is None or stage.rule_id != rule_id:
                raise AuthError("rule_not_found")
            if stage.stage_kind not in {"FIXED_SPEECH", "FREE_DEBATE"}:
                raise AuthError("rule_invalid", {"stage_id": "该阶段不接受 Agent Prompt"})
            if purpose == "DECISION" and stage.stage_kind != "FREE_DEBATE":
                raise AuthError("rule_invalid", {"purpose": "仅自由辩论支持决策 Prompt"})
            required = (
                FREE_DECISION_VARIABLES
                if purpose == "DECISION"
                else FREE_SPEECH_VARIABLES
                if stage.stage_kind == "FREE_DEBATE"
                else FIXED_SPEECH_VARIABLES
            )
            variables = self._validated_prompt(
                payload.template_text,
                required_variables=required,
                field="template_text",
            )
            if agent_id is None:
                prompt = await database_session.scalar(
                    select(StagePromptTemplate)
                    .where(
                        StagePromptTemplate.stage_id == stage_id,
                        StagePromptTemplate.purpose == purpose,
                    )
                    .with_for_update()
                )
                if prompt is None:
                    prompt = StagePromptTemplate(stage_id=stage_id, purpose=purpose)
                    database_session.add(prompt)
            else:
                agent = await database_session.get(AgentProfile, agent_id)
                if agent is None or agent.rule_id != rule_id:
                    raise AuthError("catalog_item_not_found")
                prompt = await database_session.scalar(
                    select(AgentPromptOverride)
                    .where(
                        AgentPromptOverride.agent_profile_id == agent_id,
                        AgentPromptOverride.stage_id == stage_id,
                        AgentPromptOverride.purpose == purpose,
                    )
                    .with_for_update()
                )
                if prompt is None:
                    prompt = AgentPromptOverride(
                        agent_profile_id=agent_id,
                        stage_id=stage_id,
                        purpose=purpose,
                    )
                    database_session.add(prompt)
            prompt.template_text = payload.template_text
            prompt.variables = variables
            prompt.output_contract = "SHOULD_SPEAK_V1" if purpose == "DECISION" else "TEXT"
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            assert rule is not None
            rule.config_revision += 1
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule_prompt.updated",
                target_type="rule_stage",
                target_id=str(stage_id),
                details={
                    "rule_id": str(rule_id),
                    "purpose": purpose,
                    "override": agent_id is not None,
                },
            )
            await database_session.flush()
        return prompt

    async def delete_prompt_override(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        stage_id: UUID,
        purpose: str,
        agent_id: UUID,
        actor_user_id: UUID,
    ) -> None:
        async with database_session.begin():
            prompt = await database_session.scalar(
                select(AgentPromptOverride)
                .join(AgentProfile, AgentProfile.id == AgentPromptOverride.agent_profile_id)
                .join(RuleStage, RuleStage.id == AgentPromptOverride.stage_id)
                .where(
                    AgentPromptOverride.agent_profile_id == agent_id,
                    AgentPromptOverride.stage_id == stage_id,
                    AgentPromptOverride.purpose == purpose,
                    AgentProfile.rule_id == rule_id,
                    RuleStage.rule_id == rule_id,
                )
                .with_for_update()
            )
            if prompt is not None:
                await database_session.delete(prompt)
                rule = await database_session.get(Rule, rule_id, with_for_update=True)
                assert rule is not None
                rule.config_revision += 1
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule_prompt.default_restored",
                target_type="agent_profile",
                target_id=str(agent_id),
                details={"rule_id": str(rule_id), "stage_id": str(stage_id), "purpose": purpose},
            )

    async def update_judge(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        payload: RuleJudgeUpdate,
        actor_user_id: UUID,
    ) -> RuleJudgeConfig:
        async with database_session.begin():
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            if rule is None:
                raise AuthError("rule_not_found")
            if payload.model_profile_id is not None:
                model = await database_session.get(ModelProfile, payload.model_profile_id)
                if model is None or model.status != "ENABLED":
                    raise AuthError("model_profile_unavailable")
            judge = await database_session.get(RuleJudgeConfig, rule_id, with_for_update=True)
            if judge is None:
                judge = RuleJudgeConfig(rule_id=rule_id)
                database_session.add(judge)
            judge.enabled = payload.enabled
            judge.model_profile_id = payload.model_profile_id
            judge.judge_prompt = payload.judge_prompt
            judge.include_in_leaderboard = payload.include_in_leaderboard
            rule.config_revision += 1
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule_judge.updated",
                target_type="rule",
                target_id=str(rule_id),
                details={
                    "enabled": judge.enabled,
                    "include_in_leaderboard": judge.include_in_leaderboard,
                },
            )
            await database_session.flush()
        return judge

    async def review_audio(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        now: datetime | None = None,
        actor_user_id: UUID | None = None,
    ) -> Rule:
        async with database_session.begin():
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            if rule is None:
                raise AuthError("rule_not_found")
            non_ready = await database_session.scalar(
                select(func.count())
                .select_from(HostAudioAsset)
                .where(HostAudioAsset.rule_id == rule_id, HostAudioAsset.status != "READY")
            )
            if non_ready:
                raise AuthError("rule_audio_not_ready")
            rule.status = "READY"
            rule.audio_reviewed_at = now or datetime.now(UTC)
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule.audio_reviewed",
                target_type="rule",
                target_id=str(rule.id),
            )
            await database_session.flush()
        return rule

    async def enable_rule(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> Rule:
        async with database_session.begin():
            candidate = await database_session.get(Rule, rule_id)
            if candidate is None:
                raise AuthError("rule_not_found")
            versions = list(
                (
                    await database_session.scalars(
                        select(Rule)
                        .where(Rule.rule_key == candidate.rule_key)
                        .order_by(Rule.id)
                        .with_for_update()
                    )
                ).all()
            )
            rule = next(version for version in versions if version.id == rule_id)
            if rule.status not in {"READY", "DISABLED"} or rule.audio_reviewed_at is None:
                raise AuthError("rule_not_ready")
            if (
                rule.side_size != 4
                or rule.historical_read_only
                or rule.default_agent_model_profile_id is None
            ):
                raise AuthError("rule_not_ready")
            enabled_agent_count = int(
                await database_session.scalar(
                    select(func.count())
                    .select_from(AgentProfile)
                    .join(ModelProfile, ModelProfile.id == AgentProfile.model_profile_id)
                    .join(VoiceProfile, VoiceProfile.id == AgentProfile.voice_profile_id)
                    .where(
                        AgentProfile.rule_id == rule_id,
                        AgentProfile.status == "ENABLED",
                        ModelProfile.status == "ENABLED",
                        VoiceProfile.status == "ENABLED",
                    )
                )
                or 0
            )
            if enabled_agent_count < 8:
                raise AuthError("agent_capacity_insufficient")
            stages = list(
                (
                    await database_session.scalars(
                        select(RuleStage).where(RuleStage.rule_id == rule_id)
                    )
                ).all()
            )
            prompt_rows = list(
                (
                    await database_session.execute(
                        select(StagePromptTemplate.stage_id, StagePromptTemplate.purpose).where(
                            StagePromptTemplate.stage_id.in_([stage.id for stage in stages])
                        )
                    )
                ).all()
            )
            prompt_slots = set(prompt_rows)
            for stage in stages:
                if stage.stage_kind == "FIXED_SPEECH" and (stage.id, "SPEECH") not in prompt_slots:
                    raise AuthError("rule_not_ready")
                if stage.stage_kind == "FREE_DEBATE" and not {
                    (stage.id, "SPEECH"),
                    (stage.id, "DECISION"),
                }.issubset(prompt_slots):
                    raise AuthError("rule_not_ready")
            for version in versions:
                if version.id != rule.id and version.status == "ENABLED":
                    version.status = "DISABLED"
            rule.status = "ENABLED"
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule.enabled",
                target_type="rule",
                target_id=str(rule.id),
            )
            await database_session.flush()
        return rule

    async def disable_rule(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> Rule:
        async with database_session.begin():
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            if rule is None:
                raise AuthError("rule_not_found")
            if rule.status != "ENABLED":
                raise AuthError("rule_not_enabled")
            rule.status = "DISABLED"
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule.disabled",
                target_type="rule",
                target_id=str(rule.id),
            )
            await database_session.flush()
        return rule

    async def delete_rule(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> list[str]:
        storage_paths: list[str] = []
        async with database_session.begin():
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            if rule is None:
                raise AuthError("rule_not_found")
            room_reference = await database_session.scalar(
                select(Room.id).where(Room.rule_id == rule_id).limit(1)
            )
            format_reference = await database_session.scalar(
                select(FormatVersion.id).where(FormatVersion.rule_id == rule_id).limit(1)
            )
            experiment_reference = await database_session.scalar(
                select(ExperimentBatch.id).where(ExperimentBatch.rule_id == rule_id).limit(1)
            )
            if any(
                reference is not None
                for reference in (room_reference, format_reference, experiment_reference)
            ):
                rule.status = "ARCHIVED"
                AuditService().record(
                    database_session,
                    actor_user_id=actor_user_id,
                    action="admin.rule.archived",
                    target_type="rule",
                    target_id=str(rule_id),
                )
                return storage_paths
            tasks = list(
                (
                    await database_session.scalars(
                        select(BackgroundTask).where(BackgroundTask.task_type == "HOST_TTS")
                    )
                ).all()
            )
            for task in tasks:
                if str(task.payload.get("rule_id", "")) == str(rule_id):
                    await database_session.delete(task)
            assets = list(
                (
                    await database_session.scalars(
                        select(HostAudioAsset)
                        .where(HostAudioAsset.rule_id == rule_id)
                        .with_for_update()
                    )
                ).all()
            )
            for asset in assets:
                if asset.storage_path:
                    storage_paths.append(asset.storage_path)
                await database_session.delete(asset)
            await database_session.delete(rule)
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule.deleted",
                target_type="rule",
                target_id=str(rule_id),
            )
        return storage_paths


__all__ = ["CatalogService", "RuleService"]

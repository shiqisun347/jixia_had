"""Transactional catalog and finite rule services for the 004 slice."""

from __future__ import annotations

from datetime import UTC, datetime
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
    RuleAudioGeneration,
    RuleJudgeConfig,
    RuleStage,
    Seat,
    StageAction,
    StagePromptTemplate,
    Topic,
    VoiceProfile,
)
from ..security.crypto import encrypt_secret
from .agent_pool import lock_agent_pool, sync_agent_pools
from .audio import (
    AudioConfiguration,
    aggregate,
    audio_file,
    build_manifest,
    create_generation,
    fingerprint,
    project,
    repair_generation,
)
from .host_voice import is_host_reserved_voice
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
from .readiness import inspect_rule
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
    RuleStageCreate,
    RuleStageReorder,
    RuleStageUpdate,
    TopicCreate,
    TopicUpdate,
    VoiceProfileCreate,
    VoiceProfileUpdate,
)
from .structure import validate_structure
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
        if is_host_reserved_voice(voice.provider_voice):
            raise AuthError("host_voice_reserved")
        return voice

    async def create_voice(
        self,
        database_session: AsyncSession,
        *,
        payload: VoiceProfileCreate,
        actor_user_id: UUID | None = None,
    ) -> VoiceProfile:
        async with database_session.begin():
            await lock_agent_pool(database_session)
            if payload.kind == "AGENT" and is_host_reserved_voice(payload.provider_voice):
                raise AuthError("host_voice_reserved")
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
            await sync_agent_pools(database_session, voice_id=voice.id, actor_user_id=actor_user_id)
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
            await lock_agent_pool(database_session)
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
        audio_configuration: AudioConfiguration | None = None,
    ) -> VoiceProfile:
        async with database_session.begin():
            await lock_agent_pool(database_session)
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
            if payload.kind != voice.kind:
                raise AuthError("voice_kind_immutable")
            values = payload.model_dump(exclude_unset=True)
            if (
                voice.kind == "AGENT"
                and is_host_reserved_voice(str(values.get("provider_voice", voice.provider_voice)))
                and (values.get("status", voice.status) == "ENABLED")
            ):
                raise AuthError("host_voice_reserved")
            host_audio_changed = voice.kind == "HOST" and any(
                values.get(field) != getattr(voice, field)
                for field in ("provider_voice", "rate")
                if field in values
            )
            if any(
                values.get(field) != getattr(voice, field)
                for field in ("provider_voice", "rate", "chars_per_second", "playback_gain")
                if field in values
            ):
                voice.calibration_status = "STALE"
                voice.calibrated_at = None
            for field, value in values.items():
                setattr(voice, field, value)
            await sync_agent_pools(
                database_session,
                voice_id=voice.id,
                actor_user_id=actor_user_id,
                refresh_voice=True,
            )
            if host_audio_changed:
                rules = list(
                    (
                        await database_session.scalars(
                            select(Rule)
                            .where(
                                Rule.host_voice_profile_id == voice.id,
                                Rule.lifecycle_status != "ARCHIVED",
                                Rule.historical_read_only.is_(False),
                            )
                            .order_by(Rule.id)
                            .with_for_update()
                        )
                    ).all()
                )
                for rule in rules:
                    rule.config_revision += 1
                    await create_generation(
                        database_session,
                        rule=rule,
                        actor_user_id=actor_user_id,
                        configuration=audio_configuration,
                    )
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
        await lock_agent_pool(database_session)
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

    audio_configuration: AudioConfiguration = AudioConfiguration()

    def __init__(self, audio_configuration: AudioConfiguration | None = None) -> None:
        if audio_configuration is not None:
            self.audio_configuration = audio_configuration

    async def _create_audio_generation(
        self,
        database_session: AsyncSession,
        *,
        rule: Rule,
        segments: list[tuple[str, str]],
        voice_profile_id: UUID | None,
        actor_user_id: UUID | None,
        now: datetime,
    ) -> RuleAudioGeneration:
        return await create_generation(
            database_session,
            rule=rule,
            actor_user_id=actor_user_id,
            configuration=self.audio_configuration,
        )

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
            await lock_agent_pool(database_session)
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
            rule.lifecycle_status = "DISABLED"
            await self._create_audio_generation(
                database_session,
                rule=rule,
                segments=host_segments,
                voice_profile_id=host_voice.id,
                actor_user_id=creator_user_id,
                now=current,
            )
            await sync_agent_pools(database_session, rule_id=rule.id, actor_user_id=creator_user_id)
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
            await lock_agent_pool(database_session)
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            if rule is None or rule.historical_read_only or rule.status == "ARCHIVED":
                raise AuthError("rule_invalid", {"rule_id": "历史规则不可编辑"})
            agent = await database_session.get(AgentProfile, agent_id, with_for_update=True)
            if agent is None or agent.rule_id != rule_id:
                raise AuthError("catalog_item_not_found")
            blocked = await CatalogService().status_change_block(
                database_session, kind="agents", item_id=agent_id, status=payload.status
            )
            if blocked is not None:
                raise AuthError(blocked)
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

    async def set_agent_status(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        agent_id: UUID,
        status: str,
        actor_user_id: UUID,
    ) -> AgentProfile:
        async with database_session.begin():
            await lock_agent_pool(database_session)
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            if rule is None or rule.historical_read_only or rule.status == "ARCHIVED":
                raise AuthError("rule_invalid", {"rule_id": "历史规则不可编辑"})
            agent = await database_session.get(AgentProfile, agent_id, with_for_update=True)
            if agent is None or agent.rule_id != rule_id:
                raise AuthError("catalog_item_not_found")
            blocked = await CatalogService().status_change_block(
                database_session, kind="agents", item_id=agent_id, status=status
            )
            if blocked is not None:
                raise AuthError(blocked)
            agent.status = status
            rule.config_revision += 1
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule_agent.status_updated",
                target_type="agent_profile",
                target_id=str(agent.id),
                details={"rule_id": str(rule_id), "status": status},
            )
            await database_session.flush()
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
            await lock_agent_pool(database_session)
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
            await sync_agent_pools(database_session, rule_id=rule.id, actor_user_id=actor_user_id)
            rule.topic_policy = payload.topic_policy
            rule.postmatch_questionnaire_enabled = payload.postmatch_questionnaire_enabled
            rule.config_revision += 1
            if host_changed:
                stages = list(
                    (
                        await database_session.scalars(
                            select(RuleStage)
                            .where(RuleStage.rule_id == rule_id)
                            .order_by(RuleStage.position)
                        )
                    ).all()
                )
                segments = [
                    (f"stage-{stage.position}-{suffix}", text_value)
                    for stage in stages
                    for suffix, text_value in (
                        ("start", stage.start_host_text),
                        ("end", stage.end_host_text),
                    )
                    if text_value
                ]
                await self._create_audio_generation(
                    database_session,
                    rule=rule,
                    segments=segments,
                    voice_profile_id=host_voice.id,
                    actor_user_id=actor_user_id,
                    now=current,
                )
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
                # Persist the edited stage before rebuilding the manifest.  The
                # audio builder reads stages through a separate SELECT; an
                # explicit flush prevents it from reusing the previous hash
                # when a caller has disabled session autoflush.
                await database_session.flush()
                segments = [
                    (f"stage-{item.position}-{suffix}", text_value)
                    for item in stages
                    for suffix, text_value in (
                        ("start", item.start_host_text),
                        ("end", item.end_host_text),
                    )
                    if text_value
                ]
                await self._create_audio_generation(
                    database_session,
                    rule=rule,
                    segments=segments,
                    voice_profile_id=rule.host_voice_profile_id,
                    actor_user_id=actor_user_id,
                    now=current,
                )
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

    async def create_rule_stage(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        payload: RuleStageCreate,
        actor_user_id: UUID,
    ) -> RuleStage:
        async with database_session.begin():
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            if rule is None:
                raise AuthError("rule_not_found")
            if rule.historical_read_only or rule.lifecycle_status == "ARCHIVED":
                raise AuthError("rule_invalid", {"rule_id": "历史规则不可编辑"})
            stages = list(
                (
                    await database_session.scalars(
                        select(RuleStage)
                        .where(RuleStage.rule_id == rule_id)
                        .order_by(RuleStage.position)
                        .with_for_update()
                    )
                ).all()
            )
            if len(stages) >= 50 or payload.stage_kind == "END":
                raise AuthError("rule_invalid", {"stage_kind": "只允许一个末尾结束阶段"})
            position = payload.position or max(1, len(stages))
            if position > len(stages):
                raise AuthError("rule_invalid", {"position": "新阶段必须放在结束阶段之前"})
            original_positions = {item.id: item.position for item in stages}
            for item in stages:
                item.position += len(stages) + 1
            await database_session.flush()
            for item in stages:
                original = original_positions[item.id]
                item.position = original + (1 if original >= position else 0)
            stage = RuleStage(
                rule_id=rule_id,
                position=position,
                name=payload.name.strip(),
                stage_kind=payload.stage_kind,
                duration_seconds=payload.duration_seconds,
                start_host_text=payload.start_host_text,
                end_host_text=payload.end_host_text,
                parameters=dict(payload.parameters),
            )
            database_session.add(stage)
            await database_session.flush()
            for action_position, action in enumerate(payload.actions, start=1):
                database_session.add(
                    StageAction(stage_id=stage.id, position=action_position, **action.model_dump())
                )
            if stage.stage_kind in {"FIXED_SPEECH", "FREE_DEBATE"}:
                text_value = (
                    DEFAULT_FREE_SPEECH_PROMPT
                    if stage.stage_kind == "FREE_DEBATE"
                    else DEFAULT_FIXED_SPEECH_PROMPT
                )
                required = (
                    FREE_SPEECH_VARIABLES
                    if stage.stage_kind == "FREE_DEBATE"
                    else FIXED_SPEECH_VARIABLES
                )
                database_session.add(
                    StagePromptTemplate(
                        stage_id=stage.id,
                        purpose="SPEECH",
                        template_text=text_value,
                        variables=self._validated_prompt(
                            text_value, required_variables=required, field="speech_prompt"
                        ),
                        output_contract="TEXT",
                    )
                )
            if stage.stage_kind == "FREE_DEBATE":
                database_session.add(
                    StagePromptTemplate(
                        stage_id=stage.id,
                        purpose="DECISION",
                        template_text=DEFAULT_FREE_DECISION_PROMPT,
                        variables=self._validated_prompt(
                            DEFAULT_FREE_DECISION_PROMPT,
                            required_variables=FREE_DECISION_VARIABLES,
                            field="decision_prompt",
                        ),
                        output_contract="SHOULD_SPEAK_V1",
                    )
                )
            await validate_structure(database_session, rule)
            await validate_structure(database_session, rule)
            rule.config_revision += 1
            await create_generation(
                database_session,
                rule=rule,
                actor_user_id=actor_user_id,
                configuration=self.audio_configuration,
            )
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule.stage_created",
                target_type="rule_stage",
                target_id=str(stage.id),
                details={"rule_id": str(rule_id)},
            )
            await database_session.flush()
        return stage

    async def reorder_rule_stages(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        payload: RuleStageReorder,
        actor_user_id: UUID,
    ) -> Rule:
        async with database_session.begin():
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            if rule is None:
                raise AuthError("rule_not_found")
            if rule.historical_read_only or rule.lifecycle_status == "ARCHIVED":
                raise AuthError("rule_invalid", {"rule_id": "历史规则不可编辑"})
            stages = list(
                (
                    await database_session.scalars(
                        select(RuleStage)
                        .where(RuleStage.rule_id == rule_id)
                        .order_by(RuleStage.position)
                        .with_for_update()
                    )
                ).all()
            )
            if len(stages) != len(payload.stage_ids) or {stage.id for stage in stages} != set(
                payload.stage_ids
            ):
                raise AuthError("rule_invalid", {"stage_ids": "阶段列表必须完整且不可重复"})
            offset = len(stages) + 1
            for stage in stages:
                stage.position += offset
            await database_session.flush()
            by_id = {stage.id: stage for stage in stages}
            for position, stage_id in enumerate(payload.stage_ids, start=1):
                by_id[stage_id].position = position
            await validate_structure(database_session, rule)
            rule.config_revision += 1
            await create_generation(
                database_session,
                rule=rule,
                actor_user_id=actor_user_id,
                configuration=self.audio_configuration,
            )
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule.stages_reordered",
                target_type="rule",
                target_id=str(rule_id),
            )
            await database_session.flush()
        return rule

    async def delete_rule_stage(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        stage_id: UUID,
        actor_user_id: UUID,
    ) -> Rule:
        async with database_session.begin():
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            stage = await database_session.get(RuleStage, stage_id, with_for_update=True)
            if rule is None or stage is None or stage.rule_id != rule_id:
                raise AuthError("rule_not_found")
            if rule.historical_read_only or rule.lifecycle_status == "ARCHIVED":
                raise AuthError("rule_invalid", {"rule_id": "历史规则不可编辑"})
            if stage.stage_kind == "END":
                raise AuthError("rule_invalid", {"stage_id": "结束阶段不能删除"})
            stages = list(
                (
                    await database_session.scalars(
                        select(RuleStage)
                        .where(RuleStage.rule_id == rule_id)
                        .order_by(RuleStage.position)
                        .with_for_update()
                    )
                ).all()
            )
            if len(stages) <= 1:
                raise AuthError("rule_invalid", {"stage_id": "至少保留一个阶段"})
            await database_session.delete(stage)
            await database_session.flush()
            remaining = [item for item in stages if item.id != stage_id]
            for item in remaining:
                item.position += len(stages) + 1
            await database_session.flush()
            for position, item in enumerate(remaining, start=1):
                item.position = position
            await validate_structure(database_session, rule)
            rule.config_revision += 1
            await create_generation(
                database_session,
                rule=rule,
                actor_user_id=actor_user_id,
                configuration=self.audio_configuration,
            )
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule.stage_deleted",
                target_type="rule_stage",
                target_id=str(stage_id),
                details={"rule_id": str(rule_id)},
            )
            await database_session.flush()
        return rule

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
                .with_for_update(of=AgentPromptOverride)
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

    async def generate_audio(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        actor_user_id: UUID | None,
        force: bool = False,
    ) -> RuleAudioGeneration:
        async with database_session.begin():
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            if rule is None:
                raise AuthError("rule_not_found")
            if rule.historical_read_only or rule.lifecycle_status == "ARCHIVED":
                raise AuthError("rule_invalid", {"rule_id": "历史规则不可编辑"})
            generation = await create_generation(
                database_session,
                rule=rule,
                actor_user_id=actor_user_id,
                configuration=self.audio_configuration,
                force=force,
            )
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule.audio_generation_requested",
                target_type="rule",
                target_id=str(rule.id),
                details={"generation_id": str(generation.id), "force": force},
            )
            await database_session.flush()
        return generation

    async def retry_audio(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        actor_user_id: UUID | None,
    ) -> RuleAudioGeneration:
        async with database_session.begin():
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            if rule is None:
                raise AuthError("rule_not_found")
            if rule.historical_read_only or rule.lifecycle_status == "ARCHIVED":
                raise AuthError("rule_invalid", {"rule_id": "历史规则不可编辑"})
            generation = await repair_generation(database_session, rule=rule, retry=True)
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule.audio_generation_retried",
                target_type="rule",
                target_id=str(rule.id),
                details={"generation_id": str(generation.id)},
            )
            await database_session.flush()
        return generation

    async def review_audio(
        self,
        database_session: AsyncSession,
        *,
        rule_id: UUID,
        now: datetime | None = None,
        actor_user_id: UUID | None = None,
        generation_id: UUID | None = None,
        manifest_hash: str | None = None,
    ) -> Rule:
        async with database_session.begin():
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            if rule is None:
                raise AuthError("rule_not_found")
            if rule.lifecycle_status == "ARCHIVED" or rule.historical_read_only:
                raise AuthError("rule_audio_not_ready")
            if rule.current_audio_generation_id is None:
                raise AuthError("rule_audio_not_ready")
            generation = await database_session.get(
                RuleAudioGeneration, rule.current_audio_generation_id, with_for_update=True
            )
            if (
                generation is None
                or not generation.is_current
                or (generation_id is not None and generation.id != generation_id)
                or (manifest_hash is not None and generation.manifest_hash != manifest_hash)
            ):
                raise AuthError("rule_audio_not_ready")
            expected = await build_manifest(
                database_session, rule=rule, configuration=self.audio_configuration
            )
            if generation.manifest_hash != fingerprint(expected):
                raise AuthError("rule_audio_not_ready")
            await aggregate(database_session, rule, generation)
            if generation.status not in {"SUCCEEDED", "NOT_REQUIRED"}:
                raise AuthError("rule_audio_not_ready")
            assets = list(
                (
                    await database_session.scalars(
                        select(HostAudioAsset).where(HostAudioAsset.generation_id == generation.id)
                    )
                ).all()
            )
            expected_inputs = {
                item["segment_key"]: item["input_hash"] for item in expected["segments"]
            }
            if len(assets) != len(expected_inputs):
                raise AuthError("rule_audio_not_ready")
            for asset in assets:
                if (
                    asset.status != "READY"
                    or not asset.storage_path
                    or (asset.duration_ms or 0) <= 0
                    or expected_inputs.get(asset.segment_key) != asset.input_hash
                ):
                    raise AuthError("rule_audio_not_ready")
                if self.audio_configuration.storage_root is not None:
                    path = audio_file(self.audio_configuration.storage_root, asset.storage_path)
                    if not path.is_file() or path.stat().st_size <= 0:
                        raise AuthError("host_audio_unavailable")
            generation.review_status = "APPROVED" if assets else "NOT_REQUIRED"
            generation.approved_by = actor_user_id if assets else None
            generation.approved_at = now or datetime.now(UTC) if assets else None
            project(rule, generation)
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.rule.audio_reviewed",
                target_type="rule",
                target_id=str(rule.id),
                details={
                    "generation_id": str(generation.id),
                    "manifest_hash": generation.manifest_hash,
                },
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
            inspection = await inspect_rule(
                database_session, rule, storage_root=self.audio_configuration.storage_root
            )
            if rule.lifecycle_status == "ARCHIVED" or not inspection.ready:
                raise AuthError(
                    "rule_not_ready", {item.target: item.message for item in inspection.blockers}
                )
            for version in versions:
                if version.id != rule.id and version.status == "ENABLED":
                    version.status = "DISABLED"
                    version.lifecycle_status = "DISABLED"
            rule.status = "ENABLED"
            rule.lifecycle_status = "ENABLED"
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
            rule.lifecycle_status = "DISABLED"
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
                rule.lifecycle_status = "ARCHIVED"
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

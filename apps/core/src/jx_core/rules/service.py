"""Transactional catalog and finite rule services for the 004 slice."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit.service import AuditService
from ..auth.errors import AuthError
from ..models import (
    AgentProfile,
    BackgroundTask,
    HostAudioAsset,
    Match,
    ModelProfile,
    Room,
    Rule,
    RuleStage,
    Seat,
    StageAction,
    Topic,
    VoiceProfile,
)
from ..security.crypto import encrypt_secret
from .schemas import (
    AgentProfileCreate,
    AgentProfileUpdate,
    ModelProfileCreate,
    ModelProfileUpdate,
    RuleCreate,
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
        master_key: str | None = None,
        actor_user_id: UUID | None = None,
    ) -> ModelProfile:
        values = payload.model_dump(exclude={"api_key"})
        async with database_session.begin():
            model = await database_session.get(ModelProfile, model_id, with_for_update=True)
            if model is None:
                raise AuthError("catalog_item_not_found")
            active_reference = await database_session.scalar(
                select(Match.id)
                .join(Room, Room.id == Match.room_id)
                .join(Seat, Seat.room_id == Room.id)
                .join(AgentProfile, AgentProfile.id == Seat.agent_profile_id)
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
            if payload.api_key is not None:
                if master_key is None:
                    raise AuthError("model_encryption_not_configured")
                ciphertext, nonce, last4 = encrypt_secret(
                    payload.api_key.get_secret_value(), master_key
                )
                model.api_key_ciphertext = ciphertext
                model.api_key_nonce = nonce
                model.api_key_last4 = last4
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.model.updated",
                target_type="model_profile",
                target_id=str(model_id),
                details={"api_key_rotated": payload.api_key is not None},
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
                .join(AgentProfile, AgentProfile.id == Seat.agent_profile_id)
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
            for field, value in payload.model_dump(exclude_unset=True).items():
                setattr(voice, field, value)
            AuditService().record(
                database_session,
                actor_user_id=actor_user_id,
                action="admin.voice.updated",
                target_type="voice_profile",
                target_id=str(voice_id),
            )
        return voice

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
            AuditService().record(
                database_session,
                actor_user_id=creator_user_id,
                action="admin.rule.created",
                target_type="rule",
                target_id=str(rule.id),
            )
            await database_session.flush()
        return rule

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
            if room_reference is not None:
                raise AuthError("rule_in_use")
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

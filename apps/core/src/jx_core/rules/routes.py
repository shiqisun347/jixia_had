"""Admin catalog and rule endpoints for the 004 foundation."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Annotated, NoReturn, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit.service import AuditService
from ..auth.dependencies import (
    get_admin_auth,
    get_changed_password_auth,
    get_database_session,
    require_browser_origin,
)
from ..auth.errors import APIError, AuthError
from ..auth.session import AuthContext
from ..config import Settings
from ..models import (
    AgentProfile,
    HostAudioAsset,
    ModelProfile,
    Rule,
    RuleStage,
    StageAction,
    Topic,
    VoiceProfile,
)
from .schemas import (
    AgentProfileCreate,
    AgentProfileResponse,
    AgentProfileUpdate,
    CatalogResponse,
    CatalogStatusUpdate,
    ModelProfileCreate,
    ModelProfileResponse,
    ModelProfileUpdate,
    RuleCreate,
    RuleResponse,
    TopicCreate,
    TopicResponse,
    TopicUpdate,
    VoiceProfileCreate,
    VoiceProfileResponse,
    VoiceProfileUpdate,
)
from .service import CatalogService, RuleService

router = APIRouter()


def _raise(error: AuthError) -> NoReturn:
    raise APIError(error.code, error.field_errors) from None


def _rule_response(rule: Rule) -> RuleResponse:
    return RuleResponse.model_validate(rule)


def _agent_response(agent: AgentProfile, voice_avatars: dict[UUID, str]) -> AgentProfileResponse:
    avatar_key = voice_avatars.get(agent.voice_profile_id)
    if avatar_key is None:
        _raise(AuthError("voice_profile_unavailable"))
    return AgentProfileResponse(
        id=agent.id,
        name=agent.name,
        model_profile_id=agent.model_profile_id,
        voice_profile_id=agent.voice_profile_id,
        system_prompt=agent.system_prompt,
        debater_prompt=agent.debater_prompt,
        generation_params=agent.generation_params,
        avatar_key=avatar_key,
        status=agent.status,
    )


async def _voice_avatar_map(
    database_session: AsyncSession, voice_ids: set[UUID]
) -> dict[UUID, str]:
    if not voice_ids:
        return {}
    rows = (
        await database_session.execute(
            select(VoiceProfile.id, VoiceProfile.avatar_key).where(
                VoiceProfile.id.in_(voice_ids), VoiceProfile.kind == "AGENT"
            )
        )
    ).all()
    return {voice_id: avatar_key for voice_id, avatar_key in rows if avatar_key is not None}


@router.get("/api/admin/catalog", response_model=CatalogResponse, tags=["admin-catalog"])
async def get_catalog(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CatalogResponse:
    voices, models, agents, topics, rules = await CatalogService().list_catalog(database_session)
    voice_avatars = await _voice_avatar_map(
        database_session, {agent.voice_profile_id for agent in agents}
    )
    return CatalogResponse(
        voices=[VoiceProfileResponse.model_validate(item) for item in voices],
        models=[ModelProfileResponse.model_validate(item) for item in models],
        agents=[_agent_response(item, voice_avatars) for item in agents],
        topics=[TopicResponse.model_validate(item) for item in topics],
        rules=[_rule_response(item) for item in rules],
    )


@router.post(
    "/api/admin/catalog/voices",
    response_model=VoiceProfileResponse,
    tags=["admin-catalog"],
    dependencies=[Depends(require_browser_origin)],
)
async def create_voice(
    payload: VoiceProfileCreate,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> VoiceProfileResponse:
    try:
        item = await CatalogService().create_voice(
            database_session, payload=payload, actor_user_id=context.user_id
        )
    except AuthError as error:
        _raise(error)
    return VoiceProfileResponse.model_validate(item)


@router.patch(
    "/api/admin/catalog/voices/{voice_id}",
    response_model=VoiceProfileResponse,
    tags=["admin-catalog"],
    dependencies=[Depends(require_browser_origin)],
)
async def update_voice(
    voice_id: UUID,
    payload: VoiceProfileUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> VoiceProfileResponse:
    try:
        item = await CatalogService().update_voice(
            database_session, voice_id=voice_id, payload=payload, actor_user_id=context.user_id
        )
    except AuthError as error:
        _raise(error)
    preview = (
        Path(request.app.state.settings.agent_audio_storage_dir)
        / "voice-previews"
        / f"{voice_id}.ogg"
    )
    with suppress(OSError):
        preview.unlink(missing_ok=True)
    return VoiceProfileResponse.model_validate(item)


@router.post(
    "/api/admin/catalog/models",
    response_model=ModelProfileResponse,
    tags=["admin-catalog"],
    dependencies=[Depends(require_browser_origin)],
)
async def create_model(
    payload: ModelProfileCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ModelProfileResponse:
    settings = cast(Settings, request.app.state.settings)
    item = await CatalogService().create_model(
        database_session,
        payload=payload,
        master_key=(
            settings.llm_key_encryption_key.get_secret_value()
            if settings.llm_key_encryption_key is not None
            else None
        ),
        actor_user_id=context.user_id,
    )
    return ModelProfileResponse.model_validate(item)


@router.patch(
    "/api/admin/catalog/models/{model_id}",
    response_model=ModelProfileResponse,
    tags=["admin-catalog"],
    dependencies=[Depends(require_browser_origin)],
)
async def update_model(
    model_id: UUID,
    payload: ModelProfileUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ModelProfileResponse:
    settings = cast(Settings, request.app.state.settings)
    try:
        item = await CatalogService().update_model(
            database_session,
            model_id=model_id,
            payload=payload,
            master_key=(
                settings.llm_key_encryption_key.get_secret_value()
                if settings.llm_key_encryption_key is not None
                else None
            ),
            actor_user_id=context.user_id,
        )
    except AuthError as error:
        _raise(error)
    return ModelProfileResponse.model_validate(item)


@router.post(
    "/api/admin/catalog/agents",
    response_model=AgentProfileResponse,
    tags=["admin-catalog"],
    dependencies=[Depends(require_browser_origin)],
)
async def create_agent(
    payload: AgentProfileCreate,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> AgentProfileResponse:
    try:
        item = await CatalogService().create_agent(
            database_session, payload=payload, actor_user_id=context.user_id
        )
    except AuthError as error:
        _raise(error)
    voice_avatars = await _voice_avatar_map(database_session, {item.voice_profile_id})
    return _agent_response(item, voice_avatars)


@router.patch(
    "/api/admin/catalog/agents/{agent_id}",
    response_model=AgentProfileResponse,
    tags=["admin-catalog"],
    dependencies=[Depends(require_browser_origin)],
)
async def update_agent(
    agent_id: UUID,
    payload: AgentProfileUpdate,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> AgentProfileResponse:
    try:
        item = await CatalogService().update_agent(
            database_session,
            agent_id=agent_id,
            payload=payload,
            actor_user_id=context.user_id,
        )
    except AuthError as error:
        _raise(error)
    voice_avatars = await _voice_avatar_map(database_session, {item.voice_profile_id})
    return _agent_response(item, voice_avatars)


@router.post(
    "/api/admin/catalog/topics",
    response_model=TopicResponse,
    tags=["admin-catalog"],
    dependencies=[Depends(require_browser_origin)],
)
async def create_topic(
    payload: TopicCreate,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> TopicResponse:
    item = await CatalogService().create_topic(
        database_session, creator_user_id=context.user_id, payload=payload
    )
    return TopicResponse.model_validate(item)


@router.patch(
    "/api/admin/catalog/topics/{topic_id}",
    response_model=TopicResponse,
    tags=["admin-catalog"],
    dependencies=[Depends(require_browser_origin)],
)
async def update_topic(
    topic_id: UUID,
    payload: TopicUpdate,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> TopicResponse:
    try:
        item = await CatalogService().update_topic(
            database_session, topic_id=topic_id, payload=payload, actor_user_id=context.user_id
        )
    except AuthError as error:
        _raise(error)
    return TopicResponse.model_validate(item)


@router.post(
    "/api/admin/rules",
    response_model=RuleResponse,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def create_rule(
    payload: RuleCreate,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RuleResponse:
    try:
        rule = await RuleService().create_rule(
            database_session, creator_user_id=context.user_id, payload=payload
        )
    except AuthError as error:
        _raise(error)
    return _rule_response(rule)


@router.get(
    "/api/admin/rules/{rule_id}/draft",
    tags=["rules"],
)
async def get_rule_draft(
    rule_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, object]:
    rule = await database_session.get(Rule, rule_id)
    if rule is None:
        _raise(AuthError("rule_not_found"))
    stages = list(
        (
            await database_session.scalars(
                select(RuleStage).where(RuleStage.rule_id == rule_id).order_by(RuleStage.position)
            )
        ).all()
    )
    stage_ids = [stage.id for stage in stages]
    actions = (
        list(
            (
                await database_session.scalars(
                    select(StageAction)
                    .where(StageAction.stage_id.in_(stage_ids))
                    .order_by(StageAction.stage_id, StageAction.position)
                )
            ).all()
        )
        if stage_ids
        else []
    )
    actions_by_stage: dict[UUID, list[StageAction]] = {}
    for action in actions:
        actions_by_stage.setdefault(action.stage_id, []).append(action)
    host_voice_id = await database_session.scalar(
        select(HostAudioAsset.voice_profile_id).where(HostAudioAsset.rule_id == rule_id).limit(1)
    )
    if host_voice_id is None:
        host_voice_id = await database_session.scalar(
            select(VoiceProfile.id)
            .where(VoiceProfile.kind == "HOST", VoiceProfile.status == "ENABLED")
            .order_by(VoiceProfile.name)
            .limit(1)
        )
    if host_voice_id is None:
        _raise(AuthError("voice_profile_unavailable"))
    return {
        "rule_key": rule.rule_key,
        "host_voice_profile_id": str(host_voice_id),
        "draft": {
            "name": rule.name,
            "description": rule.description,
            "side_size": rule.side_size,
            "stages": [
                {
                    "name": stage.name,
                    "stage_kind": stage.stage_kind,
                    "duration_seconds": stage.duration_seconds,
                    "start_host_text": stage.start_host_text,
                    "end_host_text": stage.end_host_text,
                    "parameters": stage.parameters,
                    "actions": [
                        {
                            "action_kind": action.action_kind,
                            "side": action.side,
                            "seat_no": action.seat_no,
                            "duration_seconds": action.duration_seconds,
                            "parameters": action.parameters,
                        }
                        for action in actions_by_stage.get(stage.id, [])
                    ],
                }
                for stage in stages
                if stage.stage_kind != "END"
            ],
        },
    }


@router.delete(
    "/api/admin/rules/{rule_id}",
    status_code=204,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def delete_rule(
    rule_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> None:
    try:
        storage_paths = await RuleService().delete_rule(
            database_session, rule_id=rule_id, actor_user_id=context.user_id
        )
    except AuthError as error:
        _raise(error)
    root = Path(request.app.state.settings.agent_audio_storage_dir).resolve()
    for relative_path in storage_paths:
        candidate = (root / relative_path).resolve()
        with suppress(ValueError, OSError):
            candidate.relative_to(root)
            candidate.unlink(missing_ok=True)


@router.post(
    "/api/admin/rules/{rule_id}/review-audio",
    response_model=RuleResponse,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def review_rule_audio(
    rule_id: UUID,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RuleResponse:
    try:
        rule = await RuleService().review_audio(
            database_session, rule_id=rule_id, actor_user_id=context.user_id
        )
    except AuthError as error:
        _raise(error)
    return _rule_response(rule)


@router.post(
    "/api/admin/rules/{rule_id}/enable",
    response_model=RuleResponse,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def enable_rule(
    rule_id: UUID,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RuleResponse:
    try:
        rule = await RuleService().enable_rule(
            database_session, rule_id=rule_id, actor_user_id=context.user_id
        )
    except AuthError as error:
        _raise(error)
    return _rule_response(rule)


@router.post(
    "/api/admin/rules/{rule_id}/disable",
    response_model=RuleResponse,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def disable_rule(
    rule_id: UUID,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RuleResponse:
    try:
        rule = await RuleService().disable_rule(
            database_session, rule_id=rule_id, actor_user_id=context.user_id
        )
    except AuthError as error:
        _raise(error)
    return _rule_response(rule)


@router.patch(
    "/api/admin/catalog/{kind}/{item_id}/status",
    tags=["admin-catalog"],
    dependencies=[Depends(require_browser_origin)],
)
async def update_catalog_status(
    kind: str,
    item_id: UUID,
    payload: CatalogStatusUpdate,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, str]:
    model_types = {
        "models": ModelProfile,
        "agents": AgentProfile,
        "voices": VoiceProfile,
        "topics": Topic,
    }
    model_type = model_types.get(kind)
    if model_type is None:
        _raise(AuthError("catalog_kind_invalid"))
    async with database_session.begin():
        item = await database_session.get(model_type, item_id, with_for_update=True)
        if item is None:
            _raise(AuthError("catalog_item_not_found"))
        voice_item = cast(VoiceProfile, item) if kind == "voices" else None
        if voice_item is not None and payload.status == "ENABLED" and voice_item.kind == "HOST":
            other_host = await database_session.scalar(
                select(VoiceProfile.id).where(
                    VoiceProfile.kind == "HOST",
                    VoiceProfile.status == "ENABLED",
                    VoiceProfile.id != item_id,
                )
            )
            if other_host is not None:
                _raise(AuthError("host_voice_already_configured"))
        item.status = payload.status
        AuditService().record(
            database_session,
            actor_user_id=context.user_id,
            action="admin.catalog.status_updated",
            target_type=kind,
            target_id=str(item_id),
            details={"status": payload.status},
        )
    return {"status": payload.status}


@router.get("/api/lobby/rules", response_model=list[RuleResponse], tags=["lobby"])
async def list_enabled_rules(
    _: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[RuleResponse]:
    rules = list(
        (
            await database_session.scalars(
                select(Rule).where(Rule.status == "ENABLED").order_by(Rule.name)
            )
        ).all()
    )
    return [_rule_response(rule) for rule in rules]


@router.get("/api/lobby/catalog", response_model=CatalogResponse, tags=["lobby"])
async def get_lobby_catalog(
    _: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CatalogResponse:
    voices, models, agents, topics, rules = await CatalogService().list_catalog(database_session)
    enabled_agents = [item for item in agents if item.status == "ENABLED"]
    voice_avatars = await _voice_avatar_map(
        database_session, {agent.voice_profile_id for agent in enabled_agents}
    )
    return CatalogResponse(
        voices=[
            VoiceProfileResponse.model_validate(item) for item in voices if item.status == "ENABLED"
        ],
        models=[
            ModelProfileResponse.model_validate(item) for item in models if item.status == "ENABLED"
        ],
        agents=[_agent_response(item, voice_avatars) for item in enabled_agents],
        topics=[TopicResponse.model_validate(item) for item in topics if item.status == "ENABLED"],
        rules=[_rule_response(item) for item in rules if item.status == "ENABLED"],
    )


__all__ = ["router"]

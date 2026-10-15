"""Admin catalog and rule endpoints for the 004 foundation."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Annotated, NoReturn, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select
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
    AgentPromptOverride,
    HostAudioAsset,
    ModelProfile,
    Rule,
    RuleAudioGeneration,
    RuleJudgeConfig,
    RuleStage,
    StageAction,
    StagePromptTemplate,
    Topic,
    VoiceProfile,
)
from .agent_pool import lock_agent_pool, sync_agent_pools
from .audio import AudioConfiguration, audio_file, repair_generation
from .host_voice import is_host_reserved_voice
from .readiness import inspect_rule
from .schemas import (
    AgentProfileCreate,
    AgentProfileResponse,
    AgentProfileUpdate,
    CatalogResponse,
    CatalogStatusUpdate,
    ModelApiKeyRotate,
    ModelApiKeyRotateResponse,
    ModelProfileCreate,
    ModelProfileResponse,
    ModelProfileUpdate,
    RuleAgentUpdate,
    RuleAudioApprove,
    RuleAudioAssetResponse,
    RuleAudioGenerationResponse,
    RuleBasicUpdate,
    RuleBlockerResponse,
    RuleCreate,
    RuleJudgeUpdate,
    RulePromptUpdate,
    RuleResponse,
    RuleStageCreate,
    RuleStageReorder,
    RuleStageUpdate,
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


async def _inspected_response(session: AsyncSession, rule: Rule) -> RuleResponse:
    inspection = await inspect_rule(session, rule)
    response = _rule_response(rule)
    response.readiness = "READY" if inspection.ready else "BLOCKED"
    response.availability = (
        "ARCHIVED"
        if rule.lifecycle_status == "ARCHIVED"
        else "AVAILABLE"
        if rule.lifecycle_status == "ENABLED" and inspection.ready
        else "BLOCKED"
    )
    response.blockers = [RuleBlockerResponse.model_validate(item) for item in inspection.blockers]
    response.pure_human_available = response.availability == "AVAILABLE"
    response.mixed_available = response.pure_human_available
    response.pure_agent_available = (
        response.pure_human_available and inspection.available_agent_count >= 8
    )
    response.available_agent_count = inspection.available_agent_count
    response.stage_count = inspection.stage_count
    response.audio = await _audio_response(session, rule)
    return response


def _audio_configuration(request: Request) -> AudioConfiguration:
    settings = cast(Settings, request.app.state.settings)
    return AudioConfiguration(
        model=settings.tts_model,
        workspace_id=settings.tts_workspace_id or "",
        websocket_url=settings.tts_ws_url,
        storage_root=settings.host_audio_storage_dir,
    )


def _rule_service(request: Request) -> RuleService:
    return RuleService(_audio_configuration(request))


async def _audio_response(
    database_session: AsyncSession, rule: Rule
) -> RuleAudioGenerationResponse | None:
    if rule.current_audio_generation_id is None:
        return None
    generation = await database_session.get(RuleAudioGeneration, rule.current_audio_generation_id)
    if generation is None:
        return None
    assets = list(
        (
            await database_session.scalars(
                select(HostAudioAsset)
                .where(HostAudioAsset.generation_id == generation.id)
                .order_by(HostAudioAsset.segment_key)
            )
        ).all()
    )
    return RuleAudioGenerationResponse(
        id=generation.id,
        rule_id=generation.rule_id,
        config_revision=generation.config_revision,
        manifest_hash=generation.manifest_hash,
        status=generation.status,
        review_status=generation.review_status,
        requested_at=generation.requested_at,
        started_at=generation.started_at,
        completed_at=generation.completed_at,
        approved_at=generation.approved_at,
        total_count=generation.total_count,
        ready_count=generation.ready_count,
        failed_count=generation.failed_count,
        last_error_code=generation.last_error_code,
        is_current=generation.is_current,
        assets=[
            RuleAudioAssetResponse(
                id=asset.id,
                generation_id=asset.generation_id,
                segment_key=asset.segment_key,
                text=asset.text,
                voice_profile_id=asset.voice_profile_id,
                status=asset.status,
                error_code=asset.error_code,
                duration_ms=asset.duration_ms,
                byte_count=asset.byte_count,
                checksum=asset.checksum,
                preview_url=(
                    f"/api/admin/rules/{rule.id}/audio/assets/{asset.id}"
                    if asset.status == "READY" and asset.storage_path
                    else None
                ),
            )
            for asset in assets
        ],
    )


def _agent_response(
    agent: AgentProfile,
    voices: dict[UUID, VoiceProfile],
    model_statuses: dict[UUID, str],
    *,
    prompt_override_count: int = 0,
) -> AgentProfileResponse:
    voice = voices.get(agent.voice_profile_id)
    avatar_key = voice.avatar_key if voice is not None else None
    if avatar_key is None:
        _raise(AuthError("voice_profile_unavailable"))
    return AgentProfileResponse(
        id=agent.id,
        format_version_id=agent.format_version_id,
        rule_id=agent.rule_id,
        name=voice.name if voice is not None else agent.name,
        model_profile_id=agent.model_profile_id,
        voice_profile_id=agent.voice_profile_id,
        system_prompt=agent.system_prompt,
        debater_prompt=agent.debater_prompt,
        generation_params=agent.generation_params,
        avatar_key=avatar_key,
        status=agent.status,
        voice_status=voice.status if voice is not None else "DISABLED",
        model_status=model_statuses.get(agent.model_profile_id, "DISABLED"),
        available=(
            agent.status == "ENABLED"
            and voice is not None
            and voice.status == "ENABLED"
            and model_statuses.get(agent.model_profile_id) == "ENABLED"
        ),
        unavailable_reason=(
            "RULE_DISABLED"
            if agent.status != "ENABLED"
            else "VOICE_DISABLED"
            if voice is None or voice.status != "ENABLED"
            else "MODEL_DISABLED"
            if model_statuses.get(agent.model_profile_id) != "ENABLED"
            else None
        ),
        prompt_override_count=prompt_override_count,
    )


async def _agent_resource_maps(
    database_session: AsyncSession, agents: list[AgentProfile]
) -> tuple[dict[UUID, VoiceProfile], dict[UUID, str]]:
    if not agents:
        return {}, {}
    voices = list(
        (
            await database_session.scalars(
                select(VoiceProfile).where(
                    VoiceProfile.id.in_({agent.voice_profile_id for agent in agents})
                )
            )
        ).all()
    )
    models = (
        await database_session.execute(
            select(ModelProfile.id, ModelProfile.status).where(
                ModelProfile.id.in_({agent.model_profile_id for agent in agents})
            )
        )
    ).all()
    return {voice.id: voice for voice in voices}, {model_id: status for model_id, status in models}


@router.get("/api/admin/catalog", response_model=CatalogResponse, tags=["admin-catalog"])
async def get_catalog(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CatalogResponse:
    voices, models, agents, topics, rules = await CatalogService().list_catalog(database_session)
    voice_resources = {voice.id: voice for voice in voices}
    model_statuses = {model.id: model.status for model in models}
    return CatalogResponse(
        voices=[VoiceProfileResponse.model_validate(item) for item in voices],
        models=[ModelProfileResponse.model_validate(item) for item in models],
        agents=[_agent_response(item, voice_resources, model_statuses) for item in agents],
        topics=[TopicResponse.model_validate(item) for item in topics],
        rules=[await _inspected_response(database_session, item) for item in rules],
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
            database_session,
            voice_id=voice_id,
            payload=payload,
            actor_user_id=context.user_id,
            audio_configuration=_audio_configuration(request),
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
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ModelProfileResponse:
    try:
        item = await CatalogService().update_model(
            database_session,
            model_id=model_id,
            payload=payload,
            actor_user_id=context.user_id,
        )
    except AuthError as error:
        _raise(error)
    return ModelProfileResponse.model_validate(item)


@router.post(
    "/api/admin/catalog/models/{model_id}/api-key",
    response_model=ModelApiKeyRotateResponse,
    tags=["admin-catalog"],
    dependencies=[Depends(require_browser_origin)],
)
async def rotate_model_api_key(
    model_id: UUID,
    payload: ModelApiKeyRotate,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ModelApiKeyRotateResponse:
    settings = cast(Settings, request.app.state.settings)
    try:
        item = await CatalogService().rotate_model_api_key(
            database_session,
            model_id=model_id,
            api_key=payload.api_key.get_secret_value(),
            master_key=(
                settings.llm_key_encryption_key.get_secret_value()
                if settings.llm_key_encryption_key is not None
                else None
            ),
            actor_user_id=context.user_id,
        )
    except AuthError as error:
        _raise(error)
    return ModelApiKeyRotateResponse(status="rotated", api_key_last4=item.api_key_last4 or "")


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
    _raise(AuthError("legacy_agent_read_only"))


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
        existing = await database_session.get(AgentProfile, agent_id)
        if existing is None:
            _raise(AuthError("catalog_item_not_found"))
        if existing.format_version_id is None:
            _raise(AuthError("legacy_agent_read_only"))
        item = await CatalogService().update_agent(
            database_session,
            agent_id=agent_id,
            payload=payload,
            actor_user_id=context.user_id,
        )
    except AuthError as error:
        _raise(error)
    voice_resources, model_statuses = await _agent_resource_maps(database_session, [item])
    return _agent_response(item, voice_resources, model_statuses)


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
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RuleResponse:
    try:
        rule = await _rule_service(request).create_rule(
            database_session, creator_user_id=context.user_id, payload=payload
        )
    except AuthError as error:
        _raise(error)
    return await _inspected_response(database_session, rule)


@router.get("/api/admin/rules", response_model=list[RuleResponse], tags=["rules"])
async def list_rules(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[RuleResponse]:
    rules = list(
        (
            await database_session.scalars(
                select(Rule)
                .where(Rule.lifecycle_status != "ARCHIVED")
                .order_by(Rule.created_at.desc(), Rule.name)
            )
        ).all()
    )
    return [await _inspected_response(database_session, rule) for rule in rules]


@router.patch(
    "/api/admin/rules/{rule_id}",
    response_model=RuleResponse,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def update_rule_basic(
    rule_id: UUID,
    payload: RuleBasicUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RuleResponse:
    try:
        rule = await _rule_service(request).update_rule_basic(
            database_session,
            rule_id=rule_id,
            payload=payload,
            actor_user_id=context.user_id,
        )
    except AuthError as error:
        _raise(error)
    return await _inspected_response(database_session, rule)


@router.post(
    "/api/admin/rules/{rule_id}/stages",
    response_model=dict[str, str],
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def create_rule_stage(
    rule_id: UUID,
    payload: RuleStageCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, str]:
    try:
        stage = await _rule_service(request).create_rule_stage(
            database_session,
            rule_id=rule_id,
            payload=payload,
            actor_user_id=context.user_id,
        )
    except AuthError as error:
        _raise(error)
    return {"id": str(stage.id), "status": "created"}


@router.patch(
    "/api/admin/rules/{rule_id}/stages/{stage_id}",
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def update_rule_stage(
    rule_id: UUID,
    stage_id: UUID,
    payload: RuleStageUpdate,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, str]:
    try:
        stage = await _rule_service(request).update_rule_stage(
            database_session,
            rule_id=rule_id,
            stage_id=stage_id,
            payload=payload,
            actor_user_id=context.user_id,
        )
    except AuthError as error:
        _raise(error)
    return {"id": str(stage.id), "status": "updated"}


@router.put(
    "/api/admin/rules/{rule_id}/stages/reorder",
    response_model=RuleResponse,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def reorder_rule_stages(
    rule_id: UUID,
    payload: RuleStageReorder,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RuleResponse:
    try:
        rule = await _rule_service(request).reorder_rule_stages(
            database_session,
            rule_id=rule_id,
            payload=payload,
            actor_user_id=context.user_id,
        )
    except AuthError as error:
        _raise(error)
    return await _inspected_response(database_session, rule)


@router.delete(
    "/api/admin/rules/{rule_id}/stages/{stage_id}",
    response_model=RuleResponse,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def delete_rule_stage(
    rule_id: UUID,
    stage_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RuleResponse:
    try:
        rule = await _rule_service(request).delete_rule_stage(
            database_session,
            rule_id=rule_id,
            stage_id=stage_id,
            actor_user_id=context.user_id,
        )
    except AuthError as error:
        _raise(error)
    return await _inspected_response(database_session, rule)


@router.get("/api/admin/rules/{rule_id}/workspace", tags=["rules"])
async def get_rule_workspace(
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
    prompts = (
        list(
            (
                await database_session.scalars(
                    select(StagePromptTemplate).where(StagePromptTemplate.stage_id.in_(stage_ids))
                )
            ).all()
        )
        if stage_ids
        else []
    )
    actions_by_stage: dict[UUID, list[StageAction]] = {}
    prompts_by_stage: dict[UUID, list[StagePromptTemplate]] = {}
    for action in actions:
        actions_by_stage.setdefault(action.stage_id, []).append(action)
    for prompt in prompts:
        prompts_by_stage.setdefault(prompt.stage_id, []).append(prompt)
    judge = await database_session.get(RuleJudgeConfig, rule_id)
    agent_count = int(
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
    return {
        "rule": (await _inspected_response(database_session, rule)).model_dump(mode="json"),
        "agent_count": agent_count,
        "stages": [
            {
                "id": str(stage.id),
                "position": stage.position,
                "name": stage.name,
                "stage_kind": stage.stage_kind,
                "duration_seconds": stage.duration_seconds,
                "start_host_text": stage.start_host_text,
                "end_host_text": stage.end_host_text,
                "parameters": stage.parameters,
                "actions": [
                    {
                        "id": str(action.id),
                        "position": action.position,
                        "side": action.side,
                        "seat_no": action.seat_no,
                        "duration_seconds": action.duration_seconds,
                    }
                    for action in actions_by_stage.get(stage.id, [])
                ],
                "prompts": [
                    {
                        "purpose": prompt.purpose,
                        "template_text": prompt.template_text,
                        "variables": prompt.variables,
                        "output_contract": prompt.output_contract,
                    }
                    for prompt in prompts_by_stage.get(stage.id, [])
                ],
            }
            for stage in stages
        ],
        "judge": (
            {
                "enabled": judge.enabled,
                "model_profile_id": (
                    str(judge.model_profile_id) if judge.model_profile_id is not None else None
                ),
                "judge_prompt": judge.judge_prompt,
                "include_in_leaderboard": judge.include_in_leaderboard,
            }
            if judge is not None
            else None
        ),
    }


@router.get(
    "/api/admin/rules/{rule_id}/agents",
    response_model=list[AgentProfileResponse],
    tags=["rules"],
)
async def list_rule_agents(
    rule_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[AgentProfileResponse]:
    if await database_session.get(Rule, rule_id) is None:
        _raise(AuthError("rule_not_found"))
    agents = list(
        (
            await database_session.scalars(
                select(AgentProfile)
                .where(AgentProfile.rule_id == rule_id)
                .order_by(AgentProfile.name)
            )
        ).all()
    )
    voice_resources, model_statuses = await _agent_resource_maps(database_session, agents)
    override_rows = (
        await database_session.execute(
            select(
                AgentPromptOverride.agent_profile_id,
                func.count(AgentPromptOverride.id),
            )
            .where(AgentPromptOverride.agent_profile_id.in_([agent.id for agent in agents]))
            .group_by(AgentPromptOverride.agent_profile_id)
        )
    ).all()
    override_counts: dict[UUID, int] = {agent_id: int(count) for agent_id, count in override_rows}
    return [
        _agent_response(
            agent,
            voice_resources,
            model_statuses,
            prompt_override_count=int(override_counts.get(agent.id, 0)),
        )
        for agent in agents
    ]


@router.get(
    "/api/admin/rules/{rule_id}/agents/{agent_id}/stages/{stage_id}/prompts/{purpose}",
    tags=["rules"],
)
async def get_agent_prompt_slot(
    rule_id: UUID,
    agent_id: UUID,
    stage_id: UUID,
    purpose: str,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, object]:
    agent = await database_session.get(AgentProfile, agent_id)
    stage = await database_session.get(RuleStage, stage_id)
    normalized_purpose = purpose.upper()
    if agent is None or agent.rule_id != rule_id or stage is None or stage.rule_id != rule_id:
        _raise(AuthError("catalog_item_not_found"))
    default = await database_session.scalar(
        select(StagePromptTemplate).where(
            StagePromptTemplate.stage_id == stage_id,
            StagePromptTemplate.purpose == normalized_purpose,
        )
    )
    if default is None:
        _raise(AuthError("rule_invalid"))
    override = await database_session.scalar(
        select(AgentPromptOverride).where(
            AgentPromptOverride.agent_profile_id == agent_id,
            AgentPromptOverride.stage_id == stage_id,
            AgentPromptOverride.purpose == normalized_purpose,
        )
    )
    effective = override or default
    return {
        "uses_default": override is None,
        "template_text": effective.template_text,
        "default_template_text": default.template_text,
        "variables": effective.variables,
    }


@router.patch(
    "/api/admin/rules/{rule_id}/agents/{agent_id}",
    response_model=AgentProfileResponse,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def update_rule_agent(
    rule_id: UUID,
    agent_id: UUID,
    payload: RuleAgentUpdate,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> AgentProfileResponse:
    try:
        agent = await RuleService().update_rule_agent(
            database_session,
            rule_id=rule_id,
            agent_id=agent_id,
            payload=payload,
            actor_user_id=context.user_id,
        )
    except AuthError as error:
        _raise(error)
    voice_resources, model_statuses = await _agent_resource_maps(database_session, [agent])
    return _agent_response(agent, voice_resources, model_statuses)


@router.patch(
    "/api/admin/rules/{rule_id}/agents/{agent_id}/status",
    response_model=AgentProfileResponse,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def set_rule_agent_status(
    rule_id: UUID,
    agent_id: UUID,
    payload: CatalogStatusUpdate,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> AgentProfileResponse:
    try:
        agent = await RuleService().set_agent_status(
            database_session,
            rule_id=rule_id,
            agent_id=agent_id,
            status=payload.status,
            actor_user_id=context.user_id,
        )
    except AuthError as error:
        _raise(error)
    voice_resources, model_statuses = await _agent_resource_maps(database_session, [agent])
    return _agent_response(agent, voice_resources, model_statuses)


@router.put(
    "/api/admin/rules/{rule_id}/stages/{stage_id}/prompts/{purpose}",
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def save_rule_stage_prompt(
    rule_id: UUID,
    stage_id: UUID,
    purpose: str,
    payload: RulePromptUpdate,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, object]:
    try:
        prompt = await RuleService().save_prompt(
            database_session,
            rule_id=rule_id,
            stage_id=stage_id,
            purpose=purpose.upper(),
            payload=payload,
            actor_user_id=context.user_id,
        )
    except AuthError as error:
        _raise(error)
    return {"id": str(prompt.id), "variables": prompt.variables}


@router.put(
    "/api/admin/rules/{rule_id}/agents/{agent_id}/stages/{stage_id}/prompts/{purpose}",
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def save_agent_prompt_override(
    rule_id: UUID,
    agent_id: UUID,
    stage_id: UUID,
    purpose: str,
    payload: RulePromptUpdate,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, object]:
    try:
        prompt = await RuleService().save_prompt(
            database_session,
            rule_id=rule_id,
            stage_id=stage_id,
            purpose=purpose.upper(),
            payload=payload,
            actor_user_id=context.user_id,
            agent_id=agent_id,
        )
    except AuthError as error:
        _raise(error)
    return {"id": str(prompt.id), "variables": prompt.variables}


@router.delete(
    "/api/admin/rules/{rule_id}/agents/{agent_id}/stages/{stage_id}/prompts/{purpose}",
    status_code=204,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def restore_agent_prompt_default(
    rule_id: UUID,
    agent_id: UUID,
    stage_id: UUID,
    purpose: str,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> None:
    await RuleService().delete_prompt_override(
        database_session,
        rule_id=rule_id,
        stage_id=stage_id,
        purpose=purpose.upper(),
        agent_id=agent_id,
        actor_user_id=context.user_id,
    )


@router.put(
    "/api/admin/rules/{rule_id}/judge",
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def update_rule_judge(
    rule_id: UUID,
    payload: RuleJudgeUpdate,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, object]:
    try:
        judge = await RuleService().update_judge(
            database_session,
            rule_id=rule_id,
            payload=payload,
            actor_user_id=context.user_id,
        )
    except AuthError as error:
        _raise(error)
    return {
        "enabled": judge.enabled,
        "model_profile_id": (
            str(judge.model_profile_id) if judge.model_profile_id is not None else None
        ),
        "judge_prompt": judge.judge_prompt,
        "include_in_leaderboard": judge.include_in_leaderboard,
    }


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
    host_voice_id = rule.host_voice_profile_id
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


@router.get(
    "/api/admin/rules/{rule_id}/audio",
    response_model=RuleAudioGenerationResponse | None,
    tags=["rules"],
)
async def get_rule_audio(
    rule_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RuleAudioGenerationResponse | None:
    rule = await database_session.get(Rule, rule_id)
    if rule is None:
        _raise(AuthError("rule_not_found"))
    return await _audio_response(database_session, rule)


@router.post(
    "/api/admin/rules/{rule_id}/audio/generate",
    response_model=RuleAudioGenerationResponse,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def generate_rule_audio(
    rule_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
    force: bool = False,
) -> RuleAudioGenerationResponse:
    try:
        await _rule_service(request).generate_audio(
            database_session,
            rule_id=rule_id,
            actor_user_id=context.user_id,
            force=force,
        )
        rule = await database_session.get(Rule, rule_id)
        assert rule is not None
        response = await _audio_response(database_session, rule)
        assert response is not None
        return response
    except AuthError as error:
        _raise(error)


@router.post(
    "/api/admin/rules/{rule_id}/audio/retry",
    response_model=RuleAudioGenerationResponse,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def retry_rule_audio(
    rule_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RuleAudioGenerationResponse:
    try:
        await _rule_service(request).retry_audio(
            database_session, rule_id=rule_id, actor_user_id=context.user_id
        )
        rule = await database_session.get(Rule, rule_id)
        assert rule is not None
        response = await _audio_response(database_session, rule)
        assert response is not None
        return response
    except AuthError as error:
        _raise(error)


@router.post(
    "/api/admin/rules/{rule_id}/audio/approve",
    response_model=RuleResponse,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def approve_rule_audio(
    rule_id: UUID,
    payload: RuleAudioApprove,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RuleResponse:
    try:
        rule = await _rule_service(request).review_audio(
            database_session,
            rule_id=rule_id,
            actor_user_id=context.user_id,
            generation_id=payload.generation_id,
            manifest_hash=payload.manifest_hash,
        )
    except AuthError as error:
        _raise(error)
    return await _inspected_response(database_session, rule)


@router.post(
    "/api/admin/rules/{rule_id}/audio/reconcile",
    response_model=RuleAudioGenerationResponse,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def reconcile_rule_audio(
    rule_id: UUID,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RuleAudioGenerationResponse:
    try:
        async with database_session.begin():
            rule = await database_session.get(Rule, rule_id, with_for_update=True)
            if rule is None:
                raise AuthError("rule_not_found")
            await repair_generation(database_session, rule=rule)
            await database_session.flush()
        rule = await database_session.get(Rule, rule_id)
        assert rule is not None
        response = await _audio_response(database_session, rule)
        if response is None:
            raise AuthError("rule_audio_not_ready")
        return response
    except AuthError as error:
        _raise(error)


@router.get(
    "/api/admin/rules/{rule_id}/audio/assets/{asset_id}",
    response_class=FileResponse,
    tags=["rules"],
)
async def preview_rule_audio(
    rule_id: UUID,
    asset_id: UUID,
    request: Request,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> FileResponse:
    rule = await database_session.get(Rule, rule_id)
    asset = await database_session.get(HostAudioAsset, asset_id)
    if (
        rule is None
        or asset is None
        or rule.current_audio_generation_id is None
        or asset.rule_id != rule.id
        or asset.generation_id != rule.current_audio_generation_id
        or asset.status != "READY"
        or not asset.storage_path
    ):
        _raise(AuthError("rule_audio_not_ready"))
    settings = cast(Settings, request.app.state.settings)
    path = audio_file(settings.host_audio_storage_dir, asset.storage_path)
    if not path.is_file():
        _raise(AuthError("host_audio_unavailable"))
    return FileResponse(path, media_type="audio/ogg", filename=f"{asset.segment_key}.ogg")


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
    root = Path(request.app.state.settings.host_audio_storage_dir).resolve()
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
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RuleResponse:
    try:
        current_rule = await database_session.get(Rule, rule_id)
        if current_rule is None or current_rule.current_audio_generation_id is None:
            _raise(AuthError("rule_audio_not_ready"))
        generation = await database_session.get(
            RuleAudioGeneration, current_rule.current_audio_generation_id
        )
        if generation is None:
            _raise(AuthError("rule_audio_not_ready"))
        rule = await _rule_service(request).review_audio(
            database_session,
            rule_id=rule_id,
            actor_user_id=context.user_id,
            generation_id=generation.id,
            manifest_hash=generation.manifest_hash,
        )
    except AuthError as error:
        _raise(error)
    return await _inspected_response(database_session, rule)


@router.post(
    "/api/admin/rules/{rule_id}/enable",
    response_model=RuleResponse,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def enable_rule(
    rule_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RuleResponse:
    try:
        rule = await _rule_service(request).enable_rule(
            database_session, rule_id=rule_id, actor_user_id=context.user_id
        )
    except AuthError as error:
        _raise(error)
    return await _inspected_response(database_session, rule)


@router.post(
    "/api/admin/rules/{rule_id}/disable",
    response_model=RuleResponse,
    tags=["rules"],
    dependencies=[Depends(require_browser_origin)],
)
async def disable_rule(
    rule_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RuleResponse:
    try:
        rule = await _rule_service(request).disable_rule(
            database_session, rule_id=rule_id, actor_user_id=context.user_id
        )
    except AuthError as error:
        _raise(error)
    return await _inspected_response(database_session, rule)


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
        await lock_agent_pool(database_session)
        item = await database_session.get(model_type, item_id, with_for_update=True)
        if item is None:
            _raise(AuthError("catalog_item_not_found"))
        agent_item = cast(AgentProfile, item) if kind == "agents" else None
        if agent_item is not None and agent_item.format_version_id is None:
            _raise(AuthError("legacy_agent_read_only"))
        blocked = await CatalogService().status_change_block(
            database_session,
            kind=kind,
            item_id=item_id,
            status=payload.status,
        )
        if blocked is not None:
            _raise(AuthError(blocked))
        voice_item = cast(VoiceProfile, item) if kind == "voices" else None
        if (
            voice_item is not None
            and payload.status == "ENABLED"
            and voice_item.kind == "AGENT"
            and is_host_reserved_voice(voice_item.provider_voice)
        ):
            _raise(AuthError("host_voice_reserved"))
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
        if voice_item is not None:
            await sync_agent_pools(
                database_session,
                voice_id=item_id,
                actor_user_id=context.user_id,
                refresh_voice=True,
            )
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
                select(Rule).where(Rule.lifecycle_status == "ENABLED").order_by(Rule.name)
            )
        ).all()
    )
    responses = [await _inspected_response(database_session, rule) for rule in rules]
    return [response for response in responses if response.availability == "AVAILABLE"]


@router.get("/api/lobby/catalog", response_model=CatalogResponse, tags=["lobby"])
async def get_lobby_catalog(
    _: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CatalogResponse:
    voices, models, agents, topics, rules = await CatalogService().list_catalog(database_session)
    voice_resources = {voice.id: voice for voice in voices}
    model_statuses = {model.id: model.status for model in models}
    enabled_agents = [
        item
        for item in agents
        if item.status == "ENABLED"
        and voice_resources[item.voice_profile_id].status == "ENABLED"
        and model_statuses.get(item.model_profile_id) == "ENABLED"
    ]
    return CatalogResponse(
        voices=[
            VoiceProfileResponse.model_validate(item) for item in voices if item.status == "ENABLED"
        ],
        models=[
            ModelProfileResponse.model_validate(item) for item in models if item.status == "ENABLED"
        ],
        agents=[_agent_response(item, voice_resources, model_statuses) for item in enabled_agents],
        topics=[TopicResponse.model_validate(item) for item in topics if item.status == "ENABLED"],
        rules=[
            response
            for response in [await _inspected_response(database_session, item) for item in rules]
            if response.availability == "AVAILABLE"
        ],
    )


__all__ = ["router"]

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_admin_auth, get_database_session
from ..auth.errors import APIError, AuthError
from ..auth.session import AuthContext
from ..models import (
    AgentProfile,
    FormatJudgeProfile,
    FormatModule,
    FormatTopicReference,
    FormatVersion,
    PromptTemplate,
)
from .schemas import FormatVersionResponse, FormatWorkspaceResponse
from .service import FormatService

router = APIRouter(tags=["admin-formats"])


def _raise(error: AuthError) -> NoReturn:
    raise APIError(error.code, error.field_errors) from None


def _response(version: FormatVersion) -> FormatVersionResponse:
    return FormatVersionResponse.model_validate(version)


@router.get("/api/admin/formats", response_model=list[FormatVersionResponse])
async def list_formats(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[FormatVersionResponse]:
    return [_response(item) for item in await FormatService().list_versions(session)]


@router.get("/api/admin/formats/{version_id}", response_model=FormatWorkspaceResponse)
async def get_format(
    version_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> FormatWorkspaceResponse:
    version = await session.get(FormatVersion, version_id)
    if version is None:
        _raise(AuthError("format_not_found"))
    agents = list(
        (
            await session.scalars(
                select(AgentProfile)
                .where(AgentProfile.format_version_id == version_id)
                .order_by(AgentProfile.name)
            )
        ).all()
    )
    prompts = list(
        (
            await session.scalars(
                select(PromptTemplate)
                .where(PromptTemplate.format_version_id == version_id)
                .order_by(PromptTemplate.stage_key, PromptTemplate.purpose)
            )
        ).all()
    )
    modules = list(
        (
            await session.scalars(
                select(FormatModule)
                .where(FormatModule.format_version_id == version_id)
                .order_by(FormatModule.module_key)
            )
        ).all()
    )
    judge = await session.scalar(
        select(FormatJudgeProfile).where(FormatJudgeProfile.format_version_id == version_id)
    )
    references = list(
        (
            await session.scalars(
                select(FormatTopicReference)
                .where(FormatTopicReference.format_version_id == version_id)
                .order_by(FormatTopicReference.position)
            )
        ).all()
    )
    return FormatWorkspaceResponse(
        **_response(version).model_dump(),
        agents=[
            {
                "id": agent.id,
                "name": agent.name,
                "model_profile_id": agent.model_profile_id,
                "voice_profile_id": agent.voice_profile_id,
                "generation_params": agent.generation_params,
            }
            for agent in agents
        ],
        prompts=[
            {
                "id": prompt.id,
                "agent_profile_id": prompt.agent_profile_id,
                "stage_key": prompt.stage_key,
                "purpose": prompt.purpose,
                "template_text": prompt.template_text,
                "variables": prompt.variables,
                "output_contract": prompt.output_contract,
            }
            for prompt in prompts
        ],
        modules=[
            {
                "id": module.id,
                "module_key": module.module_key,
                "enabled": module.enabled,
                "config": module.config,
            }
            for module in modules
        ],
        judge=(
            {
                "id": judge.id,
                "model_profile_id": judge.model_profile_id,
                "system_prompt": judge.system_prompt,
                "judge_prompt": judge.judge_prompt,
                "generation_params": judge.generation_params,
                "dimensions": judge.dimensions,
                "output_contract": judge.output_contract,
            }
            if judge is not None
            else None
        ),
        topic_references=[
            {
                "id": ref.id,
                "position": ref.position,
                "topic_id": ref.topic_id,
                "private_snapshot": ref.private_snapshot,
            }
            for ref in references
        ],
    )

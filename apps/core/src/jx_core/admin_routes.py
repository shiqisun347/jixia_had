"""Small, read-heavy administration surface for the MVP."""

from __future__ import annotations

import asyncio
import os
import shutil
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import String, case, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from .agent.audio import apply_ogg_opus_gain
from .agent.llm import LlmProviderError, OpenAIStreamingClient
from .agent.tts import QwenTtsConnection
from .audit.service import AuditService
from .auth.dependencies import get_admin_auth, get_database_session, require_browser_origin
from .auth.session import AuthContext
from .config import Settings
from .data_capture.content import load_content_blob
from .matches.domain import MatchCommand, MatchDomainError
from .matches.service import MatchRuntimeManager
from .models import (
    AgentFreeDebateDecision,
    AgentGeneration,
    AgentProfile,
    AuditLog,
    BackgroundTask,
    CallContentBlob,
    DeviceCheck,
    ExternalCall,
    JudgeProfile,
    JudgeResult,
    LeaderboardSnapshot,
    Match,
    MatchFile,
    MatchParticipant,
    ModelProfile,
    Room,
    RoomMember,
    Rule,
    Seat,
    Speech,
    Topic,
    TranscriptSubmission,
    User,
    UserSession,
    VoiceProfile,
)
from .postmatch import PostmatchService
from .security.crypto import decrypt_secret

router = APIRouter(prefix="/api/admin", tags=["admin"])
VOICE_PREVIEW_TEXT = "观点需要证据支撑，反驳也应准确回应对方的核心论证。"


class UserPatch(BaseModel):
    real_name: str | None = Field(default=None, min_length=2, max_length=30)
    role: str | None = None
    status: str | None = None


class AgentGenerationView(BaseModel):
    id: UUID
    action_key: str
    agent_profile_id: UUID
    agent_name: str
    context_version: int
    attempt_no: int
    status: str
    first_token_latency_ms: int | None
    completed_latency_ms: int | None
    completion_tokens: int | None
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


class AgentGenerationDetail(AgentGenerationView):
    input_snapshot: dict[str, Any]
    llm_draft_text: str


class AgentFreeDebateDecisionView(BaseModel):
    id: UUID
    action_key: str
    decision_round_id: UUID
    agent_profile_id: UUID
    agent_name: str
    side: str
    seat_no: int
    status: str
    should_speak: bool | None
    willingness: float | None
    attempt_no: int
    duration_ms: int | None
    error_code: str | None
    result_order: int | None
    final_queue_rank: int | None
    human_hand_at_result: bool
    human_hand_at_lock: bool
    selected: bool
    fallback: bool
    started_at: datetime
    completed_at: datetime | None


def agent_generation_view(generation: AgentGeneration, agent_name: str) -> AgentGenerationView:
    return AgentGenerationView(
        id=generation.id,
        action_key=generation.action_key,
        agent_profile_id=generation.agent_profile_id,
        agent_name=agent_name,
        context_version=generation.context_version,
        attempt_no=generation.attempt_no,
        status=generation.status,
        first_token_latency_ms=generation.first_token_latency_ms,
        completed_latency_ms=generation.completed_latency_ms,
        completion_tokens=generation.completion_tokens,
        error_code=generation.error_code,
        created_at=generation.created_at,
        completed_at=generation.completed_at,
    )


def agent_generation_detail(generation: AgentGeneration, agent_name: str) -> AgentGenerationDetail:
    return AgentGenerationDetail(
        **agent_generation_view(generation, agent_name).model_dump(),
        input_snapshot=generation.input_snapshot,
        llm_draft_text=generation.llm_draft_text,
    )


class JudgeProfileCreate(BaseModel):
    model_profile_id: UUID
    system_prompt: str = Field(min_length=1, max_length=20_000)
    judge_prompt: str = Field(min_length=1, max_length=30_000)
    generation_params: dict[str, Any] = Field(default_factory=dict)


class PermanentFilesUpdate(BaseModel):
    permanent: bool


class JudgeResultPatch(BaseModel):
    winner: Literal["AFFIRMATIVE", "NEGATIVE", "DRAW"]
    team_scores: dict[str, dict[str, int]]
    participants: list[dict[str, Any]]
    team_comments: dict[str, str] = Field(default_factory=dict)


class MatchMetadataPatch(BaseModel):
    label: str = Field(min_length=1, max_length=32)
    display_topic: str = Field(min_length=1, max_length=500)
    admin_note: str = Field(default="", max_length=2000)


PAGE_SIZES = {10, 25, 50, 100}


def _page_args(page: int, page_size: int) -> tuple[int, int]:
    if page < 1 or page_size not in PAGE_SIZES:
        _api_error("admin_query_invalid")
    return page, page_size


def _page_result(
    items: list[dict[str, Any]], *, page: int, page_size: int, total: int
) -> dict[str, Any]:
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
    }


def _api_error(code: str) -> NoReturn:
    from .auth.errors import APIError

    raise APIError(code)


@router.get("/users")
async def list_users(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    page: int = Query(default=1),
    page_size: int = Query(default=25),
    q: str = Query(default=""),
    status: str = Query(default=""),
    sort: str = Query(default="created_at"),
    order: str = Query(default="desc"),
) -> dict[str, Any]:
    page, page_size = _page_args(page, page_size)
    if status and status not in {"ACTIVE", "DISABLED"}:
        _api_error("admin_query_invalid")
    sort_columns = {
        "created_at": User.created_at,
        "username": User.username,
        "real_name": User.real_name,
        "status": User.status,
    }
    if sort not in sort_columns or order not in {"asc", "desc"}:
        _api_error("admin_query_invalid")
    match_counts = (
        select(MatchParticipant.user_id.label("user_id"), func.count().label("match_count"))
        .group_by(MatchParticipant.user_id)
        .subquery()
    )
    finished_counts = (
        select(
            MatchParticipant.user_id.label("user_id"),
            func.count(func.distinct(MatchParticipant.match_id)).label("finished_count"),
        )
        .join(Match, Match.id == MatchParticipant.match_id)
        .where(Match.status == "FINISHED")
        .group_by(MatchParticipant.user_id)
        .subquery()
    )
    latest_snapshot = (
        select(func.max(LeaderboardSnapshot.generated_at))
        .where(LeaderboardSnapshot.kind == "HUMAN")
        .scalar_subquery()
    )
    leaderboard = (
        select(
            MatchParticipant.user_id.label("user_id"),
            func.coalesce(func.max(LeaderboardSnapshot.wins), 0).label("wins"),
            func.coalesce(func.max(LeaderboardSnapshot.points), 0).label("points"),
            func.coalesce(func.max(LeaderboardSnapshot.average_personal_score), 0).label(
                "average_personal_score"
            ),
        )
        .join(MatchParticipant, MatchParticipant.id == LeaderboardSnapshot.participant_id)
        .where(
            LeaderboardSnapshot.kind == "HUMAN",
            LeaderboardSnapshot.generated_at == latest_snapshot,
        )
        .group_by(MatchParticipant.user_id)
        .subquery()
    )
    filters: list[ColumnElement[bool]] = []
    needle = q.strip()
    if needle:
        filters.append((User.username.ilike(f"%{needle}%")) | (User.real_name.ilike(f"%{needle}%")))
    if status:
        filters.append(User.status == status)
    total = int(await session.scalar(select(func.count()).select_from(User).where(*filters)) or 0)
    sort_column = sort_columns[sort]
    ordered = sort_column.asc() if order == "asc" else sort_column.desc()
    rows = (
        await session.execute(
            select(
                User,
                func.coalesce(match_counts.c.match_count, 0),
                func.coalesce(finished_counts.c.finished_count, 0),
                func.coalesce(leaderboard.c.wins, 0),
                func.coalesce(leaderboard.c.points, 0),
                func.coalesce(leaderboard.c.average_personal_score, 0),
            )
            .outerjoin(match_counts, match_counts.c.user_id == User.id)
            .outerjoin(finished_counts, finished_counts.c.user_id == User.id)
            .outerjoin(leaderboard, leaderboard.c.user_id == User.id)
            .where(*filters)
            .order_by(ordered, User.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = [
        {
            "id": str(user.id),
            "username": user.username,
            "real_name": user.real_name,
            "role": user.role,
            "status": user.status,
            "match_count": int(match_count),
            "finished_count": int(finished_count),
            "wins": int(wins),
            "points": int(points),
            "average_personal_score": float(average_personal_score),
        }
        for user, match_count, finished_count, wins, points, average_personal_score in rows
    ]
    return _page_result(items, page=page, page_size=page_size, total=total)


@router.get("/overview")
async def get_admin_overview(
    request: Request,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    active_matches = int(
        await session.scalar(
            select(func.count()).select_from(Match).where(Match.status.in_({"RUNNING", "PAUSED"}))
        )
        or 0
    )
    enabled_agents = int(
        await session.scalar(
            select(func.count()).select_from(AgentProfile).where(AgentProfile.status == "ENABLED")
        )
        or 0
    )
    enabled_models = int(
        await session.scalar(
            select(func.count()).select_from(ModelProfile).where(ModelProfile.status == "ENABLED")
        )
        or 0
    )
    enabled_voices = int(
        await session.scalar(
            select(func.count()).select_from(VoiceProfile).where(VoiceProfile.status == "ENABLED")
        )
        or 0
    )
    recent_failures = list(
        (
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.result.not_in(("SUCCESS", "SUCCEEDED")))
                .order_by(AuditLog.created_at.desc(), AuditLog.id)
                .limit(4)
            )
        ).all()
    )
    recent_matches = list(
        (
            await session.execute(
                select(Match, Room)
                .join(Room, Room.id == Match.room_id)
                .order_by(Match.created_at.desc(), Match.id)
                .limit(6)
            )
        ).all()
    )
    storage_path = Path(request.app.state.settings.agent_audio_storage_dir)
    storage_path.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(storage_path)
    return {
        "active_matches": active_matches,
        "capacity": 5,
        "enabled_agents": enabled_agents,
        "enabled_models": enabled_models,
        "enabled_voices": enabled_voices,
        "storage": {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "used_ratio": usage.used / usage.total if usage.total else 1.0,
            "estimated_days_remaining": None,
            "automatic_backup": False,
        },
        "recent_failures": [
            {
                "id": str(log.id),
                "action": log.action,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "result": log.result,
                "details": log.details,
                "created_at": log.created_at,
            }
            for log in recent_failures
        ],
        "recent_matches": [
            {
                "id": str(match.id),
                "room_id": str(match.room_id),
                "status": match.status,
                "created_at": match.created_at,
                "ended_at": match.ended_at,
                "archived_at": match.archived_at,
                "context_version": match.context_version,
                "file_count": 0,
                "files_permanent": False,
                "label": room.label,
                "display_topic": str(room.topic_snapshot.get("title", "")),
                "admin_note": match.admin_note,
            }
            for match, room in recent_matches
        ],
    }


@router.patch("/users/{user_id}", dependencies=[Depends(require_browser_origin)])
async def patch_user(
    user_id: UUID,
    payload: UserPatch,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, str]:
    async with session.begin():
        user = await session.get(User, user_id, with_for_update=True)
        if user is None:
            from .auth.errors import APIError

            raise APIError("user_not_found")
        if user.username_normalized == "admin":
            if payload.role is not None and payload.role != "ADMIN":
                _api_error("forbidden")
            if payload.status == "DISABLED":
                _api_error("forbidden")
        elif payload.role == "ADMIN":
            _api_error("forbidden")
        if payload.real_name is not None:
            user.real_name = payload.real_name.strip()
        if payload.role in {"USER", "ADMIN"}:
            user.role = payload.role
        if payload.status in {"ACTIVE", "DISABLED"}:
            user.status = payload.status
        AuditService().record(
            session,
            actor_user_id=context.user_id,
            action="admin.user.updated",
            target_type="user",
            target_id=str(user_id),
            details={
                "fields": [key for key, value in payload.model_dump().items() if value is not None]
            },
        )
    return {"status": "updated"}


@router.delete("/users/{user_id}", dependencies=[Depends(require_browser_origin)])
async def delete_user(
    user_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, str]:
    if user_id == context.user_id:
        _api_error("user_delete_forbidden")
    avatar_path: str | None = None
    async with session.begin():
        user = await session.get(User, user_id, with_for_update=True)
        if user is None:
            _api_error("user_not_found")
        relation_queries = (
            select(Room.id).where(Room.organizer_user_id == user_id).limit(1),
            select(Topic.id).where(Topic.created_by == user_id).limit(1),
            select(Rule.id).where(Rule.created_by == user_id).limit(1),
            select(RoomMember.id).where(RoomMember.user_id == user_id).limit(1),
            select(MatchParticipant.id).where(MatchParticipant.user_id == user_id).limit(1),
            select(Seat.id).where(Seat.user_id == user_id).limit(1),
            select(DeviceCheck.id).where(DeviceCheck.user_id == user_id).limit(1),
            select(Speech.id).where(Speech.user_id == user_id).limit(1),
            select(TranscriptSubmission.id).where(TranscriptSubmission.user_id == user_id).limit(1),
            select(MatchFile.id).where(MatchFile.owner_user_id == user_id).limit(1),
        )
        for query in relation_queries:
            if await session.scalar(query) is not None:
                _api_error("user_has_history")
        avatar_path = user.avatar_path
        await session.execute(delete(UserSession).where(UserSession.user_id == user_id))
        await session.delete(user)
        AuditService().record(
            session,
            actor_user_id=context.user_id,
            action="admin.user.deleted",
            target_type="user",
            target_id=str(user_id),
            details={"username": user.username},
        )
    if avatar_path:
        candidate = Path(request.app.state.settings.avatar_storage_dir) / Path(avatar_path).name
        with suppress(OSError):
            candidate.unlink(missing_ok=True)
    return {"status": "deleted"}


@router.get("/matches")
async def list_matches(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    page: int = Query(default=1),
    page_size: int = Query(default=25),
    q: str = Query(default=""),
    status: str = Query(default=""),
    sort: str = Query(default="created_at"),
    order: str = Query(default="desc"),
) -> dict[str, Any]:
    page, page_size = _page_args(page, page_size)
    statuses = {
        "RUNNING",
        "PAUSED",
        "FINISHED",
        "TERMINATED",
        "START_PENDING_RUNTIME",
        "START_COUNTDOWN",
        "SYSTEM_RECOVERY",
    }
    if status and status not in statuses:
        _api_error("admin_query_invalid")
    sort_columns = {
        "created_at": Match.created_at,
        "ended_at": Match.ended_at,
        "status": Match.status,
    }
    if sort not in sort_columns or order not in {"asc", "desc"}:
        _api_error("admin_query_invalid")
    file_counts = (
        select(
            MatchFile.match_id.label("match_id"),
            func.count().label("file_count"),
            func.sum(case((MatchFile.permanent.is_(True), 1), else_=0)).label("permanent_count"),
        )
        .group_by(MatchFile.match_id)
        .subquery()
    )
    filters: list[ColumnElement[bool]] = []
    needle = q.strip()
    if needle:
        filters.append(
            (Room.label.ilike(f"%{needle}%"))
            | (func.coalesce(Room.topic_snapshot["title"].astext, "").ilike(f"%{needle}%"))
            | (Match.id.cast(String).ilike(f"%{needle}%"))
        )
    if status:
        filters.append(Match.status == status)
    total = int(
        await session.scalar(
            select(func.count())
            .select_from(Match)
            .join(Room, Room.id == Match.room_id)
            .where(*filters)
        )
        or 0
    )
    ordered = sort_columns[sort].asc() if order == "asc" else sort_columns[sort].desc()
    rows = (
        await session.execute(
            select(
                Match,
                Room,
                func.coalesce(file_counts.c.file_count, 0),
                func.coalesce(file_counts.c.permanent_count, 0),
            )
            .join(Room, Room.id == Match.room_id)
            .outerjoin(file_counts, file_counts.c.match_id == Match.id)
            .where(*filters)
            .order_by(ordered, Match.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = [
        {
            "id": str(match.id),
            "room_id": str(match.room_id),
            "status": match.status,
            "created_at": match.created_at,
            "ended_at": match.ended_at,
            "archived_at": match.archived_at,
            "context_version": match.context_version,
            "file_count": int(file_count),
            "files_permanent": bool(file_count) and int(file_count) == int(permanent_count),
            "label": room.label,
            "display_topic": str(room.topic_snapshot.get("title", "")),
            "admin_note": match.admin_note,
        }
        for match, room, file_count, permanent_count in rows
    ]
    return _page_result(items, page=page, page_size=page_size, total=total)


@router.get(
    "/matches/{match_id}/agent-generations",
    response_model=list[AgentGenerationView],
)
async def list_match_agent_generations(
    match_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    limit: int = Query(default=25, ge=1, le=100),
) -> list[AgentGenerationView]:
    if await session.get(Match, match_id) is None:
        _api_error("match_not_found")
    rows = (
        await session.execute(
            select(AgentGeneration, AgentProfile.name)
            .join(AgentProfile, AgentProfile.id == AgentGeneration.agent_profile_id)
            .where(AgentGeneration.match_id == match_id)
            .order_by(AgentGeneration.created_at.desc(), AgentGeneration.id.desc())
            .limit(limit)
        )
    ).all()
    return [agent_generation_view(generation, agent_name) for generation, agent_name in rows]


@router.get(
    "/matches/{match_id}/free-debate-decisions",
    response_model=list[AgentFreeDebateDecisionView],
)
async def list_match_free_debate_decisions(
    match_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AgentFreeDebateDecisionView]:
    if await session.get(Match, match_id) is None:
        _api_error("match_not_found")
    rows = (
        await session.execute(
            select(AgentFreeDebateDecision, AgentProfile.name)
            .join(AgentProfile, AgentProfile.id == AgentFreeDebateDecision.agent_profile_id)
            .where(AgentFreeDebateDecision.match_id == match_id)
            .order_by(
                AgentFreeDebateDecision.created_at.desc(),
                AgentFreeDebateDecision.seat_no,
            )
            .limit(limit)
        )
    ).all()
    return [
        AgentFreeDebateDecisionView(
            id=decision.id,
            action_key=decision.action_key,
            decision_round_id=decision.decision_round_id,
            agent_profile_id=decision.agent_profile_id,
            agent_name=agent_name,
            side=decision.side,
            seat_no=decision.seat_no,
            status=decision.status,
            should_speak=decision.should_speak,
            willingness=decision.willingness,
            attempt_no=decision.attempt_no,
            duration_ms=decision.duration_ms,
            error_code=decision.error_code,
            result_order=decision.result_order,
            final_queue_rank=decision.final_queue_rank,
            human_hand_at_result=decision.human_hand_at_result,
            human_hand_at_lock=decision.human_hand_at_lock,
            selected=decision.selected,
            fallback=decision.fallback,
            started_at=decision.started_at,
            completed_at=decision.completed_at,
        )
        for decision, agent_name in rows
    ]


@router.get(
    "/matches/{match_id}/agent-generations/{generation_id}",
    response_model=AgentGenerationDetail,
)
async def get_match_agent_generation(
    match_id: UUID,
    generation_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> AgentGenerationDetail:
    row = (
        await session.execute(
            select(AgentGeneration, AgentProfile.name)
            .join(AgentProfile, AgentProfile.id == AgentGeneration.agent_profile_id)
            .where(
                AgentGeneration.id == generation_id,
                AgentGeneration.match_id == match_id,
            )
        )
    ).one_or_none()
    if row is None:
        _api_error("match_not_found")
    generation, agent_name = row
    input_snapshot = generation.input_snapshot
    draft_text = generation.llm_draft_text
    if generation.request_blob_id is not None:
        request_payload = await load_content_blob(session, generation.request_blob_id)
        if isinstance(request_payload, dict):
            input_snapshot = cast(dict[str, Any], request_payload)
    if generation.response_blob_id is not None:
        response_payload = await load_content_blob(session, generation.response_blob_id)
        if isinstance(response_payload, dict):
            typed_response = cast(dict[str, Any], response_payload)
            if isinstance(typed_response.get("text"), str):
                draft_text = str(typed_response["text"])
    return AgentGenerationDetail(
        **agent_generation_view(generation, agent_name).model_dump(),
        input_snapshot=input_snapshot,
        llm_draft_text=draft_text,
    )


@router.patch(
    "/matches/{match_id}/metadata",
    dependencies=[Depends(require_browser_origin)],
)
async def patch_match_metadata(
    match_id: UUID,
    payload: MatchMetadataPatch,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, str]:
    async with session.begin():
        match = await session.get(Match, match_id, with_for_update=True)
        if match is None:
            _api_error("match_not_found")
        if match.status not in {"FINISHED", "TERMINATED"}:
            _api_error("match_not_finished")
        room = await session.get(Room, match.room_id, with_for_update=True)
        if room is None:
            _api_error("room_not_found")
        room.label = payload.label.strip()
        room.topic_snapshot = {
            **room.topic_snapshot,
            "title": payload.display_topic.strip(),
        }
        match.admin_note = payload.admin_note.strip()
        AuditService().record(
            session,
            actor_user_id=context.user_id,
            action="admin.match.metadata_updated",
            target_type="match",
            target_id=str(match_id),
            details={"fields": ["label", "display_topic", "admin_note"]},
        )
    return {"status": "updated"}


@router.post(
    "/matches/{match_id}/terminate",
    dependencies=[Depends(require_browser_origin)],
)
async def terminate_match(
    match_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    match = await session.get(Match, match_id)
    if match is None:
        _api_error("match_not_found")
    if match.status in {"FINISHED", "TERMINATED"}:
        _api_error("match_not_running")
    await session.commit()
    manager = cast(MatchRuntimeManager | None, request.app.state.match_runtime_manager)
    if manager is None:
        _api_error("match_runtime_unavailable")
    try:
        state = await manager.snapshot(session, match_id)
        result = await manager.submit(
            match_id,
            MatchCommand(
                type="match.terminate",
                message_id=f"admin-terminate:{match_id}:{uuid4().hex}",
                actor_user_id=context.user_id,
                payload={"privileged": True, "authorized": True},
            ),
        )
    except MatchDomainError as error:
        _api_error(error.code)
    async with session.begin():
        AuditService().record(
            session,
            actor_user_id=context.user_id,
            action="admin.match.terminated",
            target_type="match",
            target_id=str(match_id),
            details={"previous_sequence": state.sequence},
        )
    return {"status": result.state.status, "sequence": result.state.sequence}


@router.post(
    "/matches/{match_id}/judge-retry",
    dependencies=[Depends(require_browser_origin)],
)
async def retry_match_judge(
    match_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, str]:
    match = await session.get(Match, match_id)
    if match is None:
        _api_error("match_not_found")
    if match.status != "FINISHED":
        _api_error("match_not_finished")
    await session.commit()
    service = cast(PostmatchService | None, request.app.state.postmatch_service)
    if service is None:
        _api_error("judge_unavailable")
    result_id = await service.request_judge(match_id, force=True)
    if result_id is None:
        _api_error("judge_unavailable")
    async with session.begin():
        AuditService().record(
            session,
            actor_user_id=context.user_id,
            action="admin.match.judge_retried",
            target_type="match",
            target_id=str(match_id),
            details={"judge_result_id": str(result_id)},
        )
    return {"status": "queued", "judge_result_id": str(result_id)}


@router.post(
    "/matches/{match_id}/audio-retry",
    dependencies=[Depends(require_browser_origin)],
)
async def retry_match_audio(
    match_id: UUID,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, str]:
    async with session.begin():
        match = await session.get(Match, match_id)
        if match is None:
            _api_error("match_not_found")
        if match.status not in {"FINISHED", "TERMINATED"}:
            _api_error("match_not_finished")
        pending = await session.scalar(
            select(BackgroundTask.id).where(
                BackgroundTask.task_type == "POSTMATCH_AUDIO",
                BackgroundTask.status.in_(("PENDING", "RUNNING")),
                func.jsonb_extract_path_text(BackgroundTask.payload, "match_id") == str(match_id),
            )
        )
        if pending is None:
            session.add(
                BackgroundTask(
                    task_type="POSTMATCH_AUDIO",
                    payload={"match_id": str(match_id)},
                    max_attempts=2,
                )
            )
        replay = await session.scalar(
            select(MatchFile).where(
                MatchFile.match_id == match_id,
                MatchFile.file_key == "replay",
            )
        )
        if replay is None:
            session.add(
                MatchFile(
                    match_id=match_id,
                    file_key="replay",
                    file_kind="MATCH_REPLAY",
                    status="PROCESSING",
                )
            )
        else:
            replay.status = "PROCESSING"
            replay.error_code = None
        AuditService().record(
            session,
            actor_user_id=context.user_id,
            action="admin.match.audio_retried",
            target_type="match",
            target_id=str(match_id),
            details={"already_queued": pending is not None},
        )
    return {"status": "queued" if pending is None else "already_queued"}


@router.patch(
    "/matches/{match_id}/judge-result",
    dependencies=[Depends(require_browser_origin)],
)
async def patch_match_judge_result(
    match_id: UUID,
    payload: JudgeResultPatch,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, str]:
    dimensions = {
        "argument": 30,
        "rebuttal": 25,
        "evidence": 20,
        "teamwork": 15,
        "expression": 10,
    }
    for side in ("AFFIRMATIVE", "NEGATIVE"):
        scores = payload.team_scores.get(side)
        if scores is None or set(scores) != set(dimensions) or sum(scores.values()) > 100:
            _api_error("judge_result_invalid")
        if any(scores[key] < 0 or scores[key] > maximum for key, maximum in dimensions.items()):
            _api_error("judge_result_invalid")
    for participant in payload.participants:
        score = participant.get("score")
        if not isinstance(score, int) or not 0 <= score <= 20:
            _api_error("judge_result_invalid")
    async with session.begin():
        match = await session.get(Match, match_id)
        if match is None or match.status != "FINISHED":
            _api_error("match_not_finished")
        result = await session.scalar(
            select(JudgeResult)
            .where(JudgeResult.match_id == match_id)
            .order_by(JudgeResult.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        if result is None:
            _api_error("judge_unavailable")
        result.result = payload.model_dump(mode="json")
        result.status = "SUCCEEDED"
        result.error_code = None
        session.add(
            BackgroundTask(
                task_type="LEADERBOARD_DAILY",
                payload={"requested_by": str(context.user_id), "reason": "judge_updated"},
                max_attempts=2,
            )
        )
        AuditService().record(
            session,
            actor_user_id=context.user_id,
            action="admin.match.judge_updated",
            target_type="match",
            target_id=str(match_id),
            details={"winner": payload.winner},
        )
    return {"status": "updated"}


def _stage_file_deletions(
    storage_paths: list[str], storage_roots: list[str], deletion_id: str
) -> list[tuple[Path, Path]]:
    roots = [Path(item).resolve() for item in storage_roots]
    staged: list[tuple[Path, Path]] = []
    try:
        for raw_path in dict.fromkeys(storage_paths):
            source = Path(raw_path).resolve()
            if not any(source.is_relative_to(root) for root in roots):
                _api_error("match_file_unavailable")
            if not source.exists():
                continue
            if not source.is_file():
                _api_error("match_file_unavailable")
            target = source.with_name(f".{source.name}.delete-{deletion_id}")
            os.replace(source, target)
            staged.append((source, target))
    except OSError:
        for source, target in reversed(staged):
            with suppress(OSError):
                if target.exists():
                    os.replace(target, source)
        _api_error("storage_unavailable")
    except Exception:
        for source, target in reversed(staged):
            with suppress(OSError):
                if target.exists():
                    os.replace(target, source)
        raise
    return staged


def _restore_staged_files(staged: list[tuple[Path, Path]]) -> None:
    for source, target in reversed(staged):
        with suppress(OSError):
            if target.exists():
                os.replace(target, source)


@router.delete(
    "/matches/{match_id}",
    dependencies=[Depends(require_browser_origin)],
)
async def delete_match(
    match_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, str]:
    match = await session.get(Match, match_id)
    if match is None:
        _api_error("match_not_found")
    if match.status not in {"FINISHED", "TERMINATED"}:
        _api_error("match_delete_forbidden")
    room_id = match.room_id
    files = list(
        (
            await session.scalars(
                select(MatchFile).where(
                    MatchFile.match_id == match_id,
                    MatchFile.storage_path.is_not(None),
                )
            )
        ).all()
    )
    storage_paths = [item.storage_path for item in files if item.storage_path]
    blob_ids: set[UUID] = set()
    for request_blob_id, response_blob_id in (
        await session.execute(
            select(AgentGeneration.request_blob_id, AgentGeneration.response_blob_id).where(
                AgentGeneration.match_id == match_id
            )
        )
    ).all():
        blob_ids.update(item for item in (request_blob_id, response_blob_id) if item is not None)
    for request_blob_id, response_blob_id in (
        await session.execute(
            select(JudgeResult.request_blob_id, JudgeResult.response_blob_id).where(
                JudgeResult.match_id == match_id
            )
        )
    ).all():
        blob_ids.update(item for item in (request_blob_id, response_blob_id) if item is not None)
    for request_blob_id, response_blob_id in (
        await session.execute(
            select(ExternalCall.request_blob_id, ExternalCall.response_blob_id).where(
                ExternalCall.match_id == match_id
            )
        )
    ).all():
        blob_ids.update(item for item in (request_blob_id, response_blob_id) if item is not None)
    processing_task = await session.scalar(
        select(BackgroundTask.id).where(
            BackgroundTask.status == "RUNNING",
            func.jsonb_extract_path_text(BackgroundTask.payload, "match_id") == str(match_id),
        )
    )
    processing_judge = await session.scalar(
        select(JudgeResult.id).where(
            JudgeResult.match_id == match_id,
            JudgeResult.status.in_(("PENDING", "RUNNING")),
        )
    )
    if processing_task is not None or processing_judge is not None:
        _api_error("match_delete_processing")
    await session.commit()

    manager = cast(MatchRuntimeManager | None, request.app.state.match_runtime_manager)
    if manager is None:
        _api_error("match_runtime_unavailable")
    try:
        await manager.remove_terminal(match_id)
    except MatchDomainError as error:
        _api_error(error.code)

    staged = _stage_file_deletions(
        storage_paths,
        [
            request.app.state.settings.agent_audio_storage_dir,
            request.app.state.settings.match_audio_storage_dir,
        ],
        uuid4().hex,
    )
    try:
        async with session.begin():
            locked_match = await session.get(Match, match_id, with_for_update=True)
            if locked_match is None:
                _api_error("match_not_found")
            if locked_match.status not in {"FINISHED", "TERMINATED"}:
                _api_error("match_delete_forbidden")
            await session.execute(
                delete(BackgroundTask).where(
                    func.jsonb_extract_path_text(BackgroundTask.payload, "match_id")
                    == str(match_id)
                )
            )
            await session.delete(locked_match)
            await session.flush()
            if blob_ids:
                await session.execute(
                    delete(CallContentBlob).where(
                        CallContentBlob.id.in_(blob_ids),
                        ~select(AgentGeneration.id)
                        .where(
                            (AgentGeneration.request_blob_id == CallContentBlob.id)
                            | (AgentGeneration.response_blob_id == CallContentBlob.id)
                        )
                        .exists(),
                        ~select(JudgeResult.id)
                        .where(
                            (JudgeResult.request_blob_id == CallContentBlob.id)
                            | (JudgeResult.response_blob_id == CallContentBlob.id)
                        )
                        .exists(),
                        ~select(ExternalCall.id)
                        .where(
                            (ExternalCall.request_blob_id == CallContentBlob.id)
                            | (ExternalCall.response_blob_id == CallContentBlob.id)
                        )
                        .exists(),
                    )
                )
            room = await session.get(Room, room_id, with_for_update=True)
            if room is not None:
                await session.delete(room)
            session.add(
                BackgroundTask(
                    task_type="LEADERBOARD_DAILY",
                    payload={"requested_by": str(context.user_id), "reason": "match_deleted"},
                    max_attempts=2,
                )
            )
            AuditService().record(
                session,
                actor_user_id=context.user_id,
                action="admin.match.deleted",
                target_type="match",
                target_id=str(match_id),
                details={
                    "room_id": str(room_id),
                    "status": locked_match.status,
                    "file_count": len(files),
                },
            )
    except Exception:
        _restore_staged_files(staged)
        raise
    for _, target in staged:
        with suppress(OSError):
            target.unlink(missing_ok=True)
    return {"status": "deleted"}


@router.get("/logs")
async def list_logs(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    page: int = Query(default=1),
    page_size: int = Query(default=25),
    q: str = Query(default=""),
    status: str = Query(default=""),
    sort: str = Query(default="created_at"),
    order: str = Query(default="desc"),
) -> dict[str, Any]:
    page, page_size = _page_args(page, page_size)
    if status and status not in {"SUCCESS", "FAILURE", "FAILED"}:
        _api_error("admin_query_invalid")
    sort_columns = {
        "created_at": AuditLog.created_at,
        "action": AuditLog.action,
        "result": AuditLog.result,
    }
    if sort not in sort_columns or order not in {"asc", "desc"}:
        _api_error("admin_query_invalid")
    filters: list[ColumnElement[bool]] = []
    needle = q.strip()
    if needle:
        filters.append(
            (AuditLog.action.ilike(f"%{needle}%"))
            | (AuditLog.target_id.ilike(f"%{needle}%"))
            | (AuditLog.target_type.ilike(f"%{needle}%"))
        )
    if status:
        filters.append(AuditLog.result == status)
    total = int(
        await session.scalar(select(func.count()).select_from(AuditLog).where(*filters)) or 0
    )
    ordered = sort_columns[sort].asc() if order == "asc" else sort_columns[sort].desc()
    logs = list(
        (
            await session.scalars(
                select(AuditLog)
                .where(*filters)
                .order_by(ordered, AuditLog.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    items = [
        {
            "id": str(log.id),
            "action": log.action,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "result": log.result,
            "details": log.details,
            "created_at": log.created_at,
        }
        for log in logs
    ]
    return _page_result(items, page=page, page_size=page_size, total=total)


@router.get("/storage")
async def get_storage_status(
    request: Request,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    path = Path(settings.agent_audio_storage_dir)
    path.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(path)
    recent_bytes = await session.scalar(
        select(func.coalesce(func.sum(MatchFile.byte_count), 0)).where(
            MatchFile.created_at >= func.now() - text("interval '7 days'")
        )
    )
    daily_growth = float(recent_bytes or 0) / 7
    return {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_ratio": usage.used / usage.total if usage.total else 1.0,
        "estimated_days_remaining": round(usage.free / daily_growth, 1)
        if daily_growth > 0
        else None,
        "automatic_backup": False,
    }


@router.patch(
    "/matches/{match_id}/files/permanent",
    dependencies=[Depends(require_browser_origin)],
)
async def update_match_file_retention(
    match_id: UUID,
    payload: PermanentFilesUpdate,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    async with session.begin():
        match = await session.get(Match, match_id)
        if match is None:
            _api_error("match_not_found")
        files = list(
            (
                await session.scalars(
                    select(MatchFile).where(MatchFile.match_id == match_id).with_for_update()
                )
            ).all()
        )
        for file in files:
            file.permanent = payload.permanent
            if payload.permanent:
                file.expires_at = None
            else:
                retention_days = 90 if file.file_kind == "MATCH_REPLAY" else 30
                file.expires_at = file.created_at + timedelta(days=retention_days)
        AuditService().record(
            session,
            actor_user_id=context.user_id,
            action="admin.match.file_retention_updated",
            target_type="match",
            target_id=str(match_id),
            details={"permanent": payload.permanent, "file_count": len(files)},
        )
    return {"permanent": payload.permanent, "file_count": len(files)}


@router.get("/judge-profile")
async def get_judge_profile(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any] | None:
    profile = await session.scalar(
        select(JudgeProfile).order_by(JudgeProfile.updated_at.desc()).limit(1)
    )
    if profile is None:
        return None
    return {
        "id": str(profile.id),
        "model_profile_id": str(profile.model_profile_id),
        "system_prompt": profile.system_prompt,
        "judge_prompt": profile.judge_prompt,
        "generation_params": profile.generation_params,
        "status": profile.status,
    }


@router.put("/judge-profile", dependencies=[Depends(require_browser_origin)])
async def create_judge_profile(
    payload: JudgeProfileCreate,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, str]:
    async with session.begin():
        model = await session.get(ModelProfile, payload.model_profile_id)
        if model is None or model.status != "ENABLED":
            from .auth.errors import APIError

            raise APIError("model_profile_unavailable")
        profiles = list((await session.scalars(select(JudgeProfile).with_for_update())).all())
        for profile in profiles:
            profile.status = "DISABLED"
        profile = JudgeProfile(
            model_profile_id=payload.model_profile_id,
            system_prompt=payload.system_prompt,
            judge_prompt=payload.judge_prompt,
            generation_params=payload.generation_params,
        )
        session.add(profile)
        AuditService().record(
            session,
            actor_user_id=context.user_id,
            action="admin.judge_profile.created",
            target_type="judge_profile",
            target_id=str(profile.id),
        )
        await session.flush()
    return {"id": str(profile.id), "status": "ENABLED"}


@router.post(
    "/models/{model_id}/test",
    dependencies=[Depends(require_browser_origin)],
)
async def test_model_connection(
    model_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    model = await session.get(ModelProfile, model_id)
    if (
        model is None
        or model.status != "ENABLED"
        or not model.base_url
        or not model.model_id
        or model.api_key_ciphertext is None
        or model.api_key_nonce is None
        or settings.llm_key_encryption_key is None
    ):
        _api_error("model_profile_unavailable")
    api_key = decrypt_secret(
        model.api_key_ciphertext,
        model.api_key_nonce,
        settings.llm_key_encryption_key.get_secret_value(),
    )
    await session.commit()
    client = OpenAIStreamingClient(
        base_url=model.base_url,
        api_key=api_key,
        model=model.model_id,
    )
    try:
        result = await client.stream_chat(
            messages=[{"role": "user", "content": "只回复：连接成功"}],
            max_tokens=16,
            generation_params={"temperature": 0},
            on_delta=_ignore_delta,
        )
    except LlmProviderError as error:
        async with session.begin():
            AuditService().record(
                session,
                actor_user_id=context.user_id,
                action="admin.model.tested",
                target_type="model_profile",
                target_id=str(model_id),
                result="FAILURE",
                details={"error_code": error.code},
            )
        _api_error(error.code)
    finally:
        await client.close()
    async with session.begin():
        AuditService().record(
            session,
            actor_user_id=context.user_id,
            action="admin.model.tested",
            target_type="model_profile",
            target_id=str(model_id),
            details={
                "first_token_latency_ms": result.first_token_latency_ms,
                "completed_latency_ms": result.completed_latency_ms,
            },
        )
    return {
        "status": "ok",
        "first_token_latency_ms": result.first_token_latency_ms,
        "completed_latency_ms": result.completed_latency_ms,
    }


async def _ignore_delta(_: str) -> None:
    return None


def _preview_path(settings: Settings, voice_id: UUID) -> Path:
    return Path(settings.agent_audio_storage_dir) / "voice-previews" / f"{voice_id}.ogg"


@router.get("/voices/{voice_id}/preview", response_class=FileResponse)
async def get_voice_preview(
    voice_id: UUID,
    request: Request,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> FileResponse:
    voice = await session.get(VoiceProfile, voice_id)
    path = _preview_path(request.app.state.settings, voice_id)
    if voice is None or not path.is_file():
        from .auth.errors import APIError

        raise APIError("voice_preview_unavailable")
    return FileResponse(path, media_type="audio/ogg", filename=f"{voice.name}.ogg")


@router.post(
    "/voices/{voice_id}/preview",
    dependencies=[Depends(require_browser_origin)],
)
async def regenerate_voice_preview(
    voice_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    voice = await session.get(VoiceProfile, voice_id)
    key = settings.tts_api_key or settings.asr_api_key
    if voice is None or voice.status != "ENABLED" or key is None:
        from .auth.errors import APIError

        raise APIError("voice_profile_unavailable")
    path = _preview_path(settings, voice_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    last_error = "voice_preview_failed"
    for attempt in (1, 2):
        connection = QwenTtsConnection(
            url=settings.tts_ws_url,
            api_key=key.get_secret_value(),
            model=settings.tts_model,
            workspace_id=settings.tts_workspace_id,
        )
        audio = bytearray()

        async def chunks():
            yield VOICE_PREVIEW_TEXT

        async def on_audio(chunk: bytes, target: bytearray = audio) -> None:
            target.extend(chunk)

        try:
            result = await connection.synthesize(
                chunks(), voice=voice.provider_voice, rate=voice.rate, on_audio=on_audio
            )
            preview_audio = await asyncio.to_thread(
                apply_ogg_opus_gain, bytes(audio), voice.playback_gain
            )
            temporary = path.with_suffix(f".attempt-{attempt}.part")
            await asyncio.to_thread(temporary.write_bytes, preview_audio)
            os.replace(temporary, path)
            await session.commit()
            async with session.begin():
                AuditService().record(
                    session,
                    actor_user_id=context.user_id,
                    action="admin.voice.preview.generated",
                    target_type="voice_profile",
                    target_id=str(voice_id),
                    details={"attempt": attempt, "byte_count": len(preview_audio)},
                )
            return {
                "status": "ready",
                "byte_count": len(preview_audio),
                "first_audio_latency_ms": result.first_audio_latency_ms,
            }
        except Exception as error:
            last_error = str(getattr(error, "code", "voice_preview_failed"))
        finally:
            await connection.close()
    from .auth.errors import APIError

    raise APIError(last_error)


__all__ = ["router"]

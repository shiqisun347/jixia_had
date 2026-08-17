"""Post-match review, judging and leaderboard read endpoints."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.dependencies import (
    get_admin_auth,
    get_changed_password_auth,
    get_database_session,
    require_browser_origin,
)
from .auth.errors import APIError
from .auth.session import AuthContext
from .models import (
    AgentProfile,
    BackgroundTask,
    JudgeResult,
    LeaderboardSnapshot,
    Match,
    MatchFile,
    MatchParticipant,
    Room,
    Speech,
    TranscriptSubmission,
    User,
    VoiceProfile,
)
from .postmatch import PostmatchService
from .users.avatar_catalog import is_avatar_key

router = APIRouter()


class ParticipantView(BaseModel):
    id: UUID
    kind: str
    display_name: str
    side: str
    seat_no: int


class PostmatchResponse(BaseModel):
    match_id: UUID
    status: str
    title: str
    label: str
    display_topic: str
    admin_note: str | None
    context_version: int
    speeches: list[dict[str, Any]]
    participants: list[ParticipantView]
    submissions: list[dict[str, Any]]
    judge: dict[str, Any] | None
    can_retry_judge: bool = False
    archived_at: datetime | None
    files: list[dict[str, Any]]


def can_retry_judge_for_viewer(
    *,
    match_status: str,
    judge_status: str | None,
    role: str,
    user_id: UUID,
    organizer_user_id: UUID,
) -> bool:
    return bool(
        match_status == "FINISHED"
        and judge_status == "FAILED"
        and (role == "ADMIN" or user_id == organizer_user_id)
    )


def public_leaderboard_avatar_key(
    kind: str,
    participant_id: UUID,
    human_avatar_keys: dict[UUID, str],
    agent_avatar_keys: dict[UUID, str],
) -> str | None:
    expected_kind = "HUMAN" if kind == "HUMAN" else "AGENT"
    avatar_key = (
        human_avatar_keys.get(participant_id)
        if kind == "HUMAN"
        else agent_avatar_keys.get(participant_id)
    )
    return avatar_key if avatar_key and is_avatar_key(avatar_key, expected_kind) else None


async def _is_participant(session: AsyncSession, match_id: UUID, user_id: UUID) -> bool:
    participant_id = await session.scalar(
        select(MatchParticipant.id).where(
            MatchParticipant.match_id == match_id,
            MatchParticipant.user_id == user_id,
        )
    )
    return participant_id is not None


async def _can_access_terminated(session: AsyncSession, match: Match, context: AuthContext) -> bool:
    if context.role == "ADMIN" or await _is_participant(session, match.id, context.user_id):
        return True
    room = await session.get(Room, match.room_id)
    return room is not None and room.organizer_user_id == context.user_id


async def _response(session: AsyncSession, match: Match, context: AuthContext) -> PostmatchResponse:
    room = await session.get(Room, match.room_id)
    if room is None:
        raise APIError("room_not_found")
    speeches = list(
        (
            await session.scalars(
                select(Speech)
                .where(Speech.match_id == match.id, Speech.status == "FINALIZED")
                .order_by(Speech.created_at)
            )
        ).all()
    )
    participants = list(
        (
            await session.scalars(
                select(MatchParticipant)
                .where(MatchParticipant.match_id == match.id)
                .order_by(MatchParticipant.side, MatchParticipant.seat_no)
            )
        ).all()
    )
    submissions = list(
        (
            await session.execute(
                select(TranscriptSubmission).where(TranscriptSubmission.match_id == match.id)
            )
        )
        .scalars()
        .all()
    )
    judge = await session.scalar(
        select(JudgeResult)
        .where(JudgeResult.match_id == match.id)
        .order_by(JudgeResult.created_at.desc())
        .limit(1)
    )
    is_participant = await _is_participant(session, match.id, context.user_id)
    can_retry_judge = can_retry_judge_for_viewer(
        match_status=match.status,
        judge_status=judge.status if judge else None,
        role=context.role,
        user_id=context.user_id,
        organizer_user_id=room.organizer_user_id,
    )
    files = list(
        (
            await session.scalars(
                select(MatchFile)
                .where(MatchFile.match_id == match.id)
                .order_by(MatchFile.created_at)
            )
        ).all()
    )

    def file_visible(file: MatchFile) -> bool:
        if context.role == "ADMIN":
            return True
        if file.file_kind == "MATCH_REPLAY":
            return is_participant
        return file.file_kind == "HUMAN_RAW" and file.owner_user_id == context.user_id

    return PostmatchResponse(
        match_id=match.id,
        status=match.status,
        title=room.title,
        label=room.label,
        display_topic=str(room.topic_snapshot.get("title", "")),
        admin_note=match.admin_note if context.role == "ADMIN" else None,
        context_version=match.context_version,
        speeches=[
            {
                "id": str(s.id),
                "action_key": s.action_key,
                "user_id": str(s.user_id) if s.user_id else None,
                "speaker_kind": s.speaker_kind,
                "side": s.side,
                "seat_no": s.seat_no,
                "display_text": s.display_text,
                "asr_raw_final_text": s.asr_raw_final_text,
                "finalized_at": s.finalized_at,
            }
            for s in speeches
        ],
        participants=[
            ParticipantView(
                id=p.id, kind=p.kind, display_name=p.display_name, side=p.side, seat_no=p.seat_no
            )
            for p in participants
        ],
        submissions=[
            {
                "user_id": str(s.user_id),
                "context_version": s.context_version,
                "submitted_at": s.submitted_at,
                "auto_submitted": s.auto_submitted,
            }
            for s in submissions
        ],
        judge={
            "id": str(judge.id),
            "status": judge.status,
            "context_version": judge.context_version,
            "result": judge.result,
            "error_code": judge.error_code,
        }
        if judge
        else None,
        can_retry_judge=can_retry_judge,
        archived_at=match.archived_at,
        files=[
            {
                "id": str(file.id),
                "file_kind": file.file_kind,
                "status": file.status,
                "owner_user_id": str(file.owner_user_id) if file.owner_user_id else None,
                "duration_ms": file.duration_ms,
                "byte_count": file.byte_count,
                "error_code": file.error_code,
                "download_url": f"/api/matches/{match.id}/files/{file.id}",
            }
            for file in files
            if file_visible(file)
        ],
    )


@router.get(
    "/api/matches/{match_id}/postmatch", response_model=PostmatchResponse, tags=["postmatch"]
)
async def get_postmatch(
    match_id: UUID,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PostmatchResponse:
    match = await session.get(Match, match_id)
    if match is None:
        raise APIError("match_not_found")
    if match.status == "TERMINATED":
        if not await _can_access_terminated(session, match, context):
            raise APIError("forbidden")
        return await _response(session, match, context)
    if match.status != "FINISHED":
        raise APIError("match_not_finished")
    return await _response(session, match, context)


@router.post(
    "/api/matches/{match_id}/transcripts/submit",
    response_model=PostmatchResponse,
    tags=["postmatch"],
    dependencies=[Depends(require_browser_origin)],
)
async def submit_transcripts(
    match_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PostmatchResponse:
    match = await session.get(Match, match_id)
    if match is None or not await _is_participant(session, match_id, context.user_id):
        raise APIError("room_member_required")
    if match.status != "FINISHED":
        raise APIError("match_not_finished")
    await session.commit()
    async with session.begin():
        current = await session.scalar(
            select(TranscriptSubmission)
            .where(
                TranscriptSubmission.match_id == match_id,
                TranscriptSubmission.user_id == context.user_id,
            )
            .with_for_update()
        )
        if current is None:
            session.add(
                TranscriptSubmission(
                    match_id=match_id,
                    user_id=context.user_id,
                    context_version=match.context_version,
                )
            )
        else:
            current.context_version = match.context_version
            current.auto_submitted = False
        human_ids = set(
            (
                await session.scalars(
                    select(MatchParticipant.user_id).where(
                        MatchParticipant.match_id == match_id,
                        MatchParticipant.kind == "HUMAN",
                        MatchParticipant.user_id.is_not(None),
                    )
                )
            ).all()
        )
        submitted_ids = set(
            (
                await session.scalars(
                    select(TranscriptSubmission.user_id).where(
                        TranscriptSubmission.match_id == match_id
                    )
                )
            ).all()
        )
        if human_ids and human_ids <= (submitted_ids | {context.user_id}):
            match.archived_at = match.archived_at or match.ended_at
    return await _response(session, match, context)


@router.post(
    "/api/matches/{match_id}/judge/retry",
    response_model=PostmatchResponse,
    tags=["postmatch"],
    dependencies=[Depends(require_browser_origin)],
)
async def retry_judge(
    match_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PostmatchResponse:
    match = await session.get(Match, match_id)
    if match is None:
        raise APIError("match_not_found")
    if match.status != "FINISHED":
        raise APIError("match_not_finished")
    room = await session.get(Room, match.room_id)
    if context.role != "ADMIN" and (room is None or room.organizer_user_id != context.user_id):
        raise APIError("forbidden")
    service = cast(PostmatchService | None, request.app.state.postmatch_service)
    if service is None:
        raise APIError("judge_unavailable")
    await service.request_judge(match_id, force=True)
    return await _response(session, match, context)


@router.get("/api/matches/{match_id}/downloads/transcript", tags=["postmatch"])
async def download_transcript(
    match_id: UUID,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> JSONResponse:
    match = await session.get(Match, match_id)
    if match is None or match.status not in {"FINISHED", "TERMINATED"}:
        raise APIError("match_not_finished")
    if match.status == "TERMINATED" and not await _can_access_terminated(session, match, context):
        raise APIError("forbidden")
    response = await _response(session, match, context)
    payload = {
        "match_id": str(match.id),
        "status": match.status,
        "context_version": match.context_version,
        "participants": [item.model_dump(mode="json") for item in response.participants],
        "speeches": [
            {
                "id": item["id"],
                "speaker_kind": item["speaker_kind"],
                "side": item["side"],
                "seat_no": item["seat_no"],
                "display_text": item["display_text"],
                "finalized_at": (
                    item["finalized_at"].isoformat() if item["finalized_at"] else None
                ),
            }
            for item in response.speeches
        ],
        "judge": response.judge,
    }
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f'attachment; filename="jixia-{match.id}-transcript.json"'},
    )


@router.get("/api/matches/{match_id}/files/{file_id}", response_class=FileResponse)
async def download_match_file(
    match_id: UUID,
    file_id: UUID,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> FileResponse:
    match = await session.get(Match, match_id)
    file = await session.get(MatchFile, file_id)
    if match is None or file is None or file.match_id != match_id:
        raise APIError("match_file_not_found")
    if match.status not in {"FINISHED", "TERMINATED"}:
        raise APIError("match_not_finished")
    if match.status == "TERMINATED" and not await _can_access_terminated(session, match, context):
        raise APIError("forbidden")
    is_participant = await _is_participant(session, match_id, context.user_id)
    allowed = (
        context.role == "ADMIN"
        or (file.file_kind == "MATCH_REPLAY" and is_participant)
        or (file.file_kind == "HUMAN_RAW" and file.owner_user_id == context.user_id)
    )
    if not allowed:
        raise APIError("forbidden")
    if file.status != "READY" or not file.storage_path:
        raise APIError("match_file_unavailable")
    path = Path(file.storage_path)
    if not path.is_file():
        raise APIError("match_file_unavailable")
    extension = ".opus" if file.file_kind == "MATCH_REPLAY" else ".pcm"
    media_type = "audio/ogg" if file.file_kind == "MATCH_REPLAY" else "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=f"jixia-{match_id}-{file.file_key}{extension}",
    )


@router.get("/api/leaderboards", tags=["leaderboards"])
async def get_leaderboards(
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    latest = await session.scalar(
        select(LeaderboardSnapshot.generated_at)
        .order_by(LeaderboardSnapshot.generated_at.desc())
        .limit(1)
    )
    if latest is None:
        return {"generated_at": None, "human": [], "agent": []}
    rows = list(
        (
            await session.scalars(
                select(LeaderboardSnapshot)
                .where(LeaderboardSnapshot.generated_at == latest)
                .order_by(LeaderboardSnapshot.kind, LeaderboardSnapshot.rank)
            )
        ).all()
    )
    human_ids = [row.participant_id for row in rows if row.kind == "HUMAN"]
    agent_ids = [row.participant_id for row in rows if row.kind == "AGENT"]
    human_avatar_keys: dict[UUID, str] = {}
    if human_ids:
        users = list((await session.scalars(select(User).where(User.id.in_(human_ids)))).all())
        human_avatar_keys = {user.id: user.default_avatar_key for user in users}
    agent_avatar_keys: dict[UUID, str] = {}
    if agent_ids:
        agent_rows = (
            await session.execute(
                select(AgentProfile.id, VoiceProfile.avatar_key)
                .join(VoiceProfile, VoiceProfile.id == AgentProfile.voice_profile_id)
                .where(AgentProfile.id.in_(agent_ids))
            )
        ).all()
        agent_avatar_keys = {
            agent_id: avatar_key for agent_id, avatar_key in agent_rows if avatar_key is not None
        }

    def serialize(row: LeaderboardSnapshot) -> dict[str, Any]:
        return {
            "rank": row.rank,
            "participant_id": str(row.participant_id),
            "display_name": row.display_name,
            "points": row.points,
            "wins": row.wins,
            "matches": row.matches,
            "average_personal_score": row.average_personal_score,
            "avatar_key": public_leaderboard_avatar_key(
                row.kind,
                row.participant_id,
                human_avatar_keys,
                agent_avatar_keys,
            ),
        }

    return {
        "generated_at": latest,
        "human": [serialize(row) for row in rows if row.kind == "HUMAN"],
        "agent": [serialize(row) for row in rows if row.kind == "AGENT"],
    }


@router.post(
    "/api/admin/leaderboards/rebuild",
    tags=["admin"],
    dependencies=[Depends(require_browser_origin)],
)
async def rebuild_leaderboards(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, str]:
    async with session.begin():
        session.add(
            BackgroundTask(
                task_type="LEADERBOARD_DAILY",
                payload={"requested_by": "admin"},
                max_attempts=2,
            )
        )
    return {"status": "queued"}


__all__ = ["router"]

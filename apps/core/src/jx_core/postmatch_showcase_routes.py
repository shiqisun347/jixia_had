"""Conference postmatch presentation, judging and audience voting."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.dependencies import (
    get_changed_password_auth,
    get_database_session,
    require_browser_origin,
)
from .auth.errors import APIError
from .auth.session import AuthContext
from .models import (
    AudienceVote,
    Match,
    MatchParticipant,
    PostmatchJudgeEntry,
    PostmatchShowcase,
    Room,
    RoomMember,
)

router = APIRouter(tags=["postmatch-showcase"])
SHOWCASE_PHASES = {"JUDGE_REVIEW", "AUDIENCE_VOTE", "AUDIENCE_RESULTS", "AWARDS", "CLOSED"}
WINNER_SIDES = {"AFFIRMATIVE", "NEGATIVE", "DRAW"}


class ShowcasePhaseRequest(BaseModel):
    phase: Literal["JUDGE_REVIEW", "AUDIENCE_VOTE", "AUDIENCE_RESULTS", "AWARDS", "CLOSED"]
    published: bool = True
    display_payload: dict[str, Any] = Field(default_factory=dict)


class JudgeEntryRequest(BaseModel):
    comments: str = Field(default="", max_length=20_000)
    winner_side: Literal["AFFIRMATIVE", "NEGATIVE", "DRAW"]
    best_participant_id: UUID
    published: bool = False


class AudienceVoteRequest(BaseModel):
    winner_side: Literal["AFFIRMATIVE", "NEGATIVE", "DRAW"]
    ranking: list[UUID] = Field(min_length=1, max_length=16)


class ShowcaseParticipant(BaseModel):
    id: UUID
    display_name: str
    kind: str
    side: str
    seat_no: int


class ShowcaseResponse(BaseModel):
    match_id: UUID
    phase: str
    published: bool
    display_payload: dict[str, Any]
    participants: list[ShowcaseParticipant]
    judge: dict[str, Any] | None
    audience: dict[str, Any]
    can_manage: bool
    has_voted: bool


async def _access(
    session: AsyncSession, match_id: UUID, context: AuthContext
) -> tuple[Match, Room, bool]:
    match = await session.get(Match, match_id)
    if match is None:
        raise APIError("match_not_found")
    room = await session.get(Room, match.room_id)
    if room is None:
        raise APIError("room_not_found")
    member = await session.scalar(
        select(RoomMember).where(
            RoomMember.room_id == room.id,
            RoomMember.user_id == context.user_id,
        )
    )
    can_manage = context.role == "ADMIN" or room.organizer_user_id == context.user_id
    if member is None and not can_manage:
        raise APIError("room_member_required")
    if match.status not in {"FINISHED", "TERMINATED"}:
        raise APIError("match_not_finished")
    return match, room, can_manage


async def _showcase(session: AsyncSession, match_id: UUID) -> PostmatchShowcase:
    current = await session.get(PostmatchShowcase, match_id)
    if current is not None:
        return current
    # GET may create the durable default once; this is idempotent and does not
    # touch the match snapshot or any historical result.
    await session.commit()
    current = PostmatchShowcase(match_id=match_id)
    session.add(current)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        current = await session.get(PostmatchShowcase, match_id)
        if current is None:
            raise APIError("internal_server_error") from None
    return current


async def _participants(session: AsyncSession, match_id: UUID) -> list[MatchParticipant]:
    return list(
        (
            await session.scalars(
                select(MatchParticipant)
                .where(MatchParticipant.match_id == match_id)
                .order_by(MatchParticipant.side, MatchParticipant.seat_no)
            )
        ).all()
    )


async def _audience_totals(
    session: AsyncSession,
    match_id: UUID,
    context_user_id: UUID,
    participants: list[MatchParticipant],
) -> tuple[int, bool, dict[str, int], dict[UUID, int], dict[UUID, int]]:
    """Aggregate votes incrementally so a large audience never fills Core RSS."""

    participant_count = len(participants)
    point_totals: dict[UUID, int] = {participant.id: 0 for participant in participants}
    ranking_votes: dict[UUID, int] = {participant.id: 0 for participant in participants}
    winner_counts = {side: 0 for side in sorted(WINNER_SIDES)}
    total_votes = 0
    has_voted = False
    result = await session.stream(
        select(
            AudienceVote.voter_user_id,
            AudienceVote.winner_side,
            AudienceVote.ranking,
        ).where(AudienceVote.match_id == match_id)
    )
    async for voter_user_id, winner_side, ranking in result:
        total_votes += 1
        has_voted = has_voted or voter_user_id == context_user_id
        winner_counts[winner_side] = winner_counts.get(winner_side, 0) + 1
        for index, raw_participant_id in enumerate(ranking):
            participant_id = UUID(str(raw_participant_id))
            if participant_id in point_totals:
                point_totals[participant_id] += max(1, participant_count - index)
                ranking_votes[participant_id] += 1
    return total_votes, has_voted, winner_counts, point_totals, ranking_votes


async def _response(
    session: AsyncSession,
    match_id: UUID,
    context: AuthContext,
    *,
    can_manage: bool,
) -> ShowcaseResponse:
    showcase = await _showcase(session, match_id)
    participants = await _participants(session, match_id)
    judge = await session.scalar(
        select(PostmatchJudgeEntry).where(PostmatchJudgeEntry.match_id == match_id)
    )
    total_votes, has_voted, winner_counts, point_totals, ranking_votes = await _audience_totals(
        session, match_id, context.user_id, participants
    )
    visible_judge = can_manage or (
        showcase.is_published and judge is not None and judge.is_published
    )
    visible_results = can_manage or (
        showcase.is_published and showcase.phase in {"AUDIENCE_RESULTS", "AWARDS", "CLOSED"}
    )

    ordered = sorted(
        participants,
        key=lambda participant: (
            -point_totals[participant.id],
            -ranking_votes[participant.id],
            participant.side,
            participant.seat_no,
        ),
    )
    audience: dict[str, Any] = {"total_votes": total_votes, "winner_counts": {}, "ranking": []}
    if visible_results:
        audience["winner_counts"] = winner_counts
        audience["ranking"] = [
            {
                "rank": index,
                "participant_id": participant.id,
                "display_name": participant.display_name,
                "side": participant.side,
                "seat_no": participant.seat_no,
                "points": point_totals[participant.id],
                "ranking_votes": ranking_votes[participant.id],
            }
            for index, participant in enumerate(ordered, start=1)
        ]
    return ShowcaseResponse(
        match_id=match_id,
        phase=showcase.phase,
        published=showcase.is_published,
        display_payload=showcase.display_payload if can_manage or showcase.is_published else {},
        participants=[
            ShowcaseParticipant(
                id=participant.id,
                display_name=participant.display_name,
                kind=participant.kind,
                side=participant.side,
                seat_no=participant.seat_no,
            )
            for participant in participants
        ],
        judge=(
            {
                "id": judge.id,
                "comments": judge.comments,
                "winner_side": judge.winner_side,
                "best_participant_id": judge.best_participant_id,
                "is_published": judge.is_published,
                "published_at": judge.published_at,
            }
            if judge is not None and visible_judge
            else None
        ),
        audience=audience,
        can_manage=can_manage,
        has_voted=has_voted,
    )


@router.get(
    "/api/matches/{match_id}/showcase",
    response_model=ShowcaseResponse,
    response_model_exclude_none=True,
)
async def get_showcase(
    match_id: UUID,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ShowcaseResponse:
    _, _, can_manage = await _access(session, match_id, context)
    return await _response(session, match_id, context, can_manage=can_manage)


@router.post(
    "/api/matches/{match_id}/showcase/phase",
    response_model=ShowcaseResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_browser_origin)],
)
async def update_showcase_phase(
    match_id: UUID,
    payload: ShowcasePhaseRequest,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ShowcaseResponse:
    _, _, can_manage = await _access(session, match_id, context)
    if not can_manage:
        raise APIError("forbidden")
    showcase = await _showcase(session, match_id)
    showcase.phase = payload.phase
    showcase.is_published = payload.published
    showcase.display_payload = payload.display_payload
    showcase.updated_by_user_id = context.user_id
    await session.commit()
    return await _response(session, match_id, context, can_manage=True)


@router.post(
    "/api/matches/{match_id}/showcase/judge",
    response_model=ShowcaseResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_browser_origin)],
)
async def save_judge_entry(
    match_id: UUID,
    payload: JudgeEntryRequest,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ShowcaseResponse:
    _, _, can_manage = await _access(session, match_id, context)
    if not can_manage:
        raise APIError("forbidden")
    participant = await session.scalar(
        select(MatchParticipant).where(
            MatchParticipant.id == payload.best_participant_id,
            MatchParticipant.match_id == match_id,
        )
    )
    if participant is None:
        raise APIError("postmatch_judge_invalid")
    entry = await session.scalar(
        select(PostmatchJudgeEntry).where(PostmatchJudgeEntry.match_id == match_id)
    )
    if entry is None:
        entry = PostmatchJudgeEntry(
            match_id=match_id,
            judge_user_id=context.user_id,
            comments=payload.comments.strip(),
            winner_side=payload.winner_side,
            best_participant_id=payload.best_participant_id,
            is_published=payload.published,
            published_at=datetime.now(UTC) if payload.published else None,
        )
        session.add(entry)
    else:
        entry.judge_user_id = context.user_id
        entry.comments = payload.comments.strip()
        entry.winner_side = payload.winner_side
        entry.best_participant_id = payload.best_participant_id
        entry.is_published = payload.published
        entry.published_at = datetime.now(UTC) if payload.published else None
    await session.commit()
    return await _response(session, match_id, context, can_manage=True)


@router.post(
    "/api/matches/{match_id}/showcase/vote",
    response_model=ShowcaseResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_browser_origin)],
)
async def submit_audience_vote(
    match_id: UUID,
    payload: AudienceVoteRequest,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ShowcaseResponse:
    _, _, can_manage = await _access(session, match_id, context)
    showcase = await _showcase(session, match_id)
    if showcase.phase != "AUDIENCE_VOTE" or not showcase.is_published:
        raise APIError("audience_vote_closed")
    participants = await _participants(session, match_id)
    participant_ids = {participant.id for participant in participants}
    if len(participants) != 8 or len(payload.ranking) != 8:
        raise APIError("audience_vote_invalid_ranking")
    if set(payload.ranking) != participant_ids:
        raise APIError("audience_vote_invalid_ranking")
    existing = await session.scalar(
        select(AudienceVote).where(
            AudienceVote.match_id == match_id,
            AudienceVote.voter_user_id == context.user_id,
        )
    )
    if existing is not None:
        raise APIError("audience_vote_duplicate")
    session.add(
        AudienceVote(
            match_id=match_id,
            voter_user_id=context.user_id,
            winner_side=payload.winner_side,
            ranking=[str(participant_id) for participant_id in payload.ranking],
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise APIError("audience_vote_duplicate") from None
    return await _response(session, match_id, context, can_manage=can_manage)

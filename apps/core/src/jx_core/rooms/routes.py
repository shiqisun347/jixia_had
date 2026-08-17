"""HTTP boundary for public rooms and preparation state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, NoReturn, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import (
    get_changed_password_auth,
    get_database_session,
    require_browser_origin,
)
from ..auth.errors import APIError, AuthError
from ..auth.schemas import TermsResponse
from ..auth.session import AuthContext
from ..config import Settings
from ..legal.terms import get_current_human_participation_terms
from ..models import AgentProfile, Match, RoomMember, Seat, SeatSwapRequest, User, VoiceProfile
from .schemas import (
    DeviceCheckRequest,
    DeviceCheckSummaryResponse,
    LobbyRoomResponse,
    MemberResponse,
    ReadyRequest,
    RoleChangeRequest,
    RoomCodeLookupResponse,
    RoomCreateRequest,
    RoomJoinRequest,
    RoomSnapshotResponse,
    SeatResponse,
    SeatSelectRequest,
    SeatSwapCreateRequest,
    SeatSwapRespondRequest,
    SeatSwapResponse,
)
from .service import RoomService

router = APIRouter()


def _raise(error: AuthError) -> NoReturn:
    raise APIError(error.code, error.field_errors) from None


async def _snapshot(
    service: RoomService,
    database_session: AsyncSession,
    room_id: UUID,
    viewer_user_id: UUID,
) -> RoomSnapshotResponse:
    try:
        room, members, seats = await service.snapshot(database_session, room_id=room_id)
    except AuthError as error:
        _raise(error)
    viewer = await service.viewer_context(database_session, room_id=room_id, user_id=viewer_user_id)
    user_ids = [member.user_id for member in members]
    agent_ids = [seat.agent_profile_id for seat in seats if seat.agent_profile_id is not None]
    user_rows = (
        await database_session.execute(
            select(
                User.id,
                User.real_name,
                User.default_avatar_key,
                User.avatar_version,
                User.avatar_path,
            ).where(User.id.in_(user_ids))
        )
    ).all()
    agent_rows = (
        await database_session.execute(
            select(AgentProfile.id, AgentProfile.name, VoiceProfile.avatar_key)
            .join(VoiceProfile, VoiceProfile.id == AgentProfile.voice_profile_id)
            .where(AgentProfile.id.in_(agent_ids))
        )
    ).all()
    user_names: dict[UUID, str] = {row[0]: row[1] for row in user_rows}
    agent_names: dict[UUID, str] = {row[0]: row[1] for row in agent_rows}
    user_avatars: dict[UUID, tuple[str, int, bool]] = {
        row[0]: (row[2], row[3], row[4] is not None) for row in user_rows
    }
    agent_avatars: dict[UUID, str] = {row[0]: row[2] for row in agent_rows}
    current = viewer.member if viewer.member is not None and viewer.member.left_at is None else None
    viewer_membership_state = (
        "ACTIVE" if current is not None else "LEFT" if viewer.member else "NONE"
    )
    latest_check = viewer.latest_device_check
    now = datetime.now(UTC)
    return RoomSnapshotResponse(
        id=room.id,
        code=room.code,
        title=room.title,
        label=room.label,
        status=room.status,
        organizer_user_id=room.organizer_user_id,
        is_all_agent=room.is_all_agent,
        auto_fill_agents=room.auto_fill_agents,
        sequence=room.sequence,
        topic=room.topic_snapshot,
        rule=room.rule_snapshot,
        members=[
            MemberResponse(
                user_id=member.user_id,
                member_role=member.member_role,
                online=member.online,
                ready=member.ready,
                joined_at=member.joined_at,
                real_name=user_names.get(member.user_id, "未知用户"),
                default_avatar_key=user_avatars.get(member.user_id, ("human-01", 0, False))[0],
                avatar_version=user_avatars.get(member.user_id, ("human-01", 0, False))[1],
                has_custom_avatar=user_avatars.get(member.user_id, ("human-01", 0, False))[2],
            )
            for member in members
        ],
        seats=[
            SeatResponse(
                id=seat.id,
                side=seat.side,
                seat_no=seat.seat_no,
                occupant_type=seat.occupant_type,
                user_id=seat.user_id,
                agent_profile_id=seat.agent_profile_id,
                occupant_name=user_names.get(seat.user_id)
                if seat.user_id is not None
                else agent_names.get(seat.agent_profile_id)
                if seat.agent_profile_id is not None
                else None,
                occupant_avatar_key=(
                    user_avatars.get(seat.user_id, ("human-01", 0, False))[0]
                    if seat.user_id is not None
                    else agent_avatars.get(seat.agent_profile_id)
                    if seat.agent_profile_id is not None
                    else None
                ),
                occupant_avatar_version=(
                    user_avatars.get(seat.user_id, ("human-01", 0, False))[1]
                    if seat.user_id is not None
                    else None
                ),
                occupant_has_custom_avatar=(
                    user_avatars.get(seat.user_id, ("human-01", 0, False))[2]
                    if seat.user_id is not None
                    else False
                ),
            )
            for seat in seats
        ],
        match_id=viewer.match_id,
        viewer_membership_state=viewer_membership_state,
        viewer_member_role=viewer.member.member_role if viewer.member is not None else None,
        viewer_ready=bool(current and current.ready),
        latest_device_check=(
            DeviceCheckSummaryResponse(
                check_version=latest_check.check_version,
                status=cast(Literal["PASS", "WARN", "FAIL"], latest_check.status),
                checked_at=latest_check.checked_at,
                valid_until=latest_check.valid_until,
                is_valid=(
                    latest_check.status in ("PASS", "WARN") and latest_check.valid_until > now
                ),
            )
            if latest_check is not None
            else None
        ),
    )


@router.get(
    "/api/legal/human-participation/current",
    response_model=TermsResponse,
    tags=["legal"],
)
async def current_human_participation_terms() -> TermsResponse:
    terms = get_current_human_participation_terms()
    return TermsResponse(version=terms.version, title=terms.title, body=terms.body)


@router.get("/api/lobby/rooms", response_model=list[LobbyRoomResponse], tags=["lobby"])
async def list_lobby_rooms(
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[LobbyRoomResponse]:
    service = RoomService()
    response: list[LobbyRoomResponse] = []
    active_spectators = await service.active_spectator_count(database_session)
    spectator_remaining = max(0, 10 - active_spectators)
    rooms = await service.lobby(database_session)
    room_ids = [room.id for room in rooms]
    occupied_by_room: dict[UUID, int] = {}
    spectators_by_room: dict[UUID, int] = {}
    members_by_room: dict[UUID, RoomMember] = {}
    matches_by_room: dict[UUID, UUID] = {}
    if room_ids:
        occupied_by_room = {
            row[0]: int(row[1])
            for row in (
                await database_session.execute(
                    select(Seat.room_id, func.count())
                    .where(Seat.room_id.in_(room_ids), Seat.occupant_type != "EMPTY")
                    .group_by(Seat.room_id)
                )
            ).all()
        }
        spectators_by_room = {
            row[0]: int(row[1])
            for row in (
                await database_session.execute(
                    select(RoomMember.room_id, func.count())
                    .where(
                        RoomMember.room_id.in_(room_ids),
                        RoomMember.member_role == "SPECTATOR",
                        RoomMember.left_at.is_(None),
                    )
                    .group_by(RoomMember.room_id)
                )
            ).all()
        }
        members_by_room = {
            member.room_id: member
            for member in (
                await database_session.scalars(
                    select(RoomMember).where(
                        RoomMember.room_id.in_(room_ids),
                        RoomMember.user_id == context.user_id,
                    )
                )
            ).all()
        }
        matches_by_room = {
            row[0]: row[1]
            for row in (
                await database_session.execute(
                    select(Match.room_id, Match.id).where(Match.room_id.in_(room_ids))
                )
            ).all()
        }
    for room in rooms:
        member = members_by_room.get(room.id)
        current = member if member is not None and member.left_at is None else None
        response.append(
            LobbyRoomResponse(
                id=room.id,
                code=room.code,
                title=room.title,
                label=room.label,
                status=room.status,
                auto_fill_agents=room.auto_fill_agents,
                topic_title=str(room.topic_snapshot.get("title", "")),
                rule_name=str(room.rule_snapshot.get("name", "")),
                side_size=int(room.rule_snapshot.get("side_size", 0)),
                occupied_seats=occupied_by_room.get(room.id, 0),
                spectator_count=spectators_by_room.get(room.id, 0),
                spectator_remaining=spectator_remaining,
                spectator_capacity_full=spectator_remaining == 0,
                match_id=matches_by_room.get(room.id),
                viewer_membership_state=(
                    "ACTIVE" if current is not None else "LEFT" if member else "NONE"
                ),
                viewer_member_role=member.member_role if member is not None else None,
                viewer_ready=bool(current and current.ready),
            )
        )
    return response


@router.get("/api/rooms/lookup", response_model=RoomCodeLookupResponse, tags=["rooms"])
async def lookup_room_code(
    code: str,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoomCodeLookupResponse:
    del context
    try:
        room = await RoomService().lookup_room_by_code(database_session, code=code)
    except AuthError as error:
        _raise(error)
    return RoomCodeLookupResponse(room_id=room.id, code=room.code, status=room.status)


@router.post(
    "/api/rooms",
    response_model=RoomSnapshotResponse,
    tags=["rooms"],
    dependencies=[Depends(require_browser_origin)],
)
async def create_room(
    payload: RoomCreateRequest,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoomSnapshotResponse:
    service = RoomService()
    try:
        room = await service.create_room(
            database_session,
            organizer_user_id=context.user_id,
            organizer_role=context.role,
            payload=payload,
        )
    except AuthError as error:
        _raise(error)
    return await _snapshot(service, database_session, room.id, context.user_id)


@router.get("/api/rooms/{room_id}/snapshot", response_model=RoomSnapshotResponse, tags=["rooms"])
async def get_room_snapshot(
    room_id: UUID,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoomSnapshotResponse:
    return await _snapshot(RoomService(), database_session, room_id, context.user_id)


@router.post(
    "/api/rooms/{room_id}/join",
    response_model=RoomSnapshotResponse,
    tags=["rooms"],
    dependencies=[Depends(require_browser_origin)],
)
async def join_room(
    room_id: UUID,
    payload: RoomJoinRequest,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoomSnapshotResponse:
    service = RoomService()
    try:
        await service.join_room(
            database_session, user_id=context.user_id, room_id=room_id, payload=payload
        )
    except AuthError as error:
        _raise(error)
    return await _snapshot(service, database_session, room_id, context.user_id)


@router.post(
    "/api/rooms/{room_id}/seat",
    response_model=RoomSnapshotResponse,
    tags=["rooms"],
    dependencies=[Depends(require_browser_origin)],
)
async def select_room_seat(
    room_id: UUID,
    payload: SeatSelectRequest,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoomSnapshotResponse:
    service = RoomService()
    try:
        await service.select_seat(
            database_session, user_id=context.user_id, room_id=room_id, payload=payload
        )
    except AuthError as error:
        _raise(error)
    return await _snapshot(service, database_session, room_id, context.user_id)


async def _swap_response(database_session: AsyncSession, item: SeatSwapRequest) -> SeatSwapResponse:
    requester = await database_session.get(User, item.requester_user_id)
    target = await database_session.get(User, item.target_user_id)
    return SeatSwapResponse(
        id=item.id,
        room_id=item.room_id,
        requester_user_id=item.requester_user_id,
        target_user_id=item.target_user_id,
        requester_seat_id=item.requester_seat_id,
        target_seat_id=item.target_seat_id,
        requester_name=requester.real_name if requester else "未知用户",
        target_name=target.real_name if target else "未知用户",
        status=cast(Literal["PENDING", "ACCEPTED", "REJECTED", "CANCELLED"], item.status),
        created_at=item.created_at,
    )


@router.get(
    "/api/rooms/{room_id}/seat-swap-requests", response_model=list[SeatSwapResponse], tags=["rooms"]
)
async def list_seat_swap_requests(
    room_id: UUID,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[SeatSwapResponse]:
    service = RoomService()
    return [
        await _swap_response(database_session, item)
        for item in await service.list_seat_swap_requests(
            database_session, user_id=context.user_id, room_id=room_id
        )
    ]


@router.post(
    "/api/rooms/{room_id}/seat-swap-requests",
    response_model=SeatSwapResponse,
    tags=["rooms"],
    dependencies=[Depends(require_browser_origin)],
)
async def create_seat_swap_request(
    room_id: UUID,
    payload: SeatSwapCreateRequest,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> SeatSwapResponse:
    service = RoomService()
    try:
        item = await service.create_seat_swap_request(
            database_session, user_id=context.user_id, room_id=room_id, payload=payload
        )
    except AuthError as error:
        _raise(error)
    return await _swap_response(database_session, item)


@router.post(
    "/api/rooms/{room_id}/seat-swap-requests/{request_id}/respond",
    response_model=SeatSwapResponse,
    tags=["rooms"],
    dependencies=[Depends(require_browser_origin)],
)
async def respond_seat_swap_request(
    room_id: UUID,
    request_id: UUID,
    payload: SeatSwapRespondRequest,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> SeatSwapResponse:
    service = RoomService()
    try:
        item = await service.respond_seat_swap_request(
            database_session,
            user_id=context.user_id,
            room_id=room_id,
            request_id=request_id,
            payload=payload,
        )
    except AuthError as error:
        _raise(error)
    return await _swap_response(database_session, item)


@router.post(
    "/api/rooms/{room_id}/device-check",
    response_model=RoomSnapshotResponse,
    tags=["rooms"],
    dependencies=[Depends(require_browser_origin)],
)
async def save_device_check(
    room_id: UUID,
    payload: DeviceCheckRequest,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoomSnapshotResponse:
    service = RoomService()
    try:
        await service.save_device_check(
            database_session, user_id=context.user_id, room_id=room_id, payload=payload
        )
    except AuthError as error:
        _raise(error)
    return await _snapshot(service, database_session, room_id, context.user_id)


@router.post(
    "/api/rooms/{room_id}/device-check/invalidate",
    response_model=RoomSnapshotResponse,
    tags=["rooms"],
    dependencies=[Depends(require_browser_origin)],
)
async def invalidate_device_check(
    room_id: UUID,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoomSnapshotResponse:
    service = RoomService()
    try:
        await service.invalidate_device_check(
            database_session, user_id=context.user_id, room_id=room_id
        )
    except AuthError as error:
        _raise(error)
    return await _snapshot(service, database_session, room_id, context.user_id)


@router.post(
    "/api/rooms/{room_id}/ready",
    response_model=RoomSnapshotResponse,
    tags=["rooms"],
    dependencies=[Depends(require_browser_origin)],
)
async def ready_room_member(
    room_id: UUID,
    payload: ReadyRequest,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoomSnapshotResponse:
    service = RoomService()
    try:
        await service.ready(
            database_session,
            user_id=context.user_id,
            room_id=room_id,
            check_version=payload.check_version,
        )
    except AuthError as error:
        _raise(error)
    return await _snapshot(service, database_session, room_id, context.user_id)


@router.post(
    "/api/rooms/{room_id}/role",
    response_model=RoomSnapshotResponse,
    tags=["rooms"],
    dependencies=[Depends(require_browser_origin)],
)
async def change_room_role(
    room_id: UUID,
    payload: RoleChangeRequest,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoomSnapshotResponse:
    service = RoomService()
    try:
        await service.change_role(
            database_session, user_id=context.user_id, room_id=room_id, payload=payload
        )
    except AuthError as error:
        _raise(error)
    return await _snapshot(service, database_session, room_id, context.user_id)


@router.post(
    "/api/rooms/{room_id}/start",
    response_model=RoomSnapshotResponse,
    tags=["rooms"],
    dependencies=[Depends(require_browser_origin)],
)
async def start_room(
    room_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoomSnapshotResponse:
    service = RoomService()
    try:
        await service.start_room(
            database_session,
            actor_user_id=context.user_id,
            actor_role=context.role,
            room_id=room_id,
            storage_path=cast(Settings, request.app.state.settings).agent_audio_storage_dir,
        )
    except AuthError as error:
        _raise(error)
    return await _snapshot(service, database_session, room_id, context.user_id)


@router.post(
    "/api/rooms/{room_id}/leave",
    response_model=RoomSnapshotResponse,
    tags=["rooms"],
    dependencies=[Depends(require_browser_origin)],
)
async def leave_room(
    room_id: UUID,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoomSnapshotResponse:
    service = RoomService()
    try:
        await service.leave_room(database_session, user_id=context.user_id, room_id=room_id)
    except AuthError as error:
        _raise(error)
    return await _snapshot(service, database_session, room_id, context.user_id)


@router.post(
    "/api/rooms/{room_id}/terminate",
    response_model=RoomSnapshotResponse,
    tags=["rooms"],
    dependencies=[Depends(require_browser_origin)],
)
async def terminate_room(
    room_id: UUID,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> RoomSnapshotResponse:
    service = RoomService()
    try:
        await service.terminate_room(
            database_session,
            actor_user_id=context.user_id,
            actor_role=context.role,
            room_id=room_id,
        )
    except AuthError as error:
        _raise(error)
    return await _snapshot(service, database_session, room_id, context.user_id)


__all__ = ["router"]

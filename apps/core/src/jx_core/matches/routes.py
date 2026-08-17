"""HTTP and WebSocket boundaries for the authoritative match runtime."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, NoReturn, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from livekit import api
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..audit.service import AuditService
from ..auth.dependencies import (
    get_changed_password_auth,
    get_database_session,
    require_browser_origin,
)
from ..auth.errors import APIError, AuthError, error_message
from ..auth.permissions import PermissionError, require_password_changed
from ..auth.service import AuthService
from ..auth.session import AuthContext, cookie_policy
from ..config import Settings
from ..models import (
    DeviceCheck,
    Match,
    Room,
    RoomConnectionLease,
    RoomMember,
    Seat,
    Speech,
    TranscriptSubmission,
    User,
)
from ..room_connections import RoomConnectionService
from ..runtime import CoreRuntime
from .domain import MatchCommand, MatchDomainError, MatchEvent, MatchRuntimeView
from .schemas import (
    AgentDecisionResponse,
    FreeDebateHandEntryResponse,
    MatchActionResponse,
    MatchCommandRequest,
    MatchEventResponse,
    MatchLiveKitTokenResponse,
    MatchSnapshotResponse,
    SpeechTextUpdateRequest,
    SpeechTranscriptResponse,
    TranscriptResponse,
)
from .service import MatchRuntimeManager

router = APIRouter()
MATCH_TOKEN_TTL_SECONDS = 600


def _raise(code: str) -> NoReturn:
    raise APIError(code) from None


def _manager(app: Any) -> MatchRuntimeManager:
    current = cast(MatchRuntimeManager | None, app.state.match_runtime_manager)
    if current is not None:
        return current
    runtime = cast(CoreRuntime, app.state.runtime)
    factory = cast(async_sessionmaker[AsyncSession], runtime.database.session_factory)
    current = MatchRuntimeManager(factory)
    app.state.match_runtime_manager = current
    return current


def _candidate_side(view: MatchRuntimeView) -> str | None:
    state = view.state
    action = state.current_action
    if action is None or action.action_kind != "FREE_DEBATE":
        return None
    if state.action_state in {
        "HUMAN_SPEAKING",
        "SPEECH_FINALIZING",
        "AGENT_SPEAKING",
        "AGENT_FINALIZING",
    } and state.current_speaker_side in {"AFFIRMATIVE", "NEGATIVE"}:
        return "NEGATIVE" if state.current_speaker_side == "AFFIRMATIVE" else "AFFIRMATIVE"
    return state.free_holder_side


def _can_view_team_state(
    view: MatchRuntimeView,
    *,
    viewer_user_id: UUID | None,
    member_role: str | None,
    admin_control: bool = False,
) -> bool:
    if admin_control:
        return True
    if viewer_user_id is None or member_role != "DEBATER":
        return False
    action = view.state.current_action
    side = _candidate_side(view)
    return bool(
        action
        and side
        and any(
            item.user_id == viewer_user_id and item.side == side for item in action.participants
        )
    )


def _snapshot_response(
    view: MatchRuntimeView,
    room_id: UUID,
    *,
    viewer_user_id: UUID | None = None,
    member_role: str | None = None,
    admin_control: bool = False,
    resume_reasons: list[str] | None = None,
) -> MatchSnapshotResponse:
    state = view.state
    action = state.current_action
    can_view_team = _can_view_team_state(
        view,
        viewer_user_id=viewer_user_id,
        member_role=member_role,
        admin_control=admin_control,
    )
    visible_humans = state.hand_queue if can_view_team else ()
    visible_agents = state.agent_hand_queue if can_view_team else ()
    human_entries: list[FreeDebateHandEntryResponse] = []
    if action is not None and can_view_team:
        for rank, user_id in enumerate(visible_humans, start=1):
            participant = next(
                (item for item in action.participants if item.user_id == user_id), None
            )
            if participant is not None:
                human_entries.append(
                    FreeDebateHandEntryResponse(
                        speaker_kind="HUMAN",
                        user_id=user_id,
                        side=participant.side,
                        seat_no=participant.seat_no,
                        rank=rank,
                    )
                )
    agent_ranks = {
        agent_id: len(human_entries) + index + 1 for index, agent_id in enumerate(visible_agents)
    }
    agent_entries = [
        FreeDebateHandEntryResponse(
            speaker_kind="AGENT",
            agent_profile_id=item.agent_profile_id,
            side=item.side,
            seat_no=item.seat_no,
            rank=agent_ranks[item.agent_profile_id],
        )
        for item in state.agent_decisions
        if can_view_team and item.agent_profile_id in agent_ranks
    ]
    return MatchSnapshotResponse(
        match_id=state.match_id,
        room_id=room_id,
        status=state.status,
        action_state=state.action_state,
        sequence=state.sequence,
        current_action_index=state.current_action_index,
        current_action=MatchActionResponse(
            stage_position=action.stage_position,
            action_position=action.action_position,
            action_kind=action.action_kind,
            duration_seconds=action.duration_seconds,
            side=action.side,
            seat_no=action.seat_no,
            speaker_user_id=action.speaker_user_id,
            speaker_kind=action.speaker_kind,
            agent_profile_id=action.agent_profile_id,
            host_audio_path=(
                f"/api/matches/{state.match_id}/host-audio/{action.action_key}"
                if action.host_audio_path
                else None
            ),
        )
        if action
        else None,
        current_speech_id=state.current_speech_id,
        current_speaker_user_id=state.current_speaker_user_id,
        current_agent_profile_id=state.current_agent_profile_id,
        speech_remaining_ms=view.speech_remaining_ms,
        countdown_remaining_ms=view.countdown_remaining_ms,
        current_speaker_side=state.current_speaker_side,
        current_speaker_seat_no=state.current_speaker_seat_no,
        free_holder_side=state.free_holder_side,
        free_affirmative_remaining_ms=view.free_affirmative_remaining_ms,
        free_negative_remaining_ms=view.free_negative_remaining_ms,
        hand_queue=list(visible_humans),
        agent_hand_queue=list(visible_agents),
        agent_selection_mode=state.agent_selection_mode if can_view_team else None,
        agent_decisions=[
            AgentDecisionResponse(
                agent_profile_id=item.agent_profile_id,
                side=item.side,
                seat_no=item.seat_no,
                status=item.status,
                queue_rank=agent_ranks.get(item.agent_profile_id),
            )
            for item in state.agent_decisions
        ]
        if can_view_team
        else [],
        team_hand_queue=[*human_entries, *agent_entries],
        hand_window_open=state.hand_window_open,
        error_code=state.error_code,
        offline_user_id=state.offline_user_id,
        pause_initiator_user_id=state.pause_initiator_user_id,
        resume_reasons=(resume_reasons or []) if member_role in {"ORGANIZER", "DEBATER"} else [],
    )


def _event_response(event: MatchEvent, *, can_view_team: bool = True) -> MatchEventResponse:
    sensitive = event.type in {
        "hand.raised",
        "hand.cancelled",
        "agent.decision_started",
        "agent.decision_progress",
        "free.queue_reordered",
    }
    if sensitive and not can_view_team:
        return MatchEventResponse(
            type="match.updated",
            match_id=event.match_id,
            sequence=event.sequence,
            server_time_ms=event.server_time_ms,
            payload={},
        )
    if event.type == "agent.decision_started":
        payload = {
            "decision_round_id": event.payload.get("decision_round_id"),
            "side": event.payload.get("side"),
        }
    elif event.type == "agent.decision_progress":
        payload = {
            "decision_round_id": event.payload.get("decision_round_id"),
            "agent_profile_id": event.payload.get("agent_profile_id"),
            "status": event.payload.get("status"),
        }
    else:
        payload = dict(event.payload)
    return MatchEventResponse(
        type=event.type,
        match_id=event.match_id,
        sequence=event.sequence,
        server_time_ms=event.server_time_ms,
        payload=payload,
    )


async def _match_room_id(session: AsyncSession, match_id: UUID) -> UUID:
    room_id = await session.scalar(select(Match.room_id).where(Match.id == match_id))
    if room_id is None:
        _raise("match_not_found")
    return room_id


async def _member(
    session: AsyncSession, match_id: UUID, user_id: UUID
) -> tuple[Match, Room, RoomMember]:
    row = (
        await session.execute(
            select(Match, Room, RoomMember)
            .join(Room, Room.id == Match.room_id)
            .join(
                RoomMember,
                (RoomMember.room_id == Room.id)
                & (RoomMember.user_id == user_id)
                & RoomMember.left_at.is_(None),
            )
            .where(Match.id == match_id)
        )
    ).first()
    if row is None:
        _raise("room_member_required")
    return row[0], row[1], row[2]


def match_page_permissions(
    *, actor_user_id: UUID, organizer_user_id: UUID, member_role: str
) -> tuple[bool, bool]:
    """Resolve ordinary match-page permissions without global admin elevation."""
    privileged = actor_user_id == organizer_user_id
    authorized = privileged or member_role == "DEBATER"
    return privileged, authorized


async def _resume_reasons(session: AsyncSession, room_id: UUID) -> list[str]:
    members = list(
        (
            await session.execute(
                select(RoomMember, User.real_name)
                .join(User, User.id == RoomMember.user_id)
                .where(
                    RoomMember.room_id == room_id,
                    RoomMember.member_role == "DEBATER",
                    RoomMember.left_at.is_(None),
                )
            )
        ).all()
    )
    reasons: list[str] = []
    for member, real_name in members:
        speaker_label = f"辩手 {real_name}"
        if not member.online:
            reasons.append(f"{speaker_label} 当前离线")
        connection = await session.scalar(
            select(RoomConnectionLease).where(
                RoomConnectionLease.user_id == member.user_id,
                RoomConnectionLease.room_id == room_id,
            )
        )
        if connection is None:
            reasons.append(f"{speaker_label} 尚未连接比赛")
        latest = await session.scalar(
            select(DeviceCheck)
            .where(DeviceCheck.room_id == room_id, DeviceCheck.user_id == member.user_id)
            .order_by(DeviceCheck.checked_at.desc())
            .limit(1)
        )
        # A check is performed when entering the room. It remains valid for the
        # match lifetime; explicit device invalidation handles hardware changes.
        if latest is None or latest.status == "FAIL":
            reasons.append(f"{speaker_label} 的麦克风或扬声器检测已失效")
    return reasons


async def _set_member_online(
    session: AsyncSession, *, room_id: UUID, user_id: UUID, online: bool
) -> None:
    async with session.begin():
        member = await session.scalar(
            select(RoomMember)
            .where(
                RoomMember.room_id == room_id,
                RoomMember.user_id == user_id,
                RoomMember.left_at.is_(None),
            )
            .with_for_update()
        )
        if member is not None:
            member.online = online


@router.post(
    "/api/rooms/{room_id}/runtime-start",
    response_model=MatchSnapshotResponse,
    tags=["matches"],
    dependencies=[Depends(require_browser_origin)],
)
async def start_match_runtime(
    room_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> MatchSnapshotResponse:
    manager = _manager(request.app)
    try:
        state = await manager.start_room_match(
            session,
            room_id=room_id,
            actor_user_id=context.user_id,
            actor_role=context.role,
        )
    except (AuthError, MatchDomainError) as error:
        _raise(error.code)
    view = await manager.snapshot_view(session, state.match_id)
    _, _, member = await _member(session, state.match_id, context.user_id)
    return _snapshot_response(
        view,
        room_id,
        viewer_user_id=context.user_id,
        member_role=member.member_role,
    )


@router.get(
    "/api/matches/{match_id}/snapshot",
    response_model=MatchSnapshotResponse,
    tags=["matches"],
)
async def get_match_snapshot(
    match_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> MatchSnapshotResponse:
    _, _, member = await _member(session, match_id, context.user_id)
    room_id = await _match_room_id(session, match_id)
    try:
        view = await _manager(request.app).snapshot_view(session, match_id)
    except AuthError as error:
        _raise(error.code)
    resume_reasons = (
        await _resume_reasons(session, room_id)
        if view.state.status in {"PAUSED", "SYSTEM_RECOVERY", "ERROR"}
        else []
    )
    return _snapshot_response(
        view,
        room_id,
        viewer_user_id=context.user_id,
        member_role=member.member_role,
        resume_reasons=resume_reasons,
    )


@router.get(
    "/api/matches/{match_id}/transcript",
    response_model=TranscriptResponse,
    tags=["matches"],
)
async def get_match_transcript(
    match_id: UUID,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> TranscriptResponse:
    if context.role != "ADMIN":
        await _member(session, match_id, context.user_id)
    match = await session.get(Match, match_id)
    if match is None:
        _raise("match_not_found")
    speeches = list(
        (
            await session.scalars(
                select(Speech)
                .where(Speech.match_id == match_id, Speech.status == "FINALIZED")
                .order_by(Speech.created_at)
            )
        ).all()
    )
    return TranscriptResponse(
        match_id=match_id,
        context_version=match.context_version,
        speeches=[
            SpeechTranscriptResponse(
                id=speech.id,
                match_id=speech.match_id,
                action_key=speech.action_key,
                user_id=speech.user_id,
                speaker_kind=speech.speaker_kind,
                agent_profile_id=speech.agent_profile_id,
                generation_id=speech.generation_id,
                side=speech.side,
                seat_no=speech.seat_no,
                status=speech.status,
                asr_raw_final_text=speech.asr_raw_final_text,
                display_text=speech.display_text,
                audio_duration_ms=speech.audio_duration_ms,
                finalized_at=speech.finalized_at,
                audio_truncated=speech.audio_truncated,
            )
            for speech in speeches
        ],
    )


@router.patch(
    "/api/matches/{match_id}/speeches/{speech_id}/display-text",
    response_model=TranscriptResponse,
    tags=["matches"],
    dependencies=[Depends(require_browser_origin)],
)
async def update_speech_display_text(
    match_id: UUID,
    speech_id: UUID,
    payload: SpeechTextUpdateRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> TranscriptResponse:
    text_value = payload.display_text.strip()
    if len(text_value) > 20_000:
        _raise("transcript_text_invalid")
    async with session.begin():
        if context.role != "ADMIN":
            await _member(session, match_id, context.user_id)
        speech = await session.scalar(
            select(Speech)
            .where(Speech.id == speech_id, Speech.match_id == match_id)
            .with_for_update()
        )
        match = await session.get(Match, match_id, with_for_update=True)
        if speech is None or match is None:
            _raise("match_not_found")
        is_admin = context.role == "ADMIN"
        if not is_admin and speech.user_id != context.user_id:
            _raise("speech_edit_forbidden")
        if match.archived_at is not None and not is_admin:
            _raise("transcript_archived")
        if speech.status != "FINALIZED" or (not is_admin and speech.asr_raw_final_text is None):
            _raise("speech_not_finalized")
        speech.display_text = text_value
        match.context_version += 1
        if is_admin:
            AuditService().record(
                session,
                actor_user_id=context.user_id,
                action="admin.transcript.updated",
                target_type="speech",
                target_id=str(speech.id),
                details={"match_id": str(match_id), "context_version": match.context_version},
            )
        submission = (
            await session.scalar(
                select(TranscriptSubmission).where(
                    TranscriptSubmission.match_id == match_id,
                    TranscriptSubmission.user_id == speech.user_id,
                )
            )
            if speech.user_id is not None
            else None
        )
        if submission is not None:
            await session.delete(submission)
    await _manager(request.app).publish_transcript_update(match_id, speech_id)
    return await get_match_transcript(match_id, context, session)


@router.post(
    "/api/matches/{match_id}/command",
    response_model=MatchSnapshotResponse,
    tags=["matches"],
    dependencies=[Depends(require_browser_origin)],
)
async def submit_match_command(
    match_id: UUID,
    payload: MatchCommandRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> MatchSnapshotResponse:
    match, room, member = await _member(session, match_id, context.user_id)
    manager = _manager(request.app)
    try:
        state = await manager.snapshot(session, match.id)
        if state.sequence != payload.expected_sequence:
            _raise("match_state_conflict")
        connection = await session.scalar(
            select(RoomConnectionLease).where(
                RoomConnectionLease.user_id == context.user_id,
                RoomConnectionLease.room_id == room.id,
                RoomConnectionLease.connection_epoch == payload.connection_epoch,
            )
        )
        if connection is None:
            _raise("match_connection_stale")
        privileged, authorized = match_page_permissions(
            actor_user_id=context.user_id,
            organizer_user_id=room.organizer_user_id,
            member_role=member.member_role,
        )
        resume_reasons = (
            await _resume_reasons(session, room.id) if payload.type == "match.resume" else []
        )
        result = await manager.submit(
            match_id,
            MatchCommand(
                type=payload.type,
                message_id=payload.message_id,
                actor_user_id=context.user_id,
                payload={
                    "privileged": privileged,
                    "authorized": authorized,
                    "reasons": resume_reasons or payload.reasons,
                },
            ),
        )
    except (AuthError, MatchDomainError) as error:
        _raise(error.code)
    view = await manager.snapshot_view(session, result.state.match_id)
    return _snapshot_response(
        view,
        room.id,
        viewer_user_id=context.user_id,
        member_role=member.member_role,
    )


@router.get(
    "/api/matches/{match_id}/host-audio/{action_key}",
    response_class=FileResponse,
    tags=["matches"],
)
async def get_match_host_audio(
    match_id: UUID,
    action_key: str,
    request: Request,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> FileResponse:
    match, _, _ = await _member(session, match_id, context.user_id)
    raw_action_value = match.runtime_snapshot.get("current_action")
    if not isinstance(raw_action_value, dict):
        _raise("host_audio_unavailable")
    raw_action = cast(dict[str, Any], raw_action_value)
    expected_action_key = f"{raw_action.get('stage_position')}:{raw_action.get('action_position')}"
    raw_path = raw_action.get("host_audio_path")
    if expected_action_key != action_key or not isinstance(raw_path, str) or not raw_path:
        _raise("host_audio_unavailable")
    settings = cast(Settings, request.app.state.settings)
    storage_root = Path(settings.host_audio_storage_dir).resolve()
    candidate = Path(raw_path)
    resolved = (candidate if candidate.is_absolute() else storage_root / candidate).resolve()
    if not resolved.is_relative_to(storage_root) or not resolved.is_file():
        _raise("host_audio_unavailable")
    return FileResponse(
        resolved,
        media_type="audio/ogg",
    )


@router.post(
    "/api/matches/{match_id}/livekit-token",
    response_model=MatchLiveKitTokenResponse,
    tags=["matches"],
    dependencies=[Depends(require_browser_origin)],
)
async def create_match_livekit_token(
    match_id: UUID,
    request: Request,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> MatchLiveKitTokenResponse:
    settings: Settings = request.app.state.settings
    if not settings.livekit_url or not settings.livekit_api_key or not settings.livekit_api_secret:
        _raise("livekit_not_configured")
    _, _, member = await _member(session, match_id, context.user_id)
    user = await session.get(User, context.user_id)
    if user is None:
        _raise("not_authenticated")
    is_human_seat = (
        await session.scalar(
            select(Seat.id).where(Seat.room_id == member.room_id, Seat.user_id == context.user_id)
        )
    ) is not None
    room_name = f"jx-match-{match_id}"
    token = (
        api.AccessToken(
            settings.livekit_api_key.get_secret_value(),
            settings.livekit_api_secret.get_secret_value(),
        )
        .with_identity(f"user-{context.user_id}-{uuid4().hex[:8]}")
        .with_name(user.real_name)
        .with_ttl(timedelta(seconds=MATCH_TOKEN_TTL_SECONDS))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=is_human_seat,
                can_publish_sources=["microphone"] if is_human_seat else [],
                can_subscribe=True,
                can_publish_data=False,
            )
        )
        .to_jwt()
    )
    return MatchLiveKitTokenResponse(
        server_url=settings.livekit_url,
        participant_token=token,
        room_name=room_name,
        expires_in_seconds=MATCH_TOKEN_TTL_SECONDS,
    )


async def _websocket_context(websocket: WebSocket) -> AuthContext | None:
    app = websocket.app
    settings: Settings = app.state.settings
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in settings.cors_origin_list:
        return None
    runtime = cast(CoreRuntime, app.state.runtime)
    factory = cast(async_sessionmaker[AsyncSession], runtime.database.session_factory)
    auth_service = cast(AuthService, app.state.auth_service)
    policy = cookie_policy(settings.app_env)
    async with factory() as session:
        validation = await auth_service.sessions.validate(
            session, websocket.cookies.get(policy.name)
        )
        if validation.context is None:
            return None
        try:
            context = require_password_changed(validation.context)
        except PermissionError:
            return None
        await session.commit()
        return context


@router.websocket("/api/matches/{match_id}/events")
async def match_events(websocket: WebSocket, match_id: UUID) -> None:
    context = await _websocket_context(websocket)
    if context is None:
        await websocket.close(code=4401)
        return
    runtime = cast(CoreRuntime, websocket.app.state.runtime)
    factory = cast(async_sessionmaker[AsyncSession], runtime.database.session_factory)
    manager = _manager(websocket.app)
    connection_id = uuid4()
    async with factory() as session:
        try:
            match, room, member = await _member(session, match_id, context.user_id)
            view = await manager.snapshot_view(session, match.id)
        except (APIError, AuthError):
            await websocket.close(code=4403)
            return
    async with factory() as session:
        lease = await RoomConnectionService().acquire(
            session,
            user_id=context.user_id,
            room_id=room.id,
            connection_id=connection_id,
        )
    async with factory() as session:
        await _set_member_online(session, room_id=room.id, user_id=context.user_id, online=True)
    resume_reasons = (
        await _resume_reasons(session, room.id)
        if view.state.status in {"PAUSED", "SYSTEM_RECOVERY", "ERROR"}
        else []
    )
    await websocket.accept()
    await websocket.send_json(
        {
            "type": "match.snapshot",
            "connection_epoch": lease.connection_epoch,
            "payload": _snapshot_response(
                view,
                room.id,
                viewer_user_id=context.user_id,
                member_role=member.member_role,
                resume_reasons=resume_reasons,
            ).model_dump(mode="json"),
        }
    )
    queue = await manager.subscribe(match_id)
    if member.member_role == "DEBATER":
        try:
            await manager.submit(
                match_id,
                MatchCommand(
                    type="member.online",
                    message_id=f"member-online:{context.user_id}:{lease.connection_epoch}",
                    actor_user_id=context.user_id,
                    payload={
                        "connection_epoch": lease.connection_epoch,
                        "connected_at_ms": lease.connected_at_ms,
                    },
                ),
            )
        except MatchDomainError:
            pass

    async def send_events() -> None:
        while True:
            event = await queue.get()
            current_view = (await manager.get_actor(match_id)).view()
            await websocket.send_json(
                _event_response(
                    event,
                    can_view_team=_can_view_team_state(
                        current_view,
                        viewer_user_id=context.user_id,
                        member_role=member.member_role,
                    ),
                ).model_dump(mode="json")
            )

    sender = asyncio.create_task(send_events())
    try:
        while True:
            raw = await websocket.receive_json()
            try:
                payload = MatchCommandRequest.model_validate(raw)
                if payload.connection_epoch != lease.connection_epoch:
                    raise MatchDomainError("match_connection_stale")
                current = await manager.get_actor(match_id)
                if current.state.sequence != payload.expected_sequence:
                    raise MatchDomainError("match_state_conflict")
                privileged, authorized = match_page_permissions(
                    actor_user_id=context.user_id,
                    organizer_user_id=room.organizer_user_id,
                    member_role=member.member_role,
                )
                resume_reasons: list[str] = []
                if payload.type == "match.resume":
                    # A WebSocket can remain open for hours. Never reuse the
                    # initial authorization session here: SQLAlchemy may reopen
                    # a transaction on that closed object and retain a pool
                    # connection for the rest of the socket lifetime.
                    async with factory() as command_session:
                        resume_reasons = await _resume_reasons(command_session, room.id)
                result = await manager.submit(
                    match_id,
                    MatchCommand(
                        type=payload.type,
                        message_id=payload.message_id,
                        actor_user_id=context.user_id,
                        payload={
                            "privileged": privileged,
                            "authorized": authorized,
                            "reasons": resume_reasons or payload.reasons,
                        },
                    ),
                )
                async with factory() as snapshot_session:
                    committed_view = await manager.snapshot_view(snapshot_session, match_id)
                await websocket.send_json(
                    {
                        "type": "command.ack",
                        "message_id": payload.message_id,
                        "duplicate": result.duplicate,
                        "sequence": result.state.sequence,
                        "snapshot": _snapshot_response(
                            committed_view,
                            room.id,
                            viewer_user_id=context.user_id,
                            member_role=member.member_role,
                        ).model_dump(mode="json"),
                    }
                )
            except (ValidationError, MatchDomainError) as error:
                code = error.code if isinstance(error, MatchDomainError) else "validation_error"
                raw_message = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
                await websocket.send_json(
                    {
                        "type": "command.error",
                        "message_id": raw_message.get("message_id"),
                        "code": code,
                        "message": error_message(code),
                    }
                )
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        await manager.unsubscribe(match_id, queue)
        async with factory() as session:
            released = await RoomConnectionService().release(
                session,
                user_id=context.user_id,
                connection_id=connection_id,
                connection_epoch=lease.connection_epoch,
            )
        if released:
            async with factory() as session:
                await _set_member_online(
                    session, room_id=room.id, user_id=context.user_id, online=False
                )
            if member.member_role == "DEBATER":
                try:
                    await manager.submit(
                        match_id,
                        MatchCommand(
                            type="member.offline",
                            message_id=(
                                f"member-offline:{context.user_id}:{lease.connection_epoch}"
                            ),
                            actor_user_id=context.user_id,
                            payload={
                                "connection_epoch": lease.connection_epoch,
                                "offline_since_ms": int(datetime.now(UTC).timestamp() * 1000),
                            },
                        ),
                    )
                except MatchDomainError:
                    pass


__all__ = ["router"]

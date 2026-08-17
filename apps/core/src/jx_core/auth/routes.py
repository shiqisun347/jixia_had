"""HTTP transport for the first authentication slice."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..legal.terms import get_current_platform_terms
from ..logging import current_request_id
from ..models import (
    DeviceCheck,
    LeaderboardSnapshot,
    Match,
    MatchParticipant,
    Room,
    RoomMember,
    User,
)
from ..users.avatar import AvatarError, AvatarService
from .dependencies import (
    get_admin_auth,
    get_auth_service,
    get_avatar_service,
    get_changed_password_auth,
    get_current_auth,
    get_database_session,
    require_browser_origin,
)
from .errors import APIError, AuthError
from .schemas import (
    AuthResponse,
    AvatarPresetUpdateRequest,
    ChangePasswordRequest,
    CurrentMatchSummary,
    LoginRequest,
    LogoutResponse,
    ProfileUpdateRequest,
    RecentMatchSummary,
    RegisterRequest,
    TemporaryPasswordResponse,
    TermsResponse,
    UserResponse,
    UserSummaryResponse,
)
from .service import AuthService
from .session import AuthContext, cookie_policy, is_safe_return_to

router = APIRouter(prefix="/api")


def _raise_auth_error(error: AuthError) -> NoReturn:
    raise APIError(error.code, error.field_errors) from None


def _raise_avatar_error(error: AvatarError) -> NoReturn:
    raise APIError(error.code) from None


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        real_name=user.real_name,
        role=user.role,
        avatar_version=user.avatar_version,
        default_avatar_key=user.default_avatar_key,
        has_custom_avatar=user.avatar_path is not None,
        must_change_password=user.must_change_password,
    )


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    policy = cookie_policy(request.app.state.runtime.settings.app_env)
    response.set_cookie(
        key=policy.name,
        value=token,
        max_age=policy.max_age,
        path=policy.path,
        secure=policy.secure,
        httponly=policy.httponly,
        samesite=policy.samesite,
    )
    response.headers["Cache-Control"] = "no-store"


@router.get("/legal/platform-terms/current", response_model=TermsResponse, tags=["legal"])
async def current_platform_terms() -> TermsResponse:
    terms = get_current_platform_terms()
    return TermsResponse(version=terms.version, title=terms.title, body=terms.body)


@router.post(
    "/auth/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_browser_origin)],
    tags=["auth"],
)
async def register(
    request: Request,
    response: Response,
    payload: RegisterRequest,
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthResponse:
    try:
        result = await auth_service.register(
            database_session,
            username=payload.username,
            real_name=payload.real_name,
            password=payload.password,
            platform_terms_version=payload.platform_terms_version,
            avatar_key=payload.avatar_key,
        )
    except AuthError as error:
        _raise_auth_error(error)
    _set_session_cookie(response, request, result.session.token)
    return AuthResponse(user=_user_response(result.user))


@router.post(
    "/auth/login",
    response_model=AuthResponse,
    dependencies=[Depends(require_browser_origin)],
    tags=["auth"],
)
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthResponse:
    if not is_safe_return_to(payload.return_to):
        raise APIError("validation_error", {"return_to": "返回地址无效"})
    try:
        result = await auth_service.login(
            database_session,
            username=payload.username,
            password=payload.password,
        )
    except AuthError as error:
        _raise_auth_error(error)
    _set_session_cookie(response, request, result.session.token)
    return AuthResponse(user=_user_response(result.user))


@router.get("/auth/me", response_model=AuthResponse, tags=["auth"])
async def me(
    context: Annotated[AuthContext, Depends(get_current_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> AuthResponse:
    user = await database_session.get(User, context.user_id)
    if user is None or user.status != "ACTIVE":
        raise APIError("not_authenticated")
    return AuthResponse(user=_user_response(user))


@router.get("/users/me/summary", response_model=UserSummaryResponse, tags=["users"])
async def summary(
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
) -> UserSummaryResponse:
    user_id = context.user_id
    current_row = (
        await database_session.execute(
            select(Match.id, Room.id, Room.status, Room.title, Room.code)
            .select_from(RoomMember)
            .join(Room, Room.id == RoomMember.room_id)
            .outerjoin(Match, Match.room_id == Room.id)
            .where(
                RoomMember.user_id == user_id,
                RoomMember.left_at.is_(None),
                Room.status.in_(("WAITING", "START_PENDING_RUNTIME", "RUNNING", "PAUSED")),
            )
            .order_by(Room.created_at.desc())
            .limit(1)
        )
    ).first()
    counts = (
        await database_session.execute(
            select(
                func.count(func.distinct(Match.id)),
                func.count(func.distinct(case((Match.status == "FINISHED", Match.id)))),
            )
            .join(MatchParticipant, MatchParticipant.match_id == Match.id)
            .where(MatchParticipant.user_id == user_id)
        )
    ).one()
    recent_rows = (
        await database_session.execute(
            select(Match.id, Match.status, Match.created_at, Room.title, MatchParticipant.side)
            .join(Room, Room.id == Match.room_id)
            .join(MatchParticipant, MatchParticipant.match_id == Match.id)
            .where(MatchParticipant.user_id == user_id)
            .order_by(Match.created_at.desc())
            .limit(10)
        )
    ).all()
    latest_rank = (
        await database_session.execute(
            select(
                LeaderboardSnapshot.rank,
                LeaderboardSnapshot.average_personal_score,
                LeaderboardSnapshot.wins,
            )
            .where(
                LeaderboardSnapshot.kind == "HUMAN",
                LeaderboardSnapshot.participant_id == user_id,
            )
            .order_by(LeaderboardSnapshot.generated_at.desc())
            .limit(1)
        )
    ).first()
    latest_check = (
        await database_session.execute(
            select(DeviceCheck.checked_at)
            .where(DeviceCheck.user_id == user_id)
            .order_by(DeviceCheck.checked_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return UserSummaryResponse(
        current_match=(
            CurrentMatchSummary(
                match_id=current_row[0],
                room_id=current_row[1],
                status=current_row[2],
                title=current_row[3],
                code=current_row[4],
            )
            if current_row
            else None
        ),
        matches=int(counts[0] or 0),
        finished_matches=int(counts[1] or 0),
        wins=int(latest_rank[2]) if latest_rank else 0,
        average_score=float(latest_rank[1]) if latest_rank else 0,
        leaderboard_rank=int(latest_rank[0]) if latest_rank else None,
        recent_matches=[
            RecentMatchSummary(
                id=row[0], title=row[3], status=row[1], created_at=row[2], side=row[4]
            )
            for row in recent_rows
        ],
        latest_device_check=latest_check,
    )


@router.post(
    "/auth/logout",
    response_model=LogoutResponse,
    dependencies=[Depends(require_browser_origin)],
    tags=["auth"],
)
async def logout(
    request: Request,
    response: Response,
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> LogoutResponse:
    policy = cookie_policy(request.app.state.runtime.settings.app_env)
    await auth_service.sessions.revoke_current(database_session, request.cookies.get(policy.name))
    await database_session.commit()
    response.delete_cookie(
        key=policy.name,
        path=policy.path,
        secure=policy.secure,
        httponly=policy.httponly,
        samesite=policy.samesite,
    )
    response.headers["Cache-Control"] = "no-store"
    return LogoutResponse()


@router.post(
    "/auth/change-password",
    response_model=AuthResponse,
    dependencies=[Depends(require_browser_origin)],
    tags=["auth"],
)
async def change_password(
    request: Request,
    response: Response,
    payload: ChangePasswordRequest,
    context: Annotated[AuthContext, Depends(get_current_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthResponse:
    try:
        result = await auth_service.change_password(
            database_session,
            user_id=context.user_id,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except AuthError as error:
        _raise_auth_error(error)
    _set_session_cookie(response, request, result.session.token)
    response.headers["X-Other-Sessions-Revoked"] = "true"
    return AuthResponse(user=_user_response(result.user))


@router.patch(
    "/users/me",
    response_model=AuthResponse,
    dependencies=[Depends(require_browser_origin)],
    tags=["users"],
)
async def update_profile(
    payload: ProfileUpdateRequest,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthResponse:
    try:
        user = await auth_service.update_profile(
            database_session,
            user_id=context.user_id,
            real_name=payload.real_name,
        )
    except AuthError as error:
        _raise_auth_error(error)
    return AuthResponse(user=_user_response(user))


@router.patch(
    "/users/me/avatar-preset",
    response_model=AuthResponse,
    dependencies=[Depends(require_browser_origin)],
    tags=["users"],
)
async def update_avatar_preset(
    payload: AvatarPresetUpdateRequest,
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthResponse:
    try:
        user = await auth_service.update_avatar_preset(
            database_session,
            user_id=context.user_id,
            avatar_key=payload.avatar_key,
        )
    except AuthError as error:
        _raise_auth_error(error)
    return AuthResponse(user=_user_response(user))


@router.put(
    "/users/me/avatar",
    response_model=AuthResponse,
    dependencies=[Depends(require_browser_origin)],
    tags=["users"],
)
async def upload_avatar(
    file: Annotated[UploadFile, File(...)],
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
    avatar_service: Annotated[AvatarService, Depends(get_avatar_service)],
) -> AuthResponse:
    try:
        user = await avatar_service.replace(
            database_session,
            user_id=context.user_id,
            upload=file,
        )
    except AvatarError as error:
        _raise_avatar_error(error)
    return AuthResponse(user=_user_response(user))


@router.delete(
    "/users/me/avatar",
    response_model=AuthResponse,
    dependencies=[Depends(require_browser_origin)],
    tags=["users"],
)
async def delete_avatar(
    context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
    avatar_service: Annotated[AvatarService, Depends(get_avatar_service)],
) -> AuthResponse:
    try:
        user = await avatar_service.delete(database_session, user_id=context.user_id)
    except AvatarError as error:
        _raise_avatar_error(error)
    return AuthResponse(user=_user_response(user))


@router.get("/users/{user_id}/avatar", tags=["users"])
async def get_avatar(
    request: Request,
    user_id: UUID,
    _context: Annotated[AuthContext, Depends(get_changed_password_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
    avatar_service: Annotated[AvatarService, Depends(get_avatar_service)],
) -> Response:
    user = await database_session.get(User, user_id)
    if user is None or user.status != "ACTIVE":
        raise APIError("avatar_unavailable")
    path = avatar_service.read_path(user.avatar_path)
    if path is None:
        etag = f'"preset-{user.default_avatar_key}-{user.avatar_version}"'
        try:
            content = avatar_service.preset_bytes(user.default_avatar_key)
        except AvatarError:
            content = avatar_service.default_bytes()
        media_response = Response(content=content, media_type="image/webp")
    else:
        etag = f'"avatar-{user.id}-{user.avatar_version}"'
        media_response = FileResponse(path, media_type="image/webp")
    media_response.headers["ETag"] = etag
    media_response.headers["Cache-Control"] = "private, max-age=31536000"
    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": "private, max-age=31536000"},
        )
    return media_response


@router.post(
    "/admin/users/{user_id}/temporary-password",
    response_model=TemporaryPasswordResponse,
    dependencies=[Depends(require_browser_origin)],
    tags=["admin"],
)
async def reset_temporary_password(
    user_id: UUID,
    response: Response,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> TemporaryPasswordResponse:
    try:
        result = await auth_service.reset_temporary_password(
            database_session,
            actor_user_id=context.user_id,
            target_user_id=user_id,
            request_id=current_request_id(),
        )
    except AuthError as error:
        _raise_auth_error(error)
    response.headers["Cache-Control"] = "no-store"
    return TemporaryPasswordResponse(temporary_password=result.temporary_password)


__all__ = ["router"]

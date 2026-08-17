"""FastAPI dependencies that turn the core auth services into route guards."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, cast
from urllib.parse import urlsplit

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Database
from ..runtime import CoreRuntime
from ..users.avatar import AvatarService
from .errors import APIError
from .permissions import PermissionError, require_admin, require_password_changed
from .service import AuthService
from .session import AuthContext, cookie_policy


def _runtime(request: Request) -> CoreRuntime:
    return cast(CoreRuntime, request.app.state.runtime)


def _database(request: Request) -> Database:
    return cast(Database, _runtime(request).database)


async def get_database_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = _database(request).session_factory
    async with factory() as database_session:
        yield database_session


def get_auth_service(request: Request) -> AuthService:
    return cast(AuthService, request.app.state.auth_service)


def get_avatar_service(request: Request) -> AvatarService:
    return cast(AvatarService, request.app.state.avatar_service)


def require_browser_origin(request: Request) -> None:
    """Reject cross-site state changes while allowing non-browser test clients in dev."""

    settings = _runtime(request).settings
    origin = request.headers.get("origin")
    if origin is not None:
        if origin in settings.cors_origin_list:
            return
        raise APIError("csrf_origin_rejected")

    referer = request.headers.get("referer")
    if referer is not None:
        parsed = urlsplit(referer)
        referer_origin = f"{parsed.scheme}://{parsed.netloc}"
        if referer_origin in settings.cors_origin_list:
            return
        raise APIError("csrf_origin_rejected")

    if settings.app_env == "production":
        raise APIError("csrf_origin_rejected")


async def get_current_auth(
    request: Request,
    response: Response,
    database_session: Annotated[AsyncSession, Depends(get_database_session)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthContext:
    policy = cookie_policy(_runtime(request).settings.app_env)
    token = request.cookies.get(policy.name)
    validation = await auth_service.sessions.validate(database_session, token)
    if validation.context is None:
        raise APIError(validation.reason or "not_authenticated")
    await database_session.commit()
    if validation.refresh_cookie and token is not None:
        response.set_cookie(
            key=policy.name,
            value=token,
            max_age=policy.max_age,
            path=policy.path,
            secure=policy.secure,
            httponly=policy.httponly,
            samesite=policy.samesite,
        )
    return validation.context


async def get_changed_password_auth(
    context: Annotated[AuthContext, Depends(get_current_auth)],
) -> AuthContext:
    try:
        return require_password_changed(context)
    except PermissionError as error:
        raise APIError(error.code) from None


async def get_admin_auth(
    context: Annotated[AuthContext, Depends(get_current_auth)],
) -> AuthContext:
    try:
        return require_admin(context)
    except PermissionError as error:
        raise APIError(error.code) from None


__all__ = [
    "get_admin_auth",
    "get_auth_service",
    "get_avatar_service",
    "get_changed_password_auth",
    "get_current_auth",
    "get_database_session",
    "require_browser_origin",
]

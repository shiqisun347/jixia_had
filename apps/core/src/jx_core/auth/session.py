"""PostgreSQL-backed opaque sessions and cookie policy."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import User, UserSession

SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
SESSION_ROLLING_REFRESH_SECONDS = 15 * 60
SESSION_TOKEN_BYTES = 32

AppEnvironment = Literal["development", "test", "production"]


@dataclass(frozen=True, slots=True)
class AuthContext:
    user_id: UUID
    role: str
    session_id: UUID
    must_change_password: bool


@dataclass(frozen=True, slots=True)
class SessionValidation:
    context: AuthContext | None
    reason: Literal["not_authenticated", "session_expired", "account_disabled"] | None = None
    refresh_cookie: bool = False


@dataclass(frozen=True, slots=True)
class CreatedSession:
    token: str
    record: UserSession


@dataclass(frozen=True, slots=True)
class CookiePolicy:
    name: str
    secure: bool
    httponly: bool = True
    samesite: Literal["lax"] = "lax"
    path: str = "/"
    max_age: int = SESSION_TTL_SECONDS


def _utc_now() -> datetime:
    return datetime.now(UTC)


def token_hash(token: str) -> str:
    """Hash an opaque browser token before it reaches PostgreSQL."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def cookie_policy(app_env: AppEnvironment) -> CookiePolicy:
    if app_env == "production":
        return CookiePolicy(name="__Host-jx_session", secure=True)
    return CookiePolicy(name="jx_session", secure=False)


def is_safe_return_to(value: str | None) -> bool:
    """Accept only a same-origin path for post-login navigation."""

    if value is None:
        return True
    return (
        value.startswith("/")
        and not value.startswith("//")
        and "\\" not in value
        and all(ord(character) >= 32 and ord(character) != 127 for character in value)
    )


class SessionService:
    """Create, validate, roll, and revoke opaque PostgreSQL sessions."""

    def __init__(
        self,
        *,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        rolling_refresh_seconds: int = SESSION_ROLLING_REFRESH_SECONDS,
        token_bytes: int = SESSION_TOKEN_BYTES,
    ) -> None:
        if ttl_seconds <= 0 or rolling_refresh_seconds <= 0 or token_bytes < 16:
            raise ValueError("invalid session policy")
        self.ttl_seconds = ttl_seconds
        self.rolling_refresh_seconds = rolling_refresh_seconds
        self.token_bytes = token_bytes

    def create(
        self,
        database_session: AsyncSession,
        user_id: UUID,
        *,
        now: datetime | None = None,
    ) -> CreatedSession:
        current = now or _utc_now()
        token = secrets.token_urlsafe(self.token_bytes)
        record = UserSession(
            token_hash=token_hash(token),
            user_id=user_id,
            expires_at=current + timedelta(seconds=self.ttl_seconds),
            last_seen_at=current,
        )
        database_session.add(record)
        return CreatedSession(token=token, record=record)

    async def validate(
        self,
        database_session: AsyncSession,
        token: str | None,
        *,
        now: datetime | None = None,
    ) -> SessionValidation:
        if not token:
            return SessionValidation(context=None, reason="not_authenticated")
        current = now or _utc_now()
        result = await database_session.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(UserSession.token_hash == token_hash(token))
        )
        row = result.first()
        if row is None:
            return SessionValidation(context=None, reason="not_authenticated")
        session_record, user = row
        if user.status != "ACTIVE":
            return SessionValidation(context=None, reason="account_disabled")
        if session_record.revoked_at is not None or session_record.expires_at <= current:
            return SessionValidation(context=None, reason="session_expired")

        refresh_cookie = current - session_record.last_seen_at >= timedelta(
            seconds=self.rolling_refresh_seconds
        )
        if refresh_cookie:
            session_record.last_seen_at = current
            session_record.expires_at = current + timedelta(seconds=self.ttl_seconds)
            await database_session.flush()
        return SessionValidation(
            context=AuthContext(
                user_id=user.id,
                role=user.role,
                session_id=session_record.id,
                must_change_password=user.must_change_password,
            ),
            refresh_cookie=refresh_cookie,
        )

    async def revoke_current(
        self,
        database_session: AsyncSession,
        token: str | None,
        *,
        now: datetime | None = None,
    ) -> bool:
        if not token:
            return False
        result = await database_session.execute(
            update(UserSession)
            .where(UserSession.token_hash == token_hash(token), UserSession.revoked_at.is_(None))
            .values(revoked_at=now or _utc_now())
        )
        cursor_result = result if isinstance(result, CursorResult) else None
        return cursor_result is not None and cursor_result.rowcount == 1

    async def revoke_all(
        self,
        database_session: AsyncSession,
        user_id: UUID,
        *,
        now: datetime | None = None,
    ) -> int:
        result = await database_session.execute(
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=now or _utc_now())
        )
        cursor_result = result if isinstance(result, CursorResult) else None
        return cursor_result.rowcount if cursor_result is not None else 0


__all__ = [
    "AuthContext",
    "CookiePolicy",
    "CreatedSession",
    "SESSION_ROLLING_REFRESH_SECONDS",
    "SESSION_TTL_SECONDS",
    "SessionService",
    "SessionValidation",
    "cookie_policy",
    "is_safe_return_to",
    "token_hash",
]

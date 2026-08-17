"""Authentication application service; routes do not own business rules."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit.service import AuditService
from ..legal.terms import get_current_platform_terms, get_platform_terms
from ..models import RoomConnection, RoomConnectionLease, User, UserConsent
from ..users.avatar_catalog import is_avatar_key, random_avatar_key
from .errors import AuthError
from .normalization import InputNormalizationError, normalize_real_name, prepare_username
from .passwords import PasswordPolicyError, PasswordService, PasswordVerification
from .session import CreatedSession, SessionService

MAX_LOGIN_FAILURES = 5
LOGIN_LOCK_SECONDS = 15 * 60


@dataclass(frozen=True, slots=True)
class AuthResult:
    user: User
    session: CreatedSession


@dataclass(frozen=True, slots=True)
class TemporaryPasswordResult:
    user: User
    temporary_password: str


class AuthService:
    def __init__(
        self,
        *,
        password_service: PasswordService | None = None,
        session_service: SessionService | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self.passwords = password_service or PasswordService()
        self.sessions = session_service or SessionService()
        self.audit = audit_service or AuditService()

    async def register(
        self,
        database_session: AsyncSession,
        *,
        username: object,
        real_name: object,
        password: object,
        platform_terms_version: str,
        avatar_key: str | None = None,
        now: datetime | None = None,
    ) -> AuthResult:
        try:
            username_parts = prepare_username(username)
            clean_real_name = normalize_real_name(real_name)
            password_hash = self.passwords.hash(password)
        except (InputNormalizationError, PasswordPolicyError) as error:
            raise AuthError("validation_error") from error

        current_terms = get_current_platform_terms()
        if (
            platform_terms_version != current_terms.version
            or get_platform_terms(platform_terms_version) is None
        ):
            raise AuthError("platform_terms_outdated")
        if avatar_key is not None and not is_avatar_key(avatar_key, "HUMAN"):
            raise AuthError("validation_error", {"avatar_key": "请选择有效头像"})

        current = now or datetime.now(UTC)
        try:
            async with database_session.begin():
                user = User(
                    username=username_parts.display,
                    username_normalized=username_parts.normalized,
                    real_name=clean_real_name,
                    password_hash=password_hash,
                    default_avatar_key=avatar_key or random_avatar_key("HUMAN"),
                )
                database_session.add(user)
                await database_session.flush()
                database_session.add(
                    UserConsent(
                        user_id=user.id,
                        consent_type="platform_terms",
                        version=current_terms.version,
                        accepted_at=current,
                    )
                )
                created_session = self.sessions.create(database_session, user.id, now=current)
                await database_session.flush()
        except IntegrityError as error:
            if "uq_users_username_normalized" in str(error.orig):
                raise AuthError("username_taken", {"username": "请选择其他用户名"}) from None
            raise
        return AuthResult(user=user, session=created_session)

    async def login(
        self,
        database_session: AsyncSession,
        *,
        username: object,
        password: object,
        now: datetime | None = None,
    ) -> AuthResult:
        try:
            username_normalized = prepare_username(username).normalized
        except InputNormalizationError as error:
            self._verify_dummy(password)
            raise AuthError("invalid_credentials") from error

        current = now or datetime.now(UTC)
        pending_error: AuthError | None = None
        created_session: CreatedSession | None = None
        user: User | None = None
        verification = PasswordVerification(valid=False)
        async with database_session.begin():
            result = await database_session.execute(
                select(User)
                .where(User.username_normalized == username_normalized)
                .with_for_update()
            )
            user = result.scalar_one_or_none()
            if user is None:
                self._verify_dummy(password)
                pending_error = AuthError("invalid_credentials")
            elif user.status != "ACTIVE":
                raise AuthError("account_disabled")
            elif user.locked_until is not None and user.locked_until > current:
                raise AuthError("login_temporarily_locked")
            else:
                if user.locked_until is not None:
                    user.failed_login_count = 0
                    user.locked_until = None
                verification = self._verify_password(user.password_hash, password)
            if user is not None and pending_error is None and not verification.valid:
                user.failed_login_count += 1
                if user.failed_login_count >= MAX_LOGIN_FAILURES:
                    user.locked_until = current + timedelta(seconds=LOGIN_LOCK_SECONDS)
                    pending_error = AuthError("login_temporarily_locked")
                else:
                    pending_error = AuthError("invalid_credentials")
                await database_session.flush()
            elif user is not None and pending_error is None:
                user.failed_login_count = 0
                user.locked_until = None
                user.last_login_at = current
                if verification.needs_rehash:
                    user.password_hash = self.passwords.hash(password)
                created_session = self.sessions.create(database_session, user.id, now=current)
                await database_session.flush()
        if pending_error is not None:
            raise pending_error
        if user is None or created_session is None:
            raise RuntimeError("login transaction completed without a result")
        return AuthResult(user=user, session=created_session)

    async def change_password(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        current_password: object,
        new_password: object,
        now: datetime | None = None,
    ) -> AuthResult:
        current = now or datetime.now(UTC)
        async with database_session.begin():
            user = (
                await database_session.execute(
                    select(User).where(User.id == user_id).with_for_update()
                )
            ).scalar_one_or_none()
            if user is None or user.status != "ACTIVE":
                raise AuthError("account_disabled")
            if not self._verify_password(user.password_hash, current_password).valid:
                raise AuthError("current_password_incorrect")
            try:
                if self.passwords.validate(new_password) == current_password:
                    raise AuthError("new_password_must_differ")
                new_hash = self.passwords.hash(new_password)
            except PasswordPolicyError as error:
                raise AuthError("validation_error") from error
            user.password_hash = new_hash
            user.must_change_password = False
            user.password_changed_at = current
            user.failed_login_count = 0
            user.locked_until = None
            await database_session.execute(
                delete(RoomConnection).where(RoomConnection.user_id == user_id)
            )
            await database_session.execute(
                delete(RoomConnectionLease).where(RoomConnectionLease.user_id == user_id)
            )
            await self.sessions.revoke_all(database_session, user_id, now=current)
            created_session = self.sessions.create(database_session, user.id, now=current)
            await database_session.flush()
        return AuthResult(user=user, session=created_session)

    async def update_profile(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        real_name: object,
    ) -> User:
        try:
            clean_real_name = normalize_real_name(real_name)
        except InputNormalizationError as error:
            raise AuthError("validation_error", {"real_name": error.message}) from error
        async with database_session.begin():
            user = (
                await database_session.execute(
                    select(User).where(User.id == user_id).with_for_update()
                )
            ).scalar_one_or_none()
            if user is None or user.status != "ACTIVE":
                raise AuthError("not_authenticated")
            user.real_name = clean_real_name
            await database_session.flush()
        return user

    async def update_avatar_preset(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        avatar_key: str,
    ) -> User:
        if not is_avatar_key(avatar_key, "HUMAN"):
            raise AuthError("validation_error", {"avatar_key": "请选择有效头像"})
        async with database_session.begin():
            user = await database_session.get(User, user_id, with_for_update=True)
            if user is None or user.status != "ACTIVE":
                raise AuthError("not_authenticated")
            user.default_avatar_key = avatar_key
            user.avatar_version += 1
            await database_session.flush()
        return user

    async def create_admin(
        self,
        database_session: AsyncSession,
        *,
        username: object,
        real_name: object,
        password: object,
        now: datetime | None = None,
    ) -> User:
        try:
            username_parts = prepare_username(username)
            clean_real_name = normalize_real_name(real_name)
            password_hash = self.passwords.hash(password)
        except (InputNormalizationError, PasswordPolicyError) as error:
            raise AuthError("validation_error") from error
        current = now or datetime.now(UTC)
        try:
            async with database_session.begin():
                user = User(
                    username=username_parts.display,
                    username_normalized=username_parts.normalized,
                    real_name=clean_real_name,
                    password_hash=password_hash,
                    role="ADMIN",
                    default_avatar_key=random_avatar_key("HUMAN"),
                    password_changed_at=current,
                )
                database_session.add(user)
                await database_session.flush()
                self.audit.record(
                    database_session,
                    actor_user_id=None,
                    action="admin.created",
                    target_type="user",
                    target_id=str(user.id),
                    details={"username_normalized": username_parts.normalized},
                )
                await database_session.flush()
        except IntegrityError as error:
            if "uq_users_username_normalized" in str(error.orig):
                raise AuthError("username_taken") from None
            raise
        return user

    async def reset_temporary_password(
        self,
        database_session: AsyncSession,
        *,
        actor_user_id: UUID,
        target_user_id: UUID,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> TemporaryPasswordResult:
        if actor_user_id == target_user_id:
            raise AuthError("forbidden")
        current = now or datetime.now(UTC)
        temporary_password = secrets.token_urlsafe(24)
        temporary_hash = self.passwords.hash(temporary_password)
        async with database_session.begin():
            target = (
                await database_session.execute(
                    select(User).where(User.id == target_user_id).with_for_update()
                )
            ).scalar_one_or_none()
            if target is None:
                raise AuthError("user_not_found")
            target.password_hash = temporary_hash
            target.must_change_password = True
            target.password_changed_at = current
            target.failed_login_count = 0
            target.locked_until = None
            await database_session.execute(
                delete(RoomConnection).where(RoomConnection.user_id == target_user_id)
            )
            await database_session.execute(
                delete(RoomConnectionLease).where(RoomConnectionLease.user_id == target_user_id)
            )
            await self.sessions.revoke_all(database_session, target_user_id, now=current)
            self.audit.record(
                database_session,
                actor_user_id=actor_user_id,
                action="password.reset",
                target_type="user",
                target_id=str(target_user_id),
                request_id=request_id,
                details={"must_change_password": True},
            )
            await database_session.flush()
        return TemporaryPasswordResult(
            user=target,
            temporary_password=temporary_password,
        )

    def _verify_password(self, encoded_hash: str, password: object) -> PasswordVerification:
        try:
            return self.passwords.verify(encoded_hash, password)
        except PasswordPolicyError:
            return PasswordVerification(valid=False)

    def _verify_dummy(self, password: object) -> None:
        try:
            self.passwords.verify_dummy(password)
        except PasswordPolicyError:
            pass


__all__ = [
    "AuthResult",
    "AuthService",
    "LOGIN_LOCK_SECONDS",
    "MAX_LOGIN_FAILURES",
    "TemporaryPasswordResult",
]

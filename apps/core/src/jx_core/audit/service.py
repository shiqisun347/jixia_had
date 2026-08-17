"""Minimal append-only audit writer for security-sensitive actions."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditLog


class AuditService:
    def record(
        self,
        database_session: AsyncSession,
        *,
        actor_user_id: UUID | None,
        action: str,
        target_type: str,
        target_id: str | None,
        result: str = "SUCCESS",
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            result=result,
            request_id=request_id,
            details=details or {},
        )
        database_session.add(entry)
        return entry


__all__ = ["AuditService"]

"""Admin diagnostics: bounded event/task views and incident acknowledgement."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from .audit.service import AuditService
from .auth.dependencies import get_admin_auth, get_database_session, require_browser_origin
from .auth.session import AuthContext
from .models import BackgroundTask, SystemIncident, SystemLogEvent

router = APIRouter(prefix="/api/admin", tags=["admin-diagnostics"])


class IncidentPatch(BaseModel):
    status: str | None = None
    notes: str | None = Field(default=None, max_length=2000)


def _page(page: int, size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, size))


@router.get("/diagnostics/events")
async def list_system_events(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    page: int = Query(1),
    page_size: int = Query(50),
    level: str = Query(""),
    service: str = Query(""),
) -> dict[str, Any]:
    page, page_size = _page(page, page_size)
    filters: list[ColumnElement[bool]] = []
    if level:
        filters.append(SystemLogEvent.level == level.upper())
    if service:
        filters.append(SystemLogEvent.service == service)
    total = int(
        await session.scalar(select(func.count()).select_from(SystemLogEvent).where(*filters)) or 0
    )
    rows = list(
        (
            await session.scalars(
                select(SystemLogEvent)
                .where(*filters)
                .order_by(SystemLogEvent.happened_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    return {
        "items": [
            {
                "id": str(row.id),
                "level": row.level,
                "service": row.service,
                "logger_name": row.logger_name,
                "message": row.message,
                "error_code": row.error_code,
                "match_id": str(row.match_id) if row.match_id else None,
                "happened_at": row.happened_at,
                "details": row.details,
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


@router.get("/diagnostics/tasks")
async def list_background_tasks(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    page: int = Query(1),
    page_size: int = Query(50),
) -> dict[str, Any]:
    page, page_size = _page(page, page_size)
    total = int(await session.scalar(select(func.count()).select_from(BackgroundTask)) or 0)
    rows = list(
        (
            await session.scalars(
                select(BackgroundTask)
                .order_by(BackgroundTask.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    return {
        "items": [
            {
                "id": str(row.id),
                "task_type": row.task_type,
                "status": row.status,
                "attempts": row.attempts,
                "error_code": row.error_code,
                "available_at": row.available_at,
                "updated_at": row.updated_at,
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


@router.get("/incidents")
async def list_incidents(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    status: str = Query(""),
) -> dict[str, Any]:
    filters = [SystemIncident.status == status.upper()] if status else []
    rows = list(
        (
            await session.scalars(
                select(SystemIncident)
                .where(*filters)
                .order_by(SystemIncident.last_seen_at.desc())
                .limit(200)
            )
        ).all()
    )
    return {
        "items": [
            {
                "id": str(row.id),
                "fingerprint": row.fingerprint,
                "title": row.title,
                "severity": row.severity,
                "status": row.status,
                "first_seen_at": row.first_seen_at,
                "last_seen_at": row.last_seen_at,
                "occurrence_count": row.occurrence_count,
                "affected_match_count": row.affected_match_count,
                "affected_user_count": row.affected_user_count,
                "notes": row.notes,
            }
            for row in rows
        ]
    }


@router.get("/incidents/{incident_id}")
async def get_incident(
    incident_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    row = await session.get(SystemIncident, incident_id)
    if row is None:
        from .auth.errors import APIError

        raise APIError("admin_not_found")
    events = list(
        (
            await session.scalars(
                select(SystemLogEvent)
                .where(SystemLogEvent.incident_id == incident_id)
                .order_by(SystemLogEvent.happened_at.desc())
                .limit(100)
            )
        ).all()
    )
    return {
        "incident": {
            "id": str(row.id),
            "fingerprint": row.fingerprint,
            "title": row.title,
            "severity": row.severity,
            "status": row.status,
            "first_seen_at": row.first_seen_at,
            "last_seen_at": row.last_seen_at,
            "occurrence_count": row.occurrence_count,
            "affected_match_count": row.affected_match_count,
            "affected_user_count": row.affected_user_count,
            "notes": row.notes,
        },
        "events": [
            {
                "id": str(event.id),
                "level": event.level,
                "service": event.service,
                "message": event.message,
                "error_code": event.error_code,
                "happened_at": event.happened_at,
                "details": event.details,
            }
            for event in events
        ],
    }


@router.patch("/incidents/{incident_id}")
async def patch_incident(
    incident_id: UUID,
    payload: IncidentPatch,
    auth: Annotated[AuthContext, Depends(get_admin_auth)],
    _: Annotated[None, Depends(require_browser_origin)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    row = await session.get(SystemIncident, incident_id, with_for_update=True)
    if row is None:
        from .auth.errors import APIError

        raise APIError("admin_not_found")
    now = datetime.now(UTC)
    if payload.status is not None:
        next_status = payload.status.upper()
        if next_status not in {"OPEN", "ACKNOWLEDGED", "RESOLVED"}:
            from .auth.errors import APIError

            raise APIError("admin_query_invalid")
        row.status = next_status
        if next_status == "ACKNOWLEDGED":
            row.acknowledged_by_user_id, row.acknowledged_at = auth.user_id, now
        if next_status == "RESOLVED":
            row.resolved_by_user_id, row.resolved_at = auth.user_id, now
    if payload.notes is not None:
        row.notes = payload.notes.strip() or None
    AuditService().record(
        session,
        actor_user_id=auth.user_id,
        action="incident.update",
        target_type="system_incident",
        target_id=str(row.id),
        details={"status": row.status},
    )
    await session.commit()
    return {"id": str(row.id), "status": row.status, "notes": row.notes}


__all__ = ["router"]

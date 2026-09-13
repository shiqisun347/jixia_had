"""Admin diagnostics: bounded event/task views and incident acknowledgement."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import String, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from .audit.service import AuditService
from .auth.dependencies import get_admin_auth, get_database_session, require_browser_origin
from .auth.session import AuthContext
from .data_capture.diagnostics import DiagnosticWriter
from .models import BackgroundTask, SystemIncident, SystemLogEvent

router = APIRouter(prefix="/api/admin", tags=["admin-diagnostics"])


class IncidentPatch(BaseModel):
    status: str | None = None
    notes: str | None = Field(default=None, max_length=2000)


def _page(page: int, size: int) -> tuple[int, int]:
    return max(1, page), min(100, max(1, size))


def _runtime_log_cursor(row: SystemLogEvent) -> str:
    payload = f"{row.happened_at.isoformat()}|{row.id}"
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _parse_runtime_log_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        timestamp, row_id = base64.urlsafe_b64decode(padded).decode().rsplit("|", 1)
        happened_at = datetime.fromisoformat(timestamp)
        if happened_at.tzinfo is None:
            raise ValueError
        return happened_at, UUID(row_id)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        from .auth.errors import APIError

        raise APIError("admin_query_invalid") from None


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
                "request_id": row.request_id,
                "trace_id": row.trace_id,
                "format_version_id": str(row.format_version_id) if row.format_version_id else None,
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


@router.get("/runtime-logs")
async def list_runtime_logs(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    page: int = Query(1),
    page_size: int = Query(25),
    q: str = Query(""),
    level: str = Query(""),
    service: str = Query(""),
    match_id: UUID | None = None,
    format_version_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: str = Query("", max_length=256),
) -> dict[str, Any]:
    page, page_size = _page(page, page_size)
    filters: list[ColumnElement[bool]] = []
    if level:
        filters.append(SystemLogEvent.level == level.upper())
    if service:
        filters.append(SystemLogEvent.service == service)
    if match_id is not None:
        filters.append(SystemLogEvent.match_id == match_id)
    if format_version_id is not None:
        filters.append(SystemLogEvent.format_version_id == format_version_id)
    if since is not None:
        filters.append(SystemLogEvent.happened_at >= since)
    if until is not None:
        filters.append(SystemLogEvent.happened_at <= until)
    if q.strip():
        needle = f"%{q.strip()}%"
        filters.append(
            (SystemLogEvent.message.ilike(needle))
            | (SystemLogEvent.logger_name.ilike(needle))
            | (SystemLogEvent.error_code.ilike(needle))
            | (SystemLogEvent.request_id.ilike(needle))
            | (SystemLogEvent.trace_id.ilike(needle))
            | (SystemLogEvent.match_id.cast(String).ilike(needle))
        )
    if cursor:
        cursor_at, cursor_id = _parse_runtime_log_cursor(cursor)
        filters.append(
            tuple_(SystemLogEvent.happened_at, SystemLogEvent.id) < (cursor_at, cursor_id)
        )
    total = int(
        await session.scalar(select(func.count()).select_from(SystemLogEvent).where(*filters)) or 0
    )
    rows = list(
        (
            await session.scalars(
                select(SystemLogEvent)
                .where(*filters)
                .order_by(SystemLogEvent.happened_at.desc(), SystemLogEvent.id.desc())
                .offset(0 if cursor else (page - 1) * page_size)
                .limit(page_size + 1)
            )
        ).all()
    )
    has_more = len(rows) > page_size
    rows = rows[:page_size]
    return {
        "items": [
            {
                "id": str(row.id),
                "level": row.level,
                "service": row.service,
                "logger_name": row.logger_name,
                "message": row.message,
                "error_code": row.error_code,
                "request_id": row.request_id,
                "trace_id": row.trace_id,
                "format_version_id": str(row.format_version_id) if row.format_version_id else None,
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
        "next_cursor": _runtime_log_cursor(rows[-1]) if rows and has_more else None,
    }


@router.get("/runtime-logs/stats")
async def runtime_log_stats(
    request: Request,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
) -> dict[str, int]:
    writer = request.app.state.diagnostic_writer
    if not isinstance(writer, DiagnosticWriter):
        return {"queue_size": 0, "queue_capacity": 0, "dropped_count": 0}
    return {
        "queue_size": writer.queue_size,
        "queue_capacity": writer.queue_capacity,
        "dropped_count": writer.dropped_count,
    }


@router.get("/diagnostics/tasks")
async def list_background_tasks(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    page: int = Query(1),
    page_size: int = Query(50),
    status: str = Query(""),
    task_type: str = Query(""),
) -> dict[str, Any]:
    page, page_size = _page(page, page_size)
    filters: list[ColumnElement[bool]] = []
    if status:
        filters.append(BackgroundTask.status == status.upper())
    if task_type:
        filters.append(BackgroundTask.task_type == task_type)
    total = int(
        await session.scalar(select(func.count()).select_from(BackgroundTask).where(*filters)) or 0
    )
    rows = list(
        (
            await session.scalars(
                select(BackgroundTask)
                .where(*filters)
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
                "max_attempts": row.max_attempts,
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


@router.post("/diagnostics/tasks/{task_id}/retry")
async def retry_background_task(
    task_id: UUID,
    auth: Annotated[AuthContext, Depends(get_admin_auth)],
    _: Annotated[None, Depends(require_browser_origin)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    row = await session.get(BackgroundTask, task_id, with_for_update=True)
    if row is None:
        from .auth.errors import APIError

        raise APIError("admin_not_found")
    if row.status == "FAILED":
        row.status = "PENDING"
        row.attempts = 0
        row.error_code = None
        row.available_at = datetime.now(UTC)
        row.lease_until = None
        AuditService().record(
            session,
            actor_user_id=auth.user_id,
            action="admin.background_task.retried",
            target_type="background_task",
            target_id=str(row.id),
        )
        await session.commit()
    return {"id": str(row.id), "status": row.status}


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

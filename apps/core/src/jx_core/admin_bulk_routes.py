"""Small, explicit bulk-management API for administrator tables."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .audit.service import AuditService
from .auth.dependencies import get_admin_auth, get_database_session, require_browser_origin
from .auth.session import AuthContext
from .models import (
    AgentProfile,
    BulkJob,
    BulkJobItem,
    Match,
    ModelProfile,
    Topic,
    User,
    VoiceProfile,
)

router = APIRouter(prefix="/api/admin", tags=["admin-bulk"])


class BulkRequest(BaseModel):
    resource: str = Field(pattern="^(user|agent|model|voice|topic|match)$")
    operation: str = Field(pattern="^(ENABLE|DISABLE|DELETE)$")
    target_ids: list[UUID] = Field(min_length=1, max_length=500)


def _resource_model(resource: str):
    return {
        "user": User,
        "agent": AgentProfile,
        "model": ModelProfile,
        "voice": VoiceProfile,
        "topic": Topic,
        "match": Match,
    }[resource]


@router.post("/bulk/preflight")
async def bulk_preflight(
    payload: BulkRequest,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    model = _resource_model(payload.resource)
    rows = list(
        (await session.scalars(select(model).where(model.id.in_(set(payload.target_ids))))).all()
    )
    missing = sorted(str(item) for item in set(payload.target_ids) - {row.id for row in rows})
    blocked: list[dict[str, str]] = []
    if payload.resource == "match" and payload.operation == "DELETE":
        blocked = [
            {"id": str(row.id), "reason": "only terminal matches can be deleted"}
            for row in rows
            if row.status not in {"FINISHED", "TERMINATED", "ERROR"}
        ]
    return {
        "resource": payload.resource,
        "operation": payload.operation,
        "total": len(payload.target_ids),
        "available": len(rows) - len(blocked),
        "missing": missing,
        "blocked": blocked,
        "requires_confirmation": True,
    }


@router.post("/bulk")
async def create_bulk(
    payload: BulkRequest,
    auth: Annotated[AuthContext, Depends(get_admin_auth)],
    _: Annotated[None, Depends(require_browser_origin)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    model = _resource_model(payload.resource)
    unique_ids = list(dict.fromkeys(payload.target_ids))
    rows = list(
        (
            await session.scalars(select(model).where(model.id.in_(unique_ids)).with_for_update())
        ).all()
    )
    job = BulkJob(
        created_by_user_id=auth.user_id,
        resource=payload.resource,
        operation=payload.operation,
        status="RUNNING",
        total_items=len(unique_ids),
    )
    session.add(job)
    await session.flush()
    succeeded = failed = 0
    for target_id in unique_ids:
        row = next((item for item in rows if item.id == target_id), None)
        item = BulkJobItem(job_id=job.id, target_id=target_id)
        if row is None:
            item.status, item.error_code = "FAILED", "not_found"
            failed += 1
        elif (
            payload.resource == "match"
            and payload.operation == "DELETE"
            and row.status not in {"FINISHED", "TERMINATED", "ERROR"}
        ):
            item.status, item.error_code = "SKIPPED", "match_not_terminal"
            failed += 1
        else:
            if payload.resource != "match":
                row.status = (
                    "ACTIVE"
                    if payload.resource == "user" and payload.operation == "ENABLE"
                    else ("DISABLED" if payload.operation == "DISABLE" else "ENABLED")
                )
            elif payload.operation == "DELETE":
                await session.delete(row)
            item.status = "SUCCEEDED"
            succeeded += 1
        item.completed_at = datetime.now(UTC)
        session.add(item)
    job.processed_items = len(unique_ids)
    job.succeeded_items = succeeded
    job.failed_items = failed
    job.status = "SUCCEEDED" if failed == 0 else ("PARTIAL" if succeeded else "FAILED")
    job.completed_at = datetime.now(UTC)
    AuditService().record(
        session,
        actor_user_id=auth.user_id,
        action="admin.bulk",
        target_type=payload.resource,
        target_id=str(job.id),
        details={
            "operation": payload.operation,
            "total": len(unique_ids),
            "succeeded": succeeded,
            "failed": failed,
        },
    )
    await session.commit()
    return {
        "id": str(job.id),
        "status": job.status,
        "total_items": job.total_items,
        "processed_items": job.processed_items,
        "succeeded_items": succeeded,
        "failed_items": failed,
    }


@router.get("/bulk/{job_id}")
async def get_bulk(
    job_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    job = await session.get(BulkJob, job_id)
    if job is None:
        from .auth.errors import APIError

        raise APIError("admin_not_found")
    items = list(
        (
            await session.scalars(
                select(BulkJobItem)
                .where(BulkJobItem.job_id == job.id)
                .order_by(BulkJobItem.completed_at)
            )
        ).all()
    )
    return {
        "job": {
            "id": str(job.id),
            "resource": job.resource,
            "operation": job.operation,
            "status": job.status,
            "total_items": job.total_items,
            "processed_items": job.processed_items,
            "succeeded_items": job.succeeded_items,
            "failed_items": job.failed_items,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
        },
        "items": [
            {"target_id": str(item.target_id), "status": item.status, "error_code": item.error_code}
            for item in items
        ],
    }


__all__ = ["router"]

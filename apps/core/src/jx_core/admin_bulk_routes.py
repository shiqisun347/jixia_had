"""Small, explicit bulk-management API for administrator tables."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .audit.service import AuditService
from .auth.dependencies import get_admin_auth, get_database_session, require_browser_origin
from .auth.errors import APIError, AuthError
from .auth.session import AuthContext
from .models import (
    AgentProfile,
    BulkJob,
    BulkJobItem,
    Match,
    ModelProfile,
    Rule,
    Topic,
    User,
    VoiceProfile,
)
from .rules.service import CatalogService

router = APIRouter(prefix="/api/admin", tags=["admin-bulk"])


async def _sync_voice_agents(session: AsyncSession, voice_id: UUID, status: str) -> None:
    agents = list(
        (
            await session.scalars(
                select(AgentProfile)
                .where(
                    AgentProfile.voice_profile_id == voice_id,
                    AgentProfile.rule_id.is_not(None),
                )
                .with_for_update()
            )
        ).all()
    )
    for agent in agents:
        agent.status = status
    rule_ids = {agent.rule_id for agent in agents if agent.rule_id is not None}
    if rule_ids:
        rules = list(
            (
                await session.scalars(select(Rule).where(Rule.id.in_(rule_ids)).with_for_update())
            ).all()
        )
        for rule in rules:
            rule.config_revision += 1


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
    if payload.operation == "DISABLE" and payload.resource in {"agent", "model", "voice"}:
        for row in rows:
            reason = await CatalogService().status_change_block(
                session,
                kind=f"{payload.resource}s",
                item_id=row.id,
                status="DISABLED",
            )
            if reason:
                blocked.append({"id": str(row.id), "reason": reason})
    response = {
        "resource": payload.resource,
        "operation": payload.operation,
        "total": len(payload.target_ids),
        "available": len(rows) - len(blocked),
        "missing": missing,
        "blocked": blocked,
        "requires_confirmation": True,
    }
    if payload.resource == "voice" and payload.operation == "DELETE":
        response["affected_agents"] = int(
            await session.scalar(
                select(func.count())
                .select_from(AgentProfile)
                .where(AgentProfile.voice_profile_id.in_(set(payload.target_ids)))
            )
            or 0
        )
        response["affected_rules"] = int(
            await session.scalar(
                select(func.count(func.distinct(AgentProfile.rule_id))).where(
                    AgentProfile.voice_profile_id.in_(set(payload.target_ids)),
                    AgentProfile.rule_id.is_not(None),
                )
            )
            or 0
        )
    return response


@router.post("/bulk")
async def create_bulk(
    payload: BulkRequest,
    request: Request,
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
    if payload.resource == "match" and payload.operation == "DELETE":
        # Use the same storage/runtime-safe path as single-match deletion.
        await session.rollback()
        from .admin_routes import delete_match

        outcomes: dict[UUID, tuple[str, str | None]] = {}
        for target_id in unique_ids:
            try:
                await delete_match(target_id, request, auth, session)
            except APIError as error:
                await session.rollback()
                outcomes[target_id] = ("FAILED", error.code)
            except Exception:
                await session.rollback()
                outcomes[target_id] = ("FAILED", "match_delete_failed")
            else:
                outcomes[target_id] = ("SUCCEEDED", None)
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
            status, error_code = outcomes[target_id]
            succeeded += status == "SUCCEEDED"
            failed += status != "SUCCEEDED"
            session.add(
                BulkJobItem(
                    job_id=job.id,
                    target_id=target_id,
                    status=status,
                    error_code=error_code,
                    completed_at=datetime.now(UTC),
                )
            )
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
        elif payload.operation == "DISABLE" and payload.resource in {"agent", "model", "voice"}:
            reason = await CatalogService().status_change_block(
                session,
                kind=f"{payload.resource}s",
                item_id=target_id,
                status="DISABLED",
            )
            if reason:
                item.status, item.error_code = "SKIPPED", reason
                failed += 1
            else:
                row.status = "DISABLED"
                if payload.resource == "voice":
                    await _sync_voice_agents(session, target_id, "DISABLED")
                item.status = "SUCCEEDED"
                succeeded += 1
        elif payload.resource == "voice" and payload.operation == "DELETE":
            try:
                outcome = await CatalogService().delete_voice(
                    session,
                    voice_id=target_id,
                    actor_user_id=auth.user_id,
                )
            except AuthError as error:
                item.status, item.error_code = "FAILED", error.code
                failed += 1
            else:
                item.status = "SUCCEEDED"
                item.error_code = "archived" if outcome == "ARCHIVED" else None
                succeeded += 1
        else:
            if payload.resource != "match":
                row.status = (
                    "ACTIVE"
                    if payload.resource == "user" and payload.operation == "ENABLE"
                    else ("DISABLED" if payload.operation == "DISABLE" else "ENABLED")
                )
                if payload.resource == "voice":
                    await _sync_voice_agents(session, target_id, str(row.status))
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

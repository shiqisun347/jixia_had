"""Bounded PostgreSQL task claim and retry semantics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    and_,
    func,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class JobsBase(DeclarativeBase):
    pass


class BackgroundTask(JobsBase):
    __tablename__ = "background_tasks"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="ck_background_tasks_attempts"),
        CheckConstraint("max_attempts BETWEEN 1 AND 5", name="ck_background_tasks_max_attempts"),
        Index("ix_background_tasks_claim", "status", "available_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True, slots=True)
class TaskClaim:
    task_id: UUID
    task_type: str
    payload: dict[str, Any]
    attempt_no: int
    max_attempts: int


async def claim_next(
    database_session: AsyncSession,
    *,
    task_type: str,
    now: datetime | None = None,
    lease_seconds: int = 120,
) -> TaskClaim | None:
    async with database_session.begin():
        current = now or await database_session.scalar(select(func.now()))
        if not isinstance(current, datetime):
            raise RuntimeError("database_time_unavailable")
        task = (
            await database_session.execute(
                select(BackgroundTask)
                .where(
                    BackgroundTask.task_type == task_type,
                    BackgroundTask.attempts < BackgroundTask.max_attempts,
                    or_(
                        BackgroundTask.status == "PENDING",
                        and_(
                            BackgroundTask.status == "RUNNING",
                            BackgroundTask.lease_until <= current,
                        ),
                    ),
                    BackgroundTask.available_at <= current,
                )
                .order_by(BackgroundTask.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
        ).scalar_one_or_none()
        if task is None:
            return None
        task.status = "RUNNING"
        task.attempts += 1
        task.lease_until = current + timedelta(seconds=lease_seconds)
        await database_session.flush()
        return TaskClaim(
            task.id, task.task_type, dict(task.payload), task.attempts, task.max_attempts
        )


async def complete(
    database_session: AsyncSession, *, task_id: UUID, now: datetime | None = None
) -> None:
    async with database_session.begin():
        current = now or await database_session.scalar(select(func.now()))
        if not isinstance(current, datetime):
            raise RuntimeError("database_time_unavailable")
        task = await database_session.get(BackgroundTask, task_id, with_for_update=True)
        if task is not None:
            task.status = "SUCCEEDED"
            task.lease_until = None
            task.updated_at = current


async def fail(
    database_session: AsyncSession,
    *,
    task_id: UUID,
    error_code: str,
    now: datetime | None = None,
) -> bool:
    """Record failure; return True when no retry remains."""

    async with database_session.begin():
        current = now or await database_session.scalar(select(func.now()))
        if not isinstance(current, datetime):
            raise RuntimeError("database_time_unavailable")
        task = await database_session.get(BackgroundTask, task_id, with_for_update=True)
        if task is None:
            return True
        task.error_code = error_code
        task.lease_until = None
        task.updated_at = current
        terminal = task.attempts >= task.max_attempts
        task.status = "FAILED" if terminal else "PENDING"
        if not terminal:
            task.available_at = current
        return terminal


__all__ = ["BackgroundTask", "JobsBase", "TaskClaim", "claim_next", "complete", "fail"]

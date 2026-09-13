"""One result-visibility policy shared by all public experiment reads."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    ExperimentMatchAttempt,
    ExperimentResultOverride,
    ParticipantAnnotationTask,
)


async def can_view_experiment_result(
    session: AsyncSession,
    *,
    match_id: UUID,
    user_id: UUID,
    role: str,
) -> bool:
    if role == "ADMIN":
        return True
    attempt = await session.scalar(
        select(ExperimentMatchAttempt).where(ExperimentMatchAttempt.match_id == match_id)
    )
    if attempt is None:
        return True
    if attempt.public_at is not None:
        return True
    override = await session.scalar(
        select(ExperimentResultOverride.id).where(
            ExperimentResultOverride.experiment_attempt_id == attempt.id
        )
    )
    if override is not None:
        return True
    task_status = await session.scalar(
        select(ParticipantAnnotationTask.status).where(
            ParticipantAnnotationTask.experiment_attempt_id == attempt.id,
            ParticipantAnnotationTask.user_id == user_id,
        )
    )
    return task_status == "SUBMITTED"


__all__ = ["can_view_experiment_result"]

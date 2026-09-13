"""Database-backed experiment room permissions shared by room and match services."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..models import ExperimentBatch, ExperimentMatchAttempt, ScheduledMatch, ScheduledSeat


@dataclass(frozen=True, slots=True)
class ExperimentRoomLink:
    batch_id: UUID
    scheduled_match_id: UUID
    attempt_id: UUID
    attempt_status: str
    scheduled_match_kind: str


async def resolve_room_link(session: AsyncSession, *, room_id: UUID) -> ExperimentRoomLink | None:
    row = (
        await session.execute(
            select(ExperimentMatchAttempt, ScheduledMatch, ExperimentBatch)
            .join(ScheduledMatch, ScheduledMatch.id == ExperimentMatchAttempt.scheduled_match_id)
            .join(ExperimentBatch, ExperimentBatch.id == ScheduledMatch.batch_id)
            .where(
                ExperimentMatchAttempt.room_id == room_id,
                ExperimentBatch.status != "DISABLED",
            )
        )
    ).one_or_none()
    if row is None:
        return None
    attempt, scheduled, batch = row
    return ExperimentRoomLink(
        batch_id=batch.id,
        scheduled_match_id=scheduled.id,
        attempt_id=attempt.id,
        attempt_status=attempt.status,
        scheduled_match_kind=scheduled.kind,
    )


async def is_experiment_side_controller(
    session: AsyncSession, *, scheduled_match_id: UUID, user_id: UUID
) -> bool:
    candidate = aliased(ScheduledSeat)
    earlier_human = aliased(ScheduledSeat)
    seat_id = await session.scalar(
        select(candidate.id)
        .where(
            candidate.scheduled_match_id == scheduled_match_id,
            candidate.user_id == user_id,
            candidate.occupant_kind == "HUMAN",
            ~exists()
            .where(
                earlier_human.scheduled_match_id == candidate.scheduled_match_id,
                earlier_human.side == candidate.side,
                earlier_human.occupant_kind == "HUMAN",
                earlier_human.seat_no < candidate.seat_no,
            )
            .correlate(candidate),
        )
        .limit(1)
    )
    return seat_id is not None


__all__ = ["ExperimentRoomLink", "is_experiment_side_controller", "resolve_room_link"]

"""Create immutable experiment annotation work after an effective finish."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    ExperimentExpert,
    ExpertAnnotationAnswer,
    ExpertAnnotationTask,
    FreeDebateOpportunity,
    ParticipantAnnotationItem,
    ParticipantAnnotationTask,
    ScheduledMatch,
    ScheduledSeat,
    Speech,
)

QUESTIONNAIRE_VERSION = "paper-v2.1-strict-2026-08-24"


def _payload_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


async def create_postmatch_annotation_work(
    session: AsyncSession,
    *,
    attempt_id: UUID,
    scheduled_match: ScheduledMatch,
    match_id: UUID,
    now: datetime | None = None,
) -> None:
    """Idempotently create participant items and frozen expert opportunities."""

    if scheduled_match.kind == "TRAINING":
        return

    created_at = now or datetime.now(UTC)
    seats = list(
        (
            await session.scalars(
                select(ScheduledSeat)
                .where(
                    ScheduledSeat.scheduled_match_id == scheduled_match.id,
                    ScheduledSeat.occupant_kind == "HUMAN",
                )
                .order_by(ScheduledSeat.side, ScheduledSeat.seat_no)
            )
        ).all()
    )
    if len(seats) != 6 or any(seat.user_id is None for seat in seats):
        raise ValueError("experiment_participant_roster_invalid")

    speeches = list(
        (
            await session.scalars(
                select(Speech)
                .where(
                    Speech.match_id == match_id,
                    Speech.status == "FINALIZED",
                    Speech.opportunity_id.is_not(None),
                )
                .order_by(Speech.started_at, Speech.id)
            )
        ).all()
    )
    for seat in seats:
        assert seat.user_id is not None
        task = await session.scalar(
            select(ParticipantAnnotationTask).where(
                ParticipantAnnotationTask.experiment_attempt_id == attempt_id,
                ParticipantAnnotationTask.user_id == seat.user_id,
            )
        )
        if task is None:
            task = ParticipantAnnotationTask(
                experiment_attempt_id=attempt_id,
                user_id=seat.user_id,
                questionnaire_version=QUESTIONNAIRE_VERSION,
                due_at=created_at + timedelta(hours=24),
            )
            session.add(task)
            await session.flush()
        existing_speech_ids = set(
            (
                await session.scalars(
                    select(ParticipantAnnotationItem.speech_id).where(
                        ParticipantAnnotationItem.task_id == task.id
                    )
                )
            ).all()
        )
        position = len(existing_speech_ids)
        for speech in speeches:
            subject_kind = (
                "HUMAN_SELF"
                if speech.speaker_kind == "HUMAN" and speech.user_id == seat.user_id
                else "TEAM_AI"
                if speech.speaker_kind == "AGENT" and speech.side == seat.side
                else None
            )
            if (
                subject_kind is None
                or speech.opportunity_id is None
                or speech.id in existing_speech_ids
            ):
                continue
            position += 1
            session.add(
                ParticipantAnnotationItem(
                    task_id=task.id,
                    opportunity_id=speech.opportunity_id,
                    subject_kind=subject_kind,
                    speech_id=speech.id,
                    position=position,
                )
            )

    opportunities = list(
        (
            await session.scalars(
                select(FreeDebateOpportunity)
                .where(FreeDebateOpportunity.experiment_attempt_id == attempt_id)
                .order_by(FreeDebateOpportunity.sequence_no)
            )
        ).all()
    )
    experts = list(
        (
            await session.scalars(
                select(ExperimentExpert)
                .where(ExperimentExpert.batch_id == scheduled_match.batch_id)
                .order_by(ExperimentExpert.expert_code)
            )
        ).all()
    )
    for expert in experts:
        task = await session.scalar(
            select(ExpertAnnotationTask).where(
                ExpertAnnotationTask.batch_id == scheduled_match.batch_id,
                ExpertAnnotationTask.expert_user_id == expert.user_id,
            )
        )
        if task is None:
            task = ExpertAnnotationTask(
                batch_id=scheduled_match.batch_id,
                expert_user_id=expert.user_id,
            )
            session.add(task)
            await session.flush()
        existing = set(
            (
                await session.scalars(
                    select(ExpertAnnotationAnswer.opportunity_id).where(
                        ExpertAnnotationAnswer.task_id == task.id
                    )
                )
            ).all()
        )
        for opportunity in opportunities:
            if opportunity.id in existing:
                continue
            frozen_payload: dict[str, object] = {
                "opportunity_id": str(opportunity.id),
                "sequence_no": opportunity.sequence_no,
                "side": opportunity.side,
                "trigger_kind": opportunity.trigger_kind,
                "source_speech_id": (
                    str(opportunity.source_speech_id)
                    if opportunity.source_speech_id is not None
                    else None
                ),
                "context_version": opportunity.context_version,
                "context": opportunity.frozen_context,
            }
            session.add(
                ExpertAnnotationAnswer(
                    task_id=task.id,
                    opportunity_id=opportunity.id,
                    frozen_payload=frozen_payload,
                    payload_sha256=_payload_hash(frozen_payload),
                )
            )


__all__ = ["QUESTIONNAIRE_VERSION", "create_postmatch_annotation_work"]

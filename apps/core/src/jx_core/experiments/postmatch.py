"""Create immutable experiment annotation work after an effective finish."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import Text, func, select
from sqlalchemy import cast as sa_cast
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

# Annotation payloads are duplicated into participant items and expert frozen
# answers. Keep the finish transaction bounded even when a malformed match
# contains an unexpectedly large transcript or frozen context.
POSTMATCH_MAX_SPEECH_ROWS = 4_096
POSTMATCH_MAX_OPPORTUNITY_ROWS = 4_096
POSTMATCH_MAX_SPEECH_TEXT_BYTES = 64 * 1024 * 1024
POSTMATCH_MAX_CONTEXT_BYTES = 8 * 1024 * 1024
POSTMATCH_MAX_TOTAL_CONTEXT_BYTES = 64 * 1024 * 1024


class PostmatchAnnotationWorkError(ValueError):
    """Stable internal code for rejecting oversized annotation materialization."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _payload_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _payload_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def _check_speech_budget(speeches: list[Speech]) -> None:
    if (
        len(speeches) > POSTMATCH_MAX_SPEECH_ROWS
        or sum(len((speech.display_text or "").encode("utf-8")) for speech in speeches)
        > POSTMATCH_MAX_SPEECH_TEXT_BYTES
    ):
        raise PostmatchAnnotationWorkError("experiment_postmatch_payload_too_large")


def _bounded_context(value: object) -> dict[str, object]:
    if value is None:
        context: dict[str, object] = {}
    elif isinstance(value, dict):
        context = dict(cast(dict[str, object], value))
    else:
        raise PostmatchAnnotationWorkError("experiment_postmatch_payload_too_large")
    if len(_payload_json_bytes(context)) > POSTMATCH_MAX_CONTEXT_BYTES:
        raise PostmatchAnnotationWorkError("experiment_postmatch_payload_too_large")
    return context


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

    speech_count, speech_text_bytes = (
        await session.execute(
            select(
                func.count(Speech.id),
                func.coalesce(
                    func.sum(func.octet_length(func.coalesce(Speech.display_text, ""))), 0
                ),
            ).where(
                Speech.match_id == match_id,
                Speech.status == "FINALIZED",
                Speech.opportunity_id.is_not(None),
            )
        )
    ).one()
    if (
        int(speech_count or 0) > POSTMATCH_MAX_SPEECH_ROWS
        or int(speech_text_bytes or 0) > POSTMATCH_MAX_SPEECH_TEXT_BYTES
    ):
        raise PostmatchAnnotationWorkError("experiment_postmatch_payload_too_large")

    opportunity_count, opportunity_context_bytes, max_context_bytes = (
        await session.execute(
            select(
                func.count(FreeDebateOpportunity.id),
                func.coalesce(
                    func.sum(
                        func.octet_length(
                            sa_cast(FreeDebateOpportunity.frozen_context, Text)
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.max(
                        func.octet_length(
                            sa_cast(FreeDebateOpportunity.frozen_context, Text)
                        )
                    ),
                    0,
                ),
            ).where(FreeDebateOpportunity.experiment_attempt_id == attempt_id)
        )
    ).one()
    if (
        int(opportunity_count or 0) > POSTMATCH_MAX_OPPORTUNITY_ROWS
        or int(opportunity_context_bytes or 0) > POSTMATCH_MAX_TOTAL_CONTEXT_BYTES
        or int(max_context_bytes or 0) > POSTMATCH_MAX_CONTEXT_BYTES
    ):
        raise PostmatchAnnotationWorkError("experiment_postmatch_payload_too_large")

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
                .limit(POSTMATCH_MAX_SPEECH_ROWS + 1)
            )
        ).all()
    )
    _check_speech_budget(speeches)
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
                .limit(POSTMATCH_MAX_OPPORTUNITY_ROWS + 1)
            )
        ).all()
    )
    if len(opportunities) > POSTMATCH_MAX_OPPORTUNITY_ROWS:
        raise PostmatchAnnotationWorkError("experiment_postmatch_payload_too_large")
    total_context_bytes = 0
    for opportunity in opportunities:
        context_bytes = len(_payload_json_bytes(_bounded_context(opportunity.frozen_context)))
        total_context_bytes += context_bytes
        if total_context_bytes > POSTMATCH_MAX_TOTAL_CONTEXT_BYTES:
            raise PostmatchAnnotationWorkError("experiment_postmatch_payload_too_large")
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
                "context": _bounded_context(opportunity.frozen_context),
            }
            encoded_payload = _payload_json_bytes(frozen_payload)
            if len(encoded_payload) > POSTMATCH_MAX_CONTEXT_BYTES:
                raise PostmatchAnnotationWorkError("experiment_postmatch_payload_too_large")
            session.add(
                ExpertAnnotationAnswer(
                    task_id=task.id,
                    opportunity_id=opportunity.id,
                    frozen_payload=frozen_payload,
                    payload_sha256=_payload_hash(frozen_payload),
                )
            )


__all__ = [
    "QUESTIONNAIRE_VERSION",
    "PostmatchAnnotationWorkError",
    "create_postmatch_annotation_work",
]

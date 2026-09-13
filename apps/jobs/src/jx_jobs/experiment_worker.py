"""Bounded paper-experiment export and retention-report workers."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .task_queue import claim_next, complete, fail


class ExperimentTaskError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _frozen_history(
    speeches: list[dict[str, Any]], opportunity: dict[str, Any]
) -> list[dict[str, Any]]:
    source_id = opportunity.get("source_speech_id")
    source_index = next(
        (
            index
            for index, speech in enumerate(speeches)
            if source_id is not None and str(speech["id"]) == str(source_id)
        ),
        None,
    )
    if source_index is not None:
        eligible = speeches[: source_index + 1]
    else:
        opened_at = opportunity.get("opened_at")
        eligible = [
            speech
            for speech in speeches
            if opened_at is not None
            and speech.get("finalized_at") is not None
            and speech["finalized_at"] <= opened_at
        ]
    return [
        {
            "speech_id": str(speech["id"]),
            "speaker_kind": speech["speaker_kind"],
            "side": speech["side"],
            "seat_no": speech["seat_no"],
            "text": speech.get("display_text") or "",
        }
        for speech in eligible
    ]


async def process_one_experiment_postmatch(
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    """Generate participant and expert work after the finish transaction commits."""

    async with session_factory() as session:
        claim = await claim_next(session, task_type="EXPERIMENT_POSTMATCH", lease_seconds=300)
    if claim is None:
        return False
    try:
        attempt_id = UUID(str(claim.payload.get("attempt_id", "")))
        scheduled_match_id = UUID(str(claim.payload.get("scheduled_match_id", "")))
        match_id = UUID(str(claim.payload.get("match_id", "")))
        created_at = datetime.now(UTC)
        async with session_factory() as session:
            async with session.begin():
                context = (
                    (
                        await session.execute(
                            text(
                                """
                                SELECT sm.batch_id
                                FROM experiment_match_attempts ema
                                JOIN scheduled_matches sm ON sm.id=ema.scheduled_match_id
                                WHERE ema.id=:attempt_id
                                  AND ema.scheduled_match_id=:scheduled_match_id
                                  AND ema.match_id=:match_id
                                  AND ema.status='COMPLETED'
                                  AND sm.effective_attempt_id=ema.id
                                """
                            ),
                            {
                                "attempt_id": attempt_id,
                                "scheduled_match_id": scheduled_match_id,
                                "match_id": match_id,
                            },
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if context is None:
                    raise ExperimentTaskError("experiment_postmatch_not_effective")
                batch_id = UUID(str(context["batch_id"]))
                seats = list(
                    (
                        await session.execute(
                            text(
                                """
                                SELECT side, user_id
                                FROM scheduled_seats
                                WHERE scheduled_match_id=:scheduled_match_id
                                  AND occupant_kind='HUMAN'
                                ORDER BY side, seat_no
                                """
                            ),
                            {"scheduled_match_id": scheduled_match_id},
                        )
                    ).mappings()
                )
                if len(seats) != 6:
                    raise ExperimentTaskError("experiment_participant_roster_invalid")
                for seat in seats:
                    await session.execute(
                        text(
                            """
                            INSERT INTO participant_annotation_tasks
                              (id, experiment_attempt_id, user_id, status,
                               questionnaire_version, due_at, late, created_at)
                            VALUES (:id, :attempt_id, :user_id, 'PENDING',
                                    'paper-v2.0-2026-08-21', :due_at, false, :created_at)
                            ON CONFLICT (experiment_attempt_id, user_id) DO NOTHING
                            """
                        ),
                        {
                            "id": uuid4(),
                            "attempt_id": attempt_id,
                            "user_id": seat["user_id"],
                            "due_at": created_at + timedelta(hours=24),
                            "created_at": created_at,
                        },
                    )
                speeches = list(
                    (
                        await session.execute(
                            text(
                                """
                                SELECT id, opportunity_id, speaker_kind, user_id, side,
                                       seat_no, display_text, started_at, finalized_at
                                FROM speeches
                                WHERE match_id=:match_id AND status='FINALIZED'
                                ORDER BY started_at, id
                                """
                            ),
                            {"match_id": match_id},
                        )
                    ).mappings()
                )
                for seat in seats:
                    task_id = await session.scalar(
                        text(
                            "SELECT id FROM participant_annotation_tasks "
                            "WHERE experiment_attempt_id=:attempt_id AND user_id=:user_id"
                        ),
                        {"attempt_id": attempt_id, "user_id": seat["user_id"]},
                    )
                    position = int(
                        await session.scalar(
                            text(
                                "SELECT coalesce(max(position), 0) "
                                "FROM participant_annotation_items WHERE task_id=:task_id"
                            ),
                            {"task_id": task_id},
                        )
                        or 0
                    )
                    for speech in speeches:
                        if speech["opportunity_id"] is None:
                            continue
                        subject_kind = (
                            "HUMAN_SELF"
                            if speech["speaker_kind"] == "HUMAN"
                            and speech["user_id"] == seat["user_id"]
                            else "TEAM_AI"
                            if speech["speaker_kind"] == "AGENT" and speech["side"] == seat["side"]
                            else None
                        )
                        if subject_kind is None:
                            continue
                        position += 1
                        await session.execute(
                            text(
                                """
                                INSERT INTO participant_annotation_items
                                  (id, task_id, opportunity_id, subject_kind,
                                   speech_id, position)
                                VALUES (:id, :task_id, :opportunity_id, :subject_kind,
                                        :speech_id, :position)
                                ON CONFLICT (task_id, opportunity_id, subject_kind) DO NOTHING
                                """
                            ),
                            {
                                "id": uuid4(),
                                "task_id": task_id,
                                "opportunity_id": speech["opportunity_id"],
                                "subject_kind": subject_kind,
                                "speech_id": speech["id"],
                                "position": position,
                            },
                        )
                experts = list(
                    (
                        await session.execute(
                            text(
                                "SELECT user_id FROM experiment_experts "
                                "WHERE batch_id=:batch_id ORDER BY expert_code"
                            ),
                            {"batch_id": batch_id},
                        )
                    ).mappings()
                )
                opportunities = list(
                    (
                        await session.execute(
                            text(
                                """
                                SELECT id, sequence_no, side, trigger_kind,
                                       source_speech_id, context_version, frozen_context,
                                       opened_at
                                FROM free_debate_opportunities
                                WHERE experiment_attempt_id=:attempt_id
                                ORDER BY sequence_no
                                """
                            ),
                            {"attempt_id": attempt_id},
                        )
                    ).mappings()
                )
                enriched_contexts: dict[UUID, dict[str, Any]] = {}
                speech_rows = [dict(speech) for speech in speeches]
                for opportunity in opportunities:
                    opportunity_row = dict(opportunity)
                    context = dict(opportunity["frozen_context"] or {})
                    context["history"] = _frozen_history(speech_rows, opportunity_row)
                    enriched_contexts[UUID(str(opportunity["id"]))] = context
                    await session.execute(
                        text(
                            "UPDATE free_debate_opportunities "
                            "SET frozen_context=CAST(:context AS jsonb) WHERE id=:id"
                        ),
                        {"id": opportunity["id"], "context": _json(context)},
                    )
                for expert in experts:
                    expert_task_id = await session.scalar(
                        text(
                            "SELECT id FROM expert_annotation_tasks "
                            "WHERE batch_id=:batch_id AND expert_user_id=:user_id"
                        ),
                        {"batch_id": batch_id, "user_id": expert["user_id"]},
                    )
                    if expert_task_id is None:
                        expert_task_id = uuid4()
                        await session.execute(
                            text(
                                """
                                INSERT INTO expert_annotation_tasks
                                  (id, batch_id, expert_user_id, status, created_at)
                                VALUES (:id, :batch_id, :user_id, 'PENDING', :created_at)
                                ON CONFLICT (batch_id, expert_user_id) DO NOTHING
                                """
                            ),
                            {
                                "id": expert_task_id,
                                "batch_id": batch_id,
                                "user_id": expert["user_id"],
                                "created_at": created_at,
                            },
                        )
                        expert_task_id = await session.scalar(
                            text(
                                "SELECT id FROM expert_annotation_tasks "
                                "WHERE batch_id=:batch_id AND expert_user_id=:user_id"
                            ),
                            {"batch_id": batch_id, "user_id": expert["user_id"]},
                        )
                    for opportunity in opportunities:
                        frozen_payload: dict[str, Any] = {
                            "opportunity_id": str(opportunity["id"]),
                            "sequence_no": opportunity["sequence_no"],
                            "side": opportunity["side"],
                            "trigger_kind": opportunity["trigger_kind"],
                            "source_speech_id": (
                                str(opportunity["source_speech_id"])
                                if opportunity["source_speech_id"] is not None
                                else None
                            ),
                            "context_version": opportunity["context_version"],
                            "context": enriched_contexts[UUID(str(opportunity["id"]))],
                        }
                        encoded = json.dumps(
                            frozen_payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                        await session.execute(
                            text(
                                """
                                INSERT INTO expert_annotation_answers
                                  (id, task_id, opportunity_id, frozen_payload,
                                   payload_sha256, client_version, saved_at)
                                VALUES (:id, :task_id, :opportunity_id,
                                        CAST(:payload AS jsonb), :sha256, 1, :saved_at)
                                ON CONFLICT (task_id, opportunity_id) DO NOTHING
                                """
                            ),
                            {
                                "id": uuid4(),
                                "task_id": expert_task_id,
                                "opportunity_id": opportunity["id"],
                                "payload": encoded.decode(),
                                "sha256": hashlib.sha256(encoded).hexdigest(),
                                "saved_at": created_at,
                            },
                        )
        async with session_factory() as session:
            await complete(session, task_id=claim.task_id)
        return True
    except Exception as error:
        code = getattr(error, "code", "experiment_postmatch_failed")
        async with session_factory() as session:
            await fail(session, task_id=claim.task_id, error_code=str(code))
        return True


def _safe_text(value: Any) -> str:
    raw = "" if value is None else str(value)
    return "'" + raw if raw[:1] in {"=", "+", "-", "@"} else raw


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _csv_bytes(rows: list[dict[str, Any]], columns: list[str]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows({column: _safe_text(row.get(column)) for column in columns} for row in rows)
    return ("\ufeff" + buffer.getvalue()).encode()


_EXPORT_COLUMNS: dict[str, list[str]] = {
    "schedule.csv": [
        "scheduled_match_id",
        "round_no",
        "match_no",
        "kind",
        "status",
        "schedule_version",
        "topic_id",
        "topic_title",
        "affirmative_stance",
        "negative_stance",
        "source_text",
        "cedar_id",
        "affirmative_team",
        "negative_team",
        "scheduled_at",
        "effective_attempt_id",
        "attempt_no",
        "attempt_status",
        "started_at",
        "ended_at",
        "public_at",
    ],
    "opportunities.csv": [
        "scheduled_match_id",
        "attempt_no",
        "opportunity_id",
        "sequence_no",
        "side",
        "trigger_kind",
        "source_speech_id",
        "context_version",
        "opportunity_generation",
        "status",
        "selection_phase",
        "decision_fact",
        "allocation_fact",
        "execution_fact",
        "opened_at",
        "source_ended_at",
        "selection_deadline_at",
        "human_wait_deadline_at",
        "invalidated_reason",
        "completed_at",
    ],
    "human_hand_events.csv": [
        "scheduled_match_id",
        "opportunity_id",
        "participant_code",
        "event_type",
        "server_sequence",
        "connection_epoch",
        "accepted_at",
    ],
    "speaker_allocations.csv": [
        "scheduled_match_id",
        "opportunity_id",
        "speaker_kind",
        "participant_code",
        "agent_code",
        "reason",
        "effective",
        "allocated_at",
    ],
    "speeches.csv": [
        "scheduled_match_id",
        "speech_id",
        "opportunity_id",
        "action_key",
        "speaker_kind",
        "participant_code",
        "agent_code",
        "side",
        "seat_no",
        "status",
        "attempt_no",
        "finish_reason",
        "asr_raw_final_text",
        "display_text",
        "llm_draft_text",
        "first_interim_latency_ms",
        "final_latency_ms",
        "audio_duration_ms",
        "audio_truncated",
        "playback_started_at",
        "started_at",
        "ended_at",
        "finalized_at",
    ],
    "participant_annotations.csv": [
        "scheduled_match_id",
        "participant_code",
        "task_status",
        "due_at",
        "late",
        "submitted_at",
        "opportunity_id",
        "subject_kind",
        "speech_id",
        "position",
        "stage1_locked_at",
        "revealed_at",
        "stage",
        "question_key",
        "answer",
        "response_duration_ms",
        "audio_play_count",
        "client_version",
        "saved_at",
    ],
    "match_questionnaires.csv": [
        "scheduled_match_id",
        "participant_code",
        "questionnaire_version",
        "q1",
        "q2",
        "q3",
        "q4",
        "q5",
        "q6",
        "submitted_at",
    ],
    "expert_annotations.csv": [
        "expert_code",
        "opportunity_id",
        "payload_sha256",
        "q1",
        "q2",
        "q3",
        "client_version",
        "saved_at",
        "submitted_at",
    ],
}


async def _rows(
    session: AsyncSession, statement: str, *, batch_id: UUID, cutoff_at: datetime
) -> list[dict[str, Any]]:
    result = await session.execute(text(statement), {"batch_id": batch_id, "cutoff_at": cutoff_at})
    return [dict(row) for row in result.mappings()]


async def _build_batch_files(
    session: AsyncSession, *, batch_id: UUID, cutoff_at: datetime
) -> tuple[dict[str, bytes], dict[str, Any]]:
    batch = (
        (
            await session.execute(
                text(
                    "SELECT id, code, title, status, schedule_version FROM experiment_batches "
                    "WHERE id=:batch_id"
                ),
                {"batch_id": batch_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if batch is None:
        raise ExperimentTaskError("experiment_batch_not_found")

    schedule = await _rows(
        session,
        """
        SELECT sm.id AS scheduled_match_id, sm.round_no, sm.match_no, sm.kind,
               sm.status, sm.schedule_version, sm.topic_id,
               t.title AS topic_title, t.affirmative_text AS affirmative_stance,
               t.negative_text AS negative_stance, t.source_text, t.cedar_id,
               affirmative.team_code AS affirmative_team,
               negative.team_code AS negative_team,
               sm.scheduled_at, ema.id AS effective_attempt_id, ema.attempt_no,
               ema.status AS attempt_status, ema.started_at, ema.ended_at, ema.public_at
        FROM scheduled_matches sm
        JOIN topics t ON t.id=sm.topic_id
        JOIN experiment_teams affirmative ON affirmative.id=sm.affirmative_team_id
        JOIN experiment_teams negative ON negative.id=sm.negative_team_id
        LEFT JOIN experiment_match_attempts ema ON ema.id=sm.effective_attempt_id
        WHERE sm.batch_id=:batch_id AND sm.created_at <= :cutoff_at
        ORDER BY sm.round_no, sm.match_no
        """,
        batch_id=batch_id,
        cutoff_at=cutoff_at,
    )
    opportunities = await _rows(
        session,
        """
        SELECT sm.id AS scheduled_match_id, ema.attempt_no, fdo.id AS opportunity_id,
               fdo.sequence_no, fdo.side, fdo.trigger_kind, fdo.source_speech_id,
               fdo.context_version, fdo.opportunity_generation, fdo.status,
               fdo.selection_phase, fdo.decision_fact, fdo.allocation_fact,
               fdo.execution_fact, fdo.opened_at, fdo.source_ended_at,
               fdo.selection_deadline_at, fdo.human_wait_deadline_at,
               fdo.invalidated_reason, fdo.completed_at
        FROM free_debate_opportunities fdo
        JOIN experiment_match_attempts ema ON ema.id=fdo.experiment_attempt_id
        JOIN scheduled_matches sm ON sm.id=ema.scheduled_match_id
        WHERE sm.batch_id=:batch_id AND fdo.opened_at <= :cutoff_at
        ORDER BY sm.round_no, sm.match_no, fdo.sequence_no
        """,
        batch_id=batch_id,
        cutoff_at=cutoff_at,
    )
    hand_events = await _rows(
        session,
        """
        SELECT sm.id AS scheduled_match_id, fdo.id AS opportunity_id,
               etm.participant_code, hhe.event_type, hhe.server_sequence,
               hhe.connection_epoch, hhe.accepted_at
        FROM human_hand_events hhe
        JOIN free_debate_opportunities fdo ON fdo.id=hhe.opportunity_id
        JOIN experiment_match_attempts ema ON ema.id=fdo.experiment_attempt_id
        JOIN scheduled_matches sm ON sm.id=ema.scheduled_match_id
        JOIN experiment_team_members etm
          ON etm.batch_id=sm.batch_id AND etm.user_id=hhe.user_id
        WHERE sm.batch_id=:batch_id AND hhe.accepted_at <= :cutoff_at
        ORDER BY sm.round_no, sm.match_no, fdo.sequence_no, hhe.server_sequence
        """,
        batch_id=batch_id,
        cutoff_at=cutoff_at,
    )
    decisions = await _rows(
        session,
        """
        SELECT sm.id AS scheduled_match_id, afd.opportunity_id,
               afd.decision_round_id, afd.side, afd.seat_no, afd.status,
               afd.should_speak, afd.attempt_no, afd.duration_ms, afd.error_code,
               afd.effective_status, afd.deadline_at, afd.trigger_kind,
               afd.late, afd.stale, afd.invalidated_reason,
               afd.human_hand_snapshot, afd.started_at, afd.completed_at
        FROM agent_free_debate_decisions afd
        JOIN free_debate_opportunities fdo ON fdo.id=afd.opportunity_id
        JOIN experiment_match_attempts ema ON ema.id=fdo.experiment_attempt_id
        JOIN scheduled_matches sm ON sm.id=ema.scheduled_match_id
        WHERE sm.batch_id=:batch_id AND afd.created_at <= :cutoff_at
        ORDER BY sm.round_no, sm.match_no, fdo.sequence_no, afd.created_at
        """,
        batch_id=batch_id,
        cutoff_at=cutoff_at,
    )
    allocations = await _rows(
        session,
        """
        SELECT sm.id AS scheduled_match_id, sa.opportunity_id, sa.speaker_kind,
               etm.participant_code,
               CASE WHEN sa.agent_profile_id IS NULL THEN NULL
                    ELSE team.team_code || '-AI' END AS agent_code,
               sa.reason, sa.effective, sa.allocated_at
        FROM speaker_allocations sa
        JOIN free_debate_opportunities fdo ON fdo.id=sa.opportunity_id
        JOIN experiment_match_attempts ema ON ema.id=fdo.experiment_attempt_id
        JOIN scheduled_matches sm ON sm.id=ema.scheduled_match_id
        LEFT JOIN experiment_team_members etm
          ON etm.batch_id=sm.batch_id AND etm.user_id=sa.user_id
        LEFT JOIN experiment_teams team
          ON team.batch_id=sm.batch_id AND team.agent_profile_id=sa.agent_profile_id
        WHERE sm.batch_id=:batch_id AND sa.allocated_at <= :cutoff_at
        ORDER BY sm.round_no, sm.match_no, fdo.sequence_no, sa.allocated_at
        """,
        batch_id=batch_id,
        cutoff_at=cutoff_at,
    )
    speeches = await _rows(
        session,
        """
        SELECT sm.id AS scheduled_match_id, s.id AS speech_id, s.opportunity_id,
               s.action_key, s.speaker_kind, etm.participant_code,
               CASE WHEN s.agent_profile_id IS NULL THEN NULL
                    ELSE team.team_code || '-AI' END AS agent_code,
               s.side, s.seat_no, s.status, s.attempt_no, s.finish_reason,
               s.asr_raw_final_text, s.display_text, s.llm_draft_text,
               s.first_interim_latency_ms, s.final_latency_ms,
               s.audio_duration_ms, s.audio_truncated,
               s.playback_started_at, s.started_at, s.ended_at, s.finalized_at
        FROM speeches s
        JOIN experiment_match_attempts ema ON ema.match_id=s.match_id
        JOIN scheduled_matches sm ON sm.id=ema.scheduled_match_id
        LEFT JOIN experiment_team_members etm
          ON etm.batch_id=sm.batch_id AND etm.user_id=s.user_id
        LEFT JOIN experiment_teams team
          ON team.batch_id=sm.batch_id AND team.agent_profile_id=s.agent_profile_id
        WHERE sm.batch_id=:batch_id AND s.created_at <= :cutoff_at
        ORDER BY sm.round_no, sm.match_no, s.created_at
        """,
        batch_id=batch_id,
        cutoff_at=cutoff_at,
    )
    participant_annotations = await _rows(
        session,
        """
        SELECT sm.id AS scheduled_match_id, etm.participant_code,
               pat.status AS task_status, pat.due_at, pat.late, pat.submitted_at,
               pai.opportunity_id, pai.subject_kind, pai.speech_id, pai.position,
               pai.stage1_locked_at, pai.revealed_at, paa.stage,
               paa.question_key, paa.answer, paa.response_duration_ms,
               paa.audio_play_count, paa.client_version, paa.saved_at
        FROM participant_annotation_tasks pat
        JOIN experiment_match_attempts ema ON ema.id=pat.experiment_attempt_id
        JOIN scheduled_matches sm ON sm.id=ema.scheduled_match_id
        JOIN experiment_team_members etm
          ON etm.batch_id=sm.batch_id AND etm.user_id=pat.user_id
        LEFT JOIN participant_annotation_items pai ON pai.task_id=pat.id
        LEFT JOIN participant_annotation_answers paa
          ON paa.task_id=pat.id AND paa.opportunity_id=pai.opportunity_id
             AND paa.subject_kind=pai.subject_kind
        WHERE sm.batch_id=:batch_id AND pat.created_at <= :cutoff_at
        ORDER BY sm.round_no, sm.match_no, etm.participant_code, pai.position,
                 paa.stage, paa.question_key
        """,
        batch_id=batch_id,
        cutoff_at=cutoff_at,
    )
    questionnaires = await _rows(
        session,
        """
        SELECT sm.id AS scheduled_match_id, etm.participant_code,
               pat.questionnaire_version, mq.q1, mq.q2, mq.q3, mq.q4, mq.q5,
               mq.q6, mq.submitted_at
        FROM match_questionnaires mq
        JOIN participant_annotation_tasks pat ON pat.id=mq.task_id
        JOIN experiment_match_attempts ema ON ema.id=pat.experiment_attempt_id
        JOIN scheduled_matches sm ON sm.id=ema.scheduled_match_id
        JOIN experiment_team_members etm
          ON etm.batch_id=sm.batch_id AND etm.user_id=pat.user_id
        WHERE sm.batch_id=:batch_id AND mq.submitted_at <= :cutoff_at
        ORDER BY sm.round_no, sm.match_no, etm.participant_code
        """,
        batch_id=batch_id,
        cutoff_at=cutoff_at,
    )
    expert_annotations = await _rows(
        session,
        """
        SELECT ee.expert_code, eaa.opportunity_id, eaa.payload_sha256,
               eaa.q1, eaa.q2, eaa.q3, eaa.client_version,
               eaa.saved_at, eaa.submitted_at
        FROM expert_annotation_answers eaa
        JOIN expert_annotation_tasks eat ON eat.id=eaa.task_id
        JOIN experiment_experts ee
          ON ee.batch_id=eat.batch_id AND ee.user_id=eat.expert_user_id
        WHERE eat.batch_id=:batch_id AND eaa.saved_at <= :cutoff_at
        ORDER BY ee.expert_code, eaa.saved_at, eaa.opportunity_id
        """,
        batch_id=batch_id,
        cutoff_at=cutoff_at,
    )

    datasets: dict[str, list[dict[str, Any]]] = {
        "schedule.csv": schedule,
        "opportunities.csv": opportunities,
        "human_hand_events.csv": hand_events,
        "speaker_allocations.csv": allocations,
        "speeches.csv": speeches,
        "participant_annotations.csv": participant_annotations,
        "match_questionnaires.csv": questionnaires,
        "expert_annotations.csv": expert_annotations,
    }
    files = {name: _csv_bytes(rows, _EXPORT_COLUMNS[name]) for name, rows in datasets.items()}
    files["agent_decisions.jsonl"] = (
        "\n".join(_json(row) for row in decisions) + ("\n" if decisions else "")
    ).encode()
    row_counts = {name: len(rows) for name, rows in datasets.items()}
    row_counts["agent_decisions.jsonl"] = len(decisions)
    manifest: dict[str, Any] = {
        "export_schema_version": "2.0",
        "data_dictionary_version": "paper-experiment-2026-08-21",
        "batch": {
            "id": str(batch["id"]),
            "code": batch["code"],
            "title": batch["title"],
            "status": batch["status"],
            "schedule_version": batch["schedule_version"],
        },
        "generated_at": datetime.now(UTC).isoformat(),
        "cutoff_at": cutoff_at.isoformat(),
        "identity_mapping_included": False,
        "files": {},
    }
    for name, content in files.items():
        manifest["files"][name] = {
            "row_count": row_counts[name],
            "byte_count": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "schema_version": "2.0",
        }
    return files, manifest


async def process_one_experiment_export(
    session_factory: async_sessionmaker[AsyncSession], *, storage_root: Path
) -> bool:
    async with session_factory() as session:
        claim = await claim_next(session, task_type="EXPERIMENT_BATCH_EXPORT", lease_seconds=900)
    if claim is None:
        return False
    temporary = storage_root / f".{claim.task_id}.zip.part"
    output = storage_root / f"experiment-{claim.task_id}.zip"
    try:
        batch_id = UUID(str(claim.payload.get("batch_id", "")))
        cutoff_at = datetime.fromisoformat(str(claim.payload.get("cutoff_at", "")))
        if cutoff_at.tzinfo is None:
            cutoff_at = cutoff_at.replace(tzinfo=UTC)
        storage_root.mkdir(parents=True, exist_ok=True)
        async with session_factory() as session:
            files, manifest = await _build_batch_files(
                session, batch_id=batch_id, cutoff_at=cutoff_at
            )
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
            archive.writestr("manifest.json", (_json(manifest) + "\n").encode())
        temporary.replace(output)
        async with session_factory() as session:
            await session.execute(
                text(
                    "UPDATE background_tasks SET payload = payload || "
                    "jsonb_build_object('artifact_name', :artifact_name, "
                    "'artifact_sha256', :sha256) WHERE id=:id"
                ),
                {
                    "id": claim.task_id,
                    "artifact_name": output.name,
                    "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                },
            )
            await session.commit()
        async with session_factory() as session:
            await complete(session, task_id=claim.task_id)
        return True
    except Exception as error:
        temporary.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        code = getattr(error, "code", "experiment_export_failed")
        async with session_factory() as session:
            await fail(session, task_id=claim.task_id, error_code=str(code))
        return True


async def process_one_experiment_retention(
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    async with session_factory() as session:
        claim = await claim_next(session, task_type="EXPERIMENT_RETENTION")
    if claim is None:
        return False
    try:
        if claim.payload.get("dry_run") is not True:
            raise ExperimentTaskError("experiment_retention_dry_run_required")
        batch_id = UUID(str(claim.payload.get("batch_id", "")))
        async with session_factory() as session:
            report = (
                (
                    await session.execute(
                        text(
                            """
                            SELECT
                              count(DISTINCT ema.id) AS attempts,
                              count(DISTINCT mf.id) FILTER (WHERE mf.expires_at <= now())
                                AS expired_files,
                              count(DISTINCT pat.id) AS participant_tasks,
                              count(DISTINCT eat.id) AS expert_tasks
                            FROM scheduled_matches sm
                            LEFT JOIN experiment_match_attempts ema
                              ON ema.scheduled_match_id=sm.id
                            LEFT JOIN match_files mf ON mf.match_id=ema.match_id
                            LEFT JOIN participant_annotation_tasks pat
                              ON pat.experiment_attempt_id=ema.id
                            LEFT JOIN expert_annotation_tasks eat ON eat.batch_id=sm.batch_id
                            WHERE sm.batch_id=:batch_id
                            """
                        ),
                        {"batch_id": batch_id},
                    )
                )
                .mappings()
                .one()
            )
            await session.execute(
                text(
                    "UPDATE background_tasks SET payload = payload || "
                    "jsonb_build_object('report', CAST(:report AS jsonb)) WHERE id=:id"
                ),
                {"id": claim.task_id, "report": _json(dict(report))},
            )
            await session.commit()
        async with session_factory() as session:
            await complete(session, task_id=claim.task_id)
        return True
    except Exception as error:
        code = getattr(error, "code", "experiment_retention_failed")
        async with session_factory() as session:
            await fail(session, task_id=claim.task_id, error_code=str(code))
        return True


__all__ = [
    "process_one_experiment_export",
    "process_one_experiment_postmatch",
    "process_one_experiment_retention",
]

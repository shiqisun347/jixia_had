"""Account-scoped AI debate surveys and rule-controlled post-match tasks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .audit.service import AuditService
from .auth.errors import APIError
from .models import (
    Match,
    MatchParticipant,
    PersonalAiSurveyResponse,
    PersonalAiSurveyResponseRevision,
    PersonalAiSurveyVersion,
    PostmatchSurveyTask,
    Room,
    Speech,
)
from .survey_definitions import (
    AI_ACCEPTABLE_ACTIONS,
    AI_ACTION_REALIZATION,
    HUMAN_GOALS,
    HUMAN_PRIMARY_REASONS,
    PAPER_LIKERT_OPTIONS,
    PAPER_OVERALL_QUESTIONS,
    POSTMATCH_QUESTIONNAIRE,
    POSTMATCH_QUESTIONNAIRE_VERSION,
)

PERSONAL_DEFAULT_QUESTIONS: list[dict[str, Any]] = [
    {
        "key": f"q{index + 1}",
        "type": "scale",
        "required": True,
        "scale_max": 5,
        "text": question,
        "options": [
            {"value": score + 1, "text": option}
            for score, option in enumerate(PAPER_LIKERT_OPTIONS)
        ],
    }
    for index, question in enumerate(PAPER_OVERALL_QUESTIONS)
]

POSTMATCH_WORKFLOW_KEY = "_workflow"
POSTMATCH_WORKFLOW_VERSION = "free-debate-chat-v1"
POSTMATCH_SCOPE_FREE_DEBATE = "FREE_DEBATE"
POSTMATCH_SCOPE_LEGACY = "LEGACY_ALL"


def _postmatch_questionnaire(task: PostmatchSurveyTask) -> dict[str, Any] | None:
    """Expose the current two-stage questions for unfinished legacy tasks too.

    Older pending tasks were created before the questionnaire payload was
    returned by the API. They have no answers to preserve, so showing the
    current behavior-set questions makes them actionable without rewriting
    submitted history.
    """
    if task.questionnaire_version == POSTMATCH_QUESTIONNAIRE_VERSION or task.status != "SUBMITTED":
        return POSTMATCH_QUESTIONNAIRE
    return None


def _validate_answers(
    questions: list[dict[str, Any]],
    answers: dict[str, Any],
    *,
    require_complete: bool = True,
) -> None:
    allowed = {str(item.get("key")) for item in questions}
    if set(answers) - allowed:
        raise APIError("survey_answer_invalid")
    for question in questions:
        key = str(question.get("key"))
        if require_complete and question.get("required") and key not in answers:
            raise APIError("survey_answer_incomplete")
        if key not in answers:
            continue
        value = answers[key]
        if question.get("type") == "scale" and (
            not isinstance(value, int) or not 1 <= value <= int(question.get("scale_max", 7))
        ):
            raise APIError("survey_answer_invalid")
        if question.get("type") == "textarea" and (not isinstance(value, str) or len(value) > 4000):
            raise APIError("survey_answer_invalid")


async def ensure_personal_version(
    session: AsyncSession, *, actor_user_id: UUID
) -> PersonalAiSurveyVersion:
    await session.execute(select(func.pg_advisory_xact_lock(2_220_001)))
    version = await session.scalar(
        select(PersonalAiSurveyVersion)
        .where(PersonalAiSurveyVersion.status == "PUBLISHED")
        .order_by(PersonalAiSurveyVersion.version.desc())
        .limit(1)
        .with_for_update()
    )
    if version is not None and version.questions == PERSONAL_DEFAULT_QUESTIONS:
        return version
    latest_number = await session.scalar(
        select(PersonalAiSurveyVersion.version)
        .order_by(PersonalAiSurveyVersion.version.desc())
        .limit(1)
    )
    if version is not None:
        await session.execute(
            update(PersonalAiSurveyVersion)
            .where(PersonalAiSurveyVersion.status == "PUBLISHED")
            .values(status="ARCHIVED")
        )
    version = PersonalAiSurveyVersion(
        version=int(latest_number or 0) + 1,
        status="PUBLISHED",
        title="AI 辩论感受",
        description="请根据刚才这场比赛的整体感受，选择最符合你想法的答案。",
        questions=PERSONAL_DEFAULT_QUESTIONS,
        created_by=actor_user_id,
        published_at=datetime.now(UTC),
    )
    session.add(version)
    await session.flush()
    return version


async def personal_response(session: AsyncSession, *, user_id: UUID) -> dict[str, Any]:
    async with session.begin():
        version = await ensure_personal_version(session, actor_user_id=user_id)
        response = await session.scalar(
            select(PersonalAiSurveyResponse)
            .where(
                PersonalAiSurveyResponse.user_id == user_id,
                PersonalAiSurveyResponse.version_id == version.id,
            )
            .with_for_update()
        )
        if response is None:
            response = PersonalAiSurveyResponse(user_id=user_id, version_id=version.id, answers={})
            session.add(response)
            await session.flush()
        return {
            "id": str(response.id),
            "version": version.version,
            "title": version.title,
            "description": version.description,
            "questions": version.questions,
            "status": response.status,
            "answers": response.answers,
            "updated_at": response.updated_at,
            "submitted_at": response.submitted_at,
        }


async def save_personal_response(
    session: AsyncSession, *, user_id: UUID, answers: dict[str, Any], submit: bool
) -> dict[str, Any]:
    async with session.begin():
        version = await ensure_personal_version(session, actor_user_id=user_id)
        _validate_answers(version.questions, answers, require_complete=submit)
        response = await session.scalar(
            select(PersonalAiSurveyResponse)
            .where(
                PersonalAiSurveyResponse.user_id == user_id,
                PersonalAiSurveyResponse.version_id == version.id,
            )
            .with_for_update()
        )
        if response is None:
            response = PersonalAiSurveyResponse(
                user_id=user_id, version_id=version.id, answers=answers
            )
            session.add(response)
        elif response.status == "SUBMITTED":
            session.add(
                PersonalAiSurveyResponseRevision(
                    response_id=response.id,
                    user_id=response.user_id,
                    version_id=response.version_id,
                    answers=response.answers,
                    submitted_at=response.submitted_at or datetime.now(UTC),
                )
            )
            response.status = "DRAFT"
            response.submitted_at = None
            response.answers = answers
        else:
            response.answers = answers
        if submit:
            if response.status == "SUBMITTED":
                raise APIError("survey_locked")
            response.status = "SUBMITTED"
            response.submitted_at = datetime.now(UTC)
        await session.flush()
        AuditService().record(
            session,
            actor_user_id=user_id,
            action=(
                "participant.survey.personal_submitted"
                if submit
                else "participant.survey.personal_draft_saved"
            ),
            target_type="personal_ai_survey_response",
            target_id=str(response.id),
            details={"version": version.version, "status": response.status},
        )
    return await personal_response(session, user_id=user_id)


def _speech_answer(answers: dict[str, Any], speech_id: str) -> dict[str, object]:
    speeches = answers.get("speeches")
    if not isinstance(speeches, dict):
        return {}
    answer = cast(dict[str, object], speeches).get(speech_id)
    return cast(dict[str, object], answer) if isinstance(answer, dict) else {}


def _speech_answer_complete(answers: dict[str, Any], speech_id: str) -> bool:
    answer = _speech_answer(answers, speech_id)
    return answer.get("q1") is not None and answer.get("q2") is not None


def _workflow(answers: dict[str, Any]) -> dict[str, object] | None:
    value = answers.get(POSTMATCH_WORKFLOW_KEY)
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def _annotation_scope(answers: dict[str, Any], *, status: str) -> str:
    workflow = _workflow(answers)
    scope = workflow.get("scope") if workflow is not None else None
    if scope in {POSTMATCH_SCOPE_FREE_DEBATE, POSTMATCH_SCOPE_LEGACY}:
        return cast(str, scope)
    return POSTMATCH_SCOPE_LEGACY if status == "SUBMITTED" else POSTMATCH_SCOPE_FREE_DEBATE


def _workflow_revision(answers: dict[str, Any]) -> int:
    workflow = _workflow(answers)
    value = workflow.get("revision") if workflow is not None else None
    return value if isinstance(value, int) and value >= 0 else 0


def _confirmed_speech_ids(answers: dict[str, Any], *, ordered_target_ids: list[str]) -> set[str]:
    workflow = _workflow(answers)
    if workflow is not None and workflow.get("version") == POSTMATCH_WORKFLOW_VERSION:
        value = workflow.get("confirmed_speech_ids")
        if isinstance(value, list):
            allowed = set(ordered_target_ids)
            values = cast(list[object], value)
            return {item for item in values if isinstance(item, str) and item in allowed}
    return {
        speech_id for speech_id in ordered_target_ids if _speech_answer_complete(answers, speech_id)
    }


def _answers_with_workflow(
    answers: dict[str, Any],
    *,
    scope: str,
    confirmed_speech_ids: set[str],
    revision: int | None = None,
) -> dict[str, Any]:
    return {
        **answers,
        POSTMATCH_WORKFLOW_KEY: {
            "version": POSTMATCH_WORKFLOW_VERSION,
            "scope": scope,
            "revision": _workflow_revision(answers) if revision is None else revision,
            "confirmed_speech_ids": sorted(confirmed_speech_ids),
        },
    }


def _participant_answers_projection(
    answers: dict[str, Any],
    *,
    scope: str,
    visible_target_ids: set[str],
) -> dict[str, Any]:
    """Hide retained out-of-scope and not-yet-visible answers from participants."""
    if scope != POSTMATCH_SCOPE_FREE_DEBATE:
        return answers
    speeches = answers.get("speeches")
    speech_map = cast(dict[str, object], speeches) if isinstance(speeches, dict) else {}
    return {
        "speeches": {
            speech_id: value
            for speech_id, value in speech_map.items()
            if speech_id in visible_target_ids
        }
    }


def _speech_subject_kind(speech: Speech, *, user_id: UUID, participant_side: str) -> str:
    if speech.user_id == user_id:
        return "HUMAN_SELF"
    same_side = speech.side == participant_side
    if speech.speaker_kind == "AGENT":
        return "TEAM_AI" if same_side else "OPPONENT_AI"
    return "TEAM_HUMAN" if same_side else "OPPONENT_HUMAN"


def _review_projection(
    *,
    answers: dict[str, Any],
    status: str,
    speech_ids: list[str],
    subject_kinds: list[str],
    annotatable: list[bool] | None = None,
) -> list[tuple[bool, str]]:
    target_flags = annotatable or [
        subject_kind in {"HUMAN_SELF", "TEAM_AI"} for subject_kind in subject_kinds
    ]
    target_ids = [
        speech_id
        for speech_id, is_target in zip(speech_ids, target_flags, strict=True)
        if is_target
    ]
    confirmed = _confirmed_speech_ids(answers, ordered_target_ids=target_ids)
    boundary = next(
        (
            index
            for index, is_target in enumerate(target_flags)
            if is_target and speech_ids[index] not in confirmed
        ),
        len(speech_ids),
    )
    result: list[tuple[bool, str]] = []
    for index, (speech_id, _subject_kind) in enumerate(zip(speech_ids, subject_kinds, strict=True)):
        answer = _speech_answer(answers, speech_id)
        completed = speech_id in confirmed
        is_target = index == boundary and target_flags[index]
        visible = (
            status == "SUBMITTED"
            or index < boundary
            or (is_target and answer.get("q1") is not None)
        )
        if completed:
            review_state = "COMPLETE"
        elif is_target:
            review_state = "CURRENT_POST" if answer.get("q1") is not None else "CURRENT_PRE"
        elif index < boundary:
            review_state = "CONTEXT"
        else:
            review_state = "FUTURE"
        result.append((visible, review_state))
    return result


def _stage_meta(action_key: str, snapshot: dict[str, Any] | None) -> tuple[int | None, str, str]:
    try:
        stage_position = int(action_key.split(":", 1)[0])
    except (TypeError, ValueError):
        return None, "辩论记录", "UNKNOWN"
    for stage in (snapshot or {}).get("stages", []):
        if isinstance(stage, dict):
            typed_stage = cast(dict[str, Any], stage)
            if typed_stage.get("position") != stage_position:
                continue
            return (
                stage_position,
                str(typed_stage.get("name") or "辩论阶段"),
                str(typed_stage.get("stage_kind") or "UNKNOWN"),
            )
    return stage_position, "辩论阶段", "UNKNOWN"


async def list_postmatch_tasks(session: AsyncSession, *, user_id: UUID) -> list[dict[str, Any]]:
    rows = list(
        (
            await session.scalars(
                select(PostmatchSurveyTask)
                .join(Match, Match.id == PostmatchSurveyTask.match_id)
                .where(PostmatchSurveyTask.user_id == user_id)
                .order_by(PostmatchSurveyTask.created_at.desc())
            )
        ).all()
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        match = await session.get(Match, row.match_id)
        room = await session.get(Room, match.room_id) if match is not None else None
        participant = await session.scalar(
            select(MatchParticipant).where(
                MatchParticipant.match_id == row.match_id,
                MatchParticipant.user_id == user_id,
            )
        )
        items: list[dict[str, Any]] = []
        target_total = 0
        confirmed_total = 0
        scope = _annotation_scope(row.answers, status=row.status)
        if participant is not None:
            speeches = list(
                (
                    await session.scalars(
                        select(Speech)
                        .where(
                            Speech.match_id == row.match_id,
                            Speech.status == "FINALIZED",
                        )
                        .order_by(Speech.started_at, Speech.created_at, Speech.id)
                    )
                ).all()
            )
            match_participants = list(
                (
                    await session.scalars(
                        select(MatchParticipant).where(MatchParticipant.match_id == row.match_id)
                    )
                ).all()
            )
            human_names = {
                value.user_id: value.display_name
                for value in match_participants
                if value.user_id is not None
            }
            agent_names = {
                value.agent_profile_id: value.display_name
                for value in match_participants
                if value.agent_profile_id is not None
            }
            subject_kinds = [
                _speech_subject_kind(speech, user_id=user_id, participant_side=participant.side)
                for speech in speeches
            ]
            stage_metas = [
                _stage_meta(
                    speech.action_key,
                    room.rule_snapshot if room is not None else None,
                )
                for speech in speeches
            ]
            annotatable = [
                subject_kind in {"HUMAN_SELF", "TEAM_AI"}
                and (
                    scope == POSTMATCH_SCOPE_LEGACY or stage_meta[2] == POSTMATCH_SCOPE_FREE_DEBATE
                )
                for subject_kind, stage_meta in zip(subject_kinds, stage_metas, strict=True)
            ]
            projections = _review_projection(
                answers=row.answers,
                status=row.status,
                speech_ids=[str(speech.id) for speech in speeches],
                subject_kinds=subject_kinds,
                annotatable=annotatable,
            )
            target_ids = [
                str(speech.id)
                for speech, is_annotatable in zip(speeches, annotatable, strict=True)
                if is_annotatable
            ]
            target_total = len(target_ids)
            confirmed_total = len(_confirmed_speech_ids(row.answers, ordered_target_ids=target_ids))
            for index, (speech, subject_kind, stage_meta, is_annotatable, projection) in enumerate(
                zip(
                    speeches,
                    subject_kinds,
                    stage_metas,
                    annotatable,
                    projections,
                    strict=True,
                )
            ):
                speech_id = str(speech.id)
                visible, review_state = projection
                if row.status != "SUBMITTED" and review_state == "FUTURE":
                    continue
                stage_position, stage_name, stage_kind = stage_meta
                agent_profile_id = speech.agent_profile_id
                speaker_user_id = speech.user_id
                items.append(
                    {
                        "speech_id": speech_id,
                        "subject_kind": subject_kind,
                        "annotatable": is_annotatable,
                        "review_state": review_state,
                        "side": speech.side,
                        "seat_no": speech.seat_no,
                        "sequence": index + 1,
                        "action_key": speech.action_key,
                        "stage_position": stage_position,
                        "stage_name": stage_name,
                        "stage_kind": stage_kind,
                        "speaker_name": agent_names.get(agent_profile_id, "AI 辩手")
                        if agent_profile_id is not None
                        else (
                            human_names.get(speaker_user_id, "真人辩手")
                            if speaker_user_id is not None
                            else "真人辩手"
                        ),
                        "text": speech.display_text if visible else None,
                        "started_at": speech.started_at,
                        "ended_at": speech.ended_at,
                        "finalized_at": speech.finalized_at,
                        "duration_ms": speech.audio_duration_ms,
                    }
                )
        visible_target_ids = {item["speech_id"] for item in items if item["annotatable"] is True}
        result.append(
            {
                "id": str(row.id),
                "match_id": str(row.match_id),
                "questionnaire_version": row.questionnaire_version,
                "status": row.status,
                "answers": _participant_answers_projection(
                    row.answers,
                    scope=scope,
                    visible_target_ids=visible_target_ids,
                ),
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "submitted_at": row.submitted_at,
                "match_status": match.status if match is not None else None,
                "match_ended_at": match.ended_at if match is not None else None,
                "room_title": room.title if room is not None else None,
                "room_label": room.label if room is not None else None,
                "topic": (
                    str(room.topic_snapshot.get("title") or "") if room is not None else None
                ),
                "target_total": target_total,
                "confirmed_total": confirmed_total,
                "workflow_revision": _workflow_revision(row.answers),
                "items": items,
                "questionnaire": _postmatch_questionnaire(row),
            }
        )
    return result


async def get_postmatch_task(
    session: AsyncSession, *, task_id: UUID, user_id: UUID
) -> dict[str, Any]:
    """Return one task using the same participant-scoped projection as the list endpoint."""
    task = await session.scalar(
        select(PostmatchSurveyTask).where(
            PostmatchSurveyTask.id == task_id,
            PostmatchSurveyTask.user_id == user_id,
        )
    )
    if task is None:
        raise APIError("survey_not_found")
    tasks = await list_postmatch_tasks(session, user_id=user_id)
    for item in tasks:
        if item["id"] == str(task_id):
            return item
    raise APIError("survey_not_found")


async def postmatch_task_status(
    session: AsyncSession, *, match_id: UUID, user_id: UUID
) -> dict[str, Any]:
    task = await session.scalar(
        select(PostmatchSurveyTask).where(
            PostmatchSurveyTask.match_id == match_id,
            PostmatchSurveyTask.user_id == user_id,
        )
    )
    return {
        "match_id": str(match_id),
        "exists": task is not None,
        "task_id": str(task.id) if task is not None else None,
        "status": task.status if task is not None else None,
        "pending": task is not None and task.status != "SUBMITTED",
        "href": f"/me/postmatch-surveys/{task.id}" if task is not None else "/me/postmatch-surveys",
    }


def _validate_strict_speech_answer(
    *, subject_kind: str, value: object, submit: bool = True
) -> None:
    if not isinstance(value, dict):
        raise APIError("survey_answer_invalid")
    answer = cast(dict[str, object], value)
    expected = {"q1", "q2"}
    if set(answer) - expected or (submit and set(answer) != expected):
        raise APIError("survey_answer_incomplete" if submit else "survey_answer_invalid")
    q1 = answer.get("q1")
    q2 = answer.get("q2")
    if subject_kind == "HUMAN_SELF":
        if submit and q1 not in HUMAN_PRIMARY_REASONS:
            raise APIError("survey_answer_incomplete")
        if q1 is not None and q1 not in HUMAN_PRIMARY_REASONS:
            raise APIError("survey_answer_invalid")
        if q2 is not None:
            if not isinstance(q2, list) or not q2:
                raise APIError("survey_answer_invalid")
            goals = cast(list[object], q2)
            if any(not isinstance(item, str) or item not in HUMAN_GOALS for item in goals):
                raise APIError("survey_answer_invalid")
            if len(set(cast(list[str], goals))) != len(goals):
                raise APIError("survey_answer_invalid")
        elif submit:
            raise APIError("survey_answer_incomplete")
        return
    if q1 is not None and (
        not isinstance(q1, list)
        or not 1 <= len(q1) <= 3
        or len(set(q1)) != len(q1)
        or any(not isinstance(item, str) or item not in AI_ACCEPTABLE_ACTIONS for item in q1)
    ):
        raise APIError("survey_answer_invalid")
    if submit and (not isinstance(q1, list) or not q1):
        raise APIError("survey_answer_incomplete")
    if q2 is not None and q2 not in AI_ACTION_REALIZATION:
        raise APIError("survey_answer_invalid")
    if submit and q2 not in AI_ACTION_REALIZATION:
        raise APIError("survey_answer_incomplete")
    if q2 is not None and (not isinstance(q1, list) or not q1):
        raise APIError("survey_answer_invalid")


def _validate_strict_overall(value: object, *, submit: bool) -> None:
    if not isinstance(value, dict):
        raise APIError("survey_answer_invalid")
    overall = cast(dict[str, object], value)
    expected = {"q1", "q2", "q3", "q4", "q5"}
    if set(overall) - expected or (submit and set(overall) != expected):
        raise APIError("survey_answer_incomplete" if submit else "survey_answer_invalid")
    if any(not isinstance(score, int) or not 1 <= score <= 5 for score in overall.values()):
        raise APIError("survey_answer_invalid")


def _validate_editable_progression(
    *,
    previous_answers: dict[str, Any],
    next_speeches: dict[str, object],
    ordered_speech_ids: list[str],
) -> None:
    """Keep first-pass review sequential while allowing completed segments to be edited."""
    previous = previous_answers.get("speeches")
    previous_map = cast(dict[str, object], previous) if isinstance(previous, dict) else {}
    target_id = next(
        (
            speech_id
            for speech_id in ordered_speech_ids
            if not _speech_answer_complete(previous_answers, speech_id)
        ),
        None,
    )
    target_seen = False
    for speech_id in ordered_speech_ids:
        prior = previous_map.get(speech_id)
        current = next_speeches.get(speech_id)
        if isinstance(prior, dict) and _speech_answer_complete(previous_answers, speech_id):
            continue
        if speech_id == target_id:
            target_seen = True
            typed_current = cast(dict[str, object], current) if isinstance(current, dict) else {}
            if typed_current.get("q1") is None and typed_current.get("q2") is not None:
                raise APIError("survey_answer_invalid")
            continue
        if target_seen and current != prior:
            raise APIError("survey_answer_invalid")


def _validate_strict_prespeech_answers_immutable(  # pyright: ignore[reportUnusedFunction]
    *,
    previous_answers: dict[str, Any],
    next_speeches: dict[str, object],
    subject_kinds: dict[str, str],
) -> None:
    """Legacy helper retained for historical callers and tests."""
    previous = previous_answers.get("speeches")
    previous_map = cast(dict[str, object], previous) if isinstance(previous, dict) else {}
    for speech_id, subject_kind in subject_kinds.items():
        if subject_kind not in {"HUMAN_SELF", "TEAM_AI"}:
            continue
        prior = previous_map.get(speech_id)
        if not isinstance(prior, dict) or "q1" not in prior:
            continue
        current = next_speeches.get(speech_id)
        if not isinstance(current, dict) or cast(dict[str, object], current).get("q1") != cast(
            dict[str, object], prior
        ).get("q1"):
            raise APIError("survey_locked")


def _validate_strict_progression(  # pyright: ignore[reportUnusedFunction]
    *,
    previous_answers: dict[str, Any],
    next_speeches: dict[str, object],
    ordered_speech_ids: list[str],
) -> None:
    """Legacy strict progression helper retained for compatibility."""
    first_target = next(
        (
            speech_id
            for speech_id in ordered_speech_ids
            if not _speech_answer_complete(previous_answers, speech_id)
        ),
        None,
    )
    if first_target is not None:
        candidate = next_speeches.get(first_target)
        if (
            not previous_answers.get("speeches")
            and isinstance(candidate, dict)
            and cast(dict[str, object], candidate).get("q2") is not None
        ):
            raise APIError("survey_answer_invalid")
    _validate_editable_progression(
        previous_answers=previous_answers,
        next_speeches=next_speeches,
        ordered_speech_ids=ordered_speech_ids,
    )


async def _postmatch_target_rows(
    session: AsyncSession,
    *,
    task: PostmatchSurveyTask,
    user_id: UUID,
    scope: str,
) -> tuple[list[Speech], dict[str, str]]:
    participant = await session.scalar(
        select(MatchParticipant).where(
            MatchParticipant.match_id == task.match_id,
            MatchParticipant.user_id == user_id,
        )
    )
    if participant is None:
        raise APIError("survey_not_found")
    match = await session.get(Match, task.match_id)
    room = await session.get(Room, match.room_id) if match is not None else None
    speech_rows = list(
        (
            await session.scalars(
                select(Speech)
                .where(
                    Speech.match_id == task.match_id,
                    Speech.side == participant.side,
                    Speech.status == "FINALIZED",
                    (Speech.user_id == user_id) | (Speech.speaker_kind == "AGENT"),
                )
                .order_by(Speech.started_at, Speech.created_at, Speech.id)
            )
        ).all()
    )
    if scope == POSTMATCH_SCOPE_FREE_DEBATE:
        speech_rows = [
            speech
            for speech in speech_rows
            if _stage_meta(
                speech.action_key,
                room.rule_snapshot if room is not None else None,
            )[2]
            == POSTMATCH_SCOPE_FREE_DEBATE
        ]
    allowed = {
        str(speech.id): "HUMAN_SELF" if speech.user_id == user_id else "TEAM_AI"
        for speech in speech_rows
    }
    return speech_rows, allowed


async def save_postmatch_speech_answer(
    session: AsyncSession,
    *,
    task_id: UUID,
    user_id: UUID,
    speech_id: UUID,
    answer: dict[str, Any],
    confirm: bool,
    expected_revision: int,
) -> dict[str, Any]:
    async with session.begin():
        task = await session.scalar(
            select(PostmatchSurveyTask)
            .where(
                PostmatchSurveyTask.id == task_id,
                PostmatchSurveyTask.user_id == user_id,
            )
            .with_for_update()
        )
        if task is None:
            raise APIError("survey_not_found")
        previous_status = task.status
        previous_answers = task.answers
        current_revision = _workflow_revision(task.answers)
        scope = _annotation_scope(task.answers, status=task.status)
        speech_rows, allowed = await _postmatch_target_rows(
            session,
            task=task,
            user_id=user_id,
            scope=scope,
        )
        ordered_ids = [str(speech.id) for speech in speech_rows]
        selected_id = str(speech_id)
        if selected_id not in allowed:
            raise APIError("survey_answer_invalid")
        confirmed = _confirmed_speech_ids(
            task.answers,
            ordered_target_ids=ordered_ids,
        )
        current_id = next(
            (candidate for candidate in ordered_ids if candidate not in confirmed),
            None,
        )
        if selected_id != current_id and selected_id not in confirmed:
            raise APIError("survey_answer_invalid")

        already_confirmed = selected_id in confirmed
        if expected_revision != current_revision:
            if previous_answer := _speech_answer(task.answers, selected_id):
                if previous_answer == answer and (not confirm or already_confirmed):
                    return await get_postmatch_task(
                        session,
                        task_id=task_id,
                        user_id=user_id,
                    )
            raise APIError("survey_conflict")

        previous_answer = _speech_answer(task.answers, selected_id)
        if (
            selected_id == current_id
            and previous_answer.get("q1") is None
            and answer.get("q2") is not None
        ):
            raise APIError("survey_answer_invalid")
        _validate_strict_speech_answer(
            subject_kind=allowed[selected_id],
            value=answer,
            submit=confirm,
        )
        if confirm:
            if selected_id != current_id and not already_confirmed:
                raise APIError("survey_answer_invalid")
            confirmed.add(selected_id)

        previous_speeches = task.answers.get("speeches")
        speeches = (
            dict(cast(dict[str, object], previous_speeches))
            if isinstance(previous_speeches, dict)
            else {}
        )
        speeches[selected_id] = answer
        task.answers = _answers_with_workflow(
            {**task.answers, "speeches": speeches},
            scope=scope,
            confirmed_speech_ids=confirmed,
            revision=current_revision + 1,
        )
        if task.status == "SUBMITTED":
            task.status = "IN_PROGRESS"
            task.submitted_at = None
        elif task.status == "PENDING":
            task.status = "IN_PROGRESS"
        await session.flush()
        AuditService().record(
            session,
            actor_user_id=user_id,
            action="participant.survey.postmatch_speech_confirmed"
            if confirm
            else "participant.survey.postmatch_draft_saved",
            target_type="postmatch_survey_task",
            target_id=str(task.id),
            details={
                "questionnaire_version": task.questionnaire_version,
                "speech_id": selected_id,
                "confirmed": confirm,
                "reopened_from_submitted": previous_status == "SUBMITTED",
                "previous_answers": previous_answers if previous_status == "SUBMITTED" else None,
            },
        )
    return await get_postmatch_task(session, task_id=task_id, user_id=user_id)


async def submit_postmatch_task(
    session: AsyncSession,
    *,
    task_id: UUID,
    user_id: UUID,
) -> dict[str, Any]:
    async with session.begin():
        task = await session.scalar(
            select(PostmatchSurveyTask)
            .where(
                PostmatchSurveyTask.id == task_id,
                PostmatchSurveyTask.user_id == user_id,
            )
            .with_for_update()
        )
        if task is None:
            raise APIError("survey_not_found")
        if task.status == "SUBMITTED":
            return await get_postmatch_task(
                session,
                task_id=task_id,
                user_id=user_id,
            )
        scope = _annotation_scope(task.answers, status=task.status)
        speech_rows, allowed = await _postmatch_target_rows(
            session,
            task=task,
            user_id=user_id,
            scope=scope,
        )
        ordered_ids = [str(speech.id) for speech in speech_rows]
        confirmed = _confirmed_speech_ids(
            task.answers,
            ordered_target_ids=ordered_ids,
        )
        if confirmed != set(ordered_ids):
            raise APIError("survey_answer_incomplete")
        for speech_id, subject_kind in allowed.items():
            _validate_strict_speech_answer(
                subject_kind=subject_kind,
                value=_speech_answer(task.answers, speech_id),
                submit=True,
            )
        task.answers = _answers_with_workflow(
            task.answers,
            scope=scope,
            confirmed_speech_ids=confirmed,
        )
        task.status = "SUBMITTED"
        task.submitted_at = datetime.now(UTC)
        await session.flush()
        AuditService().record(
            session,
            actor_user_id=user_id,
            action="participant.survey.postmatch_submitted",
            target_type="postmatch_survey_task",
            target_id=str(task.id),
            details={
                "questionnaire_version": task.questionnaire_version,
                "status": task.status,
            },
        )
    return await get_postmatch_task(session, task_id=task_id, user_id=user_id)


async def save_postmatch_task(
    session: AsyncSession,
    *,
    task_id: UUID,
    user_id: UUID,
    answers: dict[str, Any],
    submit: bool,
    stage: str | None = None,
) -> dict[str, Any]:
    async with session.begin():
        task = await session.scalar(
            select(PostmatchSurveyTask)
            .where(PostmatchSurveyTask.id == task_id, PostmatchSurveyTask.user_id == user_id)
            .with_for_update()
        )
        if task is None:
            raise APIError("survey_not_found")
        previous_status = task.status
        previous_answers = task.answers
        scope = _annotation_scope(task.answers, status=task.status)
        if task.status == "SUBMITTED":
            # Re-opening is intentional for post-match review. Preserve the
            # prior submitted snapshot in the audit trail before replacing it.
            task.status = "IN_PROGRESS"
            task.submitted_at = None
        if stage not in (None, "overall", "speeches"):
            raise APIError("survey_answer_invalid")
        # Keep the retired combined-flow stage readable for historical clients.
        if stage == "overall" and task.questionnaire_version == POSTMATCH_QUESTIONNAIRE_VERSION:
            overall = answers.get("overall")
            if not isinstance(overall, dict):
                raise APIError("survey_answer_invalid")
            _validate_strict_overall(cast(dict[str, object], overall), submit=True)
            task.answers = {
                **task.answers,
                "overall": overall,
                "speeches": task.answers.get("speeches", {}),
            }
            task.status = "IN_PROGRESS"
            await session.flush()
            AuditService().record(
                session,
                actor_user_id=user_id,
                action="participant.survey.postmatch_overall_saved",
                target_type="postmatch_survey_task",
                target_id=str(task.id),
                details={"questionnaire_version": task.questionnaire_version, "stage": stage},
            )
            await session.refresh(task)
            return {
                "id": str(task.id),
                "match_id": str(task.match_id),
                "questionnaire_version": task.questionnaire_version,
                "status": task.status,
                "answers": task.answers,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
                "submitted_at": task.submitted_at,
                "questionnaire": POSTMATCH_QUESTIONNAIRE,
            }
        speeches = answers.get("speeches")
        if task.questionnaire_version == POSTMATCH_QUESTIONNAIRE_VERSION:
            if set(answers) - {"speeches", "overall", POSTMATCH_WORKFLOW_KEY}:
                raise APIError("survey_answer_invalid")
            if speeches is None:
                speeches = {}
            if not isinstance(speeches, dict):
                raise APIError("survey_answer_invalid")
            speech_rows, allowed = await _postmatch_target_rows(
                session,
                task=task,
                user_id=user_id,
                scope=scope,
            )
            typed_speeches = cast(dict[str, object], speeches)
            if set(typed_speeches) - set(allowed):
                raise APIError("survey_answer_invalid")
            _validate_editable_progression(
                previous_answers=task.answers,
                next_speeches=typed_speeches,
                ordered_speech_ids=[str(speech.id) for speech in speech_rows],
            )
            for speech_id, value in typed_speeches.items():
                _validate_strict_speech_answer(
                    subject_kind=allowed[speech_id], value=value, submit=submit
                )
            if submit and set(typed_speeches) != set(allowed):
                raise APIError("survey_answer_incomplete")
            confirmed = _confirmed_speech_ids(
                task.answers,
                ordered_target_ids=[str(speech.id) for speech in speech_rows],
            )
            confirmed.update(
                speech_id
                for speech_id in allowed
                if _speech_answer_complete({"speeches": typed_speeches}, speech_id)
            )
            if submit and confirmed != set(allowed):
                raise APIError("survey_answer_incomplete")
            # `overall` belonged to the earlier combined UI. It remains
            # readable for old tasks but is no longer required for new ones.
            overall = answers.get("overall")
            if overall is not None:
                _validate_strict_overall(overall, submit=False)
            answers = _answers_with_workflow(
                {
                    **answers,
                    "speeches": typed_speeches,
                },
                scope=scope,
                confirmed_speech_ids=confirmed,
                revision=_workflow_revision(task.answers) + 1,
            )
        else:
            required = {"self", "ai", "overall"}
            if submit and not required.issubset(answers):
                raise APIError("survey_answer_incomplete")
            if speeches is not None:
                if not isinstance(speeches, dict):
                    raise APIError("survey_answer_invalid")
                legacy_speeches = cast(dict[str, object], speeches)
                for value in legacy_speeches.values():
                    if not isinstance(value, dict):
                        raise APIError("survey_answer_invalid")
                    legacy_answer = cast(dict[str, object], value)
                    ratings = legacy_answer.get("ratings", {})
                    tags = legacy_answer.get("tags", [])
                    if not isinstance(ratings, dict) or not isinstance(tags, list):
                        raise APIError("survey_answer_invalid")
                    typed_ratings = cast(dict[str, object], ratings)
                    typed_tags = cast(list[object], tags)
                    if any(
                        not isinstance(item, int) or not 1 <= item <= 7
                        for item in typed_ratings.values()
                    ):
                        raise APIError("survey_answer_invalid")
                    if any(not isinstance(item, str) or len(item) > 64 for item in typed_tags):
                        raise APIError("survey_answer_invalid")
        task.answers = answers
        task.status = "SUBMITTED" if submit else "IN_PROGRESS"
        if submit:
            task.submitted_at = datetime.now(UTC)
        await session.flush()
        AuditService().record(
            session,
            actor_user_id=user_id,
            action=(
                "participant.survey.postmatch_submitted"
                if submit
                else "participant.survey.postmatch_draft_saved"
            ),
            target_type="postmatch_survey_task",
            target_id=str(task.id),
            details={
                "questionnaire_version": task.questionnaire_version,
                "status": task.status,
                "reopened_from_submitted": previous_status == "SUBMITTED",
                "previous_answers": previous_answers if previous_status == "SUBMITTED" else None,
            },
        )
        # PostgreSQL expires server-generated onupdate columns after flush.
        # Refresh before serializing so async response construction never
        # triggers an implicit database load.
        await session.refresh(task)
        return {
            "id": str(task.id),
            "match_id": str(task.match_id),
            "questionnaire_version": task.questionnaire_version,
            "status": task.status,
            "answers": task.answers,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "submitted_at": task.submitted_at,
            "questionnaire": (
                _postmatch_questionnaire(task)
            ),
        }

"""Participant and administrator survey endpoints."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .audit.service import AuditService
from .auth.dependencies import (
    get_admin_auth,
    get_changed_password_auth,
    get_database_session,
    require_browser_origin,
)
from .auth.session import AuthContext
from .models import PersonalAiSurveyResponse, PersonalAiSurveyVersion, PostmatchSurveyTask
from .survey_service import (
    ensure_personal_version,
    get_postmatch_task,
    list_postmatch_tasks,
    personal_response,
    postmatch_task_status,
    save_personal_response,
    save_postmatch_speech_answer,
    save_postmatch_task,
    submit_postmatch_task,
)

router = APIRouter(tags=["surveys"])


@router.get("/api/me/ai-experience")
async def get_ai_experience(
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    return await personal_response(session, user_id=auth.user_id)


@router.put("/api/me/ai-experience", dependencies=[Depends(require_browser_origin)])
async def save_ai_experience(
    payload: dict[str, Any],
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        from .auth.errors import APIError

        raise APIError("survey_answer_invalid")
    return await save_personal_response(
        session,
        user_id=auth.user_id,
        answers=cast(dict[str, Any], answers),
        submit=bool(payload.get("submit", False)),
    )


@router.get("/api/me/postmatch-surveys")
async def get_postmatch_surveys(
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[dict[str, Any]]:
    return await list_postmatch_tasks(session, user_id=auth.user_id)


@router.get("/api/me/postmatch-surveys/matches/{match_id}/status")
async def get_postmatch_survey_status(
    match_id: UUID,
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    return await postmatch_task_status(session, match_id=match_id, user_id=auth.user_id)


@router.get("/api/me/postmatch-surveys/{task_id}")
async def get_postmatch_survey(
    task_id: UUID,
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    return await get_postmatch_task(session, task_id=task_id, user_id=auth.user_id)


@router.put("/api/me/postmatch-surveys/{task_id}", dependencies=[Depends(require_browser_origin)])
async def save_postmatch_survey(
    task_id: UUID,
    payload: dict[str, Any],
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    answers = payload.get("answers")
    speech_id = payload.get("speech_id")
    if speech_id is not None:
        answer = payload.get("answer")
        if not isinstance(answer, dict):
            from .auth.errors import APIError

            raise APIError("survey_answer_invalid")
        try:
            parsed_speech_id = UUID(str(speech_id))
        except ValueError as error:
            from .auth.errors import APIError

            raise APIError("survey_answer_invalid") from error
        expected_revision = payload.get("expected_revision")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            from .auth.errors import APIError

            raise APIError("survey_answer_invalid")
        return await save_postmatch_speech_answer(
            session,
            task_id=task_id,
            user_id=auth.user_id,
            speech_id=parsed_speech_id,
            answer=cast(dict[str, Any], answer),
            confirm=bool(payload.get("confirm", False)),
            expected_revision=expected_revision,
        )
    if bool(payload.get("submit", False)) and answers is None:
        return await submit_postmatch_task(
            session,
            task_id=task_id,
            user_id=auth.user_id,
        )
    if not isinstance(answers, dict):
        from .auth.errors import APIError

        raise APIError("survey_answer_invalid")
    return await save_postmatch_task(
        session,
        task_id=task_id,
        user_id=auth.user_id,
        answers=cast(dict[str, Any], answers),
        submit=bool(payload.get("submit", False)),
        stage=str(payload.get("stage")) if payload.get("stage") is not None else None,
    )


@router.get("/api/admin/surveys/personal")
async def admin_personal_survey(
    auth: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    version = await session.scalar(
        select(PersonalAiSurveyVersion)
        .where(PersonalAiSurveyVersion.status == "DRAFT")
        .order_by(PersonalAiSurveyVersion.version.desc())
        .limit(1)
    )
    if version is None:
        version = await ensure_personal_version(session, actor_user_id=auth.user_id)
    responses = list(
        (
            await session.scalars(
                select(PersonalAiSurveyResponse).order_by(
                    PersonalAiSurveyResponse.updated_at.desc()
                )
            )
        ).all()
    )
    AuditService().record(
        session,
        actor_user_id=auth.user_id,
        action="admin.survey.responses_viewed",
        target_type="personal_ai_survey",
        target_id=str(version.id),
    )
    await session.commit()
    return {
        "version": {
            "id": str(version.id),
            "version": version.version,
            "title": version.title,
            "description": version.description,
            "questions": version.questions,
            "status": version.status,
        },
        "responses": [
            {
                "id": str(item.id),
                "user_id": str(item.user_id),
                "status": item.status,
                "answers": item.answers,
                "updated_at": item.updated_at,
                "submitted_at": item.submitted_at,
            }
            for item in responses
        ],
    }


@router.put("/api/admin/surveys/personal", dependencies=[Depends(require_browser_origin)])
async def save_admin_personal_survey(
    payload: dict[str, Any],
    auth: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        from .auth.errors import APIError

        raise APIError("survey_questions_invalid")
    typed_questions = cast(list[dict[str, Any]], questions)
    for question in typed_questions:
        if (
            not isinstance(question, dict)  # type: ignore[reportUnnecessaryIsInstance]
            or not isinstance(question.get("key"), str)
            or not isinstance(question.get("text"), str)
        ):
            from .auth.errors import APIError

            raise APIError("survey_questions_invalid")
    async with session.begin():
        latest = await session.scalar(
            select(PersonalAiSurveyVersion.version)
            .order_by(PersonalAiSurveyVersion.version.desc())
            .limit(1)
        )
        version = PersonalAiSurveyVersion(
            version=int(latest or 0) + 1,
            status="DRAFT",
            title=str(payload.get("title") or "AI 辩论感受")[:200],
            description=str(payload.get("description") or "")[:4000],
            questions=typed_questions,
            created_by=auth.user_id,
        )
        session.add(version)
        AuditService().record(
            session,
            actor_user_id=auth.user_id,
            action="admin.survey.version_created",
            target_type="personal_ai_survey_version",
            target_id=str(version.id),
            details={"version": version.version},
        )
        await session.flush()
        return {"id": str(version.id), "version": version.version, "status": version.status}


@router.post(
    "/api/admin/surveys/personal/{version_id}/publish",
    dependencies=[Depends(require_browser_origin)],
)
async def publish_admin_personal_survey(
    version_id: UUID,
    auth: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    async with session.begin():
        version = await session.get(PersonalAiSurveyVersion, version_id, with_for_update=True)
        if version is None:
            from .auth.errors import APIError

            raise APIError("survey_not_found")
        from sqlalchemy import update

        await session.execute(
            update(PersonalAiSurveyVersion)
            .where(PersonalAiSurveyVersion.status == "PUBLISHED")
            .values(status="ARCHIVED")
        )
        version.status = "PUBLISHED"
        version.published_at = datetime.now(UTC)
        AuditService().record(
            session,
            actor_user_id=auth.user_id,
            action="admin.survey.version_published",
            target_type="personal_ai_survey_version",
            target_id=str(version.id),
            details={"version": version.version},
        )
        return {"id": str(version.id), "version": version.version, "status": version.status}


@router.get("/api/admin/surveys/personal/responses/{response_id}")
async def admin_personal_response(
    response_id: UUID,
    auth: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    response = await session.get(PersonalAiSurveyResponse, response_id)
    if response is None:
        from .auth.errors import APIError

        raise APIError("survey_not_found")
    AuditService().record(
        session,
        actor_user_id=auth.user_id,
        action="admin.survey.response_viewed",
        target_type="personal_ai_survey_response",
        target_id=str(response.id),
    )
    await session.commit()
    return {
        "id": str(response.id),
        "user_id": str(response.user_id),
        "version_id": str(response.version_id),
        "status": response.status,
        "answers": response.answers,
        "updated_at": response.updated_at,
        "submitted_at": response.submitted_at,
    }


@router.get("/api/admin/surveys/personal/export")
async def export_personal_surveys(
    auth: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    format: str = "json",
) -> Response:
    if format not in {"json", "csv"}:
        from .auth.errors import APIError

        raise APIError("survey_export_format_invalid")
    responses = list(
        (
            await session.scalars(
                select(PersonalAiSurveyResponse).order_by(PersonalAiSurveyResponse.updated_at)
            )
        ).all()
    )
    rows = [
        {
            "response_id": str(item.id),
            "user_id": str(item.user_id),
            "version_id": str(item.version_id),
            "status": item.status,
            "answers": item.answers,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            "submitted_at": item.submitted_at.isoformat() if item.submitted_at else None,
        }
        for item in responses
    ]
    AuditService().record(
        session,
        actor_user_id=auth.user_id,
        action="admin.survey.responses_exported",
        target_type="personal_ai_survey",
        target_id=None,
        details={"format": format, "count": len(rows)},
    )
    await session.commit()
    if format == "json":
        return Response(
            content=json.dumps(rows, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=personal-ai-surveys.json"},
        )
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "response_id",
            "user_id",
            "version_id",
            "status",
            "answers",
            "updated_at",
            "submitted_at",
        ],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({**row, "answers": json.dumps(row["answers"], ensure_ascii=False)})
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=personal-ai-surveys.csv"},
    )


@router.get("/api/admin/surveys/postmatch/export")
async def export_postmatch_surveys(
    auth: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    format: str = "json",
) -> Response:
    if format not in {"json", "csv"}:
        from .auth.errors import APIError

        raise APIError("survey_export_format_invalid")
    tasks = list(
        (
            await session.scalars(
                select(PostmatchSurveyTask).order_by(PostmatchSurveyTask.updated_at)
            )
        ).all()
    )
    rows = [
        {
            "task_id": str(item.id),
            "match_id": str(item.match_id),
            "user_id": str(item.user_id),
            "questionnaire_version": item.questionnaire_version,
            "status": item.status,
            "answers": item.answers,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            "submitted_at": item.submitted_at.isoformat() if item.submitted_at else None,
        }
        for item in tasks
    ]
    AuditService().record(
        session,
        actor_user_id=auth.user_id,
        action="admin.survey.postmatch_exported",
        target_type="postmatch_survey",
        target_id=None,
        details={"format": format, "count": len(rows)},
    )
    await session.commit()
    if format == "json":
        return Response(
            content=json.dumps(rows, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=postmatch-surveys.json"},
        )
    output = io.StringIO()
    fields = [
        "task_id",
        "match_id",
        "user_id",
        "questionnaire_version",
        "status",
        "answers",
        "updated_at",
        "submitted_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({**row, "answers": json.dumps(row["answers"], ensure_ascii=False)})
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=postmatch-surveys.csv"},
    )

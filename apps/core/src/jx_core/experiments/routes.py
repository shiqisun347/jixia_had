"""Public capabilities and administrator boundaries for paper experiments."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import (
    get_admin_auth,
    get_changed_password_auth,
    get_database_session,
    require_browser_origin,
)
from ..auth.errors import APIError
from ..auth.session import AuthContext
from ..config import Settings
from ..models import BackgroundTask, ExperimentBatch
from .prompts import (
    EXPERIMENT_PROMPT_VERSION,
    render_experiment_decision_prompt,
    render_experiment_speech_prompt,
)
from .schemas import (
    ExperimentAccountGenerationResponse,
    ExperimentAppointmentResponse,
    ExperimentBatchCreateRequest,
    ExperimentBatchDeleteResponse,
    ExperimentBatchDisableResponse,
    ExperimentBatchProgressResponse,
    ExperimentBatchResponse,
    ExperimentBatchUpdateRequest,
    ExperimentCapabilitiesResponse,
    ExperimentContextResponse,
    ExperimentEnterRequest,
    ExperimentEnterResponse,
    ExperimentJobResponse,
    ExperimentPromptTemplatesResponse,
    ExperimentPublishResponse,
    ExperimentResultOverrideRequest,
    ExperimentResultVisibilityResponse,
    ExperimentRetentionRequest,
    ExperimentRosterDetailResponse,
    ExperimentRosterPutRequest,
    ExperimentRosterResponse,
    ExperimentScheduleCsvImportRequest,
    ExperimentScheduleCsvImportResponse,
    ExperimentScheduleGenerateRequest,
    ExperimentScheduleResponse,
    ExpertAnnotationSaveRequest,
    ExpertAnnotationTaskResponse,
    ParticipantAnnotationAnswerSaveRequest,
    ParticipantAnnotationTaskResponse,
    ParticipantQuestionnaireSubmitRequest,
)
from .service import ExperimentService

router = APIRouter(tags=["experiments"])
service = ExperimentService()


def _wav_header(data_size: int) -> bytes:
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        data_size + 36,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        16_000,
        32_000,
        2,
        16,
        b"data",
        data_size,
    )


def _byte_range(value: str | None, total: int) -> tuple[int, int] | None:
    if not value:
        return 0, total - 1
    if not value.startswith("bytes=") or "," in value:
        return None
    raw_start, separator, raw_end = value[6:].partition("-")
    if not separator:
        return None
    try:
        if raw_start:
            start = int(raw_start)
            end = min(int(raw_end), total - 1) if raw_end else total - 1
        else:
            suffix = int(raw_end)
            if suffix <= 0:
                return None
            start = max(total - suffix, 0)
            end = total - 1
    except ValueError:
        return None
    if start < 0 or start >= total or end < start:
        return None
    return start, end


def _wav_chunks(path: Path, header: bytes, start: int, end: int):
    header_end = len(header) - 1
    if start <= header_end:
        yield header[start : min(end, header_end) + 1]
    data_start = max(start - len(header), 0)
    data_end = end - len(header)
    if data_end < 0:
        return
    remaining = data_end - data_start + 1
    with path.open("rb") as source:
        source.seek(data_start)
        while remaining > 0:
            chunk = source.read(min(64 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _audio_response(
    *, path: Path, speaker_kind: str, speech_id: UUID, range_header: str | None
) -> Response:
    if not path.is_file():
        raise APIError("experiment_annotation_audio_unavailable")
    if speaker_kind == "AGENT" or path.suffix.lower() in {".ogg", ".opus"}:
        return FileResponse(
            path,
            media_type="audio/ogg",
            content_disposition_type="inline",
            filename=f"speech-{speech_id}.ogg",
        )

    data_size = path.stat().st_size
    header = _wav_header(data_size)
    total = len(header) + data_size
    selected = _byte_range(range_header, total)
    if selected is None:
        return Response(
            status_code=416,
            headers={"Accept-Ranges": "bytes", "Content-Range": f"bytes */{total}"},
        )
    start, end = selected
    partial = range_header is not None
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": f'inline; filename="speech-{speech_id}.wav"',
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    return StreamingResponse(
        _wav_chunks(path, header, start, end),
        status_code=206 if partial else 200,
        media_type="audio/wav",
        headers=headers,
    )


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


@router.get("/api/experiments/capabilities", response_model=ExperimentCapabilitiesResponse)
async def experiment_capabilities(request: Request) -> ExperimentCapabilitiesResponse:
    return ExperimentCapabilitiesResponse(
        creation_enabled=_settings(request).paper_experiment_enabled
    )


@router.get("/api/admin/experiments/batches", response_model=list[ExperimentBatchResponse])
async def list_experiment_batches(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[ExperimentBatch]:
    return await service.list_batches(session)


@router.post(
    "/api/admin/experiments/accounts/generate",
    response_model=ExperimentAccountGenerationResponse,
    dependencies=[Depends(require_browser_origin)],
)
async def generate_experiment_accounts(
    request: Request,
    auth: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentAccountGenerationResponse:
    if not _settings(request).paper_experiment_enabled:
        raise APIError("experiment_disabled")
    return await service.generate_anonymous_accounts(session, actor_user_id=auth.user_id)


@router.post(
    "/api/admin/experiments/batches",
    response_model=ExperimentBatchResponse,
    status_code=201,
    dependencies=[Depends(require_browser_origin)],
)
async def create_experiment_batch(
    payload: ExperimentBatchCreateRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentBatch:
    if not _settings(request).paper_experiment_enabled:
        raise APIError("experiment_disabled")
    return await service.create_batch(session, actor_user_id=auth.user_id, payload=payload)


@router.get("/api/admin/experiments/batches/{batch_id}", response_model=ExperimentBatchResponse)
async def get_experiment_batch(
    batch_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentBatch:
    return await service.get_batch(session, batch_id)


@router.patch(
    "/api/admin/experiments/batches/{batch_id}",
    response_model=ExperimentBatchResponse,
    dependencies=[Depends(require_browser_origin)],
)
async def update_experiment_batch(
    batch_id: UUID,
    payload: ExperimentBatchUpdateRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentBatch:
    if not _settings(request).paper_experiment_enabled:
        raise APIError("experiment_disabled")
    return await service.update_batch(
        session,
        batch_id=batch_id,
        actor_user_id=auth.user_id,
        payload=payload,
    )


@router.delete(
    "/api/admin/experiments/batches/{batch_id}",
    response_model=ExperimentBatchDeleteResponse,
    dependencies=[Depends(require_browser_origin)],
)
async def delete_experiment_batch(
    batch_id: UUID,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentBatchDeleteResponse:
    if not _settings(request).paper_experiment_enabled:
        raise APIError("experiment_disabled")
    await service.delete_draft_batch(
        session,
        batch_id=batch_id,
        actor_user_id=auth.user_id,
    )
    return ExperimentBatchDeleteResponse(id=batch_id)


@router.get(
    "/api/admin/experiments/rooms/{room_id}/context",
    response_model=ExperimentContextResponse | None,
)
async def get_room_experiment_context(
    room_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentContextResponse | None:
    context = await service.resolve_context(session, room_id=room_id)
    return ExperimentContextResponse.model_validate(context) if context is not None else None


@router.put(
    "/api/admin/experiments/batches/{batch_id}/roster",
    response_model=ExperimentRosterResponse,
    dependencies=[Depends(require_browser_origin)],
)
async def replace_experiment_roster(
    batch_id: UUID,
    payload: ExperimentRosterPutRequest,
    request: Request,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentRosterResponse:
    if not _settings(request).paper_experiment_enabled:
        raise APIError("experiment_disabled")
    return await service.replace_roster(session, batch_id=batch_id, payload=payload)


@router.get(
    "/api/admin/experiments/batches/{batch_id}/roster",
    response_model=ExperimentRosterDetailResponse,
)
async def get_experiment_roster(
    batch_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentRosterDetailResponse:
    return await service.get_roster(session, batch_id=batch_id)


@router.post(
    "/api/admin/experiments/batches/{batch_id}/schedule/generate",
    response_model=ExperimentScheduleResponse,
    dependencies=[Depends(require_browser_origin)],
)
async def generate_experiment_schedule(
    batch_id: UUID,
    payload: ExperimentScheduleGenerateRequest,
    request: Request,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentScheduleResponse:
    if not _settings(request).paper_experiment_enabled:
        raise APIError("experiment_disabled")
    return await service.generate_schedule(
        session,
        batch_id=batch_id,
        topic_ids=tuple(payload.topic_ids),
        training_topic_id=payload.training_topic_id,
    )


@router.post(
    "/api/admin/experiments/batches/{batch_id}/disable",
    response_model=ExperimentBatchDisableResponse,
    dependencies=[Depends(require_browser_origin)],
)
async def disable_experiment_batch(
    batch_id: UUID,
    request: Request,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentBatchDisableResponse:
    if not _settings(request).paper_experiment_enabled:
        raise APIError("experiment_disabled")
    return await service.disable_batch(session, batch_id=batch_id)


@router.get(
    "/api/admin/experiments/batches/{batch_id}/schedule",
    response_model=ExperimentScheduleResponse,
)
async def get_experiment_schedule(
    batch_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentScheduleResponse:
    return await service.get_schedule(session, batch_id=batch_id)


@router.get("/api/admin/experiments/batches/{batch_id}/schedule.csv")
async def export_experiment_schedule_csv(
    batch_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> Response:
    content = await service.export_schedule_csv(session, batch_id=batch_id)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="experiment-{batch_id}.csv"'},
    )


@router.get("/api/admin/experiments/batches/{batch_id}/schedule-readable.csv")
async def export_readable_experiment_schedule_csv(
    batch_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> Response:
    content = await service.export_readable_schedule_csv(session, batch_id=batch_id)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="experiment-{batch_id}-readable.csv"'
        },
    )


@router.get("/api/admin/experiments/accounts.csv")
async def export_experiment_accounts_csv(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> Response:
    content = await service.export_accounts_csv(session)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="experiment-accounts.csv"'},
    )


@router.get(
    "/api/admin/experiments/prompt-templates",
    response_model=ExperimentPromptTemplatesResponse,
)
async def get_experiment_prompt_templates(
    _: Annotated[AuthContext, Depends(get_admin_auth)],
) -> ExperimentPromptTemplatesResponse:
    history = [
        {
            "side": "AFFIRMATIVE",
            "seat_no": 1,
            "speaker_kind": "HUMAN",
            "content": "{{DEBATE_HISTORY}}",
        }
    ]
    return ExperimentPromptTemplatesResponse(
        version=EXPERIMENT_PROMPT_VERSION,
        decision_prompt=render_experiment_decision_prompt(
            side="NEGATIVE",
            seat_no=2,
            topic="{{TOPIC}}",
            affirmative_stance="{{AFFIRMATIVE_STANCE}}",
            negative_stance="{{NEGATIVE_STANCE}}",
            side_remaining_ms=360000,
            opponent_remaining_ms=360000,
            history=history,
        ),
        speech_prompt=render_experiment_speech_prompt(
            side="NEGATIVE",
            seat_no=2,
            topic="{{TOPIC}}",
            affirmative_stance="{{AFFIRMATIVE_STANCE}}",
            negative_stance="{{NEGATIVE_STANCE}}",
            side_remaining_ms=360000,
            opponent_remaining_ms=360000,
            history=history,
            max_speech_seconds=30,
        ),
    )


@router.post(
    "/api/admin/experiments/batches/{batch_id}/schedule/import",
    response_model=ExperimentScheduleCsvImportResponse,
    dependencies=[Depends(require_browser_origin)],
)
async def import_experiment_schedule_csv(
    batch_id: UUID,
    payload: ExperimentScheduleCsvImportRequest,
    request: Request,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentScheduleCsvImportResponse:
    if not _settings(request).paper_experiment_enabled:
        raise APIError("experiment_disabled")
    return await service.import_schedule_csv(session, batch_id=batch_id, csv_text=payload.csv_text)


@router.post(
    "/api/admin/experiments/batches/{batch_id}/publish",
    response_model=ExperimentPublishResponse,
    dependencies=[Depends(require_browser_origin)],
)
async def publish_experiment_batch(
    batch_id: UUID,
    request: Request,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentPublishResponse:
    if not _settings(request).paper_experiment_enabled:
        raise APIError("experiment_disabled")
    return await service.publish_batch(session, batch_id=batch_id)


@router.get(
    "/api/admin/experiments/batches/{batch_id}/progress",
    response_model=ExperimentBatchProgressResponse,
)
async def get_experiment_batch_progress(
    batch_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentBatchProgressResponse:
    return await service.batch_progress(session, batch_id=batch_id)


@router.post(
    "/api/admin/experiments/attempts/{attempt_id}/publish-result",
    response_model=ExperimentResultVisibilityResponse,
    dependencies=[Depends(require_browser_origin)],
)
async def override_experiment_result_visibility(
    attempt_id: UUID,
    payload: ExperimentResultOverrideRequest,
    auth: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentResultVisibilityResponse:
    attempt = await service.override_result_visibility(
        session,
        attempt_id=attempt_id,
        actor_user_id=auth.user_id,
        payload=payload,
    )
    if attempt.public_at is None:
        raise APIError("experiment_attempt_invalid")
    return ExperimentResultVisibilityResponse(attempt_id=attempt.id, public_at=attempt.public_at)


@router.post(
    "/api/admin/experiments/batches/{batch_id}/exports",
    response_model=ExperimentJobResponse,
    status_code=202,
    dependencies=[Depends(require_browser_origin)],
)
async def create_experiment_batch_export(
    batch_id: UUID,
    auth: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentJobResponse:
    task = await service.enqueue_batch_export(
        session, batch_id=batch_id, actor_user_id=auth.user_id
    )
    return task


@router.post(
    "/api/admin/experiments/batches/{batch_id}/retention",
    response_model=ExperimentJobResponse,
    status_code=202,
    dependencies=[Depends(require_browser_origin)],
)
async def create_experiment_retention_dry_run(
    batch_id: UUID,
    payload: ExperimentRetentionRequest,
    auth: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentJobResponse:
    task = await service.enqueue_retention_dry_run(
        session,
        batch_id=batch_id,
        actor_user_id=auth.user_id,
        payload=payload,
    )
    return task


@router.get(
    "/api/admin/experiments/jobs/{task_id}",
    response_model=ExperimentJobResponse,
)
async def get_experiment_job(
    task_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentJobResponse:
    task = await session.scalar(
        select(BackgroundTask).where(
            BackgroundTask.id == task_id,
            BackgroundTask.task_type.in_(("EXPERIMENT_BATCH_EXPORT", "EXPERIMENT_RETENTION")),
        )
    )
    if task is None:
        raise APIError("experiment_job_not_found")
    return ExperimentJobResponse(
        id=task.id,
        task_type=cast(
            Literal["EXPERIMENT_BATCH_EXPORT", "EXPERIMENT_RETENTION"],
            task.task_type,
        ),
        status=task.status,
        created_at=task.created_at,
        error_code=task.error_code,
        artifact_ready=bool(task.status == "SUCCEEDED" and task.payload.get("artifact_name")),
        report=(
            cast(dict[str, object], task.payload["report"])
            if isinstance(task.payload.get("report"), dict)
            else None
        ),
    )


@router.get(
    "/api/admin/experiments/exports/{task_id}/download",
    response_class=FileResponse,
)
async def download_experiment_export(
    task_id: UUID,
    request: Request,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> FileResponse:
    task = await session.scalar(
        select(BackgroundTask).where(
            BackgroundTask.id == task_id,
            BackgroundTask.task_type == "EXPERIMENT_BATCH_EXPORT",
        )
    )
    expected_name = f"experiment-{task_id}.zip"
    if (
        task is None
        or task.status != "SUCCEEDED"
        or task.payload.get("artifact_name") != expected_name
    ):
        raise APIError("experiment_export_unavailable")
    root = Path(_settings(request).export_storage_dir).resolve()
    artifact = (root / expected_name).resolve()
    if artifact.parent != root or not artifact.is_file():
        raise APIError("experiment_export_unavailable")
    return FileResponse(
        artifact,
        media_type="application/zip",
        filename=expected_name,
    )


@router.get(
    "/api/experiments/appointments",
    response_model=list[ExperimentAppointmentResponse],
)
async def list_my_experiment_appointments(
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[ExperimentAppointmentResponse]:
    return await service.list_appointments(session, user_id=auth.user_id)


@router.post(
    "/api/experiments/appointments/{scheduled_match_id}/enter",
    response_model=ExperimentEnterResponse,
    dependencies=[Depends(require_browser_origin)],
)
async def enter_experiment_appointment(
    scheduled_match_id: UUID,
    payload: ExperimentEnterRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExperimentEnterResponse:
    if not _settings(request).paper_experiment_enabled:
        raise APIError("experiment_disabled")
    return await service.enter_scheduled_match(
        session,
        scheduled_match_id=scheduled_match_id,
        user_id=auth.user_id,
        user_role=auth.role,
        human_participation_terms_version=payload.human_participation_terms_version,
    )


@router.get(
    "/api/experiments/annotation-tasks",
    response_model=list[ParticipantAnnotationTaskResponse],
)
async def list_my_annotation_tasks(
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[ParticipantAnnotationTaskResponse]:
    return await service.list_participant_tasks(session, user_id=auth.user_id)


@router.get("/api/experiments/tasks/{task_id}/audio/{speech_id}")
async def get_annotation_audio(
    task_id: UUID,
    speech_id: UUID,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> Response:
    storage_path, speaker_kind = await service.participant_audio(
        session,
        task_id=task_id,
        speech_id=speech_id,
        user_id=auth.user_id,
    )
    return _audio_response(
        path=Path(storage_path),
        speaker_kind=speaker_kind,
        speech_id=speech_id,
        range_header=request.headers.get("range"),
    )


@router.put(
    "/api/experiments/annotation-tasks/{task_id}/items/{item_id}",
    response_model=ParticipantAnnotationTaskResponse,
    dependencies=[Depends(require_browser_origin)],
)
async def save_annotation_item(
    task_id: UUID,
    item_id: UUID,
    payload: ParticipantAnnotationAnswerSaveRequest,
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ParticipantAnnotationTaskResponse:
    return await service.save_participant_answers(
        session,
        task_id=task_id,
        item_id=item_id,
        user_id=auth.user_id,
        payload=payload,
    )


@router.put(
    "/api/experiments/annotation-tasks/{task_id}/questionnaire",
    response_model=ParticipantAnnotationTaskResponse,
    dependencies=[Depends(require_browser_origin)],
)
async def submit_annotation_questionnaire(
    task_id: UUID,
    payload: ParticipantQuestionnaireSubmitRequest,
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ParticipantAnnotationTaskResponse:
    return await service.submit_participant_questionnaire(
        session, task_id=task_id, user_id=auth.user_id, payload=payload
    )


@router.post(
    "/api/experiments/annotation-tasks/{task_id}/submit",
    response_model=ParticipantAnnotationTaskResponse,
    dependencies=[Depends(require_browser_origin)],
)
async def submit_annotation_task(
    task_id: UUID,
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ParticipantAnnotationTaskResponse:
    return await service.submit_participant_task(session, task_id=task_id, user_id=auth.user_id)


@router.get(
    "/api/experiments/expert-tasks",
    response_model=list[ExpertAnnotationTaskResponse],
)
async def list_my_expert_tasks(
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[ExpertAnnotationTaskResponse]:
    return await service.list_expert_tasks(session, user_id=auth.user_id)


@router.get("/api/experiments/expert-tasks/{task_id}/audio/{speech_id}")
async def get_expert_annotation_audio(
    task_id: UUID,
    speech_id: UUID,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> Response:
    storage_path, speaker_kind = await service.expert_audio(
        session,
        task_id=task_id,
        speech_id=speech_id,
        user_id=auth.user_id,
    )
    return _audio_response(
        path=Path(storage_path),
        speaker_kind=speaker_kind,
        speech_id=speech_id,
        range_header=request.headers.get("range"),
    )


@router.put(
    "/api/experiments/expert-tasks/{task_id}/opportunities/{opportunity_id}",
    response_model=ExpertAnnotationTaskResponse,
    dependencies=[Depends(require_browser_origin)],
)
async def save_expert_annotation(
    task_id: UUID,
    opportunity_id: UUID,
    payload: ExpertAnnotationSaveRequest,
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExpertAnnotationTaskResponse:
    return await service.save_expert_annotation(
        session,
        task_id=task_id,
        opportunity_id=opportunity_id,
        user_id=auth.user_id,
        payload=payload,
    )


@router.post(
    "/api/experiments/expert-tasks/{task_id}/submit",
    response_model=ExpertAnnotationTaskResponse,
    dependencies=[Depends(require_browser_origin)],
)
async def submit_expert_task(
    task_id: UUID,
    auth: Annotated[AuthContext, Depends(get_changed_password_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExpertAnnotationTaskResponse:
    return await service.submit_expert_task(session, task_id=task_id, user_id=auth.user_id)


__all__ = ["router"]

"""Read-only match workbench and bounded export endpoints for administrators."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.dependencies import get_admin_auth, get_database_session, require_browser_origin
from .auth.errors import APIError
from .auth.session import AuthContext
from .data_capture.content import load_content_blob
from .models import (
    AuditLog,
    BackgroundTask,
    ExternalCall,
    Match,
    MatchEvent,
    MatchExport,
    MatchExportItem,
    MatchFile,
    MatchParticipant,
    Room,
    Seat,
    Speech,
    User,
)

router = APIRouter(prefix="/api/admin", tags=["admin-data"])


class ExportRequest(BaseModel):
    match_ids: list[UUID] = Field(min_length=1, max_length=100)
    include_audio: bool = False


def _page(page: int, page_size: int) -> tuple[int, int]:
    if page < 1 or page_size not in {10, 25, 50, 100}:
        raise APIError("admin_query_invalid")
    return page, page_size


def _paged(items: list[dict[str, Any]], page: int, page_size: int, total: int) -> dict[str, Any]:
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
    }


CALL_KIND_LABELS = {
    "LLM_DECISION": "Agent 发言决策",
    "LLM_SPEECH": "Agent 发言稿",
    "JUDGE": "AI 裁判评分",
    "ASR": "语音识别",
    "TTS": "语音合成",
    "LIVEKIT": "实时音频连接",
    "HOST_AUDIO": "主持音频",
    "INTERNAL": "内部编排",
}
CALL_STATUS_LABELS = {
    "STARTED": "进行中",
    "SUCCEEDED": "成功",
    "FAILED": "失败",
    "CANCELLED": "已取消",
}


def _call_explanation(call_kind: str, status: str, error_code: str | None) -> dict[str, str]:
    label = CALL_KIND_LABELS.get(call_kind, call_kind)
    if status == "FAILED":
        return {
            "what": f"{label}失败",
            "why": error_code or "服务未提供具体错误码",
            "impact": "没有产生可用结果；如果存在重试，会生成新的调用记录。",
        }
    if status == "CANCELLED":
        return {
            "what": f"{label}被取消",
            "why": "比赛流程切换、重置或连接关闭时结束了这次调用。",
            "impact": "这条记录不会被当作正式结果使用。",
        }
    if status == "STARTED":
        return {
            "what": f"{label}进行中",
            "why": "服务已创建调用，但还没有收到完成结果。",
            "impact": "需要结合比赛当前状态判断是否仍在等待。",
        }
    return {
        "what": f"{label}成功",
        "why": "服务返回了可用结果。",
        "impact": "结果已进入后续比赛流程。",
    }


def _call_view(item: ExternalCall) -> dict[str, Any]:
    explanation = _call_explanation(item.call_kind, item.status, item.error_code)
    return {
        "id": str(item.id),
        "kind": item.call_kind,
        "kind_label": CALL_KIND_LABELS.get(item.call_kind, item.call_kind),
        "provider": item.provider,
        "operation": item.operation,
        "model": item.model,
        "voice": item.voice,
        "attempt_no": item.attempt_no,
        "status": item.status,
        "status_label": CALL_STATUS_LABELS.get(item.status, item.status),
        "match_id": str(item.match_id) if item.match_id else None,
        "speech_id": str(item.speech_id) if item.speech_id else None,
        "generation_id": str(item.agent_generation_id or item.generation_id)
        if item.agent_generation_id or item.generation_id
        else None,
        "decision_round_id": str(item.decision_round_id) if item.decision_round_id else None,
        "context_version": item.context_version,
        "request_id": item.request_id,
        "started_at": item.started_at,
        "first_result_at": item.first_result_at,
        "completed_at": item.completed_at,
        "first_result_latency_ms": item.first_result_latency_ms,
        "completed_latency_ms": item.completed_latency_ms,
        "prompt_tokens": item.prompt_tokens,
        "completion_tokens": item.completion_tokens,
        "audio_bytes": item.audio_bytes,
        "audio_duration_ms": item.audio_duration_ms,
        "error_code": item.error_code,
        "has_request": item.request_blob_id is not None,
        "has_response": item.response_blob_id is not None,
        "explanation": explanation,
    }


def _timeline_at(value: datetime | None) -> str:
    """Normalize legacy naive and current aware timestamps for stable ordering."""
    if value is None:
        return "1970-01-01T00:00:00+00:00"
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat()


def _timeline_call_view(item: ExternalCall) -> dict[str, Any]:
    explanation = _call_explanation(item.call_kind, item.status, item.error_code)
    return {
        "id": f"call:{item.id}",
        "type": "CALL",
        "type_label": CALL_KIND_LABELS.get(item.call_kind, "外部调用"),
        "at": _timeline_at(item.started_at),
        "sequence": None,
        "title": explanation["what"],
        "description": explanation["impact"],
        "status": item.status,
        "related_id": str(item.id),
    }


@router.get("/matches/{match_id}/workbench/overview")
async def workbench_overview(
    match_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    row = (
        await session.execute(
            select(Match, Room).join(Room, Room.id == Match.room_id).where(Match.id == match_id)
        )
    ).first()
    if row is None:
        raise APIError("match_not_found")
    match, room = row
    counts = {}
    for name, model in (
        ("speeches", Speech),
        ("events", MatchEvent),
        ("calls", ExternalCall),
        ("participants", MatchParticipant),
        ("files", MatchFile),
    ):
        column = model.match_id
        counts[name] = int(
            await session.scalar(select(func.count()).select_from(model).where(column == match_id))
            or 0
        )
    return {
        "match": {
            "id": str(match.id),
            "room_id": str(match.room_id),
            "status": match.status,
            "sequence": match.sequence,
            "context_version": match.context_version,
            "created_at": match.created_at,
            "ended_at": match.ended_at,
            "label": room.label,
            "topic": room.topic_snapshot,
            "admin_note": match.admin_note,
        },
        "counts": counts,
    }


@router.get("/matches/{match_id}/workbench/participants")
async def workbench_participants(
    match_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[dict[str, Any]]:
    room_id = await session.scalar(select(Match.room_id).where(Match.id == match_id))
    if room_id is None:
        raise APIError("match_not_found")
    rows = (
        await session.execute(
            select(Seat, User.real_name, User.username)
            .outerjoin(User, User.id == Seat.user_id)
            .where(Seat.room_id == room_id)
            .order_by(Seat.side, Seat.seat_no)
        )
    ).all()
    return [
        {
            "side": seat.side,
            "seat_no": seat.seat_no,
            "occupant_type": seat.occupant_type,
            "user_id": str(seat.user_id) if seat.user_id else None,
            "username": username,
            "real_name": real_name,
            "agent_profile_id": str(seat.agent_profile_id) if seat.agent_profile_id else None,
            "configured_agent_profile_id": str(seat.configured_agent_profile_id)
            if seat.configured_agent_profile_id
            else None,
        }
        for seat, real_name, username in rows
    ]


@router.get("/matches/{match_id}/workbench/transcript")
async def workbench_transcript(
    match_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    page, page_size = _page(page, page_size)
    total = int(
        await session.scalar(
            select(func.count()).select_from(Speech).where(Speech.match_id == match_id)
        )
        or 0
    )
    rows = (
        (
            await session.execute(
                select(Speech)
                .where(Speech.match_id == match_id)
                .order_by(Speech.created_at)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return _paged(
        [
            {
                "id": str(item.id),
                "action_key": item.action_key,
                "speaker_kind": item.speaker_kind,
                "user_id": str(item.user_id) if item.user_id else None,
                "agent_profile_id": str(item.agent_profile_id) if item.agent_profile_id else None,
                "side": item.side,
                "seat_no": item.seat_no,
                "status": item.status,
                "asr_raw_final_text": item.asr_raw_final_text,
                "display_text": item.display_text,
                "audio_duration_ms": item.audio_duration_ms,
                "finalized_at": item.finalized_at,
            }
            for item in rows
        ],
        page,
        page_size,
        total,
    )


@router.get("/matches/{match_id}/workbench/events")
async def workbench_events(
    match_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    page, page_size = _page(page, page_size)
    total = int(
        await session.scalar(
            select(func.count()).select_from(MatchEvent).where(MatchEvent.match_id == match_id)
        )
        or 0
    )
    rows = (
        (
            await session.execute(
                select(MatchEvent)
                .where(MatchEvent.match_id == match_id)
                .order_by(MatchEvent.sequence)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return _paged(
        [
            {
                "id": str(item.id),
                "sequence": item.sequence,
                "event_type": item.event_type,
                "payload": item.payload,
                "created_at": item.created_at,
            }
            for item in rows
        ],
        page,
        page_size,
        total,
    )


@router.get("/matches/{match_id}/workbench/calls")
async def workbench_calls(
    match_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    page, page_size = _page(page, page_size)
    total = int(
        await session.scalar(
            select(func.count()).select_from(ExternalCall).where(ExternalCall.match_id == match_id)
        )
        or 0
    )
    rows = (
        (
            await session.execute(
                select(ExternalCall)
                .where(ExternalCall.match_id == match_id)
                .order_by(ExternalCall.started_at)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return _paged([_call_view(item) for item in rows], page, page_size, total)


@router.get("/external-calls/{call_id}")
async def external_call_detail(
    call_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    call = await session.get(ExternalCall, call_id)
    if call is None:
        raise APIError("admin_call_not_found")
    detail = _call_view(call)
    request_payload: Any = None
    response_payload: Any = None
    content_errors: list[str] = []
    for field_name, blob_id in (
        ("request", call.request_blob_id),
        ("response", call.response_blob_id),
    ):
        if blob_id is None:
            continue
        try:
            payload = await load_content_blob(session, blob_id)
        except (LookupError, ValueError, OSError):
            content_errors.append(field_name)
            continue
        if field_name == "request":
            request_payload = payload
        else:
            response_payload = payload
    detail.update(
        {
            "request": request_payload,
            "response": response_payload,
            "content_errors": content_errors,
            "technical": {
                "connection_epoch": call.connection_epoch,
                "generation_id": str(call.generation_id) if call.generation_id else None,
                "agent_generation_id": (
                    str(call.agent_generation_id) if call.agent_generation_id else None
                ),
                "request_blob_id": str(call.request_blob_id) if call.request_blob_id else None,
                "response_blob_id": str(call.response_blob_id) if call.response_blob_id else None,
            },
        }
    )
    return detail


@router.get("/matches/{match_id}/workbench/timeline")
async def workbench_timeline(
    match_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    page, page_size = _page(page, page_size)
    events = list(
        (
            await session.scalars(
                select(MatchEvent)
                .where(MatchEvent.match_id == match_id)
                .order_by(MatchEvent.sequence)
                .limit(500)
            )
        ).all()
    )
    calls = list(
        (
            await session.scalars(
                select(ExternalCall)
                .where(ExternalCall.match_id == match_id)
                .order_by(ExternalCall.started_at)
                .limit(500)
            )
        ).all()
    )
    speeches = list(
        (
            await session.scalars(
                select(Speech)
                .where(Speech.match_id == match_id)
                .order_by(Speech.created_at)
                .limit(500)
            )
        ).all()
    )
    items: list[dict[str, Any]] = [
        {
            "id": f"event:{item.id}", "type": "EVENT", "type_label": "比赛事件",
            "at": _timeline_at(item.created_at),
            "sequence": item.sequence,
            "title": item.event_type,
            "description": "比赛状态或队列发生了权威变化。", "status": "RECORDED",
            "related_id": str(item.id),
        }
        for item in events
    ]
    items.extend(_timeline_call_view(item) for item in calls)
    items.extend(
        {
            "id": f"speech:{item.id}", "type": "SPEECH", "type_label": "正式发言",
            "at": _timeline_at(item.created_at), "sequence": None,
            "title": f"{'正方' if item.side == 'AFFIRMATIVE' else '反方'}{item.seat_no}辩发言",
            "description": "文字记录中的一条发言，可继续查看关联调用。",
            "status": item.status, "related_id": str(item.id),
        }
        for item in speeches
    )
    items.sort(key=lambda item: (item["at"], item["id"]))
    total = len(items)
    start = (page - 1) * page_size
    return _paged(items[start : start + page_size], page, page_size, total)


async def _export_rows(session: AsyncSession, match_ids: list[UUID]) -> list[Match]:
    rows = list(
        (
            await session.scalars(select(Match).where(Match.id.in_(match_ids)).with_for_update())
        ).all()
    )
    if len(rows) != len(set(match_ids)):
        raise APIError("match_not_found")
    return rows


@router.post("/exports/preflight", dependencies=[Depends(require_browser_origin)])
async def export_preflight(
    payload: ExportRequest,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    rows = await _export_rows(session, payload.match_ids)
    return {
        "total_items": len(rows),
        "matches": [
            {
                "match_id": str(item.id),
                "status": item.status,
                "sequence": item.sequence,
                "context_version": item.context_version,
                "allowed": True,
            }
            for item in rows
        ],
        "include_audio": payload.include_audio,
    }


@router.post("/exports", dependencies=[Depends(require_browser_origin)])
async def create_export(
    payload: ExportRequest,
    context: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    rows = await _export_rows(session, payload.match_ids)
    export = MatchExport(
        created_by_user_id=context.user_id,
        include_audio=payload.include_audio,
        total_items=len(rows),
        scope={"kind": "ids", "match_ids": [str(item.id) for item in rows]},
    )
    session.add(export)
    await session.flush()
    for match in rows:
        session.add(
            MatchExportItem(
                export_id=export.id,
                match_id=match.id,
                match_status=match.status,
                cutoff_sequence=match.sequence,
                cutoff_context_version=match.context_version,
            )
        )
    session.add(
        BackgroundTask(
            task_type="MATCH_EXPORT",
            payload={"export_id": str(export.id)},
            status="PENDING",
            attempts=0,
            max_attempts=2,
            available_at=datetime.now(UTC),
        )
    )
    AuditLogEntry = AuditLog(
        actor_user_id=context.user_id,
        action="admin.export.created",
        target_type="match_export",
        target_id=str(export.id),
        result="SUCCESS",
        details={"total_items": len(rows), "include_audio": payload.include_audio},
    )
    session.add(AuditLogEntry)
    await session.commit()
    return {
        "id": str(export.id),
        "status": export.status,
        "total_items": export.total_items,
        "processed_items": export.processed_items,
    }


@router.get("/exports/{export_id}")
async def get_export(
    export_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    export = await session.get(MatchExport, export_id)
    if export is None:
        raise APIError("export_not_found")
    return {
        "id": str(export.id),
        "status": export.status,
        "total_items": export.total_items,
        "processed_items": export.processed_items,
        "byte_count": export.byte_count,
        "sha256": export.sha256,
        "error_code": export.error_code,
        "expires_at": export.expires_at,
        "created_at": export.created_at,
        "completed_at": export.completed_at,
    }


@router.get("/exports/{export_id}/download")
async def download_export(
    export_id: UUID,
    _: Annotated[AuthContext, Depends(get_admin_auth)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> FileResponse:
    export = await session.get(MatchExport, export_id)
    if export is None or export.status not in {"SUCCEEDED", "PARTIAL"} or not export.storage_path:
        raise APIError("export_not_ready")
    if export.expires_at and export.expires_at <= datetime.now(UTC):
        raise APIError("export_expired")
    path = Path(export.storage_path).resolve()
    if not path.is_file() or path.suffix != ".zip":
        raise APIError("export_not_ready")
    return FileResponse(
        path, media_type="application/zip", filename=f"jixia-export-{export.id}.zip"
    )

"""Freeze bounded provider projections in an independent MVCC export transaction."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..auth.errors import APIError
from ..models import (
    AuditLog,
    BackgroundTask,
    ExportProviderCaptureSnapshot,
    ExternalCall,
    Match,
    MatchExport,
    MatchExportItem,
)
from .provider_projection import capture_projections

PROVIDER_EXPORT_MAX_CALL_ROWS = 4_096
PROVIDER_EXPORT_MAX_ITEMS = 100
CAPTURE_SNAPSHOT_VERSION = 1
EXPORT_CALL_FIELDS = (
    "call_kind",
    "provider",
    "operation",
    "model",
    "voice",
    "attempt_no",
    "status",
    "speech_id",
    "generation_id",
    "decision_round_id",
    "context_version",
    "started_at",
    "first_result_latency_ms",
    "completed_latency_ms",
    "error_code",
    "source_kind",
    "source_resource_id",
    "logical_call_id",
)


async def create_frozen_export(
    factory: async_sessionmaker[AsyncSession],
    *,
    match_ids: Sequence[UUID],
    include_audio: bool,
    actor_user_id: UUID,
) -> MatchExport:
    """Create items, frozen input, task and audit atomically; never commit auth state."""
    ids = sorted(set(match_ids), key=str)
    if not ids or len(ids) > PROVIDER_EXPORT_MAX_ITEMS:
        raise APIError("export_match_rows_too_large")
    async with factory() as session:
        # connection() begins this session's transaction. Isolation is selected
        # before any application SQL, independently of the route's auth session.
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        try:
            matches = list(
                await session.scalars(select(Match).where(Match.id.in_(ids)).order_by(Match.id))
            )
            if len(matches) != len(ids):
                raise APIError("match_not_found")
            export = MatchExport(
                id=uuid4(),
                created_by_user_id=actor_user_id,
                include_audio=include_audio,
                total_items=len(matches),
                scope={"kind": "ids", "match_ids": [str(row.id) for row in matches]},
            )
            session.add(export)
            await session.flush()
            for match in matches:
                item = MatchExportItem(
                    id=uuid4(),
                    export_id=export.id,
                    match_id=match.id,
                    match_status=match.status,
                    cutoff_sequence=match.sequence,
                    cutoff_context_version=match.context_version,
                    capture_snapshot_version=CAPTURE_SNAPSHOT_VERSION,
                )
                session.add(item)
                await session.flush()
                calls = list(
                    await session.scalars(
                        select(ExternalCall)
                        .where(
                            ExternalCall.match_id == match.id,
                            ExternalCall.started_at <= export.created_at,
                            ExternalCall.source_kind == "MATCH",
                            or_(
                                ExternalCall.capture_version.is_not(None),
                                ExternalCall.capture_storage_mode == "ASYNC_V1",
                            ),
                        )
                        .order_by(ExternalCall.started_at, ExternalCall.id)
                        .limit(PROVIDER_EXPORT_MAX_CALL_ROWS + 1)
                    )
                )
                if len(calls) > PROVIDER_EXPORT_MAX_CALL_ROWS:
                    raise APIError("export_match_rows_too_large")
                projections = await capture_projections(session, calls)
                session.add_all(
                    ExportProviderCaptureSnapshot(
                        export_item_id=item.id,
                        external_call_id=call.id,
                        **{name: getattr(call, name) for name in EXPORT_CALL_FIELDS},
                        **projections[call.id],
                    )
                    for call in calls
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
            session.add(
                AuditLog(
                    actor_user_id=actor_user_id,
                    action="admin.export.created",
                    target_type="match_export",
                    target_id=str(export.id),
                    result="SUCCESS",
                    details={"total_items": len(matches), "include_audio": include_audio},
                )
            )
            await session.commit()
            return export
        except BaseException:
            await session.rollback()
            raise

"""Real MVCC export creation, compatibility and rollback with synthetic payloads."""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, update

import jx_core.data_capture.export_snapshot as snapshot_module
from export_snapshot_fixture import (
    ExportCaptureProbe,
)
from export_snapshot_fixture import (
    export_capture_probe as export_capture_probe,
)
from export_snapshot_fixture import (
    probe_database_url as probe_database_url,
)
from jx_core.admin_data_routes import ExportRequest, create_export
from jx_core.auth.errors import APIError
from jx_core.data_capture.content import store_content_blob
from jx_core.data_capture.provider_projection import CAPTURE_RAW_FIELDS
from jx_core.models import (
    AuditLog,
    BackgroundTask,
    ExportProviderCaptureSnapshot,
    ExternalCall,
    Match,
    MatchExport,
    MatchExportItem,
    ProviderCallCapture,
)

pytestmark = pytest.mark.integration


async def publish_capture(
    probe: ExportCaptureProbe, call_id: UUID, *, revision: int, new_body: bool = False
) -> None:
    async with probe.factory() as session, session.begin():
        call = await session.get(ExternalCall, call_id)
        assert call is not None
        capture = await session.scalar(
            select(ProviderCallCapture).where(ProviderCallCapture.external_call_id == call_id)
        )
        if capture is None:
            capture = ProviderCallCapture(external_call_id=call_id)
            session.add(capture)
        capture.revision = revision
        capture.schema_version = 1
        for field in CAPTURE_RAW_FIELDS:
            if field != "capture_version":
                setattr(capture, field, getattr(call, field))
        if call.capture_storage_mode != "ASYNC_V1":
            call.capture_storage_mode = "ASYNC_V1"
        if new_body:
            # Model the new independent capture table's writer boundary. It
            # updates no authoritative ExternalCall fields or Match FK.
            raw = probe.capture(revision)
            for side in ("request", "response"):
                payload = getattr(raw, side)
                assert payload is not None
                blob_id = await store_content_blob(
                    session,
                    content_kind="REQUEST" if side == "request" else "RESPONSE",
                    payload=payload.payload,
                )
                probe.blob_ids.add(blob_id)
                setattr(capture, f"{side}_blob_id", blob_id)
                for target, source in (
                    ("capture_status", "status"),
                    ("original_bytes", "original_bytes"),
                    ("stored_bytes", "stored_bytes"),
                    ("sha256", "sha256"),
                ):
                    setattr(capture, f"{side}_{target}", getattr(payload, source))
            capture.provider_request_id = raw.provider_request_id
            capture.captured_at = await session.scalar(select(func.clock_timestamp()))
            raw.close()


async def mark_async(probe: ExportCaptureProbe, call_id: UUID) -> None:
    async with probe.factory() as session, session.begin():
        call = await session.get(ExternalCall, call_id)
        assert call is not None
        call.capture_storage_mode = "ASYNC_V1"


async def test_export_route_does_not_commit_the_already_open_auth_session(
    export_capture_probe: ExportCaptureProbe,
) -> None:
    probe = export_capture_probe
    pending_id = uuid4()
    async with probe.factory() as session:
        # Open the auth transaction before invoking the real route; its pending
        # independent audit must remain uncommitted after export creation.
        await session.scalar(select(func.transaction_timestamp()))
        session.add(
            AuditLog(
                id=pending_id,
                actor_user_id=probe.context.user_id,
                action="qa.uncommitted_auth",
                target_type="qa",
                result="SUCCESS",
            )
        )
        result = await create_export(
            ExportRequest(match_ids=[probe.match_id]), probe.context, session
        )
        assert session.in_transaction()
        await session.rollback()
    async with probe.factory() as session:
        assert await session.get(AuditLog, pending_id) is None
        export = await session.get(MatchExport, UUID(result["id"]))
        assert export is not None and export.status == "QUEUED"
        item = await session.scalar(
            select(MatchExportItem).where(MatchExportItem.export_id == export.id)
        )
        assert item is not None and item.capture_snapshot_version == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExportProviderCaptureSnapshot)
                .where(ExportProviderCaptureSnapshot.export_item_id == item.id)
            )
            == 0
        )


async def test_export_row_overflow_rolls_back_items_task_and_success_audit(
    export_capture_probe: ExportCaptureProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = export_capture_probe
    for _ in range(2):
        await mark_async(probe, await probe.seed_call(captured=False))
    monkeypatch.setattr(snapshot_module, "PROVIDER_EXPORT_MAX_CALL_ROWS", 1)
    export_id = uuid4()
    monkeypatch.setattr(snapshot_module, "uuid4", lambda: export_id)
    with pytest.raises(APIError) as error:
        await probe.export()
    assert error.value.code == "export_match_rows_too_large"
    async with probe.factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(MatchExportItem)
                .where(MatchExportItem.match_id == probe.match_id)
            )
            == 0
        )
        assert await session.get(MatchExport, export_id) is None
        # No task or audit may refer to an uncommitted export id.
        assert (
            await session.scalar(
                select(func.count())
                .select_from(BackgroundTask)
                .where(
                    BackgroundTask.task_type == "MATCH_EXPORT",
                    BackgroundTask.payload["export_id"].astext == str(export_id),
                )
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "admin.export.created",
                    AuditLog.target_id == str(export_id),
                )
            )
            == 0
        )


async def test_async_gap_is_frozen_without_authority_blob_fallback_or_late_completion(
    export_capture_probe: ExportCaptureProbe,
) -> None:
    probe = export_capture_probe
    call_id = await probe.seed_call(captured=True)
    await mark_async(probe, call_id)
    frozen = await probe.export()
    first = await probe.read(frozen)
    assert len(first.rows) == 1 and first.rows[0]["id"] == str(call_id)
    row = first.rows[0]
    assert row["request"] is None and row["response"] is None
    assert row["request_capture_status"] == row["response_capture_status"] == "UNAVAILABLE"
    assert row["capture_error_code"] == "capture_not_persisted"
    await publish_capture(probe, call_id, revision=1)
    assert (await probe.read(frozen)).sha256 == first.sha256
    later = await probe.read(await probe.export())
    assert all(later.rows[0][side] is not None for side in ("request", "response"))
    assert later.rows[0]["capture_revision"] == 1


async def test_repeatable_read_freezes_capture_visible_at_first_sql_not_later_lookup(
    export_capture_probe: ExportCaptureProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = export_capture_probe
    call_id = await probe.seed_call(captured=True)
    await publish_capture(probe, call_id, revision=1)
    entered, release = asyncio.Event(), asyncio.Event()
    original = snapshot_module.capture_projections

    async def blocked_projection(session, calls):
        entered.set()
        await release.wait()
        return await original(session, calls)

    monkeypatch.setattr(snapshot_module, "capture_projections", blocked_projection)
    task = asyncio.create_task(probe.export())
    try:
        await asyncio.wait_for(entered.wait(), 0.8)
        # The independent concurrent transaction commits before the projection
        # SELECT. READ COMMITTED would see revision 2 here; RR must retain 1.
        await publish_capture(probe, call_id, revision=2, new_body=True)
        release.set()
        frozen = await asyncio.wait_for(task, 0.8)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    result = await probe.read(frozen)
    assert result.rows[0]["capture_revision"] == 1
    assert result.rows[0]["request"]["body"]["revision"] == 1
    assert result.rows[0]["response"]["body"]["revision"] == 1
    async with probe.factory() as session:
        latest = await session.scalar(
            select(ProviderCallCapture).where(ProviderCallCapture.external_call_id == call_id)
        )
        assert latest is not None and latest.revision == 2
        assert isinstance(latest.captured_at, datetime)


async def test_frozen_call_metadata_and_blob_remain_after_standalone_call_deletion(
    export_capture_probe: ExportCaptureProbe,
) -> None:
    probe = export_capture_probe
    call_id = await probe.seed_call(captured=True)
    await publish_capture(probe, call_id, revision=1)
    frozen = await probe.export()
    before = await probe.read(frozen)
    async with probe.factory() as session, session.begin():
        await session.execute(delete(ExternalCall).where(ExternalCall.id == call_id))
    after = await probe.read(frozen)
    assert after.sha256 == before.sha256
    assert after.rows[0]["id"] == str(call_id)
    assert all(after.rows[0][side] is not None for side in ("request", "response"))


async def test_export_freeze_does_not_hold_match_authority_update_lock(
    export_capture_probe: ExportCaptureProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = export_capture_probe
    entered, release = asyncio.Event(), asyncio.Event()
    original = snapshot_module.capture_projections

    async def blocked_projection(session, calls):
        # The RR transaction has read the match and inserted its item (FK KEY
        # SHARE) before this pause. Authority's non-key UPDATE must still commit.
        entered.set()
        await release.wait()
        return await original(session, calls)

    monkeypatch.setattr(snapshot_module, "capture_projections", blocked_projection)
    task = asyncio.create_task(probe.export())
    try:
        await asyncio.wait_for(entered.wait(), 0.8)
        async with asyncio.timeout(0.5), probe.factory() as session, session.begin():
            sequence = await session.scalar(
                update(Match)
                .where(Match.id == probe.match_id)
                .values(sequence=Match.sequence)
                .returning(Match.sequence)
            )
            assert sequence == 3
        release.set()
        frozen = await asyncio.wait_for(task, 0.8)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert frozen.sequence == 3 and frozen.context_version == 2
    assert (await probe.read(frozen)).rows == []

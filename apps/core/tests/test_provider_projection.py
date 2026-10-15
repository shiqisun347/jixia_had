"""Compatibility and reference rules for the independent provider store."""

from __future__ import annotations

import importlib.util
import io
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

import jx_core.data_capture.content_gc as content_gc
from jx_core.auth.errors import APIError, error_status
from jx_core.data_capture.content_gc import (
    collect_unreferenced_content_blobs,
    content_blob_unreferenced,
)
from jx_core.data_capture.provider import PROVIDER_CAPTURE_VERSION
from jx_core.data_capture.provider_projection import (
    CAPTURE_RAW_FIELDS,
    capture_projections,
    project_capture,
)
from jx_core.models import (
    ExportProviderCaptureSnapshot,
    ExternalCall,
    MatchExportItem,
    ProviderCallCapture,
)


def call(mode: str | None) -> ExternalCall:
    return ExternalCall(
        id=uuid4(),
        capture_storage_mode=mode,
        capture_version=1,
        captured_at=datetime(2026, 10, 13, tzinfo=UTC),
        provider_request_id="synthetic-legacy-id",
        request_blob_id=uuid4(),
        response_blob_id=uuid4(),
        request_capture_status="COMPLETE",
        response_capture_status="TRUNCATED",
        request_original_bytes=1,
        response_original_bytes=2,
        request_stored_bytes=1,
        response_stored_bytes=1,
        request_sha256="a" * 64,
        response_sha256="b" * 64,
        capture_error_code=None,
    )


def captured(row: ExternalCall) -> ProviderCallCapture:
    return ProviderCallCapture(
        id=uuid4(),
        external_call_id=row.id,
        revision=2,
        schema_version=PROVIDER_CAPTURE_VERSION,
        captured_at=datetime(2026, 10, 14, tzinfo=UTC),
        provider_request_id="synthetic-new-id",
        request_blob_id=uuid4(),
        response_blob_id=None,
        request_capture_status="COMPLETE",
        response_capture_status="REJECTED_SENSITIVE",
        request_original_bytes=3,
        response_original_bytes=4,
        request_stored_bytes=3,
        response_stored_bytes=0,
        request_sha256="c" * 64,
        response_sha256=None,
        capture_error_code="response_sensitive",
    )


@pytest.mark.parametrize("mode", [None, "LEGACY"])
def test_legacy_projection_preserves_existing_capture_fields(mode: str | None) -> None:
    row = call(mode)
    projected = project_capture(row, None)
    assert {name: projected[name] for name in CAPTURE_RAW_FIELDS} == {
        name: getattr(row, name) for name in CAPTURE_RAW_FIELDS
    }
    assert projected["capture_revision"] is None


@pytest.mark.parametrize("mode", [None, "LEGACY", "ASYNC_V1"])
def test_new_capture_wins_without_modifying_legacy_or_business_fields(mode: str | None) -> None:
    row = call(mode)
    original = {name: getattr(row, name) for name in CAPTURE_RAW_FIELDS}
    capture = captured(row)
    projected = project_capture(row, capture)
    assert projected["capture_version"] == capture.schema_version
    assert projected["capture_revision"] == 2
    assert projected["request_blob_id"] == capture.request_blob_id
    assert projected["response_blob_id"] is None
    assert projected["response_capture_status"] == "REJECTED_SENSITIVE"
    assert projected["response_original_bytes"] == 4
    assert projected["capture_error_code"] == "response_sensitive"
    assert {name: getattr(row, name) for name in CAPTURE_RAW_FIELDS} == original


def test_async_missing_capture_reports_gap_without_business_blob_fallback() -> None:
    row = call("ASYNC_V1")
    original_blob_ids = row.request_blob_id, row.response_blob_id
    projected = project_capture(row, None)
    assert projected["capture_version"] == PROVIDER_CAPTURE_VERSION
    assert projected["capture_revision"] is None
    assert (
        projected["request_capture_status"] == projected["response_capture_status"] == "UNAVAILABLE"
    )
    assert projected["capture_error_code"] == "capture_not_persisted"
    assert projected["request_blob_id"] is projected["response_blob_id"] is None
    assert projected["request_sha256"] is projected["response_sha256"] is None
    assert projected["captured_at"] is None
    assert (row.request_blob_id, row.response_blob_id) == original_blob_ids


class ProjectionSession:
    def __init__(self, rows: list[ProviderCallCapture]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    async def scalars(self, query: Any) -> list[ProviderCallCapture]:
        self.queries.append(str(query))
        return self.rows


@pytest.mark.asyncio
async def test_batch_projection_reads_once_without_locks_or_content_fetches() -> None:
    legacy, async_row, missing = call(None), call("ASYNC_V1"), call("ASYNC_V1")
    capture = captured(async_row)
    session = ProjectionSession([capture])
    projections = await capture_projections(
        cast(AsyncSession, session), [legacy, async_row, missing]
    )
    assert len(session.queries) == 1
    assert "FOR UPDATE" not in session.queries[0]
    assert "call_content_blobs" not in session.queries[0]
    assert projections[legacy.id]["request_blob_id"] == legacy.request_blob_id
    assert projections[async_row.id]["request_blob_id"] == capture.request_blob_id
    assert projections[missing.id]["request_blob_id"] is None


@pytest.mark.asyncio
async def test_empty_projection_avoids_database_query() -> None:
    session = ProjectionSession([])
    assert await capture_projections(cast(AsyncSession, session), []) == {}
    assert session.queries == []


def test_gc_condition_protects_all_business_raw_and_frozen_references() -> None:
    query = str(content_blob_unreferenced().compile(dialect=postgresql.dialect()))
    for table in (
        "agent_generations",
        "judge_results",
        "external_calls",
        "provider_call_captures",
        "export_provider_capture_snapshots",
    ):
        assert f"{table}.request_blob_id" in query
        assert f"{table}.response_blob_id" in query


class GcRows:
    def __init__(self, values: list[UUID]) -> None:
        self.values = values

    def all(self) -> list[UUID]:
        return self.values


class GcSession:
    def __init__(self) -> None:
        self.sizes: list[int] = []

    async def scalars(self, query: Any) -> GcRows:
        values = query.compile().params["id_1"]
        self.sizes.append(len(values))
        return GcRows(values)


class GcPairRows:
    def __init__(self, pairs: list[tuple[UUID | None, UUID | None]]) -> None:
        self.pairs = iter(pairs)
        self.closed = False

    def __aiter__(self) -> GcPairRows:
        return self

    async def __anext__(self) -> tuple[UUID | None, UUID | None]:
        try:
            return next(self.pairs)
        except StopIteration:
            raise StopAsyncIteration from None

    async def close(self) -> None:
        self.closed = True


class GcPairSession:
    def __init__(self, pairs: list[tuple[UUID | None, UUID | None]]) -> None:
        self.rows = GcPairRows(pairs)

    async def stream(self, query: Any) -> GcPairRows:
        assert query.get_execution_options()["yield_per"] == content_gc.CONTENT_GC_BATCH_SIZE
        return self.rows


@pytest.mark.asyncio
async def test_gc_allows_exact_candidate_limit_and_duplicate_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(content_gc, "CONTENT_GC_MAX_CANDIDATES", 2)
    first, second = uuid4(), uuid4()
    session = GcPairSession([(first, second), (None, first), (second, second)])
    result = await content_gc._collect_pairs(  # pyright: ignore[reportPrivateUsage]
        cast(AsyncSession, session),
        [select(ExternalCall.request_blob_id, ExternalCall.response_blob_id)],
    )
    assert result == {first, second}
    assert session.rows.closed


@pytest.mark.asyncio
async def test_gc_rejects_next_unique_candidate_and_closes_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(content_gc, "CONTENT_GC_MAX_CANDIDATES", 2)
    session = GcPairSession([(uuid4(), uuid4()), (None, uuid4())])
    with pytest.raises(APIError, match="content_cleanup_too_large"):
        await content_gc._collect_pairs(  # pyright: ignore[reportPrivateUsage]
            cast(AsyncSession, session),
            [select(ExternalCall.request_blob_id, ExternalCall.response_blob_id)],
        )
    assert session.rows.closed
    assert error_status("content_cleanup_too_large") == 413


@pytest.mark.asyncio
async def test_gc_batches_only_supplied_candidates_and_never_commits() -> None:
    session = GcSession()
    candidates = [uuid4() for _ in range(513)]
    assert await collect_unreferenced_content_blobs(cast(AsyncSession, session), candidates) == 513
    assert session.sizes == [512, 1]
    assert await collect_unreferenced_content_blobs(cast(AsyncSession, session), []) == 0
    assert session.sizes == [512, 1]


def test_snapshot_marker_distinguishes_history_from_frozen_zero_rows() -> None:
    column = MatchExportItem.__table__.c.capture_snapshot_version
    assert column.nullable and column.server_default is None
    assert ExportProviderCaptureSnapshot.__table__.c.external_call_id.foreign_keys == set()
    assert (
        next(iter(ExportProviderCaptureSnapshot.__table__.c.export_item_id.foreign_keys)).ondelete
        == "CASCADE"
    )
    assert (
        next(iter(ExportProviderCaptureSnapshot.__table__.c.request_blob_id.foreign_keys)).ondelete
        == "RESTRICT"
    )


def test_new_migration_is_additive_and_never_backfills_legacy_rows() -> None:
    migration_path = (
        Path(__file__).resolve().parents[3]
        / "migrations/versions/0046_provider_capture_snapshots.py"
    )
    spec = importlib.util.spec_from_file_location("spec300_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        migration.upgrade()
    sql = buffer.getvalue()
    assert migration.down_revision == "0045_postmatch_showcase"
    assert "CREATE TABLE provider_call_captures" in sql
    assert "CREATE TABLE export_provider_capture_snapshots" in sql
    assert "ADD COLUMN capture_storage_mode" in sql
    assert "ADD COLUMN capture_snapshot_version" in sql
    assert re.search(r"(?m)^\s*(?:UPDATE|DELETE|INSERT)\b", sql) is None

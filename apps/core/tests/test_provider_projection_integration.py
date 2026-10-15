"""Actual PostgreSQL upgrade, mixed projection, FK and shared-blob GC proofs."""

from __future__ import annotations

import hashlib
import zlib
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import create_engine, delete, inspect, select, text, update
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import jx_core.data_capture.content_gc as content_gc
from jx_core.auth.errors import APIError
from jx_core.config import Settings
from jx_core.data_capture.content import (
    canonical_payload_bytes,
    load_content_blob,
    store_content_blob,
)
from jx_core.data_capture.content_gc import (
    collect_export_blob_ids,
    collect_match_blob_ids,
    collect_unreferenced_content_blobs,
)
from jx_core.data_capture.provider_projection import capture_projections
from jx_core.models import (
    CallContentBlob,
    ExportProviderCaptureSnapshot,
    ExternalCall,
    Match,
    MatchExport,
    MatchExportItem,
    ProviderCallCapture,
    Room,
    Rule,
    User,
)
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)

pytestmark = pytest.mark.integration


@dataclass
class MigrationProbe:
    engine: Engine
    settings: Settings
    config: Config

    def upgrade(self, target: str) -> None:
        # env.py's loader is explicitly overridden; no default dotenv read.
        with patch("jx_core.config.load_settings", return_value=self.settings):
            command.upgrade(self.config, target)


@pytest.fixture
def migration_probe(agent_fault_database_url: str) -> Generator[MigrationProbe, None, None]:
    assert make_url(agent_fault_database_url).drivername == "postgresql+psycopg"
    base_engine = create_engine(agent_fault_database_url)
    schema = f"qa_provider_capture_{uuid4().hex}"
    with base_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = make_url(agent_fault_database_url).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    value = scoped_url.render_as_string(hide_password=False)
    engine = create_engine(value)
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url=SecretStr(value),
        app_env="test",
    )
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    try:
        yield MigrationProbe(engine, settings, config)
    finally:
        engine.dispose()
        try:
            with base_engine.begin() as connection:
                # Only this fixture's random, owned schema is removed.
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        finally:
            base_engine.dispose()


def test_empty_schema_upgrade_matches_new_model_columns(migration_probe: MigrationProbe) -> None:
    migration_probe.upgrade("head")
    inspector = inspect(migration_probe.engine)
    for model in (ProviderCallCapture, ExportProviderCaptureSnapshot):
        assert {column["name"] for column in inspector.get_columns(model.__tablename__)} == set(
            model.__table__.columns.keys()
        )
    with migration_probe.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0047_speech_origin"
        )


def test_legacy_upgrade_preserves_capture_and_blob_without_backfill(
    migration_probe: MigrationProbe,
) -> None:
    migration_probe.upgrade("0045_postmatch_showcase")
    call_id, blob_id = uuid4(), uuid4()
    raw = canonical_payload_bytes({"probe_id": str(call_id), "legacy": "synthetic"})
    digest, compressed = hashlib.sha256(raw).hexdigest(), zlib.compress(raw)
    with migration_probe.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO call_content_blobs "
                "(id,sha256,content_kind,serialization_version,compression,uncompressed_bytes,"
                "compressed_bytes,chunk_count) VALUES (:id,:hash,'REQUEST',1,'ZLIB',:raw,:size,1)"
            ),
            {"id": blob_id, "hash": digest, "raw": len(raw), "size": len(compressed)},
        )
        connection.execute(
            text(
                "INSERT INTO call_content_blob_chunks (blob_id,chunk_no,payload) VALUES (:id,0,:p)"
            ),
            {"id": blob_id, "p": compressed},
        )
        connection.execute(
            text(
                "INSERT INTO external_calls (id,call_kind,provider,operation,attempt_no,status,"
                "started_at,capture_version,captured_at,source_kind,request_blob_id,"
                "request_capture_status,request_original_bytes,request_stored_bytes,"
                "request_sha256) "
                "VALUES (:id,'LLM_SPEECH','SYNTHETIC','synthetic',1,'SUCCEEDED',now(),1,now(),"
                "'CONFIG_TEST',:blob,'COMPLETE',:bytes,:bytes,:hash)"
            ),
            {"id": call_id, "blob": blob_id, "bytes": len(raw), "hash": digest},
        )
    migration_probe.upgrade("head")
    with migration_probe.engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT capture_storage_mode,capture_version,request_blob_id,request_sha256 "
                    "FROM external_calls WHERE id=:id"
                ),
                {"id": call_id},
            )
            .mappings()
            .one()
        )
        assert row["capture_storage_mode"] is None and row["capture_version"] == 1
        assert row["request_blob_id"] == blob_id and row["request_sha256"] == digest
        persisted = connection.scalar(
            text("SELECT payload FROM call_content_blob_chunks WHERE blob_id=:id"), {"id": blob_id}
        )
        assert hashlib.sha256(zlib.decompress(persisted)).hexdigest() == digest
        assert connection.scalar(text("SELECT count(*) FROM provider_call_captures")) == 0
        assert (
            connection.scalar(text("SELECT count(*) FROM export_provider_capture_snapshots")) == 0
        )


@dataclass
class ProjectionDb:
    factory: async_sessionmaker[AsyncSession]
    user_id: UUID
    rule_id: UUID
    room_ids: list[UUID] = field(default_factory=lambda: list[UUID]())
    match_ids: list[UUID] = field(default_factory=lambda: list[UUID]())
    call_ids: list[UUID] = field(default_factory=lambda: list[UUID]())
    export_ids: list[UUID] = field(default_factory=lambda: list[UUID]())
    blob_ids: set[UUID] = field(default_factory=lambda: set[UUID]())

    async def match(self) -> UUID:
        room_id, match_id = uuid4(), uuid4()
        async with self.factory() as session, session.begin():
            session.add(
                Room(
                    id=room_id,
                    code=room_id.hex[:6].upper(),
                    title="Synthetic capture room",
                    label="QA",
                    topic_snapshot={},
                    rule_id=self.rule_id,
                    rule_snapshot={"side_size": 4},
                    organizer_user_id=self.user_id,
                    status="FINISHED",
                )
            )
            await session.flush()
            session.add(Match(id=match_id, room_id=room_id, status="FINISHED", runtime_snapshot={}))
        self.room_ids.append(room_id)
        self.match_ids.append(match_id)
        return match_id

    async def blob(self, marker: str, *, response: bool = False) -> UUID:
        async with self.factory() as session, session.begin():
            blob_id = await store_content_blob(
                session,
                content_kind="RESPONSE" if response else "REQUEST",
                payload={"probe_id": str(self.user_id), "marker": marker},
            )
        self.blob_ids.add(blob_id)
        return blob_id

    async def call(
        self, match_id: UUID | None, *, mode: str | None = None, request_blob_id: UUID | None = None
    ) -> UUID:
        call_id = uuid4()
        async with self.factory() as session, session.begin():
            session.add(
                ExternalCall(
                    id=call_id,
                    match_id=match_id,
                    call_kind="LLM_SPEECH",
                    provider="SYNTHETIC",
                    operation="synthetic",
                    attempt_no=1,
                    status="STARTED",
                    started_at=datetime.now(UTC),
                    source_kind="MATCH" if match_id is not None else "OPS_PROBE",
                    capture_storage_mode=mode,
                    capture_version=1 if mode != "ASYNC_V1" else None,
                    request_blob_id=request_blob_id,
                    request_capture_status="COMPLETE" if request_blob_id is not None else None,
                )
            )
        self.call_ids.append(call_id)
        return call_id

    async def capture(
        self,
        call_id: UUID,
        *,
        request_blob_id: UUID | None = None,
        response_blob_id: UUID | None = None,
    ) -> None:
        async with self.factory() as session, session.begin():
            session.add(
                ProviderCallCapture(
                    external_call_id=call_id,
                    request_blob_id=request_blob_id,
                    response_blob_id=response_blob_id,
                    request_capture_status="COMPLETE"
                    if request_blob_id is not None
                    else "UNAVAILABLE",
                    response_capture_status="COMPLETE"
                    if response_blob_id is not None
                    else "UNAVAILABLE",
                )
            )

    async def snapshot(self, match_id: UUID, call_id: UUID, request_blob_id: UUID) -> UUID:
        export_id, item_id = uuid4(), uuid4()
        async with self.factory() as session, session.begin():
            session.add(MatchExport(id=export_id, created_by_user_id=self.user_id, total_items=1))
            await session.flush()
            session.add(
                MatchExportItem(
                    id=item_id,
                    export_id=export_id,
                    match_id=match_id,
                    match_status="FINISHED",
                    cutoff_sequence=0,
                    cutoff_context_version=0,
                    capture_snapshot_version=1,
                )
            )
            await session.flush()
            session.add(
                ExportProviderCaptureSnapshot(
                    export_item_id=item_id,
                    external_call_id=call_id,
                    call_kind="LLM_SPEECH",
                    provider="SYNTHETIC",
                    operation="synthetic",
                    attempt_no=1,
                    status="STARTED",
                    started_at=datetime.now(UTC),
                    capture_version=1,
                    capture_revision=1,
                    request_blob_id=request_blob_id,
                    request_capture_status="COMPLETE",
                    source_kind="MATCH",
                )
            )
        self.export_ids.append(export_id)
        return export_id


@pytest.fixture
async def projection_db(agent_fault_database_url: str) -> AsyncGenerator[ProjectionDb, None]:
    engine = create_async_engine(
        agent_fault_database_url,
        connect_args={"options": "-c statement_timeout=1500 -c lock_timeout=300"},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id, rule_id = uuid4(), uuid4()
    probe = ProjectionDb(factory, user_id, rule_id)
    try:
        async with factory() as session, session.begin():
            name = f"captureqa-{user_id.hex[:12]}"
            session.add(
                User(
                    id=user_id,
                    username=name,
                    username_normalized=name,
                    real_name="Synthetic capture QA",
                    password_hash="not-used",
                    role="USER",
                )
            )
            await session.flush()
            session.add(
                Rule(
                    id=rule_id,
                    rule_key=f"captureqa-{rule_id.hex}",
                    version=1,
                    name="Synthetic capture QA",
                    side_size=4,
                    estimated_seconds=1,
                    created_by=user_id,
                )
            )
        yield probe
    finally:
        try:
            async with factory() as session, session.begin():
                await session.execute(
                    delete(MatchExport).where(MatchExport.id.in_(probe.export_ids))
                )
                await session.execute(
                    delete(ExternalCall).where(ExternalCall.id.in_(probe.call_ids))
                )
                await session.execute(delete(Match).where(Match.id.in_(probe.match_ids)))
                await session.execute(delete(Room).where(Room.id.in_(probe.room_ids)))
                await session.execute(delete(Rule).where(Rule.id == rule_id))
                await session.execute(delete(User).where(User.id == user_id))
                await collect_unreferenced_content_blobs(session, probe.blob_ids)
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_candidate_limit_rolls_back_owned_match_deletion_transaction(
    projection_db: ProjectionDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = projection_db
    match_id = await probe.match()
    blobs = [await probe.blob(f"limit-{index}") for index in range(3)]
    call_ids = [await probe.call(match_id, request_blob_id=blob_id) for blob_id in blobs]
    monkeypatch.setattr(content_gc, "CONTENT_GC_MAX_CANDIDATES", 2)
    with pytest.raises(APIError, match="content_cleanup_too_large"):
        async with probe.factory() as session, session.begin():
            match = await session.scalar(
                select(Match).where(Match.id == match_id).with_for_update()
            )
            assert match is not None and match.status == "FINISHED"
            candidates = await collect_match_blob_ids(session, match_id)
            await session.delete(match)
            await session.flush()
            await collect_unreferenced_content_blobs(session, candidates)
    # Neither cascaded owners nor their immutable content may disappear on the
    # bounded collector failure. A separate session sees the original commit.
    async with probe.factory() as observer:
        assert await observer.get(Match, match_id) is not None
        remaining_calls = set(
            await observer.scalars(select(ExternalCall.id).where(ExternalCall.id.in_(call_ids)))
        )
        assert remaining_calls == set(call_ids)
        for blob_id in blobs:
            assert await load_content_blob(observer, blob_id) is not None


@pytest.mark.asyncio
async def test_actual_mixed_projection_never_substitutes_async_business_blob(
    projection_db: ProjectionDb,
) -> None:
    probe = projection_db
    business, raw = await probe.blob("business"), await probe.blob("raw")
    legacy = await probe.call(None, request_blob_id=business)
    new = await probe.call(None, mode="ASYNC_V1", request_blob_id=business)
    missing = await probe.call(None, mode="ASYNC_V1", request_blob_id=business)
    await probe.capture(new, request_blob_id=raw)
    async with probe.factory() as session:
        rows = list(
            await session.scalars(
                select(ExternalCall).where(ExternalCall.id.in_([legacy, new, missing]))
            )
        )
        projected = await capture_projections(session, rows)
        assert projected[legacy]["request_blob_id"] == business
        assert projected[new]["request_blob_id"] == raw
        assert projected[new]["capture_revision"] == 1
        assert projected[missing]["request_blob_id"] is None
        assert projected[missing]["capture_error_code"] == "capture_not_persisted"
        assert (await load_content_blob(session, raw))["marker"] == "raw"
        missing_call = await session.get(ExternalCall, missing)
        assert missing_call is not None and missing_call.request_blob_id == business


@pytest.mark.asyncio
async def test_capture_fk_is_compatible_with_authoritative_nonkey_call_update(
    projection_db: ProjectionDb,
) -> None:
    probe = projection_db
    call_id = await probe.call(None, mode="ASYNC_V1")
    async with probe.factory() as capture_session, capture_session.begin():
        capture_session.add(ProviderCallCapture(external_call_id=call_id))
        await capture_session.flush()
        # The uncommitted insert still owns the FK KEY SHARE, while the
        # ordinary successful-result UPDATE must commit in another session.
        async with probe.factory() as authority, authority.begin():
            await authority.execute(text("SET LOCAL lock_timeout = '200ms'"))
            await authority.execute(
                update(ExternalCall)
                .where(ExternalCall.id == call_id)
                .values(
                    status="SUCCEEDED",
                    completed_at=datetime.now(UTC),
                    completion_tokens=2,
                )
            )
        assert capture_session.in_transaction()
    async with probe.factory() as session:
        call = await session.get(ExternalCall, call_id)
        assert call is not None and call.status == "SUCCEEDED" and call.completion_tokens == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("protector", ["legacy", "other_match", "nonmatch", "snapshot"])
async def test_match_delete_gc_protects_shared_references_then_reclaims_last_reference(
    projection_db: ProjectionDb,
    protector: Literal["legacy", "other_match", "nonmatch", "snapshot"],
) -> None:
    probe = projection_db
    first, second = await probe.match(), await probe.match()
    shared = await probe.blob("shared")
    raw_only = await probe.blob("raw-owned", response=True)
    frozen_only = await probe.blob("frozen-owned")
    unrelated = await probe.blob("unrelated-unreferenced")
    first_call = await probe.call(first, request_blob_id=shared)
    await probe.capture(first_call, response_blob_id=raw_only)
    await probe.snapshot(first, first_call, frozen_only)
    other_call = await probe.call(
        None if protector == "nonmatch" else second,
        mode=None if protector == "legacy" else "ASYNC_V1",
        request_blob_id=shared if protector == "legacy" else None,
    )
    other_export: UUID | None = None
    if protector in {"other_match", "nonmatch"}:
        await probe.capture(other_call, request_blob_id=shared)
    elif protector == "snapshot":
        other_export = await probe.snapshot(second, other_call, shared)
        # The frozen scalar call ID is intentionally independent of live calls.
        async with probe.factory() as session, session.begin():
            await session.execute(delete(ExternalCall).where(ExternalCall.id == other_call))
    async with probe.factory() as session, session.begin():
        match = await session.get(Match, first, with_for_update=True)
        assert match is not None
        candidates = await collect_match_blob_ids(session, first)
        assert candidates == {shared, raw_only, frozen_only}
        await session.delete(match)
        await session.flush()
        assert await collect_unreferenced_content_blobs(session, candidates) == 2
    async with probe.factory() as session:
        assert await session.get(CallContentBlob, raw_only) is None
        assert await session.get(CallContentBlob, frozen_only) is None
        assert await session.get(CallContentBlob, unrelated) is not None
        assert (await load_content_blob(session, shared))["marker"] == "shared"
    async with probe.factory() as session, session.begin():
        if other_export is not None:
            candidates = await collect_export_blob_ids(session, other_export)
            assert candidates == {shared}
            await session.execute(delete(MatchExport).where(MatchExport.id == other_export))
        else:
            candidates = {shared}
            await session.execute(delete(ExternalCall).where(ExternalCall.id == other_call))
        await session.flush()
        assert await collect_unreferenced_content_blobs(session, candidates) == 1
    async with probe.factory() as session:
        assert await session.get(CallContentBlob, shared) is None
        assert await session.get(CallContentBlob, unrelated) is not None

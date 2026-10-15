"""Owned strict-local PostgreSQL export fixture; no dotenv or real provider."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from jx_core.admin_data_routes import ExportRequest, create_export
from jx_core.auth.session import AuthContext
from jx_core.config import Settings
from jx_core.data_capture.provider import PROVIDER_CAPTURE_VERSION, ProviderCallCapture
from jx_core.data_capture.provider_persistence import persist_provider_capture
from jx_core.models import (
    AuditLog,
    BackgroundTask,
    CallContentBlob,
    ExternalCall,
    Match,
    MatchExport,
    MatchExportItem,
    Room,
    Rule,
    User,
)
from jx_jobs import export_worker  # pyright: ignore[reportMissingTypeStubs]

pytestmark = pytest.mark.integration
# Exercise the exact private package reader used by the production worker.
# pyright: reportPrivateUsage=false

OPERATION_SECONDS = 1.2


@pytest.fixture(scope="module")
def probe_database_url() -> str:
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("explicit RUN_DATABASE_INTEGRATION=1 is required for frozen-export tests")
    value = os.environ.get("TEST_DATABASE_URL")
    if not value or value == os.environ.get("DATABASE_URL"):
        pytest.fail("an independent TEST_DATABASE_URL is required")
    parsed = make_url(value)
    if (
        parsed.drivername != "postgresql+psycopg"
        or parsed.host not in {"127.0.0.1", "localhost"}
        or parsed.database != "jx_debate_qa_agent_faults"
    ):
        pytest.fail("frozen-export tests require the dedicated local QA PostgreSQL database")
    settings = Settings(_env_file=None, database_url=SecretStr(value), app_env="test")  # pyright: ignore[reportCallIssue]
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    with patch("jx_core.config.load_settings", return_value=settings):
        command.upgrade(config, "head")
    return value


@dataclass(frozen=True)
class FrozenExport:
    export_id: UUID
    cutoff_at: datetime
    sequence: int
    context_version: int
    item_id: UUID
    capture_snapshot_version: int


@dataclass(frozen=True)
class ResearchRead:
    rows: list[dict[str, Any]]
    sha256: str


@dataclass
class ExportCaptureProbe:
    factory: async_sessionmaker[AsyncSession]
    match_id: UUID
    context: AuthContext
    blob_ids: set[UUID] = field(default_factory=lambda: set[UUID]())

    def capture(self, revision: int) -> ProviderCallCapture:
        # The unique owned match ID prevents deduplication against other
        # fixtures, so cleanup cannot remove someone else's content blob.
        capture = ProviderCallCapture(provider_request_id=f"synthetic-revision-{revision}")
        capture.capture_request(
            transport="sse",
            url="https://synthetic.invalid/v1/chat/completions",
            body={"probe_id": str(self.match_id), "revision": revision, "input": "synthetic"},
        )
        capture.capture_response(
            transport="sse",
            url="https://synthetic.invalid/v1/chat/completions",
            body={"probe_id": str(self.match_id), "revision": revision, "output": "synthetic"},
        )
        return capture

    async def seed_call(self, *, captured: bool) -> UUID:
        call_id = uuid4()
        async with asyncio.timeout(OPERATION_SECONDS), self.factory() as session:
            async with session.begin():
                now = await session.scalar(select(func.clock_timestamp()))
                assert isinstance(now, datetime)
                call = ExternalCall(
                    id=call_id,
                    match_id=self.match_id,
                    call_kind="LLM_SPEECH",
                    provider="OPENAI_COMPATIBLE",
                    operation="chat.completions.stream",
                    model="synthetic-model",
                    attempt_no=1,
                    status="SUCCEEDED",
                    source_kind="MATCH",
                    source_resource_id=str(self.match_id),
                    logical_call_id=uuid4(),
                    started_at=now,
                    completed_at=now,
                    completion_tokens=1,
                )
                session.add(call)
                if captured:
                    await self.persist_capture(session, call, revision=1)
        return call_id

    def remember_blobs(self, call: ExternalCall) -> None:
        for blob_id in (call.request_blob_id, call.response_blob_id):
            assert blob_id is not None
            self.blob_ids.add(blob_id)

    async def persist_capture(
        self, session: AsyncSession, call: ExternalCall, *, revision: int
    ) -> None:
        assert await persist_provider_capture(session, call, self.capture(revision))
        # These synthetic legacy rows model capture revisions relative to
        # the route's PostgreSQL cutoff. Use the same database clock: the
        # local Colima clock may lead the Python host by tens of milliseconds.
        # The helper and actual immutable blob storage still run unchanged.
        captured_at = await session.scalar(select(func.clock_timestamp()))
        assert isinstance(captured_at, datetime)
        call.captured_at = captured_at
        self.remember_blobs(call)

    async def write_capture(self, call_id: UUID, *, revision: int) -> None:
        async with asyncio.timeout(OPERATION_SECONDS), self.factory() as session:
            async with session.begin():
                call = await session.get(ExternalCall, call_id, with_for_update=True)
                assert call is not None and call.match_id == self.match_id
                await self.persist_capture(session, call, revision=revision)

    async def export(self) -> FrozenExport:
        async with asyncio.timeout(OPERATION_SECONDS), self.factory() as session:
            result = await create_export(
                ExportRequest(match_ids=[self.match_id], include_audio=False),
                self.context,
                session,
            )
            export_id = UUID(result["id"])
        async with asyncio.timeout(OPERATION_SECONDS), self.factory() as session:
            export = await session.get(MatchExport, export_id)
            item = await session.scalar(
                select(MatchExportItem).where(
                    MatchExportItem.export_id == export_id,
                    MatchExportItem.match_id == self.match_id,
                )
            )
            audit = await session.scalar(
                select(AuditLog.id).where(
                    AuditLog.action == "admin.export.created",
                    AuditLog.target_id == str(export_id),
                    AuditLog.actor_user_id == self.context.user_id,
                    AuditLog.result == "SUCCESS",
                )
            )
            assert export is not None and item is not None and audit is not None
            assert export.status == "QUEUED" and export.total_items == 1
            assert item.capture_snapshot_version == 1
            return FrozenExport(
                export_id,
                export.created_at,
                item.cutoff_sequence,
                item.cutoff_context_version,
                item.id,
                item.capture_snapshot_version,
            )

    async def read(self, frozen: FrozenExport) -> ResearchRead:
        async with asyncio.timeout(OPERATION_SECONDS), self.factory() as session:
            files = await export_worker._match_package(
                session,
                match_id=self.match_id,
                cutoff_sequence=frozen.sequence,
                cutoff_context_version=frozen.context_version,
                cutoff_at=frozen.cutoff_at,
                include_audio=False,
                audio_roots=[],
                export_item_id=frozen.item_id,
                capture_snapshot_version=frozen.capture_snapshot_version,
            )
        manifest = json.loads(files["manifest.json"])
        assert manifest["provider_call_capture_schema_version"] == PROVIDER_CAPTURE_VERSION
        assert manifest["cutoff_sequence"] == frozen.sequence
        assert manifest["cutoff_context_version"] == frozen.context_version
        body = files["agent-calls.jsonl"]
        rows = [json.loads(line) for line in body.splitlines() if line.strip()]
        return ResearchRead(rows, hashlib.sha256(body).hexdigest())

    async def call_state(self, call_id: UUID) -> dict[str, Any]:
        async with asyncio.timeout(OPERATION_SECONDS), self.factory() as session:
            call = await session.get(ExternalCall, call_id)
            assert call is not None and call.match_id == self.match_id
            return {
                "started_at": call.started_at,
                "captured_at": call.captured_at,
                "request_blob_id": call.request_blob_id,
                "response_blob_id": call.response_blob_id,
            }


@pytest.fixture
async def export_capture_probe(
    probe_database_url: str,
) -> AsyncGenerator[ExportCaptureProbe, None]:
    # The imported strict local fixture disables dotenv when migrating.
    engine = create_async_engine(
        probe_database_url,
        connect_args={"options": "-c statement_timeout=900 -c lock_timeout=300"},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    rule_id, room_id, match_id = uuid4(), uuid4(), uuid4()
    owned_admin_id: UUID | None = None
    probe: ExportCaptureProbe | None = None
    try:
        async with asyncio.timeout(OPERATION_SECONDS), factory() as session:
            async with session.begin():
                # Reuse the database's unique ADMIN unchanged. No auth data,
                # password, role or session on a borrowed account is modified.
                admin_id = await session.scalar(select(User.id).where(User.role == "ADMIN"))
                if admin_id is None:
                    admin_id = owned_admin_id = uuid4()
                    username = f"export-probe-{admin_id.hex[:12]}"
                    session.add(
                        User(
                            id=admin_id,
                            username=username,
                            username_normalized=username,
                            real_name="Synthetic export admin",
                            password_hash="not-used",
                            role="ADMIN",
                        )
                    )
                    await session.flush()
                session.add(
                    Rule(
                        id=rule_id,
                        rule_key=f"export-probe-{rule_id.hex}",
                        version=1,
                        name="Synthetic export rule",
                        side_size=4,
                        estimated_seconds=1,
                        created_by=admin_id,
                    )
                )
                await session.flush()
                session.add(
                    Room(
                        id=room_id,
                        code=room_id.hex[:6].upper(),
                        title="Synthetic export room",
                        label="QA",
                        topic_snapshot={"title": "Synthetic topic"},
                        rule_id=rule_id,
                        rule_snapshot={"side_size": 4},
                        organizer_user_id=admin_id,
                        status="FINISHED",
                    )
                )
                await session.flush()
                session.add(
                    Match(
                        id=match_id,
                        room_id=room_id,
                        status="FINISHED",
                        sequence=3,
                        context_version=2,
                        runtime_snapshot={},
                    )
                )
            probe = ExportCaptureProbe(
                factory, match_id, AuthContext(admin_id, "ADMIN", uuid4(), False)
            )
        yield probe
    finally:
        try:
            async with asyncio.timeout(OPERATION_SECONDS), factory() as session:
                async with session.begin():
                    export_ids = list(
                        await session.scalars(
                            select(MatchExportItem.export_id).where(
                                MatchExportItem.match_id == match_id
                            )
                        )
                    )
                    if export_ids:
                        text_ids = [str(item) for item in export_ids]
                        await session.execute(
                            delete(BackgroundTask).where(
                                BackgroundTask.task_type == "MATCH_EXPORT",
                                BackgroundTask.payload["export_id"].astext.in_(text_ids),
                            )
                        )
                        await session.execute(
                            delete(AuditLog).where(
                                AuditLog.action == "admin.export.created",
                                AuditLog.target_id.in_(text_ids),
                            )
                        )
                        await session.execute(
                            delete(MatchExport).where(MatchExport.id.in_(export_ids))
                        )
                    await session.execute(delete(Match).where(Match.id == match_id))
                    await session.execute(delete(Room).where(Room.id == room_id))
                    await session.execute(delete(Rule).where(Rule.id == rule_id))
                    if probe is not None and probe.blob_ids:
                        await session.execute(
                            delete(CallContentBlob).where(CallContentBlob.id.in_(probe.blob_ids))
                        )
                    if owned_admin_id is not None:
                        await session.execute(delete(User).where(User.id == owned_admin_id))
        finally:
            async with asyncio.timeout(OPERATION_SECONDS):
                await engine.dispose()

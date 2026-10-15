"""Actual admin HTTP readers, raw projection, audit and permission PostgreSQL proofs.

Only authentication resolution is synthetic. The admin permission dependency,
route SQL, payload loading, pagination and audit commits execute unchanged.
The imported fixture enforces the dedicated localhost QA database, disables
dotenv and cleans only its owned records; this file imports no scripts package.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, select, update

from jx_core.app import create_app
from jx_core.auth.dependencies import get_current_auth
from jx_core.auth.errors import APIError
from jx_core.auth.session import AuthContext
from jx_core.config import Settings
from jx_core.data_capture.content import load_content_blob, store_content_blob
from jx_core.models import AuditLog, ExternalCall, ProviderCallCapture
from test_provider_projection_integration import (
    ProjectionDb,
)
from test_provider_projection_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_provider_projection_integration import (
    projection_db as projection_db,
)

pytestmark = pytest.mark.integration


@dataclass
class AdminReader:
    db: ProjectionDb
    app: FastAPI
    client: AsyncClient
    context: AuthContext | None

    async def blob(self, payload: dict[str, Any], *, response: bool = False) -> UUID:
        async with self.db.factory() as session, session.begin():
            blob_id = await store_content_blob(
                session, content_kind="RESPONSE" if response else "REQUEST", payload=payload
            )
        self.db.blob_ids.add(blob_id)
        return blob_id

    async def audits(self, call_id: UUID) -> list[AuditLog]:
        async with self.db.factory() as session:
            return list(
                await session.scalars(
                    select(AuditLog)
                    .where(
                        AuditLog.target_type == "external_call",
                        AuditLog.target_id == str(call_id),
                    )
                    .order_by(AuditLog.created_at, AuditLog.id)
                )
            )


@pytest.fixture
async def admin_reader(
    projection_db: ProjectionDb, agent_fault_database_url: str, request: pytest.FixtureRequest
) -> AsyncGenerator[AdminReader, None]:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=SecretStr(agent_fault_database_url),
        provider_capture_enabled=cast(bool, getattr(request, "param", True)),
    )
    runtime = SimpleNamespace(
        settings=settings, database=SimpleNamespace(session_factory=projection_db.factory)
    )
    # No lifespan: provider workers and production recovery are not started.
    app = create_app(settings, runtime=cast(Any, runtime))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        probe = AdminReader(
            projection_db,
            app,
            client,
            AuthContext(projection_db.user_id, "ADMIN", uuid4(), False),
        )

        def current_auth() -> AuthContext:
            if probe.context is None:
                raise APIError("not_authenticated")
            return probe.context

        # Keep the real get_admin_auth/require_admin dependency in every request.
        app.dependency_overrides[get_current_auth] = current_auth
        try:
            yield probe
        finally:
            async with projection_db.factory() as session, session.begin():
                await session.execute(
                    delete(AuditLog).where(
                        AuditLog.target_type == "external_call",
                        AuditLog.target_id.in_([str(item) for item in projection_db.call_ids]),
                    )
                )


@pytest.mark.asyncio
async def test_async_gap_is_listed_without_exposing_complete_business_blobs_and_audit_appends(
    admin_reader: AdminReader,
) -> None:
    probe = admin_reader
    match_id = await probe.db.match()
    business_request = {"messages": [{"role": "user", "content": "完整业务上下文" * 1024}]}
    business_response = {"full_text": "完整发言正文" * 1024, "finish_reason": "stop"}
    request_id = await probe.blob(business_request)
    response_id = await probe.blob(business_response, response=True)
    call_id = await probe.db.call(match_id, mode="ASYNC_V1", request_blob_id=request_id)
    async with probe.db.factory() as session, session.begin():
        await session.execute(
            update(ExternalCall)
            .where(ExternalCall.id == call_id)
            .values(
                status="SUCCEEDED", response_blob_id=response_id, completed_at=datetime.now(UTC)
            )
        )

    response = await probe.client.get(
        "/api/admin/external-calls", params={"match_id": str(match_id)}
    )
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 1
    listed = page["items"][0]
    assert listed["id"] == str(call_id) and listed["status"] == "SUCCEEDED"
    assert listed["capture_error_code"] == "capture_not_persisted"
    assert listed["request_capture_status"] == listed["response_capture_status"] == "UNAVAILABLE"
    assert listed["has_request"] is listed["has_response"] is False
    assert "request" not in listed and "response" not in listed
    assert await probe.audits(call_id) == []

    first_audit_ids: set[UUID] = set()
    for read_no in (1, 2):
        response = await probe.client.get(f"/api/admin/external-calls/{call_id}")
        assert response.status_code == 200
        detail = response.json()
        assert detail["request"] is detail["response"] is None
        assert detail["technical"]["request_blob_id"] is None
        assert detail["technical"]["response_blob_id"] is None
        assert detail["content_errors"] == []
        assert detail["capture_error_code"] == "capture_not_persisted"
        audits = await probe.audits(call_id)
        assert len(audits) == read_no
        assert all(
            row.action == "admin.external_call.viewed"
            and row.actor_user_id == probe.db.user_id
            and row.result == "SUCCESS"
            for row in audits
        )
        if read_no == 1:
            first_audit_ids = {row.id for row in audits}
        else:
            assert first_audit_ids < {row.id for row in audits}
    async with probe.db.factory() as session:
        call = await session.get(ExternalCall, call_id)
        assert call is not None and call.request_blob_id == request_id
        assert call.response_blob_id == response_id and call.status == "SUCCEEDED"
        assert await load_content_blob(session, request_id) == business_request
        assert await load_content_blob(session, response_id) == business_response


@pytest.mark.asyncio
@pytest.mark.parametrize("admin_reader", [True, False], indirect=True)
async def test_new_capture_overrides_legacy_metadata_and_bodies_without_mutating_authority(
    admin_reader: AdminReader,
) -> None:
    probe = admin_reader
    legacy_request = await probe.db.blob("legacy-request")
    legacy_response = await probe.db.blob("legacy-response", response=True)
    raw_request = await probe.db.blob("new-provider-request")
    raw_response = await probe.db.blob("new-provider-response", response=True)
    call_id = await probe.db.call(None, mode="ASYNC_V1", request_blob_id=legacy_request)
    await probe.db.capture(call_id, request_blob_id=raw_request, response_blob_id=raw_response)
    new_provider_id = f"new-{uuid4()}"
    async with probe.db.factory() as session, session.begin():
        await session.execute(
            update(ExternalCall)
            .where(ExternalCall.id == call_id)
            .values(
                response_blob_id=legacy_response,
                provider_request_id="legacy-provider-id",
                request_sha256="1" * 64,
                request_original_bytes=99,
                request_stored_bytes=99,
                capture_error_code="legacy-error",
                capture_version=1,
                status="SUCCEEDED",
            )
        )
        await session.execute(
            update(ProviderCallCapture)
            .where(ProviderCallCapture.external_call_id == call_id)
            .values(
                provider_request_id=new_provider_id,
                request_sha256="2" * 64,
                request_original_bytes=123,
                request_stored_bytes=123,
                response_original_bytes=456,
                response_stored_bytes=456,
            )
        )
    response = await probe.client.get(f"/api/admin/external-calls/{call_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["request"]["marker"] == "new-provider-request"
    assert detail["response"]["marker"] == "new-provider-response"
    assert detail["technical"]["request_blob_id"] == str(raw_request)
    assert detail["technical"]["response_blob_id"] == str(raw_response)
    assert detail["provider_request_id"] == new_provider_id
    assert detail["request_sha256"] == "2" * 64 and detail["request_original_bytes"] == 123
    assert detail["capture_error_code"] is None
    assert detail["request_capture_status"] == detail["response_capture_status"] == "COMPLETE"
    assert len(await probe.audits(call_id)) == 1
    async with probe.db.factory() as session:
        call = await session.get(ExternalCall, call_id)
        assert call is not None and call.request_blob_id == legacy_request
        assert call.response_blob_id == legacy_response
        assert call.provider_request_id == "legacy-provider-id" and call.request_sha256 == "1" * 64


@pytest.mark.asyncio
async def test_provider_request_filter_uses_projection_before_count_and_pagination(
    admin_reader: AdminReader,
) -> None:
    probe = admin_reader
    match_id = await probe.db.match()
    provider_id = f"filter-{uuid4()}"
    expected: list[UUID] = []
    base_time = datetime.now(UTC) - timedelta(minutes=1)
    for index in range(13):
        # 11 matches mix new and legacy. The last two must not match: one
        # missing async row and one new row which overrides the matching legacy ID.
        mode = "ASYNC_V1" if index % 2 == 0 or index >= 11 else "LEGACY"
        call_id = await probe.db.call(match_id, mode=mode)
        if index < 11:
            expected.append(call_id)
        async with probe.db.factory() as session, session.begin():
            await session.execute(
                update(ExternalCall)
                .where(ExternalCall.id == call_id)
                .values(
                    provider_request_id=provider_id, started_at=base_time + timedelta(seconds=index)
                )
            )
            if mode == "ASYNC_V1" and index != 11:
                session.add(
                    ProviderCallCapture(
                        external_call_id=call_id,
                        provider_request_id=provider_id if index < 11 else "new-nonmatching-id",
                        request_capture_status="UNAVAILABLE",
                        response_capture_status="UNAVAILABLE",
                    )
                )
    pages: list[list[str]] = []
    for page in (1, 2):
        response = await probe.client.get(
            "/api/admin/external-calls",
            params={
                "match_id": str(match_id),
                "provider_request_id": provider_id,
                "page_size": 10,
                "page": page,
                "order": "asc",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 11 and body["total_pages"] == 2
        assert body["page"] == page and body["page_size"] == 10
        assert all(item["provider_request_id"] == provider_id for item in body["items"])
        pages.append([item["id"] for item in body["items"]])
    assert len(pages[0]) == 10 and len(pages[1]) == 1
    assert pages[0] + pages[1] == [str(item) for item in expected]


@pytest.mark.asyncio
async def test_legacy_history_retains_raw_bodies_and_metadata(admin_reader: AdminReader) -> None:
    probe = admin_reader
    request_id = await probe.db.blob("legacy-raw-request")
    response_id = await probe.db.blob("legacy-raw-response", response=True)
    call_id = await probe.db.call(None, mode=None, request_blob_id=request_id)
    provider_id = f"legacy-{uuid4()}"
    async with probe.db.factory() as session, session.begin():
        await session.execute(
            update(ExternalCall)
            .where(ExternalCall.id == call_id)
            .values(
                response_blob_id=response_id,
                response_capture_status="COMPLETE",
                provider_request_id=provider_id,
                request_sha256="3" * 64,
                status="FAILED",
                error_code="llm_provider_failed",
            )
        )
    response = await probe.client.get(f"/api/admin/external-calls/{call_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["request"]["marker"] == "legacy-raw-request"
    assert detail["response"]["marker"] == "legacy-raw-response"
    assert detail["technical"]["request_blob_id"] == str(request_id)
    assert detail["technical"]["response_blob_id"] == str(response_id)
    assert detail["provider_request_id"] == provider_id and detail["request_sha256"] == "3" * 64
    assert detail["status"] == "FAILED" and detail["error_code"] == "llm_provider_failed"
    assert len(await probe.audits(call_id)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["MATCH", "HOST_AUDIO", "CONFIG_TEST", "OPS_PROBE"])
async def test_source_filter_preserves_async_gaps_and_authority_source(
    admin_reader: AdminReader, source: str
) -> None:
    probe = admin_reader
    match_id = await probe.db.match()
    ids: dict[str, UUID] = {}
    async with probe.db.factory() as session, session.begin():
        for source_kind in ("MATCH", "HOST_AUDIO", "CONFIG_TEST", "OPS_PROBE"):
            call_id = uuid4()
            ids[source_kind] = call_id
            probe.db.call_ids.append(call_id)
            session.add(
                ExternalCall(
                    id=call_id,
                    match_id=match_id,
                    call_kind="LLM_SPEECH",
                    provider="SYNTHETIC",
                    operation="synthetic",
                    attempt_no=1,
                    status="SUCCEEDED",
                    started_at=datetime.now(UTC),
                    capture_storage_mode="ASYNC_V1",
                    source_kind=source_kind,
                )
            )
    response = await probe.client.get(
        "/api/admin/external-calls",
        params={"match_id": str(match_id), "source_kind": source.lower()},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1 and body["items"][0]["id"] == str(ids[source])
    assert body["items"][0]["source_kind"] == source
    assert body["items"][0]["capture_error_code"] == "capture_not_persisted"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [None, "USER"])
@pytest.mark.parametrize("reader", ["list", "detail", "workbench"])
async def test_admin_reader_permission_guard_denies_without_auditing_or_payload_loading(
    admin_reader: AdminReader, role: str | None, reader: str
) -> None:
    probe = admin_reader
    match_id = await probe.db.match()
    raw_id = await probe.db.blob("must-not-expose")
    call_id = await probe.db.call(match_id, request_blob_id=raw_id)
    probe.context = AuthContext(probe.db.user_id, role, uuid4(), False) if role else None
    path = {
        "list": "/api/admin/external-calls",
        "detail": f"/api/admin/external-calls/{call_id}",
        "workbench": f"/api/admin/matches/{match_id}/workbench/calls",
    }[reader]
    response = await probe.client.get(path)
    assert response.status_code == (401 if role is None else 403)
    assert response.json()["error"]["code"] == (
        "not_authenticated" if role is None else "forbidden"
    )
    assert "must-not-expose" not in response.text
    assert await probe.audits(call_id) == []

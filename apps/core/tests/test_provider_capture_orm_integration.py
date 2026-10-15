"""Real ORM regressions for optional capture savepoint rollback.

The route probe uses the actual admin function, SSE adapter and audit writer;
only the provider transport and optional capture storage are synthetic.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Request
from pydantic import SecretStr
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import jx_core.admin_routes as routes
import jx_core.data_capture.provider_persistence as capture_module
import jx_core.data_capture.provider_writer as writer_module
from jx_core.agent.llm import LlmCapacityLimiter, OpenAIStreamingClient
from jx_core.auth.session import AuthContext
from jx_core.config import Settings
from jx_core.data_capture.provider import CapturedProviderPayload, ProviderCallCapture
from jx_core.data_capture.provider_projection import capture_projection
from jx_core.data_capture.provider_writer import ProviderCaptureWriter
from jx_core.models import AuditLog, ExternalCall, ModelProfile, User
from jx_core.models import ProviderCallCapture as CaptureRow
from jx_core.security.crypto import encrypt_secret
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def capture_orm_factory(
    agent_fault_database_url: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(agent_fault_database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def capture_orm_writer(
    agent_fault_database_url: str,
) -> AsyncGenerator[ProviderCaptureWriter, None]:
    writer = ProviderCaptureWriter(agent_fault_database_url)
    await writer.start()
    try:
        yield writer
    finally:
        await writer.close()


def synthetic_capture() -> ProviderCallCapture:
    capture = ProviderCallCapture()
    capture.capture_request(
        transport="sse",
        url="https://synthetic.invalid/v1/chat/completions",
        body={"messages": [{"role": "user", "content": "Synthetic capture"}]},
    )
    capture.capture_response(
        transport="sse",
        url="https://synthetic.invalid/v1/chat/completions",
        body={"text": "Synthetic result"},
    )
    return capture


def inject_capture_fault(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fault_side: Literal["request", "response"],
    fault_kind: Literal["python", "sql"],
) -> None:
    original = capture_module._persist_side  # pyright: ignore[reportPrivateUsage]

    async def faulty_side(
        session: AsyncSession,
        call: ExternalCall,
        side: str,
        captured: CapturedProviderPayload | None,
    ) -> None:
        if side != fault_side:
            await original(session, call, side, captured)
            return
        if fault_kind == "sql":
            # A real PostgreSQL statement failure inside the savepoint.
            await session.execute(text("SELECT CAST('synthetic-not-an-integer' AS integer)"))
        raise RuntimeError("synthetic_capture_storage_failed")

    monkeypatch.setattr(capture_module, "_persist_side", faulty_side)


async def seed_call(factory: async_sessionmaker[AsyncSession]) -> UUID:
    call_id = uuid4()
    async with factory() as session:
        async with session.begin():
            session.add(
                ExternalCall(
                    id=call_id,
                    call_kind="LLM_SPEECH",
                    provider="OPENAI_COMPATIBLE",
                    operation="chat.completions.stream",
                    model="synthetic-model",
                    attempt_no=1,
                    status="STARTED",
                    source_kind="CONFIG_TEST",
                    source_resource_id="synthetic-savepoint",
                    logical_call_id=uuid4(),
                    started_at=datetime.now(UTC),
                )
            )
    return call_id


@pytest.mark.asyncio
@pytest.mark.parametrize("fault_side", ["request", "response"])
@pytest.mark.parametrize("fault_kind", ["python", "sql"])
async def test_capture_failure_preserves_loaded_orm_attributes_and_business_commit(
    capture_orm_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    fault_side: Literal["request", "response"],
    fault_kind: Literal["python", "sql"],
) -> None:
    factory = capture_orm_factory
    call_id = await seed_call(factory)
    inject_capture_fault(monkeypatch, fault_side=fault_side, fault_kind=fault_kind)
    async with factory() as session:
        async with session.begin():
            call = await session.get(ExternalCall, call_id, with_for_update=True)
            assert call is not None
            original_started_at = call.started_at
            # Savepoint creation flushes these legitimate preceding writes.
            call.status = "SUCCEEDED"
            call.completed_at = datetime.now(UTC)
            assert (
                await capture_module.persist_provider_capture(session, call, synthetic_capture())
                is False
            )
            assert not inspect(call).expired_attributes
            assert call.started_at == original_started_at
            assert call.status == "SUCCEEDED"
            assert call.capture_error_code == "capture_persistence_failed"
            assert call.request_blob_id is None and call.response_blob_id is None
            call.first_result_at = call.started_at + timedelta(milliseconds=1)
            call.first_result_latency_ms = 1
    async with factory() as session:
        persisted = await session.get(ExternalCall, call_id)
        assert persisted is not None
        assert persisted.status == "SUCCEEDED"
        assert persisted.first_result_latency_ms == 1
        assert persisted.capture_error_code == "capture_persistence_failed"
        assert persisted.request_blob_id is None and persisted.response_blob_id is None


@pytest.mark.asyncio
async def test_capture_failure_does_not_swallow_outer_business_commit_failure(
    capture_orm_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = capture_orm_factory
    call_id = await seed_call(factory)
    inject_capture_fault(monkeypatch, fault_side="request", fault_kind="python")
    with pytest.raises(IntegrityError):
        async with factory() as session:
            async with session.begin():
                call = await session.get(ExternalCall, call_id, with_for_update=True)
                assert call is not None
                assert (
                    await capture_module.persist_provider_capture(
                        session, call, synthetic_capture()
                    )
                    is False
                )
                call.status = "INVALID"
    async with factory() as session:
        call = await session.get(ExternalCall, call_id)
        assert call is not None
        assert call.status == "STARTED"
        assert call.capture_error_code is None


@pytest.mark.asyncio
async def test_capture_boundary_does_not_swallow_preceding_authority_flush_failure(
    capture_orm_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = capture_orm_factory
    call_id = await seed_call(factory)
    capture_side_calls = 0

    async def no_capture_side(
        _session: AsyncSession,
        _call: ExternalCall,
        _side: str,
        _captured: CapturedProviderPayload | None,
    ) -> None:
        nonlocal capture_side_calls
        capture_side_calls += 1

    monkeypatch.setattr(capture_module, "_persist_side", no_capture_side)
    async with factory() as session:
        await session.begin()
        call = await session.get(ExternalCall, call_id, with_for_update=True)
        assert call is not None
        call.status = "INVALID"
        try:
            with pytest.raises(IntegrityError):
                await capture_module.persist_provider_capture(session, call, synthetic_capture())
        finally:
            await session.rollback()
    assert capture_side_calls == 0
    async with factory() as session:
        call = await session.get(ExternalCall, call_id)
        assert call is not None
        assert call.status == "STARTED" and call.capture_error_code is None


@pytest.mark.asyncio
async def test_capture_boundary_propagates_refresh_failure_and_rolls_back_business_write(
    capture_orm_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = capture_orm_factory
    call_id = await seed_call(factory)
    inject_capture_fault(monkeypatch, fault_side="request", fault_kind="python")
    refresh = AsyncMock(side_effect=RuntimeError("synthetic_refresh_failed"))
    async with factory() as session:
        monkeypatch.setattr(session, "refresh", refresh)
        call: ExternalCall | None = None
        with pytest.raises(RuntimeError, match="^synthetic_refresh_failed$"):
            async with session.begin():
                call = await session.get(ExternalCall, call_id, with_for_update=True)
                assert call is not None
                call.status = "SUCCEEDED"
                await capture_module.persist_provider_capture(session, call, synthetic_capture())
        assert call is not None
        refresh.assert_awaited_once_with(call)
    async with factory() as session:
        call = await session.get(ExternalCall, call_id)
        assert call is not None
        assert call.status == "STARTED" and call.capture_error_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fault_side", ["request", "response"])
async def test_successful_admin_model_route_survives_capture_storage_failure(
    capture_orm_factory: async_sessionmaker[AsyncSession],
    capture_orm_writer: ProviderCaptureWriter,
    agent_fault_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    fault_side: Literal["request", "response"],
) -> None:
    factory = capture_orm_factory
    suffix = uuid4().hex[:12]
    master_key = base64.urlsafe_b64encode(b"x" * 32).decode()
    ciphertext, nonce, last4 = encrypt_secret("synthetic", master_key)
    async with factory() as session:
        async with session.begin():
            admin = await session.scalar(select(User).where(User.role == "ADMIN"))
            if admin is None:
                admin = User(
                    username=f"capture-admin-{suffix}",
                    username_normalized=f"capture-admin-{suffix}",
                    real_name="Synthetic admin",
                    password_hash="not-used",
                    role="ADMIN",
                )
                session.add(admin)
            model = ModelProfile(
                name=f"capture-model-{suffix}",
                config_ref="synthetic",
                base_url="https://synthetic.invalid/v1",
                model_id="synthetic-model",
                api_key_ciphertext=ciphertext,
                api_key_nonce=nonce,
                api_key_last4=last4,
                max_concurrency=1,
            )
            session.add(model)
            await session.flush()
            admin_id, model_id = admin.id, model.id
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=SecretStr(agent_fault_database_url),
        llm_key_encryption_key=SecretStr(master_key),
    )
    limiter = LlmCapacityLimiter(2)
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(
                state=SimpleNamespace(
                    settings=settings,
                    llm_capacity_limiter=limiter,
                    provider_capture_writer=capture_orm_writer,
                )
            ),
        }
    )
    provider_calls = 0
    fault_calls = 0
    original_blob = writer_module._store_encoded_blob  # pyright: ignore[reportPrivateUsage]

    async def fail_writer_side(
        session: AsyncSession, kind: str, raw: bytes, prepared: tuple[bytes, str]
    ) -> UUID:
        nonlocal fault_calls
        if kind.lower() == fault_side:
            fault_calls += 1
            raise RuntimeError("synthetic_capture_storage_failed")
        return await original_blob(session, kind, raw, prepared)

    monkeypatch.setattr(writer_module, "_store_encoded_blob", fail_writer_side)

    def provider_response(request: httpx.Request) -> httpx.Response:
        nonlocal provider_calls
        assert request.url.host == "synthetic.invalid"
        provider_calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=(
                'data: {"choices":[{"delta":{"content":"Synthetic success"}}]}\n\n'
                'data: {"choices":[],"usage":{"completion_tokens":2}}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider_response)) as http_client:

        def client_factory(**kwargs: Any) -> OpenAIStreamingClient:
            return OpenAIStreamingClient(**kwargs, client=http_client)

        monkeypatch.setattr(routes, "OpenAIStreamingClient", client_factory)
        async with factory() as session:
            try:
                result = await routes.test_model_connection(
                    model_id,
                    request,
                    AuthContext(admin_id, "ADMIN", uuid4(), False),
                    session,
                )
            except Exception as error:
                async with factory() as verifier:
                    call = await verifier.scalar(
                        select(ExternalCall).where(ExternalCall.source_resource_id == str(model_id))
                    )
                    assert call is not None
                    pytest.fail(
                        f"successful Provider route failed with {type(error).__name__}; "
                        f"persisted call={call.status}; provider_calls={provider_calls}"
                    )
    assert result["status"] == "ok" and provider_calls == 1
    async with asyncio.timeout(1.2):
        while capture_orm_writer.stats()["items"]:
            await asyncio.sleep(0.01)
    assert fault_calls == 2
    assert capture_orm_writer.stats()["retry"] == 1
    assert capture_orm_writer.stats()["persist_failed"] == 1
    async with factory() as session:
        call = await session.scalar(
            select(ExternalCall).where(ExternalCall.source_resource_id == str(model_id))
        )
        assert call is not None
        assert call.status == "SUCCEEDED" and call.completion_tokens == 2
        assert call.first_result_at is not None and call.completed_at is not None
        assert call.capture_storage_mode == "ASYNC_V1"
        assert call.request_blob_id is None and call.response_blob_id is None
        assert (
            await session.scalar(select(CaptureRow).where(CaptureRow.external_call_id == call.id))
            is None
        )
        projected = await capture_projection(session, call)
        assert projected["capture_error_code"] == "capture_not_persisted"
        assert projected["request_capture_status"] == "UNAVAILABLE"
        assert projected["response_capture_status"] == "UNAVAILABLE"
        assert projected["request_blob_id"] is projected["response_blob_id"] is None
        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == "admin.model.tested", AuditLog.target_id == str(model_id)
            )
        )
        assert audit is not None and audit.result == "SUCCESS"
    lease = await limiter.acquire(str(model_id), 1, timeout_seconds=0.1)
    limiter.release(lease)

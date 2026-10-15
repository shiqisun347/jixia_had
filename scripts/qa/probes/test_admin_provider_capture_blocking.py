"""Explicit PostgreSQL risk probe for admin raw-capture blocking.

Run this file explicitly with pytest and independent TEST_DATABASE_URL pointing
to the dedicated localhost jx_debate_qa_agent_faults database. These strong
assertions are outside the normal green gates; a failure is retained as risk
evidence. The actual route, SSE adapter, ORM and audit writer are exercised.
Only the provider transport and optional raw-capture storage are synthetic.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import Request
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import jx_core.admin_routes as routes
import jx_core.data_capture.provider_writer as capture_module
from jx_core.agent.llm import LlmCapacityLimiter, OpenAIStreamingClient
from jx_core.auth.session import AuthContext
from jx_core.config import Settings
from jx_core.data_capture.provider_projection import capture_projection
from jx_core.data_capture.provider_writer import CaptureSubmission, ProviderCaptureWriter
from jx_core.models import AuditLog, ExternalCall, ModelProfile, User
from jx_core.security.crypto import encrypt_secret

pytestmark = pytest.mark.integration
# This probe injects only the private optional-storage boundary.
# pyright: reportPrivateUsage=false

CAPTURE_ENTRY_OBSERVATION_SECONDS = 0.8
RESULT_OBSERVATION_SECONDS = 0.75
TASK_CLEANUP_SECONDS = 0.8


@pytest.fixture(scope="module")
def probe_database_url() -> str:
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("explicit RUN_DATABASE_INTEGRATION=1 is required for this risk probe")
    value = os.environ.get("TEST_DATABASE_URL")
    if not value or value == os.environ.get("DATABASE_URL"):
        pytest.fail("an independent TEST_DATABASE_URL is required")
    parsed = make_url(value)
    if (
        parsed.host not in {"127.0.0.1", "localhost"}
        or parsed.database != "jx_debate_qa_agent_faults"
    ):
        pytest.fail("risk probes require the dedicated local QA database")
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url=SecretStr(value),
        app_env="test",
    )
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    with patch("jx_core.config.load_settings", return_value=settings):
        command.upgrade(config, "head")
    return value


@dataclass
class AdminCaptureProbe:
    factory: async_sessionmaker[AsyncSession]
    engine: AsyncEngine
    request: Request
    context: AuthContext
    model_id: UUID
    limiter: LlmCapacityLimiter
    release_capture: asyncio.Event = field(default_factory=asyncio.Event)
    capture_entered: asyncio.Event = field(default_factory=asyncio.Event)
    call_id: UUID | None = None
    provider_calls: int = 0
    tasks: list[asyncio.Task[dict[str, Any]]] = field(
        default_factory=lambda: list[asyncio.Task[dict[str, Any]]]()
    )

    async def run(self) -> dict[str, Any]:
        async with self.factory() as session:
            return await routes.test_model_connection(
                self.model_id, self.request, self.context, session
            )

    def start(self) -> asyncio.Task[dict[str, Any]]:
        task = asyncio.create_task(self.run(), name="qa-admin-capture-route")
        self.tasks.append(task)
        return task

    async def committed_snapshot(self) -> dict[str, Any]:
        async with self.factory() as session:
            call = await session.scalar(
                select(ExternalCall).where(ExternalCall.source_resource_id == str(self.model_id))
            )
            audit = await session.scalar(
                select(AuditLog).where(
                    AuditLog.action == "admin.model.tested",
                    AuditLog.target_id == str(self.model_id),
                )
            )
            return {
                "call_status": call.status if call is not None else None,
                "call_completed": call is not None and call.completed_at is not None,
                "call_tokens": call.completion_tokens if call is not None else None,
                "capture_error": (await capture_projection(session, call))["capture_error_code"]
                if call is not None
                else None,
                "audit_result": audit.result if audit is not None else None,
            }

    async def authority_lock_observation(self) -> dict[str, Any]:
        assert self.call_id is not None
        started = monotonic()
        try:
            async with asyncio.timeout(0.8):
                async with self.factory() as session:
                    async with session.begin():
                        await session.execute(text("SET LOCAL lock_timeout = '200ms'"))
                        # This represents the row lock of a non-key authority
                        # UPDATE and remains compatible with a future capture
                        # table's FK KEY SHARE. No column value is changed.
                        await session.execute(
                            text("SELECT id FROM external_calls WHERE id = :id FOR NO KEY UPDATE"),
                            {"id": self.call_id},
                        )
        except DBAPIError as error:
            sqlstate = getattr(error.orig, "sqlstate", None)
            if sqlstate != "55P03":
                raise
            return {
                "lock_acquired": False,
                "sqlstate": sqlstate,
                "elapsed_ms": round((monotonic() - started) * 1000, 2),
            }
        return {
            "lock_acquired": True,
            "sqlstate": None,
            "elapsed_ms": round((monotonic() - started) * 1000, 2),
        }


@pytest.fixture
async def admin_capture_probe(
    probe_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[AdminCaptureProbe, None]:
    engine = create_async_engine(probe_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex[:12]
    master_key = base64.urlsafe_b64encode(b"x" * 32).decode()
    ciphertext, nonce, last4 = encrypt_secret("synthetic", master_key)
    async with factory() as session:
        async with session.begin():
            admin = await session.scalar(select(User).where(User.role == "ADMIN"))
            if admin is None:
                admin = User(
                    username=f"probe-admin-{suffix}",
                    username_normalized=f"probe-admin-{suffix}",
                    real_name="Synthetic admin",
                    password_hash="not-used",
                    role="ADMIN",
                )
                session.add(admin)
            model = ModelProfile(
                name=f"probe-model-{suffix}",
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
        database_url=SecretStr(probe_database_url),
        llm_key_encryption_key=SecretStr(master_key),
    )
    limiter = LlmCapacityLimiter(2)
    writer = ProviderCaptureWriter(probe_database_url, service="qa-admin")
    await writer.start()
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(
                state=SimpleNamespace(
                    settings=settings, llm_capacity_limiter=limiter, provider_capture_writer=writer
                )
            ),
        }
    )
    probe = AdminCaptureProbe(
        factory, engine, request, AuthContext(admin_id, "ADMIN", uuid4(), False), model_id, limiter
    )

    def provider_response(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "synthetic.invalid"
        probe.provider_calls += 1
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
        try:
            yield probe
        finally:
            probe.release_capture.set()
            for task in probe.tasks:
                if not task.done():
                    task.cancel()
            if probe.tasks:
                _, pending = await asyncio.wait(probe.tasks, timeout=TASK_CLEANUP_SECONDS)
                assert not pending, "owned synthetic admin task cleanup exceeded its bound"
                await asyncio.gather(*probe.tasks, return_exceptions=True)
            await writer.close()
            async with asyncio.timeout(TASK_CLEANUP_SECONDS):
                await engine.dispose()


def inject_storage_fault(
    probe: AdminCaptureProbe,
    monkeypatch: pytest.MonkeyPatch,
    *,
    side: Literal["request", "response"],
    fault: Literal["hang", "sql"],
) -> None:
    writer = probe.request.app.state.provider_capture_writer
    original_item = writer._persist_item
    original_blob = capture_module._store_encoded_blob

    async def capture_item(session: AsyncSession, item: CaptureSubmission, prepared: Any) -> None:
        probe.call_id = item.identity.external_call_id
        await original_item(session, item, prepared)

    async def fault_side(
        session: AsyncSession, kind: str, raw: bytes, prepared: tuple[bytes, str]
    ) -> UUID:
        if kind.lower() == side:
            probe.capture_entered.set()
            if fault == "sql":
                await session.execute(text("SELECT CAST('synthetic-not-an-integer' AS integer)"))
            else:
                await probe.release_capture.wait()
        return await original_blob(session, kind, raw, prepared)

    monkeypatch.setattr(writer, "_persist_item", capture_item)
    monkeypatch.setattr(capture_module, "_store_encoded_blob", fault_side)


def assert_authoritative_success(snapshot: dict[str, Any], failures: list[str]) -> None:
    if snapshot["call_status"] != "SUCCEEDED" or not snapshot["call_completed"]:
        failures.append("valid_provider_result_not_committed")
    if snapshot["call_tokens"] != 2:
        failures.append("valid_provider_metrics_not_committed")
    if snapshot["audit_result"] != "SUCCESS":
        failures.append("success_audit_not_committed")


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["request", "response"])
async def test_sql_capture_failure_control_preserves_success_and_authority_lock(
    admin_capture_probe: AdminCaptureProbe,
    monkeypatch: pytest.MonkeyPatch,
    side: Literal["request", "response"],
) -> None:
    probe = admin_capture_probe
    inject_storage_fault(probe, monkeypatch, side=side, fault="sql")
    task = probe.start()
    done, _ = await asyncio.wait({task}, timeout=RESULT_OBSERVATION_SECONDS)
    assert task in done, "ordinary SQL capture error must return an effective result"
    assert task.result()["status"] == "ok" and probe.provider_calls == 1
    await asyncio.wait_for(probe.capture_entered.wait(), timeout=CAPTURE_ENTRY_OBSERVATION_SECONDS)
    await asyncio.sleep(0.15)  # one diagnostic retry, independent of completed route
    snapshot = await probe.committed_snapshot()
    lock = await probe.authority_lock_observation()
    failures: list[str] = []
    assert_authoritative_success(snapshot, failures)
    assert snapshot["capture_error"] == "capture_not_persisted"
    assert probe.request.app.state.provider_capture_writer.stats()["persist_failed"] == 1
    assert lock["lock_acquired"]
    assert not failures, json.dumps({"failures": failures, "snapshot": snapshot, "lock": lock})


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["request", "response"])
async def test_hanging_optional_capture_does_not_block_valid_result_or_authority_update(
    admin_capture_probe: AdminCaptureProbe,
    monkeypatch: pytest.MonkeyPatch,
    side: Literal["request", "response"],
) -> None:
    probe = admin_capture_probe
    inject_storage_fault(probe, monkeypatch, side=side, fault="hang")
    task = probe.start()
    await asyncio.wait_for(probe.capture_entered.wait(), timeout=CAPTURE_ENTRY_OBSERVATION_SECONDS)
    lock = await probe.authority_lock_observation()
    done, _ = await asyncio.wait({task}, timeout=RESULT_OBSERVATION_SECONDS)
    snapshot = await probe.committed_snapshot()
    # Text provider capacity must already be free even when raw storage hangs.
    lease = await probe.limiter.acquire(str(probe.model_id), 1, timeout_seconds=0.1)
    probe.limiter.release(lease)
    failures: list[str] = []
    if task not in done:
        failures.append("valid_result_blocked_by_optional_capture")
    elif task.result()["status"] != "ok":
        failures.append("valid_result_not_returned")
    if not lock["lock_acquired"]:
        failures.append("authority_row_lock_blocked_by_optional_capture")
    assert_authoritative_success(snapshot, failures)
    assert probe.provider_calls == 1
    assert not failures, json.dumps({"failures": failures, "snapshot": snapshot, "lock": lock})


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["request", "response"])
async def test_cancel_during_optional_capture_retains_valid_result_and_success_audit(
    admin_capture_probe: AdminCaptureProbe,
    monkeypatch: pytest.MonkeyPatch,
    side: Literal["request", "response"],
) -> None:
    probe = admin_capture_probe
    inject_storage_fault(probe, monkeypatch, side=side, fault="hang")
    task = probe.start()
    await asyncio.wait_for(probe.capture_entered.wait(), timeout=CAPTURE_ENTRY_OBSERVATION_SECONDS)
    before_lock = await probe.authority_lock_observation()
    if not task.done():
        task.cancel()
    done, _ = await asyncio.wait({task}, timeout=0.3)
    assert task in done, "external cancellation must remain bounded and observable"
    if task.cancelled():
        response_outcome = "cancelled"
    else:
        assert task.result()["status"] == "ok"
        response_outcome = "ok"
    snapshot = await probe.committed_snapshot()
    after_lock = await probe.authority_lock_observation()
    failures: list[str] = []
    assert_authoritative_success(snapshot, failures)
    if not before_lock["lock_acquired"]:
        failures.append("authority_row_lock_blocked_before_cancel")
    assert after_lock["lock_acquired"] and probe.provider_calls == 1
    assert not failures, json.dumps(
        {
            "failures": failures,
            "response_outcome": response_outcome,
            "snapshot": snapshot,
            "before_lock": before_lock,
            "after_lock": after_lock,
        }
    )

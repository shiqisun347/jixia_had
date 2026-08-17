from __future__ import annotations

import asyncio
import json
import logging
import os
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from jx_core.config import Settings
from jx_core.data_capture.content import (
    canonical_payload_bytes,
    load_content_blob,
    store_content_blob,
)
from jx_core.data_capture.diagnostics import DiagnosticWriter
from jx_core.database import Database
from jx_core.models import CallContentBlob, CallContentBlobChunk, SystemLogEvent


def test_canonical_payload_is_stable_and_only_redacts_structured_secret_values() -> None:
    first = {
        "messages": [{"content": "用户原文包含 api_key=这不是结构化密钥", "role": "user"}],
        "api_key": "secret-value",
        "nested": {"Authorization": "Bearer secret", "temperature": 0.7},
    }
    second = {
        "nested": {"temperature": 0.7, "Authorization": "different-secret"},
        "api_key": "different-value",
        "messages": [{"role": "user", "content": "用户原文包含 api_key=这不是结构化密钥"}],
    }

    first_bytes = canonical_payload_bytes(first)
    second_bytes = canonical_payload_bytes(second)
    payload = json.loads(first_bytes)

    assert first_bytes == second_bytes
    assert payload["api_key"] == "[REDACTED]"
    assert payload["nested"]["Authorization"] == "[REDACTED]"
    assert "api_key=这不是结构化密钥" in payload["messages"][0]["content"]
    assert b"secret-value" not in first_bytes


def _integration_database() -> Database:
    if os.environ.get("RUN_DATABASE_INTEGRATION") != "1":
        pytest.skip("set RUN_DATABASE_INTEGRATION=1 to run real PostgreSQL tests")
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.fail("TEST_DATABASE_URL is required")
    settings = Settings(database_url=SecretStr(database_url), app_env="test")
    return Database(settings.database_url_value)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_content_blobs_deduplicate_and_round_trip_chunked_payloads() -> None:
    database = _integration_database()
    payload = {"messages": [{"role": "user", "content": os.urandom(700_000).hex()}]}
    first_id: UUID | None = None
    try:
        async with database.session_factory() as session:
            async with session.begin():
                first_id = await store_content_blob(
                    session, content_kind="REQUEST", payload=payload
                )
                second_id = await store_content_blob(
                    session, content_kind="REQUEST", payload=payload
                )
            loaded = await load_content_blob(session, first_id)
            chunk_count = await session.scalar(
                select(func.count())
                .select_from(CallContentBlobChunk)
                .where(CallContentBlobChunk.blob_id == first_id)
            )
        assert first_id == second_id
        assert loaded == payload
        assert int(chunk_count or 0) >= 2
    finally:
        async with database.session_factory() as session:
            async with session.begin():
                if first_id is not None:
                    await session.execute(
                        delete(CallContentBlob).where(CallContentBlob.id == first_id)
                    )
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_identical_content_writes_reuse_one_blob() -> None:
    database = _integration_database()
    payload = {"messages": [{"role": "user", "content": "same concurrent payload"}]}
    first_id: object | None = None

    async def write() -> object:
        async with database.session_factory() as session:
            async with session.begin():
                return await store_content_blob(session, content_kind="REQUEST", payload=payload)

    try:
        first_id, second_id = await asyncio.gather(write(), write())
        assert first_id == second_id
    finally:
        async with database.session_factory() as session:
            async with session.begin():
                if isinstance(first_id, UUID):
                    await session.execute(
                        delete(CallContentBlob).where(CallContentBlob.id == first_id)
                    )
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_diagnostic_writer_persists_redacted_warning_without_blocking_logger() -> None:
    database = _integration_database()
    writer = DiagnosticWriter(
        service="jx-core",
        session_factory=database.session_factory,
        queue_size=64,
        batch_size=10,
        flush_interval_seconds=0.01,
    )
    await writer.start()
    try:
        logging.getLogger("jx-core.capture-test").warning(
            "capture warning password=hunter2",
            extra={"error_code": "capture_test_warning"},
        )
        await asyncio.wait_for(writer.flush(), timeout=2)
        async with database.session_factory() as session:
            row = await session.scalar(
                select(SystemLogEvent)
                .where(SystemLogEvent.error_code == "capture_test_warning")
                .order_by(SystemLogEvent.created_at.desc())
            )
            assert row is not None
            assert "hunter2" not in row.message
    finally:
        await writer.stop()
        async with database.session_factory() as session:
            async with session.begin():
                await session.execute(
                    delete(SystemLogEvent).where(
                        SystemLogEvent.error_code == "capture_test_warning"
                    )
                )
        await database.dispose()

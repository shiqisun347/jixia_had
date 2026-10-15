"""Opt-in Core voice-preview/PostgreSQL capture risk probes.

Run explicitly with PYTHONPATH=., RUN_DATABASE_INTEGRATION=1 and distinct
TEST_DATABASE_URL pointing to localhost jx_debate_qa_agent_faults. The actual
admin route, Qwen TTS adapter, Ogg processing, file publication and ORM/audit
transactions are exercised. Only WebSocket transport and optional raw storage
are synthetic. Failed product assertions remain risk evidence, without xfail;
this path is outside the default green gates and does not implement Spec 300.
"""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import av
import pytest
from fastapi import Request
from pydantic import SecretStr
from scripts.qa.probes.test_admin_provider_capture_blocking import (
    probe_database_url as probe_database_url,
)
from sqlalchemy import delete, select, text, update
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
from jx_core.agent.audio import decode_ogg_opus_pcm
from jx_core.agent.tts import QwenTtsConnection, TtsStartRateLimiter, WebSocketLike
from jx_core.auth.session import AuthContext
from jx_core.config import Settings
from jx_core.data_capture.provider_projection import capture_projections
from jx_core.data_capture.provider_writer import CaptureSubmission, ProviderCaptureWriter
from jx_core.models import AuditLog, ExternalCall, User, VoiceProfile

pytestmark = pytest.mark.integration
# Observe the real optional storage and shared preview semaphore, without
# replacing transaction, audit, media gain or command handlers.
# pyright: reportPrivateUsage=false

CaptureFault = Literal["normal", "storage_raise", "storage_sql_error", "hang"]
OBSERVATION_SECONDS = 0.8
CLEANUP_SECONDS = 1.0


def _encoded_100ms_ogg() -> bytes:
    target = io.BytesIO()
    with av.open(target, mode="w", format="ogg") as container:
        stream = container.add_stream(  # pyright: ignore[reportUnknownMemberType]
            "libopus", rate=48_000
        )
        stream.layout = "mono"
        for index in range(5):
            frame = av.AudioFrame(format="s16", layout="mono", samples=960)
            frame.sample_rate = 48_000
            frame.pts = index * 960
            frame.time_base = Fraction(1, 48_000)
            frame.planes[0].update(bytes(960 * 2))
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return target.getvalue()


@dataclass
class SyntheticSocket:
    audio: bytes
    messages: asyncio.Queue[str | bytes] = field(
        default_factory=lambda: asyncio.Queue[str | bytes](4)
    )
    closed: bool = False

    async def send(self, message: str | bytes) -> None:
        assert isinstance(message, str)
        payload = cast(dict[str, Any], json.loads(message))
        header = cast(dict[str, Any], payload["header"])
        task_id = header["task_id"]
        if header["action"] == "run-task":
            await self.messages.put(
                json.dumps({"header": {"task_id": task_id, "event": "task-started"}})
            )
        elif header["action"] == "continue-task":
            assert payload["payload"]["input"]["text"] == routes.VOICE_PREVIEW_TEXT
        elif header["action"] == "finish-task":
            await self.messages.put(self.audio)
            await self.messages.put(
                json.dumps({"header": {"task_id": task_id, "event": "task-finished"}})
            )
        else:
            raise AssertionError("unexpected synthetic TTS action")

    async def recv(self) -> str | bytes:
        return await self.messages.get()

    async def close(self) -> None:
        self.closed = True


@dataclass
class PreviewCaptureProbe:
    factory: async_sessionmaker[AsyncSession]
    engine: AsyncEngine
    request: Request
    context: AuthContext
    voice_id: UUID
    path: Path
    audio: bytes
    capture_fault: CaptureFault = "normal"
    capture_entered: asyncio.Event = field(default_factory=asyncio.Event)
    release_capture: asyncio.Event = field(default_factory=asyncio.Event)
    capture_cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    sockets: list[SyntheticSocket] = field(default_factory=lambda: list[SyntheticSocket]())
    tasks: list[asyncio.Task[dict[str, Any]]] = field(
        default_factory=lambda: list[asyncio.Task[dict[str, Any]]]()
    )
    capture_calls: int = 0
    storage_failures: int = 0
    call_id: UUID | None = None

    async def run(self) -> dict[str, Any]:
        async with self.factory() as session:
            return await routes.regenerate_voice_preview(
                self.voice_id, self.request, self.context, session
            )

    def start(self) -> asyncio.Task[dict[str, Any]]:
        task = asyncio.create_task(self.run(), name="qa-preview-capture-route")
        self.tasks.append(task)
        return task

    async def preview_capacity_available(self) -> None:
        acquired = 0
        try:
            async with asyncio.timeout(0.2):
                for _ in range(routes.VOICE_PREVIEW_CONCURRENCY):
                    await routes._VOICE_PREVIEW_SEMAPHORE.acquire()
                    acquired += 1
        finally:
            for _ in range(acquired):
                routes._VOICE_PREVIEW_SEMAPHORE.release()

    async def snapshot(self, task: asyncio.Task[dict[str, Any]]) -> dict[str, Any]:
        async with self.factory() as session:
            voice = await session.get(VoiceProfile, self.voice_id)
            assert voice is not None
            calls = list(
                (
                    await session.scalars(
                        select(ExternalCall)
                        .where(
                            ExternalCall.source_kind == "CONFIG_TEST",
                            ExternalCall.source_resource_id == str(self.voice_id),
                        )
                        .order_by(ExternalCall.attempt_no)
                    )
                ).all()
            )
            audits = list(
                (
                    await session.scalars(
                        select(AuditLog).where(
                            AuditLog.action == "admin.voice.preview.generated",
                            AuditLog.target_id == str(self.voice_id),
                        )
                    )
                ).all()
            )
            projections = await capture_projections(session, calls)
        task_error = task.exception() if task.done() and not task.cancelled() else None
        encoded = self.path.read_bytes() if self.path.is_file() else b""
        duration_ms = len(decode_ogg_opus_pcm(encoded, sample_rate=48_000)) / (48_000 * 2) * 1000
        return {
            "capture_fault": self.capture_fault,
            "capture_calls": self.capture_calls,
            "storage_failures": self.storage_failures,
            "provider_calls": len(self.sockets),
            "provider_closed": all(socket.closed for socket in self.sockets),
            "task_done": task.done(),
            "task_cancelled": task.cancelled(),
            "task_error_type": type(task_error).__name__ if task_error else None,
            "voice_calibration": voice.calibration_status,
            "voice_calibrated": voice.calibrated_at is not None,
            "call_statuses": [call.status for call in calls],
            "call_completed": [call.completed_at is not None for call in calls],
            "call_errors": [call.error_code for call in calls],
            "call_audio_bytes": [call.audio_bytes for call in calls],
            "capture_errors": [projections[call.id]["capture_error_code"] for call in calls],
            "audit_results": [audit.result for audit in audits],
            "audit_attempts": [audit.details.get("attempt") for audit in audits],
            "preview_file_published": self.path.is_file(),
            "preview_file_bytes": len(encoded),
            "preview_duration_ms": duration_ms,
            "temporary_files": len(list(self.path.parent.glob("*.part"))),
        }


@pytest.fixture
async def preview_capture_probe(
    probe_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[PreviewCaptureProbe, None]:
    # The reused guard already requires explicit localhost QA DB, independent
    # DATABASE_URL, opt-in and migration Settings without dotenv; tighten driver.
    assert make_url(probe_database_url).drivername == "postgresql+psycopg"
    engine = create_async_engine(probe_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id, voice_id = uuid4(), uuid4()
    suffix = uuid4().hex
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=SecretStr(probe_database_url),
        tts_api_key=SecretStr("synthetic-preview-key"),
        tts_ws_url="wss://synthetic.invalid/tts",
        tts_model="synthetic-tts",
        agent_audio_storage_dir=str(tmp_path),
    )
    writer = ProviderCaptureWriter(probe_database_url, service="qa-preview")
    await writer.start()
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(
                state=SimpleNamespace(settings=settings, provider_capture_writer=writer)
            ),
        }
    )
    probe = PreviewCaptureProbe(
        factory,
        engine,
        request,
        AuthContext(user_id, "ADMIN", uuid4(), False),
        voice_id,
        tmp_path / "voice-previews" / f"{voice_id}.ogg",
        _encoded_100ms_ogg(),
    )
    created_admin = False
    async with factory() as session:
        async with session.begin():
            admin = await session.scalar(select(User).where(User.role == "ADMIN"))
            if admin is None:
                admin = User(
                    id=user_id,
                    username=f"preview-{suffix[:20]}",
                    username_normalized=f"preview-{suffix[:20]}",
                    real_name="Synthetic preview admin",
                    password_hash="not-used",
                    role="ADMIN",
                )
                session.add(admin)
                created_admin = True
            session.add(
                VoiceProfile(
                    id=voice_id,
                    name=f"preview-{suffix}",
                    kind="HOST",
                    provider_voice=f"synthetic-preview-{suffix}",
                    rate=1.0,
                    playback_gain=1.2,
                    status="ENABLED",
                )
            )
            await session.flush()
            user_id = admin.id
            probe.context = AuthContext(user_id, "ADMIN", uuid4(), False)

    async def socket_factory(url: str, headers: dict[str, str]) -> WebSocketLike:
        assert url == "wss://synthetic.invalid/tts"
        assert headers["Authorization"] == "Bearer synthetic-preview-key"
        socket = SyntheticSocket(probe.audio)
        probe.sockets.append(socket)
        return socket

    def connection_factory(**arguments: Any) -> QwenTtsConnection:
        return QwenTtsConnection(
            **arguments,
            socket_factory=socket_factory,
            start_rate_limiter=TtsStartRateLimiter(interval_seconds=0),
        )

    original_item = writer._persist_item
    original_blob = capture_module._store_encoded_blob

    async def capture_item(session: AsyncSession, item: CaptureSubmission, prepared: Any) -> None:
        probe.call_id = item.identity.external_call_id
        await original_item(session, item, prepared)

    async def persist_side(
        session: AsyncSession, kind: str, raw: bytes, prepared: tuple[bytes, str]
    ) -> UUID:
        if kind == "RESPONSE":
            probe.capture_calls += 1
            probe.capture_entered.set()
            if probe.capture_fault == "hang":
                try:
                    await probe.release_capture.wait()
                finally:
                    probe.capture_cancelled.set()
            elif probe.capture_fault == "storage_raise":
                probe.storage_failures += 1
                raise RuntimeError("synthetic_preview_capture_storage_failed")
            elif probe.capture_fault == "storage_sql_error":
                probe.storage_failures += 1
                await session.execute(text("SELECT 1 / 0"))
        return await original_blob(session, kind, raw, prepared)

    monkeypatch.setattr(routes, "QwenTtsConnection", connection_factory)
    monkeypatch.setattr(writer, "_persist_item", capture_item)
    monkeypatch.setattr(capture_module, "_store_encoded_blob", persist_side)
    try:
        yield probe
    finally:
        for task in probe.tasks:
            if not task.done():
                task.cancel()
        if probe.tasks:
            _, pending = await asyncio.wait(probe.tasks, timeout=CLEANUP_SECONDS)
            assert not pending, "synthetic preview cancellation cleanup exceeded its bound"
            await asyncio.gather(*probe.tasks, return_exceptions=True)
        probe.release_capture.set()
        await writer.close()
        async with asyncio.timeout(CLEANUP_SECONDS):
            async with factory() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL lock_timeout = '100ms'"))
                    await session.execute(
                        delete(AuditLog).where(AuditLog.target_id == str(voice_id))
                    )
                    await session.execute(
                        delete(ExternalCall).where(
                            ExternalCall.source_kind == "CONFIG_TEST",
                            ExternalCall.source_resource_id == str(voice_id),
                        )
                    )
                    await session.execute(delete(VoiceProfile).where(VoiceProfile.id == voice_id))
                    if created_admin:
                        await session.execute(delete(User).where(User.id == user_id))
            await engine.dispose()


def assert_published_audio(evidence: dict[str, Any]) -> None:
    assert evidence["preview_file_published"] and evidence["preview_file_bytes"] > 0, evidence
    assert 90 <= evidence["preview_duration_ms"] <= 110, evidence
    assert evidence["temporary_files"] == 0, evidence


def assert_authoritative_success(evidence: dict[str, Any], audio_bytes: int) -> None:
    assert evidence["voice_calibration"] == "READY" and evidence["voice_calibrated"], evidence
    assert evidence["call_statuses"] == ["SUCCEEDED"], evidence
    assert evidence["call_completed"] == [True] and evidence["call_errors"] == [None], evidence
    assert evidence["call_audio_bytes"] == [audio_bytes], evidence
    assert evidence["audit_results"] == ["SUCCESS"] and evidence["audit_attempts"] == [1], evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_fault", ["normal", "storage_raise", "storage_sql_error", "hang"])
async def test_preview_capture_does_not_block_authoritative_success(
    preview_capture_probe: PreviewCaptureProbe,
    capture_fault: CaptureFault,
) -> None:
    probe = preview_capture_probe
    probe.capture_fault = capture_fault
    task = probe.start()
    await asyncio.wait({task}, timeout=OBSERVATION_SECONDS)
    await asyncio.wait_for(probe.capture_entered.wait(), timeout=OBSERVATION_SECONDS)
    if capture_fault in {"storage_raise", "storage_sql_error"}:
        await asyncio.sleep(0.15)
    await probe.preview_capacity_available()
    evidence = await probe.snapshot(task)
    print(json.dumps(evidence, sort_keys=True))
    assert_published_audio(evidence)
    assert evidence["task_done"] and not evidence["task_cancelled"], evidence
    assert evidence["task_error_type"] is None and evidence["provider_closed"], evidence
    assert evidence["provider_calls"] == 1, evidence
    assert_authoritative_success(evidence, len(probe.audio))
    result = task.result()
    assert result["status"] == "ready" and result["byte_count"] == evidence["preview_file_bytes"]
    if capture_fault in {"storage_raise", "storage_sql_error"}:
        assert evidence["storage_failures"] == 2, evidence
        assert evidence["capture_errors"] == ["capture_not_persisted"], evidence


@pytest.mark.asyncio
async def test_preview_cancel_during_raw_capture_preserves_observed_success(
    preview_capture_probe: PreviewCaptureProbe,
) -> None:
    probe = preview_capture_probe
    probe.capture_fault = "hang"
    task = probe.start()
    await asyncio.wait_for(probe.capture_entered.wait(), timeout=OBSERVATION_SECONDS)
    assert not task.cancel(), "capture begins only after the request has committed and returned"
    assert (await task)["status"] == "ready"
    await probe.preview_capacity_available()
    evidence = await probe.snapshot(task)
    print(json.dumps(evidence, sort_keys=True))
    assert (
        evidence["task_done"] and not evidence["task_cancelled"] and evidence["provider_closed"]
    ), evidence
    assert_published_audio(evidence)
    assert_authoritative_success(evidence, len(probe.audio))


@pytest.mark.asyncio
async def test_preview_raw_capture_does_not_block_external_call_update(
    preview_capture_probe: PreviewCaptureProbe,
) -> None:
    probe = preview_capture_probe
    probe.capture_fault = "hang"
    task = probe.start()
    await asyncio.wait_for(probe.capture_entered.wait(), timeout=OBSERVATION_SECONDS)
    assert probe.call_id is not None
    # Observe the actual VoiceProfile row separately. The current route does
    # not lock it before capture; do not infer a voice lock from the call lock.
    async with probe.factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL lock_timeout = '100ms'"))
            await session.execute(
                select(VoiceProfile.id)
                .where(VoiceProfile.id == probe.voice_id)
                .with_for_update(key_share=True)
            )
    lock_error: DBAPIError | None = None
    try:
        async with probe.factory() as session:
            async with session.begin():
                await session.execute(text("SET LOCAL lock_timeout = '100ms'"))
                await session.execute(
                    update(ExternalCall)
                    .where(ExternalCall.id == probe.call_id)
                    .values(audio_duration_ms=100)
                )
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != "55P03":
            raise
        lock_error = error
    await probe.preview_capacity_available()
    evidence = await probe.snapshot(task)
    evidence["voice_row_lock_acquired"] = True
    evidence["external_call_update_sqlstate"] = (
        getattr(lock_error.orig, "sqlstate", None) if lock_error else None
    )
    print(json.dumps(evidence, sort_keys=True))
    assert_published_audio(evidence)
    assert lock_error is None, evidence

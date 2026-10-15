"""Pure ownership proofs for authority rollback and preview setup exceptions.

Uses the actual shared writer preparation/queue and actual file publication,
with synthetic provider/media and transactional sessions; no database or dotenv.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import Request
from pydantic import SecretStr

import jx_core.admin_routes as routes
import jx_core.data_capture.provider_writer as writer_module
from jx_core.auth.errors import APIError
from jx_core.auth.session import AuthContext
from jx_core.config import Settings
from jx_core.data_capture.provider import ProviderCallCapture
from jx_core.data_capture.provider_writer import CaptureSubmission, ProviderCaptureWriter
from jx_core.models import ExternalCall, VoiceProfile


class Transaction:
    def __init__(self, session: PreviewSession) -> None:
        self.session = session
        self.before: list[tuple[ExternalCall, str, str | None]] = []
        self.voice_before: tuple[str, Any] = ("NOT_CALIBRATED", None)

    async def __aenter__(self) -> Transaction:
        self.before = [(call, call.status, call.error_code) for call in self.session.calls]
        voice = self.session.voice
        self.voice_before = voice.calibration_status, voice.calibrated_at
        return self

    def rollback(self) -> None:
        for call, status, error_code in self.before:
            call.status, call.error_code = status, error_code
        voice = self.session.voice
        voice.calibration_status, voice.calibrated_at = self.voice_before

    async def __aexit__(self, error_type: Any, *_: Any) -> bool:
        if error_type is not None:
            self.rollback()
        elif self.session.calls[-1].status == "SUCCEEDED":
            try:
                self.session.fail("commit")
            except RuntimeError:
                self.rollback()
                raise
        return False


@dataclass
class PreviewSession:
    voice: SimpleNamespace
    calls: list[ExternalCall] = field(default_factory=lambda: list[ExternalCall]())
    fault: str | None = None
    failures_left: int = 0
    error: RuntimeError = field(default_factory=lambda: RuntimeError("synthetic-authority-error"))
    flush_error: RuntimeError | None = None

    def fail(self, stage: str) -> None:
        if self.fault == stage and self.failures_left:
            self.failures_left -= 1
            raise self.error

    async def get(self, model: type[Any], identity: Any, **_: Any) -> Any:
        if model is VoiceProfile:
            return self.voice
        return next((call for call in self.calls if call.id == identity), None)

    def add(self, call: ExternalCall) -> None:
        self.fail("add")
        self.calls.append(call)

    async def commit(self) -> None:
        self.fail("initial_commit")

    async def flush(self) -> None:
        if self.flush_error is not None and self.calls[-1].status == "FAILED":
            raise self.flush_error

    async def rollback(self) -> None:
        return None

    def begin(self) -> Transaction:
        return Transaction(self)


@dataclass
class PreviewOwnership:
    request: Request
    session: PreviewSession
    writer: ProviderCaptureWriter
    path: Path
    constructor: Mock
    synthesize: AsyncMock
    close: AsyncMock
    capture_store: AsyncMock
    captures: list[ProviderCallCapture]
    submissions: list[CaptureSubmission]
    audits: list[dict[str, Any]]

    async def run(self) -> dict[str, Any]:
        return await routes.regenerate_voice_preview(
            self.session.voice.id,
            self.request,
            AuthContext(uuid4(), "ADMIN", uuid4(), False),
            cast(Any, self.session),
        )

    async def released(self) -> None:
        async def wait() -> None:
            while self.writer.budget.items:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(wait(), 0.2)
        assert self.writer.budget.retained_bytes == 0
        assert all(capture.reservation is None for capture in self.captures)
        assert all(submission.released for submission in self.submissions)


@pytest.fixture
async def preview_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[PreviewOwnership, None]:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_env="test",
        database_url=SecretStr("postgresql+psycopg://test:test@localhost/test"),
        tts_api_key=SecretStr("synthetic-preview-key"),
    )
    writer = ProviderCaptureWriter(settings.database_url.get_secret_value())
    voice = SimpleNamespace(
        id=uuid4(),
        status="ENABLED",
        provider_voice="synthetic-voice",
        rate=1.0,
        playback_gain=1.0,
        calibration_status="NOT_CALIBRATED",
        calibrated_at=None,
    )
    session = PreviewSession(voice)
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(
                state=SimpleNamespace(settings=settings, provider_capture_writer=writer)
            ),
        }
    )
    captures: list[ProviderCallCapture] = []
    submissions: list[CaptureSubmission] = []
    original_new, original_prepare = writer.new_capture, writer.prepare

    def new_capture() -> ProviderCallCapture:
        capture = original_new()
        captures.append(capture)
        return capture

    def prepare(call: ExternalCall, capture: ProviderCallCapture) -> CaptureSubmission | None:
        submission = original_prepare(call, capture)
        if submission is not None:
            submissions.append(submission)
        return submission

    async def synthesize(*_: Any, **kwargs: Any) -> Any:
        capture = cast(ProviderCallCapture, kwargs["capture"])
        capture.capture_request(transport="WSS", url="wss://synthetic.invalid", body={"text": "QA"})
        capture.capture_response(transport="WSS", url="wss://synthetic.invalid", body={"ok": True})
        await kwargs["on_audio"](b"synthetic-media")
        return SimpleNamespace(first_audio_latency_ms=1, completed_latency_ms=2, byte_count=15)

    synthesized = AsyncMock(side_effect=synthesize)
    closed = AsyncMock()
    constructor = Mock(return_value=SimpleNamespace(synthesize=synthesized, close=closed))
    capture_store = AsyncMock()
    audits: list[dict[str, Any]] = []

    def audit_record(*_: Any, **details: Any) -> None:
        session.fail("audit")
        audits.append(details)

    path = tmp_path / "preview.ogg"

    def preview_path(*_: Any) -> Path:
        return path

    def media_gain(audio: bytes, _: float) -> bytes:
        return audio

    monkeypatch.setattr(writer, "new_capture", new_capture)
    monkeypatch.setattr(writer, "prepare", prepare)
    monkeypatch.setattr(writer, "_persist_batch", capture_store)
    monkeypatch.setattr(routes, "QwenTtsConnection", constructor)
    monkeypatch.setattr(routes, "_preview_path", preview_path)
    monkeypatch.setattr(routes, "apply_ogg_opus_gain", media_gain)
    monkeypatch.setattr(
        routes, "AuditService", Mock(return_value=SimpleNamespace(record=audit_record))
    )
    monkeypatch.setattr(writer_module, "FLUSH_SECONDS", 0.001)
    monkeypatch.setattr(writer_module, "BATCH_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(writer_module, "SHUTDOWN_SECONDS", 0.1)
    await writer.start()
    probe = PreviewOwnership(
        request,
        session,
        writer,
        path,
        constructor,
        synthesized,
        closed,
        capture_store,
        captures,
        submissions,
        audits,
    )
    try:
        yield probe
    finally:
        await writer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["add", "initial_commit", "constructor"])
async def test_preview_setup_failure_releases_capture_without_provider_or_submission(
    preview_ownership: PreviewOwnership, stage: str
) -> None:
    probe = preview_ownership
    if stage == "constructor":
        probe.constructor.side_effect = probe.session.error
    else:
        probe.session.fault, probe.session.failures_left = stage, 1
    with pytest.raises(RuntimeError) as caught:
        await probe.run()
    assert caught.value is probe.session.error
    assert len(probe.captures) == 1 and probe.captures[0].reservation is None
    assert probe.submissions == [] and probe.writer.stats().get("accepted", 0) == 0
    probe.synthesize.assert_not_awaited()
    probe.close.assert_not_awaited()
    assert probe.audits == [] and not probe.path.exists()
    await probe.released()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["audit", "commit"])
@pytest.mark.parametrize("failures", [1, 2])
async def test_preview_authority_rollback_releases_discarded_prepared_snapshot(
    preview_ownership: PreviewOwnership, stage: str, failures: int
) -> None:
    probe = preview_ownership
    probe.session.fault, probe.session.failures_left = stage, failures
    if failures == 2:
        with pytest.raises(APIError) as caught:
            await probe.run()
        assert caught.value.code == "voice_preview_failed"
        assert probe.session.voice.calibration_status == "FAILED"
    else:
        assert (await probe.run())["status"] == "ready"
        assert probe.session.voice.calibration_status == "READY"
    await probe.released()
    assert [call.status for call in probe.session.calls] == (
        ["FAILED", "FAILED"] if failures == 2 else ["FAILED", "SUCCEEDED"]
    )
    assert probe.synthesize.await_count == probe.close.await_count == 2
    discarded = [item for item in probe.submissions if not item.owned]
    assert len(discarded) == failures and all(item.released for item in discarded)
    assert probe.writer.stats().get("accepted") == 2
    assert probe.path.read_bytes() == b"synthetic-media"


@pytest.mark.asyncio
async def test_preview_failure_authority_flush_propagates_and_never_submits_rollback_snapshot(
    preview_ownership: PreviewOwnership,
) -> None:
    probe = preview_ownership
    probe.session.fault, probe.session.failures_left = "audit", 1
    probe.session.flush_error = RuntimeError("synthetic-failure-authority-flush")
    with pytest.raises(RuntimeError) as caught:
        await probe.run()
    assert caught.value is probe.session.flush_error
    assert len(probe.submissions) == 1 and probe.submissions[0].released
    assert not probe.submissions[0].owned
    assert probe.writer.stats().get("accepted", 0) == 0
    probe.capture_store.assert_not_awaited()
    probe.synthesize.assert_awaited_once()
    probe.close.assert_awaited_once()
    await probe.released()

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

import jx_core.admin_routes as routes
import jx_core.agent.llm as llm_module
import jx_core.app as app_module
import jx_core.data_capture.provider_writer as writer_module
from jx_core.agent.llm import LlmCapacityLimiter, LlmProviderError, LlmStreamResult
from jx_core.auth.dependencies import get_admin_auth, get_database_session
from jx_core.auth.errors import APIError
from jx_core.auth.session import AuthContext
from jx_core.config import Settings
from jx_core.data_capture.provider import ProviderCallCapture
from jx_core.data_capture.provider_writer import ProviderCaptureWriter
from jx_core.models import ExternalCall, ModelProfile
from jx_core.postmatch import PostmatchService
from jx_core.room_connections import RoomConnectionService


class Context:
    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False


class Session(Context):
    def __init__(self, model: SimpleNamespace) -> None:
        self.model = model
        self.calls: list[ExternalCall] = []

    async def get(self, model_type: type[Any], identity: Any, **_: Any) -> Any:
        if model_type is ModelProfile:
            return self.model
        return next((call for call in self.calls if call.id == identity), None)

    def add(self, entity: Any) -> None:
        if isinstance(entity, ExternalCall):
            self.calls.append(entity)

    async def commit(self) -> None:
        return None

    async def flush(self) -> None:
        return None

    def begin(self) -> Context:
        return Context()


@dataclass
class ModelTest:
    request: Request
    context: AuthContext
    session: Session
    limiter: LlmCapacityLimiter
    constructor: Mock
    stream: AsyncMock
    close: AsyncMock
    capture: AsyncMock
    audit: Mock
    writer: ProviderCaptureWriter

    async def run(self) -> dict[str, Any]:
        return await routes.test_model_connection(
            self.session.model.id, self.request, self.context, cast(Any, self.session)
        )


@pytest.fixture
async def model_test(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[ModelTest, None]:
    model = SimpleNamespace(
        id=uuid4(),
        status="ENABLED",
        max_concurrency=1,
        base_url="https://synthetic.invalid",
        model_id="synthetic-model",
        api_key_ciphertext=b"synthetic",
        api_key_nonce=b"synthetic",
    )
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url=SecretStr("postgresql+psycopg://test:test@localhost/test"),
        llm_key_encryption_key=SecretStr("synthetic"),
        llm_global_concurrency=2,
    )
    limiter = LlmCapacityLimiter(2)
    writer = ProviderCaptureWriter(settings.database_url.get_secret_value())
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(
                state=SimpleNamespace(
                    settings=settings,
                    llm_capacity_limiter=limiter,
                    provider_capture_writer=writer,
                )
            ),
        }
    )
    stream = AsyncMock(return_value=LlmStreamResult("连接成功", 1, 2, 1))
    close = AsyncMock()
    constructor = Mock(return_value=SimpleNamespace(stream_chat=stream, close=close))
    capture = AsyncMock()
    audit = Mock()
    monkeypatch.setattr(routes, "decrypt_secret", Mock(return_value="synthetic"))
    monkeypatch.setattr(routes, "OpenAIStreamingClient", constructor)
    # Pure tests retain the real bounded queue and ownership transfer, replacing
    # only its storage batch so they never connect to PostgreSQL.
    monkeypatch.setattr(writer, "_persist_batch", capture)
    monkeypatch.setattr(routes, "AuditService", Mock(return_value=SimpleNamespace(record=audit)))
    monkeypatch.setattr(llm_module, "LLM_CLIENT_CLOSE_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(routes, "MODEL_TEST_DIAGNOSTIC_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(writer_module, "FLUSH_SECONDS", 0.001)
    monkeypatch.setattr(writer_module, "BATCH_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(writer_module, "RETRY_SECONDS", 0.001)
    monkeypatch.setattr(writer_module, "SHUTDOWN_SECONDS", 0.1)
    await writer.start()
    case = ModelTest(
        request,
        AuthContext(uuid4(), "ADMIN", uuid4(), False),
        Session(model),
        limiter,
        constructor,
        stream,
        close,
        capture,
        audit,
        writer,
    )
    try:
        yield case
    finally:
        await writer.close()


async def wait_capture(case: ModelTest, *, terminal: bool = False) -> None:
    async def wait() -> None:
        while not case.capture.await_count or (terminal and case.writer.budget.items):
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), 0.2)


async def assert_capacity_recovered(case: ModelTest) -> None:
    first = await case.limiter.acquire(str(case.session.model.id), 1, timeout_seconds=0.1)
    try:
        second = await case.limiter.acquire("another-model", 1, timeout_seconds=0.1)
        case.limiter.release(second)
    finally:
        case.limiter.release(first)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["add", "commit"])
async def test_initial_authority_failure_closes_capture_and_preserves_exception(
    model_test: ModelTest, monkeypatch: pytest.MonkeyPatch, failure_stage: str
) -> None:
    case = model_test
    error = RuntimeError("synthetic-initial-authority-failure")
    created: list[ProviderCallCapture] = []
    new_capture = case.writer.new_capture

    def create_capture() -> ProviderCallCapture:
        capture = new_capture()
        created.append(capture)
        return capture

    capture_factory = Mock(side_effect=create_capture)
    monkeypatch.setattr(case.writer, "new_capture", capture_factory)
    monkeypatch.setattr(
        case.session,
        failure_stage,
        Mock(side_effect=error) if failure_stage == "add" else AsyncMock(side_effect=error),
    )
    with pytest.raises(RuntimeError) as caught:
        await case.run()
    assert caught.value is error
    # Retain both the exception traceback and every capture: passing cannot
    # rely on __del__/GC releasing the reservation.
    capture_factory.assert_called_once()
    assert len(created) == 1 and created[0].reservation is None
    assert case.writer.budget.items == 0 and case.writer.budget.retained_bytes == 0
    assert case.writer.stats().get("accepted", 0) == 0
    case.constructor.assert_not_called()
    case.stream.assert_not_awaited()
    case.capture.assert_not_awaited()
    case.audit.assert_not_called()
    await assert_capacity_recovered(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("full_pool", ["model", "global"])
async def test_connection_test_obeys_shared_capacity_without_constructing_provider(
    model_test: ModelTest, monkeypatch: pytest.MonkeyPatch, full_pool: str
) -> None:
    case = model_test
    held = [
        await case.limiter.acquire(
            str(case.session.model.id) if full_pool == "model" else "busy-model", 1
        )
    ]
    if full_pool == "global":
        held.append(await case.limiter.acquire("other-busy-model", 1))
    acquire = case.limiter.acquire
    queue_budgets: list[float] = []

    async def short_acquire(key: str, limit: int, timeout_seconds: float = 3.0) -> Any:
        queue_budgets.append(timeout_seconds)
        return await acquire(key, limit, timeout_seconds=0.02)

    monkeypatch.setattr(case.limiter, "acquire", short_acquire)
    try:
        with pytest.raises(APIError) as caught:
            await case.run()
        assert caught.value.code == "llm_capacity_full"
        assert queue_budgets == [3.0]
        case.constructor.assert_not_called()
        case.stream.assert_not_awaited()
        case.close.assert_not_awaited()
        assert case.session.calls[0].status == "FAILED"
        assert case.session.calls[0].error_code == "llm_capacity_full"
        assert case.audit.call_args.kwargs["details"] == {"error_code": "llm_capacity_full"}
    finally:
        for leases in held:
            case.limiter.release(leases)
    await assert_capacity_recovered(case)


@pytest.mark.asyncio
async def test_success_preserves_diagnostic_and_audit(model_test: ModelTest) -> None:
    case = model_test
    assert await case.run() == {
        "status": "ok",
        "first_token_latency_ms": 1,
        "completed_latency_ms": 2,
    }
    case.close.assert_awaited_once()
    await wait_capture(case, terminal=True)
    case.capture.assert_awaited_once()
    call = case.session.calls[0]
    assert (call.status, call.first_result_latency_ms, call.completed_latency_ms) == (
        "SUCCEEDED",
        1,
        2,
    )
    assert call.completion_tokens == 1 and call.completed_at is not None
    assert call.capture_storage_mode == "ASYNC_V1"
    assert case.audit.call_args.kwargs["action"] == "admin.model.tested"
    await assert_capacity_recovered(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_fails", [False, True])
async def test_close_failure_preserves_result_and_logs_no_raw_detail(
    model_test: ModelTest, provider_fails: bool, caplog: pytest.LogCaptureFixture
) -> None:
    case = model_test
    case.close.side_effect = RuntimeError("synthetic-private-detail")
    if provider_fails:
        case.stream.side_effect = LlmProviderError("llm_stream_stalled")
        with pytest.raises(APIError) as caught:
            await case.run()
        assert caught.value.code == "llm_stream_stalled"
        assert case.session.calls[0].status == "FAILED"
    else:
        assert (await case.run())["status"] == "ok"
        assert case.session.calls[0].status == "SUCCEEDED"
    assert "synthetic-private-detail" not in caplog.text
    cleanup_logs = [
        record
        for record in caplog.records
        if getattr(record, "error_code", None) == "llm_client_cleanup_failed"
    ]
    assert len(cleanup_logs) == 1
    assert getattr(cleanup_logs[0], "exception_type", None) == "RuntimeError"
    await assert_capacity_recovered(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_fails", [False, True])
async def test_close_hang_is_bounded_and_capacity_is_free_before_diagnosis(
    model_test: ModelTest, provider_fails: bool, caplog: pytest.LogCaptureFixture
) -> None:
    case = model_test
    close_entered, close_finished = asyncio.Event(), asyncio.Event()

    async def hang_close() -> None:
        close_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            close_finished.set()

    case.close.side_effect = hang_close
    if provider_fails:
        case.stream.side_effect = LlmProviderError("llm_provider_failed")
    task = asyncio.create_task(case.run())
    await asyncio.wait_for(close_entered.wait(), 0.1)
    case.capture.assert_not_awaited()
    await assert_capacity_recovered(case)
    if provider_fails:
        with pytest.raises(APIError) as caught:
            await asyncio.wait_for(task, 0.2)
        assert caught.value.code == "llm_provider_failed"
    else:
        assert (await asyncio.wait_for(task, 0.2))["status"] == "ok"
    await asyncio.wait_for(close_finished.wait(), 0.1)
    assert any(
        getattr(record, "error_code", None) == "llm_client_cleanup_timeout"
        for record in caplog.records
    )
    await assert_capacity_recovered(case)


@pytest.mark.asyncio
async def test_close_ignoring_cancel_returns_and_consumes_late_exception(
    model_test: ModelTest,
    caplog: pytest.LogCaptureFixture,
) -> None:
    case = model_test
    finish, finished = asyncio.Event(), asyncio.Event()
    late_errors: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    async def close() -> None:
        try:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await finish.wait()
            raise RuntimeError("synthetic-private-late-close")
        finally:
            finished.set()

    case.close.side_effect = close
    loop.set_exception_handler(lambda _, context: late_errors.append(context))
    try:
        assert (await asyncio.wait_for(case.run(), 0.2))["status"] == "ok"
        assert not finished.is_set()
        await assert_capacity_recovered(case)
    finally:
        finish.set()
        await asyncio.wait_for(finished.wait(), 0.1)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        loop.set_exception_handler(previous_handler)
    assert late_errors == []
    assert "synthetic-private-late-close" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_fails", [False, True])
async def test_slow_writer_cannot_delay_authority_result_or_hold_provider_capacity(
    model_test: ModelTest,
    provider_fails: bool,
) -> None:
    case = model_test
    entered, finish = asyncio.Event(), asyncio.Event()

    async def capture(*_: Any) -> None:
        entered.set()
        await finish.wait()

    case.capture.side_effect = capture
    if provider_fails:
        case.stream.side_effect = LlmProviderError("llm_provider_failed")
    task = asyncio.create_task(case.run())
    try:
        await asyncio.wait_for(entered.wait(), 0.1)
        case.close.assert_awaited_once()
        await assert_capacity_recovered(case)
        assert task.done()
        assert case.session.calls[0].status == ("FAILED" if provider_fails else "SUCCEEDED")
        assert case.session.calls[0].completed_at is not None
        assert case.audit.call_count == 1
    finally:
        finish.set()
    if provider_fails:
        with pytest.raises(APIError) as caught:
            await asyncio.wait_for(task, 0.2)
        assert caught.value.code == "llm_provider_failed"
    else:
        assert (await asyncio.wait_for(task, 0.2))["status"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("diagnosis", ["normal", "failure", "hang"])
async def test_constructor_failure_restores_capacity_and_preserves_exception(
    model_test: ModelTest,
    diagnosis: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    case = model_test
    error = RuntimeError("synthetic-constructor-error")
    case.constructor.side_effect = error

    async def hang_capture(*_: Any) -> None:
        await assert_capacity_recovered(case)
        await asyncio.Event().wait()

    if diagnosis == "failure":
        case.capture.side_effect = RuntimeError("synthetic-private-diagnosis")
    elif diagnosis == "hang":
        case.capture.side_effect = hang_capture
    with pytest.raises(RuntimeError) as caught:
        await asyncio.wait_for(case.run(), 0.2)
    assert caught.value is error
    case.stream.assert_not_awaited()
    case.close.assert_not_awaited()
    assert case.session.calls[0].status == "FAILED"
    assert case.session.calls[0].error_code == "llm_provider_failed"
    assert case.audit.call_args.kwargs["details"] == {"error_code": "llm_provider_failed"}
    await wait_capture(case, terminal=True)
    assert case.capture.await_count == (2 if diagnosis == "failure" else 1)
    assert "synthetic-private-diagnosis" not in caplog.text
    await assert_capacity_recovered(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("diagnosis", ["normal", "failure", "hang"])
async def test_stream_cancel_releases_resources_and_commits_before_background_capture(
    model_test: ModelTest, diagnosis: str, caplog: pytest.LogCaptureFixture
) -> None:
    case = model_test
    stream_entered = asyncio.Event()

    async def stream(**_: Any) -> None:
        stream_entered.set()
        await asyncio.Event().wait()

    async def hang_capture(*_: Any) -> None:
        await assert_capacity_recovered(case)
        await asyncio.Event().wait()

    case.stream.side_effect = stream
    if diagnosis == "failure":
        case.capture.side_effect = RuntimeError("synthetic-private-diagnosis")
    elif diagnosis == "hang":
        case.capture.side_effect = hang_capture
    task = asyncio.create_task(case.run())
    await asyncio.wait_for(stream_entered.wait(), 0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 0.2)
    case.close.assert_awaited_once()
    assert case.session.calls[0].status == "CANCELLED"
    assert case.session.calls[0].error_code == "cancelled"
    assert case.audit.call_args.kwargs["details"] == {"error_code": "cancelled"}
    await wait_capture(case, terminal=True)
    assert case.capture.await_count == (2 if diagnosis == "failure" else 1)
    assert "synthetic-private-diagnosis" not in caplog.text
    await assert_capacity_recovered(case)


@pytest.mark.asyncio
async def test_cancel_while_waiting_shared_capacity_does_not_construct_provider(
    model_test: ModelTest,
) -> None:
    case = model_test
    leases = await case.limiter.acquire(str(case.session.model.id), 1)
    task = asyncio.create_task(case.run())
    try:
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        case.constructor.assert_not_called()
        assert case.session.calls[0].status == "CANCELLED"
    finally:
        case.limiter.release(leases)
    await assert_capacity_recovered(case)


@pytest.mark.asyncio
async def test_repeated_cancel_during_close_preserves_cancel_and_recovers_capacity(
    model_test: ModelTest,
) -> None:
    case = model_test
    stream_entered, close_entered, close_finished = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )

    async def stream(**_: Any) -> None:
        stream_entered.set()
        await asyncio.Event().wait()

    async def close() -> None:
        close_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            close_finished.set()

    case.stream.side_effect = stream
    case.close.side_effect = close
    task = asyncio.create_task(case.run())
    await asyncio.wait_for(stream_entered.wait(), 0.1)
    task.cancel()
    await asyncio.wait_for(close_entered.wait(), 0.1)
    await assert_capacity_recovered(case)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 0.2)
    await asyncio.wait_for(close_finished.wait(), 0.1)
    assert case.session.calls[0].status == "CANCELLED"
    await assert_capacity_recovered(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("with_agent", [False, True])
@pytest.mark.parametrize("capture_enabled", [False, True])
@pytest.mark.parametrize(
    "failure_stage",
    [
        None,
        "manager",
        "postmatch",
        "recover",
        "recover_manager",
        "recover_postmatch",
        "recover_writer",
        "recover_diagnostic",
        "recover_runtime",
        "writer_constructor",
        "writer_start",
    ],
)
async def test_application_wires_one_limiter_for_admin_agent_and_postmatch(
    monkeypatch: pytest.MonkeyPatch,
    with_agent: bool,
    capture_enabled: bool,
    failure_stage: str | None,
) -> None:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url=SecretStr("postgresql+psycopg://test:test@localhost/test"),
        llm_key_encryption_key=SecretStr("synthetic") if with_agent else None,
        provider_capture_enabled=capture_enabled,
    )

    class SettingsSession(Context):
        async def scalars(self, *_: Any) -> Any:
            def empty_rows() -> list[Any]:
                return []

            return SimpleNamespace(all=empty_rows)

    session = SettingsSession()
    runtime = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        database=SimpleNamespace(session_factory=lambda: session),
    )
    manager = SimpleNamespace(
        agent=None,
        postmatch=None,
        recover_unfinished=AsyncMock(),
        close=AsyncMock(),
        set_presence_runtime=Mock(),
        set_terminal_callback=Mock(),
        capture_writer=None,
    )
    failure = RuntimeError("synthetic-lifecycle-failure")
    if failure_stage == "manager":
        manager.close.side_effect = failure
    elif failure_stage is not None and failure_stage.startswith("recover"):
        manager.recover_unfinished.side_effect = failure
    cleanup_error = RuntimeError("synthetic-private-cleanup-error")
    if failure_stage == "recover_manager":
        manager.close.side_effect = cleanup_error
    elif failure_stage == "recover_runtime":
        runtime.stop.side_effect = cleanup_error

    def set_agent(value: Any) -> None:
        manager.agent = value

    def set_postmatch(value: Any) -> None:
        manager.postmatch = value

    manager.set_agent_runtime = set_agent
    manager.set_postmatch_runtime = set_postmatch

    class ManagerStub:
        def __new__(cls, *_: Any, capture_writer: Any = None) -> Any:
            manager.capture_writer = capture_writer
            return manager

    diagnostic = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), configure_debug=Mock())
    if failure_stage == "recover_diagnostic":
        diagnostic.stop.side_effect = cleanup_error

    class DiagnosticStub:
        def __new__(cls, **_: Any) -> Any:
            return diagnostic

    writer = SimpleNamespace(start=AsyncMock(), close=AsyncMock())
    if failure_stage == "recover_writer":
        writer.close.side_effect = cleanup_error
    writer_constructor = Mock(return_value=writer)
    if failure_stage == "writer_constructor":
        writer_constructor.side_effect = failure
    elif failure_stage == "writer_start":
        writer.start.side_effect = failure

    class WriterStub:
        def __new__(cls, *args: Any, **kwargs: Any) -> Any:
            return writer_constructor(*args, **kwargs)

    class PostmatchStub:
        def __new__(cls, **kwargs: Any) -> Any:
            service = PostmatchService(**kwargs)
            service.close = AsyncMock(
                side_effect=failure
                if failure_stage == "postmatch"
                else cleanup_error
                if failure_stage == "recover_postmatch"
                else None
            )
            return service

    monkeypatch.setattr(app_module, "MatchRuntimeManager", ManagerStub)
    monkeypatch.setattr(RoomConnectionService, "clear_active_leases", AsyncMock(return_value=[]))
    monkeypatch.setattr(app_module, "reconcile_terminal_room_end_times", AsyncMock(return_value=0))
    monkeypatch.setattr(app_module, "DiagnosticWriter", DiagnosticStub)
    monkeypatch.setattr(app_module, "ProviderCaptureWriter", WriterStub)
    monkeypatch.setattr(app_module, "PostmatchService", PostmatchStub)
    monkeypatch.setattr(app_module, "MatchPresenceCoordinator", Mock())
    app = app_module.create_app(settings, runtime=cast(Any, runtime))

    async def lifespan() -> None:
        async with app.router.lifespan_context(app):
            limiter = app.state.llm_capacity_limiter
            assert isinstance(limiter, LlmCapacityLimiter)
            assert manager.postmatch._limiter is limiter
            assert manager.postmatch._capture_writer is (writer if capture_enabled else None)
            assert manager.capture_writer is (writer if capture_enabled else None)
            if with_agent:
                assert manager.agent.capacity_limiter is limiter
                assert manager.agent._capture_writer is (writer if capture_enabled else None)
            else:
                assert manager.agent is None

    writer_startup_failed = capture_enabled and failure_stage in {
        "writer_constructor",
        "writer_start",
    }
    failure_expected = failure_stage is not None and (
        failure_stage not in {"writer_constructor", "writer_start"} or capture_enabled
    )
    if failure_expected:
        with pytest.raises(RuntimeError) as caught:
            await lifespan()
        assert caught.value is failure
    else:
        await lifespan()
    if writer_startup_failed:
        manager.close.assert_not_awaited()
        assert manager.postmatch is None
    else:
        manager.set_terminal_callback.assert_called_once_with(
            app.state.match_emergency_coordinator.cancel
        )
        manager.close.assert_awaited_once()
        manager.postmatch.close.assert_awaited_once()
    if capture_enabled:
        writer_constructor.assert_called_once()
        if failure_stage == "writer_constructor":
            writer.start.assert_not_awaited()
            writer.close.assert_not_awaited()
        else:
            writer.start.assert_awaited_once()
            writer.close.assert_awaited_once()
    else:
        writer_constructor.assert_not_called()
        writer.start.assert_not_awaited()
        writer.close.assert_not_awaited()
    runtime.start.assert_awaited_once()
    diagnostic.stop.assert_awaited_once()
    runtime.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_model_test_uses_application_shared_limiter(
    model_test: ModelTest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = model_test
    settings = case.request.app.state.settings
    runtime = SimpleNamespace(settings=settings)
    app = app_module.create_app(settings, runtime=cast(Any, runtime))
    app.state.llm_capacity_limiter = case.limiter
    app.state.provider_capture_writer = case.writer
    app.dependency_overrides[get_admin_auth] = lambda: case.context
    app.dependency_overrides[get_database_session] = lambda: case.session
    held = await case.limiter.acquire(str(case.session.model.id), 1)
    acquire = case.limiter.acquire

    async def short_acquire(key: str, limit: int, timeout_seconds: float = 3.0) -> Any:
        assert timeout_seconds == 3.0
        return await acquire(key, limit, timeout_seconds=0.02)

    monkeypatch.setattr(case.limiter, "acquire", short_acquire)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(f"/api/admin/models/{case.session.model.id}/test")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "llm_capacity_full"
        case.constructor.assert_not_called()
    finally:
        case.limiter.release(held)
        monkeypatch.setattr(case.limiter, "acquire", acquire)
    await assert_capacity_recovered(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["provider", "capacity"])
@pytest.mark.parametrize("diagnosis", ["failure", "hang"])
async def test_http_original_failure_survives_background_writer_failure(
    model_test: ModelTest,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_kind: str,
    diagnosis: str,
) -> None:
    case = model_test
    settings = case.request.app.state.settings
    app = app_module.create_app(settings, runtime=cast(Any, SimpleNamespace(settings=settings)))
    app.state.llm_capacity_limiter = case.limiter
    app.state.provider_capture_writer = case.writer
    app.dependency_overrides[get_admin_auth] = lambda: case.context
    app.dependency_overrides[get_database_session] = lambda: case.session
    acquire = case.limiter.acquire
    route_task: asyncio.Task[Any] | None = None
    diagnostic_finished = asyncio.Event()
    expected_code = "llm_stream_stalled" if failure_kind == "provider" else "llm_capacity_full"
    held = await acquire(str(case.session.model.id), 1) if failure_kind == "capacity" else None

    async def short_acquire(key: str, limit: int, timeout_seconds: float = 3.0) -> Any:
        nonlocal route_task
        route_task = asyncio.current_task()
        assert timeout_seconds == 3.0
        return await acquire(key, limit, timeout_seconds=0.02)

    async def provider_failure(**_: Any) -> None:
        raise LlmProviderError(expected_code)

    async def diagnostic(*_: Any) -> None:
        assert asyncio.current_task() is not route_task
        try:
            if failure_kind == "provider":
                case.close.assert_awaited_once()
                model_lease = await acquire(str(case.session.model.id), 1, timeout_seconds=0.1)
                try:
                    other_lease = await acquire("other-model", 1, timeout_seconds=0.1)
                    case.limiter.release(other_lease)
                finally:
                    case.limiter.release(model_lease)
            else:
                case.constructor.assert_not_called()
                case.stream.assert_not_awaited()
                case.close.assert_not_awaited()
                remaining_lease = await acquire("other-model", 1, timeout_seconds=0.1)
                case.limiter.release(remaining_lease)
            if diagnosis == "failure":
                raise RuntimeError("synthetic-private-diagnostic")
            await asyncio.Event().wait()
        finally:
            diagnostic_finished.set()

    monkeypatch.setattr(case.limiter, "acquire", short_acquire)
    case.stream.side_effect = provider_failure
    case.capture.side_effect = diagnostic
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await asyncio.wait_for(
                client.post(f"/api/admin/models/{case.session.model.id}/test"), timeout=0.5
            )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == expected_code
        await wait_capture(case, terminal=True)
        assert diagnostic_finished.is_set()
        assert case.capture.await_count == (2 if diagnosis == "failure" else 1)
        assert case.session.calls[0].status == "FAILED"
        assert case.session.calls[0].error_code == expected_code
        assert case.audit.call_args.kwargs["details"] == {"error_code": expected_code}
        if failure_kind == "capacity":
            case.constructor.assert_not_called()
            case.stream.assert_not_awaited()
        else:
            case.stream.assert_awaited_once()
            case.close.assert_awaited_once()
        warnings = [
            record
            for record in caplog.records
            if getattr(record, "error_code", None) == "model_test_capture_failed"
        ]
        assert warnings == []
        assert case.writer.stats().get("persist_failed") == 1
        assert "synthetic-private-diagnostic" not in caplog.text
        assert "synthetic-private-diagnostic" not in response.text
    finally:
        if held is not None:
            case.limiter.release(held)
        monkeypatch.setattr(case.limiter, "acquire", acquire)
    await assert_capacity_recovered(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["provider", "constructor"])
async def test_external_cancel_during_authority_failure_commit_propagates_without_submission(
    model_test: ModelTest,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_kind: str,
) -> None:
    case = model_test
    entered, finished = asyncio.Event(), asyncio.Event()
    # Keep the normal production diagnostic budget so this cancellation is
    # explicit, rather than the timeout cancellation converted to TimeoutError.
    monkeypatch.setattr(routes, "MODEL_TEST_DIAGNOSTIC_TIMEOUT_SECONDS", 0.5)
    if failure_kind == "provider":
        case.stream.side_effect = LlmProviderError("llm_stream_stalled")
    else:
        case.constructor.side_effect = RuntimeError("synthetic-constructor-error")

    async def flush() -> None:
        assert asyncio.current_task() is route_task
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    monkeypatch.setattr(case.session, "flush", flush)
    route_task = asyncio.create_task(case.run())
    try:
        await asyncio.wait_for(entered.wait(), timeout=0.2)
        await assert_capacity_recovered(case)
        route_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(route_task, timeout=0.2)
        assert route_task.cancelled()
        assert finished.is_set()
        case.capture.assert_not_awaited()
        assert case.writer.stats().get("accepted", 0) == 0
        if failure_kind == "provider":
            case.close.assert_awaited_once()
        else:
            case.stream.assert_not_awaited()
            case.close.assert_not_awaited()
        assert not any(
            getattr(record, "error_code", None) == "model_test_capture_failed"
            for record in caplog.records
        )
    finally:
        route_task.cancel()
        await asyncio.gather(route_task, return_exceptions=True)
    await assert_capacity_recovered(case)

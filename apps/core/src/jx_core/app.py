"""FastAPI application factory for the foundation slice."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Protocol, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .admin_bulk_routes import router as admin_bulk_router
from .admin_data_routes import router as admin_data_router
from .admin_diagnostic_routes import router as admin_diagnostic_router
from .admin_routes import router as admin_router
from .agent.runtime import AgentRuntime
from .asr.runtime import AsrRuntime
from .auth.errors import APIError, error_payload, error_status
from .auth.routes import router as auth_router
from .auth.service import AuthService
from .auth.session import SessionService
from .config import Settings, load_settings
from .data_capture.diagnostics import DiagnosticWriter
from .devices.routes import router as devices_router
from .logging import (
    configure_logging,
    current_request_id,
    normalize_request_id,
    reset_request_id,
    set_request_id,
)
from .matches.routes import router as matches_router
from .matches.service import MatchRuntimeManager
from .postmatch import PostmatchService
from .postmatch_routes import router as postmatch_router
from .rooms.routes import router as rooms_router
from .rules.routes import router as rules_router
from .runtime import CoreRuntime, CoreStartupError, Readiness
from .users.avatar import AvatarService


class RuntimeProtocol(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def readiness(self) -> Readiness: ...


class LiveResponse(BaseModel):
    status: str = "alive"
    service: str = "jx-core"


class ReadyResponse(BaseModel):
    status: str = "ready"
    service: str = "jx-core"


class NotReadyResponse(BaseModel):
    status: str = "not_ready"
    service: str = "jx-core"
    error_code: str


def _request_id(request: Request) -> str:
    return normalize_request_id(request.headers.get("x-request-id"))


def create_app(
    settings: Settings | None = None,
    *,
    runtime: RuntimeProtocol | None = None,
) -> FastAPI:
    """Create an app without connecting to external services immediately."""

    resolved_settings = settings or load_settings()
    configure_logging("jx-core", resolved_settings.log_level)
    resolved_runtime = runtime or CoreRuntime(resolved_settings)
    http_logger = logging.getLogger("jx-core.http")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        try:
            await resolved_runtime.start()
        except CoreStartupError:
            # The runtime has already emitted a redacted error code. Exit
            # without allowing Uvicorn to print a traceback containing paths.
            raise SystemExit(1) from None
        database = getattr(resolved_runtime, "database", None)
        session_factory = getattr(database, "session_factory", None)
        if session_factory is not None:
            diagnostic_writer = DiagnosticWriter(
                service="jx-core",
                session_factory=cast(async_sessionmaker[AsyncSession], session_factory),
                queue_size=resolved_settings.diagnostic_queue_size,
                batch_size=resolved_settings.diagnostic_batch_size,
                flush_interval_seconds=resolved_settings.diagnostic_flush_interval_ms / 1000,
            )
            await diagnostic_writer.start()
            app.state.diagnostic_writer = diagnostic_writer
            manager = MatchRuntimeManager(cast(async_sessionmaker[AsyncSession], session_factory))
            agent_runtime: AgentRuntime | None = None
            if resolved_settings.asr_api_key is not None:
                manager.set_speech_runtime(
                    AsrRuntime(settings=resolved_settings, callbacks=manager)
                )
            if resolved_settings.llm_key_encryption_key is not None:
                agent_runtime = AgentRuntime(
                    settings=resolved_settings,
                    session_factory=cast(async_sessionmaker[AsyncSession], session_factory),
                    callbacks=manager,
                )
                manager.set_agent_runtime(agent_runtime)
            postmatch_service = PostmatchService(
                settings=resolved_settings,
                session_factory=cast(async_sessionmaker[AsyncSession], session_factory),
                limiter=agent_runtime.capacity_limiter if agent_runtime is not None else None,
            )
            manager.set_postmatch_runtime(postmatch_service)
            app.state.postmatch_service = postmatch_service
            await manager.recover_unfinished()
            app.state.match_runtime_manager = manager
        try:
            yield
        finally:
            manager = cast(MatchRuntimeManager | None, app.state.match_runtime_manager)
            if manager is not None:
                await manager.close()
            postmatch_service = cast(PostmatchService | None, app.state.postmatch_service)
            if postmatch_service is not None:
                await postmatch_service.close()
            diagnostic_writer = cast(DiagnosticWriter | None, app.state.diagnostic_writer)
            if diagnostic_writer is not None:
                await diagnostic_writer.stop()
            await resolved_runtime.stop()

    app = FastAPI(
        title="Jixia Debate Core",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.runtime = resolved_runtime
    app.state.settings = resolved_settings
    app.state.auth_service = AuthService(
        session_service=SessionService(
            ttl_seconds=resolved_settings.session_ttl_seconds,
            rolling_refresh_seconds=resolved_settings.session_rolling_refresh_seconds,
        )
    )
    app.state.avatar_service = AvatarService(resolved_settings.avatar_storage_dir)
    app.state.match_runtime_manager = None
    app.state.postmatch_service = None
    app.state.diagnostic_writer = None
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    async def _request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = _request_id(request)
        token = set_request_id(request_id)
        try:
            try:
                response = await call_next(request)
            except Exception:
                http_logger.error(
                    "http request failed",
                    extra={"error_code": "internal_server_error"},
                )
                raise
            response.headers["X-Request-ID"] = request_id
            http_logger.info("http request complete")
            return response
        finally:
            reset_request_id(token)

    app.middleware("http")(_request_context)

    async def _api_error_handler(request: Request, error: Exception) -> JSONResponse:
        if not isinstance(error, APIError):
            raise error
        request_id = current_request_id() or _request_id(request)
        return JSONResponse(
            status_code=error_status(error.code),
            content=error_payload(error.code, request_id, error.field_errors),
        )

    app.add_exception_handler(APIError, _api_error_handler)

    async def _validation_error_handler(request: Request, error: Exception) -> JSONResponse:
        if not isinstance(error, RequestValidationError):
            raise error
        field_errors: dict[str, str] = {}
        for item in error.errors():
            location = item.get("loc", ())
            if isinstance(location, (tuple, list)) and location:
                field = str(cast(object, location[-1]))
                field_errors.setdefault(field, "格式不正确")
        request_id = current_request_id() or _request_id(request)
        return JSONResponse(
            status_code=422,
            content=error_payload("validation_error", request_id, field_errors),
        )

    app.add_exception_handler(RequestValidationError, _validation_error_handler)

    async def _health_live() -> LiveResponse:
        return LiveResponse()

    app.get("/health/live", response_model=LiveResponse, tags=["health"])(_health_live)

    async def _health_ready() -> ReadyResponse | JSONResponse:
        state = await resolved_runtime.readiness()
        if state.ready:
            return ReadyResponse()
        return JSONResponse(
            status_code=503,
            content=NotReadyResponse(error_code=state.error_code or "not_ready").model_dump(),
        )

    app.get(
        "/health/ready",
        response_model=ReadyResponse,
        responses={503: {"model": NotReadyResponse}},
        tags=["health"],
    )(_health_ready)

    app.include_router(auth_router)
    app.include_router(devices_router)
    app.include_router(rules_router)
    app.include_router(rooms_router)
    app.include_router(matches_router)
    app.include_router(postmatch_router)
    app.include_router(admin_router)
    app.include_router(admin_data_router)
    app.include_router(admin_diagnostic_router)
    app.include_router(admin_bulk_router)

    return app

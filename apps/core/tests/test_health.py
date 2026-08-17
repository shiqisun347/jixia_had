from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from jx_core.app import create_app
from jx_core.config import Settings
from jx_core.runtime import Readiness


class FakeRuntime:
    def __init__(self, readiness: Readiness) -> None:
        self._readiness = readiness
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def readiness(self) -> Readiness:
        return self._readiness


def settings() -> Settings:
    return Settings(
        database_url="postgresql+psycopg://jx:secret@127.0.0.1:5432/jx_debate",
    )


@pytest.mark.asyncio
async def test_live_and_ready_health_are_typed_and_redacted() -> None:
    runtime = FakeRuntime(Readiness(True))
    app = create_app(settings(), runtime=runtime)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            live = await client.get(
                "/health/live",
                headers={"X-Request-ID": "123e4567-e89b-12d3-a456-426614174000"},
            )
            ready = await client.get("/health/ready")

    assert runtime.started is True
    assert runtime.stopped is True
    assert live.status_code == 200
    assert live.json() == {"status": "alive", "service": "jx-core"}
    assert live.headers["x-request-id"] == "123e4567-e89b-12d3-a456-426614174000"
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "service": "jx-core"}


@pytest.mark.asyncio
async def test_ready_failure_has_stable_code_without_connection_details() -> None:
    app = create_app(
        settings(),
        runtime=FakeRuntime(Readiness(False, "database_unavailable")),
    )

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "service": "jx-core",
        "error_code": "database_unavailable",
    }
    assert "secret" not in response.text
    assert "postgresql" not in response.text


@pytest.mark.asyncio
async def test_non_uuid_request_id_is_replaced_and_not_logged(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = create_app(settings(), runtime=FakeRuntime(Readiness(True)))
    user_supplied_token = "sk_live_user_controlled_token"

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get(
                "/health/live",
                headers={"X-Request-ID": user_supplied_token},
            )

    generated = response.headers["x-request-id"]
    assert generated != user_supplied_token
    assert len(generated) == 32
    assert uuid.UUID(hex=generated).hex == generated
    assert user_supplied_token not in capsys.readouterr().out


def test_openapi_contains_foundation_and_approved_auth_routes() -> None:
    app = create_app(settings(), runtime=FakeRuntime(Readiness(True)))
    paths = app.openapi()["paths"]

    assert {"/health/live", "/health/ready"}.issubset(paths)
    assert {
        "/api/legal/platform-terms/current",
        "/api/auth/register",
        "/api/auth/login",
        "/api/auth/me",
        "/api/auth/logout",
        "/api/auth/change-password",
        "/api/users/me",
        "/api/matches/{match_id}/host-audio/{action_key}",
    }.issubset(paths)

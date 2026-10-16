"""One actual Core ASGI lifespan for the isolated Spec307 qualification."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit

import httpx
import uvicorn

from jx_core.app import create_app
from jx_core.config import Settings


class OwnedCoreApp:
    def __init__(self, settings: Settings) -> None:
        database = urlsplit(settings.database_url_value)
        if (
            settings.app_env == "production"
            or settings.core_host != "127.0.0.1"
            or database.hostname != "127.0.0.1"
            or database.path != "/jx_debate_qa_agent_faults"
        ):
            raise RuntimeError("qa_owned_core_settings_required")
        self.settings = settings
        self.app = create_app(settings)
        self.server = uvicorn.Server(
            uvicorn.Config(
                self.app,
                host=settings.core_host,
                port=settings.core_port,
                log_config=None,
                access_log=False,
                timeout_graceful_shutdown=8,
                limit_concurrency=64,
            )
        )
        self.task: asyncio.Task[None] | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.settings.core_port}"

    @property
    def factory(self) -> Any:
        return self.app.state.runtime.database.session_factory

    async def start(self) -> None:
        if self.task is not None:
            raise RuntimeError("qa_core_already_started")
        self.task = asyncio.create_task(self.server.serve(), name="spec307-owned-core-app")
        async with asyncio.timeout(25):
            while not self.server.started:
                if self.task.done():
                    await self.task
                    raise RuntimeError("qa_core_lifespan_failed")
                await asyncio.sleep(0.05)
            async with httpx.AsyncClient(base_url=self.base_url, trust_env=False) as client:
                if (await client.get("/health/ready")).status_code != 200:
                    raise RuntimeError("qa_core_not_ready")

    async def close(self) -> None:
        if self.task is None:
            return
        self.server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(self.task), 20)
        finally:
            if not self.task.done():
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            self.task = None


__all__ = ["OwnedCoreApp"]

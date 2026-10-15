"""Real PG/Actor/limiter and real adapter, synthetic held HTTP transport."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from sqlalchemy import select

import jx_core.matches.emergency as emergency_module
from jx_core.agent.llm import LlmCapacityLimiter, OpenAIStreamingClient
from jx_core.matches.domain import MatchCommand
from jx_core.matches.emergency import MatchEmergencyCoordinator
from jx_core.models import ExternalCall, Match, ModelProfile, Room, Speech
from test_agent_runtime_fault_integration import AgentFaultProbe
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe
from test_match_emergency_domain import (  # pyright: ignore[reportUnknownVariableType]
    operator_payload,  # pyright: ignore[reportUnknownVariableType]
)
from test_match_handoff_recovery_integration import (
    prepare,
    start_human,  # pyright: ignore[reportUnknownVariableType]
)

pytestmark = pytest.mark.integration
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
@pytest.mark.parametrize("register_terminal_hook", [False, True])
async def test_pending_placeholder_terminal_retires_actual_provider_and_capacity(
    agent_fault_probe: AgentFaultProbe,
    monkeypatch: pytest.MonkeyPatch,
    register_terminal_hook: bool,
) -> None:
    """The no-hook control must retain the provider until explicitly cancelled.

    The hooked case goes through shared Manager.submit, the same authority used
    by HTTP and WS. Neither the Coordinator worker nor its diagnostic capture
    finalizer is mocked. Only the external HTTP boundary is synthetic.
    """
    probe = agent_fault_probe
    now, first, _, _ = await prepare(probe)
    match_id = probe.actor.state.match_id
    await start_human(probe, first)
    now[0] += 4
    async with probe.factory() as session:
        async with session.begin():
            match = await session.get(Match, match_id)
            model = await session.get(ModelProfile, probe.model_id)
            assert match is not None and model is not None
            room = await session.get(Room, match.room_id)
            assert room is not None
            room.format_snapshot = {
                "emergency_model": {
                    "id": str(model.id),
                    "model_id": model.model_id,
                    "base_url": model.base_url,
                    "max_concurrency": 1,
                    "generation_params": {},
                },
            }

    entered, cancelled = asyncio.Event(), asyncio.Event()
    clients: list[httpx.AsyncClient] = []
    adapter_closes: list[bool] = []

    async def held_provider(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "synthetic.invalid"
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("the held provider must be cancelled")

    class OwnedAdapter(OpenAIStreamingClient):
        async def close(self) -> None:
            adapter_closes.append(True)
            await self._client.aclose()

    def adapter(**arguments: Any) -> OpenAIStreamingClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(held_provider))
        clients.append(client)
        return OwnedAdapter(**arguments, client=client)

    monkeypatch.setattr(emergency_module, "OpenAIStreamingClient", adapter)
    limiter = LlmCapacityLimiter(global_limit=1)
    coordinator = MatchEmergencyCoordinator(
        settings=probe.runtime._settings,
        session_factory=probe.factory,
        manager=probe.manager,
        limiter=limiter,
        capture_writer=probe.writer,
    )
    if register_terminal_hook:
        probe.manager.set_terminal_callback(coordinator.cancel)
    try:
        await coordinator.start_skip(
            match_id, actor_user_id=first, payload=operator_payload(probe.actor)
        )
        async with asyncio.timeout(3):
            await entered.wait()
        operation = dict(probe.actor.state.emergency_operation or {})
        assert operation["status"] == "PENDING" and coordinator._tasks
        assert limiter._global.locked() and limiter._models[str(probe.model_id)].locked()
        await probe.manager.submit(
            match_id,
            MatchCommand(
                type="match.terminate",
                message_id="terminal-retirement-from-shared-manager",
                payload={"privileged": True, "reason": "owned QA terminal retirement"},
            ),
        )
        assert probe.actor.state.status == "TERMINATED"
        assert (probe.actor.state.emergency_operation or {})["status"] == "CANCELLED"
        if not register_terminal_hook:
            await asyncio.sleep(0.1)
            assert not cancelled.is_set() and coordinator._tasks
            assert limiter._global.locked() and not adapter_closes
            # Discriminating control: only this explicit cleanup retires the
            # original implementation lacking the shared terminal callback.
            await coordinator.cancel(match_id)
        async with asyncio.timeout(3):
            while coordinator._tasks or coordinator._detached or coordinator._retiring:
                await asyncio.sleep(0.01)
        assert cancelled.is_set() and adapter_closes == [True]
        assert len(clients) == 1 and clients[0].is_closed
        leases = await limiter.acquire(str(probe.model_id), 1, timeout_seconds=0.1)
        limiter.release(leases)
        assert not limiter._global.locked() and not limiter._models[str(probe.model_id)].locked()
        async with asyncio.timeout(3):
            while probe.writer.stats()["items"] or probe.writer.stats()["queued"]:
                await asyncio.sleep(0.01)
        assert probe.writer.stats()["retained_bytes"] == 0
        assert probe.writer.stats()["workspace_bytes"] == 0
        async with probe.factory() as session:
            match = await session.get(Match, match_id)
            calls = list(
                (
                    await session.scalars(
                        select(ExternalCall).where(
                            ExternalCall.match_id == match_id,
                            ExternalCall.operation == "admin.speech.placeholder",
                        )
                    )
                ).all()
            )
            placeholders = list(
                (
                    await session.scalars(
                        select(Speech).where(
                            Speech.match_id == match_id,
                            Speech.origin == "ADMIN_PLACEHOLDER",
                        )
                    )
                ).all()
            )
            assert match is not None and match.status == "TERMINATED"
            assert match.context_version == operation["context_version"]
            assert match.runtime_snapshot["emergency_operation"]["status"] == "CANCELLED"
            assert len(calls) == 1 and calls[0].status == "CANCELLED"
            assert calls[0].error_code == "placeholder_generation_failed"
            assert calls[0].completed_at is not None
            assert not placeholders
    finally:
        await coordinator.close()
        for client in clients:
            if not client.is_closed:
                await client.aclose()

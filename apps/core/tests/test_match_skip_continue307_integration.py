"""One actual ASR input owner and placeholder adapter; external transports controlled."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from typing import Any, cast

import httpx
import pytest
from sqlalchemy import select

import jx_core.matches.emergency as emergency_module
from jx_core.agent.llm import LlmCapacityLimiter, OpenAIStreamingClient
from jx_core.matches.domain import MatchCommand
from jx_core.matches.emergency import MatchEmergencyCoordinator
from jx_core.models import ExternalCall, Match, ModelProfile, Room, Speech
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe
from test_asr import FakeSocket
from test_match_asr_receiver307_integration import connect, wait
from test_match_emergency_domain import (
    operator_payload,  # pyright: ignore[reportUnknownVariableType]
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("new_pause", [False, True])
async def test_skip_exact_owner_cleanup_and_automatic_checked_continuation(
    agent_fault_probe: Any, monkeypatch: pytest.MonkeyPatch, new_pause: bool
) -> None:
    probe = agent_fault_probe
    socket = FakeSocket()

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        return socket

    runtime, receiver, stream, user = await connect(probe, monkeypatch, factory, fixed=False)
    # A natural server 3second countdown, never injected resume.elapsed.
    probe.actor._sleep = asyncio.sleep
    await probe.manager.submit(
        probe.actor.state.match_id, MatchCommand("hand.raise", "first-hand", actor_user_id=user)
    )
    await probe.manager.submit(
        probe.actor.state.match_id, MatchCommand("hand.window_closed", "first-cutoff")
    )
    await probe.manager.submit(
        probe.actor.state.match_id,
        MatchCommand("speech.start", "skip-input-start", actor_user_id=user),
    )
    old = probe.actor.state.current_speech_id
    assert old is not None
    await stream.emit()
    await wait(lambda: len(socket.audio_frames) == 1)
    async with probe.factory() as session:
        async with session.begin():
            match = await session.get(Match, probe.actor.state.match_id)
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
                }
            }
            context = match.context_version
    entered, release = asyncio.Event(), asyncio.Event()
    clients: list[httpx.AsyncClient] = []

    async def provider(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "synthetic.invalid"
        entered.set()
        await release.wait()
        content = (
            'data: {"choices":[{"delta":{"content":"管理员占位：保留现有论证并进入下一回合。"}}]}'
            "\n\ndata: [DONE]\n\n"
        )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=content.encode()
        )

    class OwnedAdapter(OpenAIStreamingClient):
        async def close(self) -> None:
            # This factory owns the injected synthetic transport client.
            await self._client.aclose()

    def adapter(**kwargs: Any) -> OpenAIStreamingClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        clients.append(client)
        return OwnedAdapter(**kwargs, client=client)

    monkeypatch.setattr(emergency_module, "OpenAIStreamingClient", adapter)
    limiter = LlmCapacityLimiter(global_limit=1)
    coordinator = MatchEmergencyCoordinator(
        settings=probe.runtime._settings,
        session_factory=probe.factory,
        manager=probe.manager,
        limiter=limiter,
        capture_writer=probe.writer,
    )
    try:
        payload = cast(dict[str, Any], operator_payload(probe.actor, context_version=context))
        await coordinator.start_skip(
            probe.actor.state.match_id, actor_user_id=user, payload=payload
        )
        await asyncio.wait_for(entered.wait(), 2)
        assert receiver._speech is None and not runtime.recovery_source_available(
            probe.actor.state.match_id, old
        )
        before_frames = len(socket.audio_frames)
        await stream.emit()
        await asyncio.sleep(0.02)
        assert len(socket.audio_frames) == before_frames
        if new_pause:
            await probe.manager.submit(
                probe.actor.state.match_id,
                MatchCommand(
                    "match.pause",
                    "explicit-pause-during-placeholder",
                    payload={"authorized": True, "privileged": True},
                ),
            )
        released_at = asyncio.get_running_loop().time()
        release.set()
        await wait(
            lambda: bool(
                probe.actor.state.emergency_operation
                and probe.actor.state.emergency_operation.get("status") == "SUCCEEDED"
            )
        )
        if new_pause:
            await asyncio.sleep(0.05)
            assert (
                probe.actor.state.status == "PAUSED"
                and probe.actor.state.action_state == "RECOVERY_REQUIRED"
            )
        else:
            await wait(lambda: probe.actor.state.status == "RUNNING", seconds=5)
            assert asyncio.get_running_loop().time() - released_at >= 2.9
            assert probe.actor.state.action_state == "FREE_SELECTING"
            assert probe.actor.state.opportunity_id is not None
        async with probe.factory() as session:
            placeholders = list(
                (
                    await session.scalars(
                        select(Speech).where(
                            Speech.match_id == probe.actor.state.match_id,
                            Speech.origin == "ADMIN_PLACEHOLDER",
                        )
                    )
                ).all()
            )
            old_speech = await session.get(Speech, old)
            tts = list(
                (
                    await session.scalars(
                        select(ExternalCall).where(
                            ExternalCall.match_id == probe.actor.state.match_id,
                            ExternalCall.call_kind == "TTS",
                        )
                    )
                ).all()
            )
        async with probe.factory() as session:
            calls = list(
                (
                    await session.scalars(
                        select(ExternalCall).where(
                            ExternalCall.match_id == probe.actor.state.match_id,
                            ExternalCall.speech_id == old,
                            ExternalCall.call_kind == "ASR",
                        )
                    )
                ).all()
            )
        assert calls and all(call.status != "STARTED" for call in calls), [
            call.status for call in calls
        ]
        assert len(placeholders) == 1 and placeholders[0].audio_duration_ms == 0 and not tts
        assert old_speech is not None and old_speech.status == "RESET"
        await wait(
            lambda: (
                not coordinator._tasks and not coordinator._detached and not coordinator._retiring
            )
        )
        assert not limiter._global.locked() and all(client.is_closed for client in clients)
    finally:
        release.set()
        await coordinator.close()
        await probe.manager.close()
        assert not runtime._receivers and stream.closed
        assert not probe.manager._asr_retry_tasks and not probe.manager._asr_final_deadlines

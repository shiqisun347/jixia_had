"""Actual Receiver/Session/Protocol/Actor/PG after reset; transport is controlled."""

from typing import Any, cast

import pytest
from sqlalchemy import select

from jx_core.matches.domain import MatchCommand
from jx_core.models import ExternalCall, Match, Speech
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

# pyright: reportPrivateUsage=false


async def test_fixed_human_after_actual_reset_registers_asr_and_finalizes(
    agent_fault_probe: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sockets: list[FakeSocket] = []

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        socket = FakeSocket()
        sockets.append(socket)
        return socket

    probe = agent_fault_probe
    runtime, receiver, stream, user = await connect(probe, monkeypatch, factory)
    match_id = probe.actor.state.match_id
    try:
        await probe.manager.submit(
            match_id, MatchCommand("speech.start", "before-reset", actor_user_id=user)
        )
        old_id = probe.actor.state.current_speech_id
        old_owner = runtime.speech_callback_envelope(match_id, old_id)
        assert old_owner is not None
        await stream.emit()
        await wait(lambda: any(socket.audio_frames for socket in sockets))
        async with probe.factory() as session:
            match = await session.get(Match, match_id)
            assert match is not None
            payload = cast(
                dict[str, Any], operator_payload(probe.actor, context_version=match.context_version)
            )
        await probe.manager.submit(
            match_id, MatchCommand("speech.reset", "actual-reset", payload=payload)
        )
        generation = probe.actor.state.opportunity_generation
        assert generation > 0 and probe.actor.state.opportunity_id is None
        await probe.manager.submit(
            match_id,
            MatchCommand(
                "match.resume", "resume-after-reset", payload={"privileged": True, "reasons": []}
            ),
        )
        await probe.manager.submit(match_id, MatchCommand("resume.elapsed", "reset-countdown-done"))
        await probe.manager.submit(
            match_id, MatchCommand("speech.start", "after-reset", actor_user_id=user)
        )
        speech_id = probe.actor.state.current_speech_id
        assert speech_id is not None and speech_id != old_id
        owner = runtime.speech_callback_envelope(match_id, speech_id)
        await stream.emit()
        await wait(lambda: len(sockets) >= 2 and bool(sockets[-1].audio_frames))
        await probe.manager.submit(
            match_id, MatchCommand("speech.finish", "finish-after-reset", actor_user_id=user)
        )
        await wait(lambda: probe.actor.state.current_action_index == 1)
        assert owner is not None and owner.opportunity_id is None
        assert owner.opportunity_generation is None
        assert probe.actor.state.opportunity_generation == generation
        await probe.manager.handle_asr_failure(envelope=old_owner, code="asr_stream_failed")
        assert probe.actor.state.status == "RUNNING"
        async with probe.factory() as session:
            source = await session.get(Speech, speech_id)
            old = await session.get(Speech, old_id)
            calls = list(
                (
                    await session.scalars(
                        select(ExternalCall).where(
                            ExternalCall.speech_id == speech_id, ExternalCall.call_kind == "ASR"
                        )
                    )
                ).all()
            )
        assert old is not None and old.status == "RESET"
        assert source is not None and source.status == "FINALIZED" and source.display_text
        assert source.attempt_no == 2 and source.audio_duration_ms == 100
        assert len(calls) == 1 and calls[0].status == "SUCCEEDED"
    finally:
        await runtime.close()
        assert stream.closed and receiver._speech is None and not runtime._receivers

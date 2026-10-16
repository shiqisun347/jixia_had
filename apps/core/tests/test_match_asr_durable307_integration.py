"""Spec307: recover a failed actual Receiver recording in a fresh authority."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import select

from jx_core.asr.protocol import FunAsrConnection
from jx_core.asr.runtime import AsrRuntime, MatchAudioReceiver
from jx_core.matches.domain import MatchCommand, MatchDomainError
from jx_core.matches.service import MatchRuntimeManager
from jx_core.models import AsrSegment, ExternalCall, Match, Speech
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


@pytest.mark.parametrize("missing_recording", [False, True])
async def test_failed_actual_recording_survives_new_manager_and_explicit_retry(
    agent_fault_probe: Any, monkeypatch: pytest.MonkeyPatch, missing_recording: bool
) -> None:
    class Broken(FakeSocket):
        async def send(self, message: str | bytes) -> None:
            if isinstance(message, bytes):
                raise OSError("controlled transport failure")
            await super().send(message)

    sockets: list[FakeSocket] = []

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        value = Broken() if len(sockets) < 2 else FakeSocket()
        sockets.append(value)
        return value

    probe = agent_fault_probe
    runtime, receiver, stream, user = await connect(probe, monkeypatch, factory)
    match_id = probe.actor.state.match_id
    fresh: MatchRuntimeManager | None = None
    try:
        await probe.manager.submit(
            match_id, MatchCommand("speech.start", "durable-original-start", actor_user_id=user)
        )
        speech_id = probe.actor.state.current_speech_id
        assert speech_id is not None
        await stream.emit()
        await wait(lambda: probe.actor.state.status == "ERROR" and receiver._speech is None)
        remaining = probe.actor.state.speech_remaining_ms
        old_owner = runtime.recovery_source_envelope(match_id, speech_id)
        assert old_owner is not None
        async with probe.factory() as session:
            source = await session.get(Speech, speech_id)
            assert source is not None and source.status == "FAILED" and source.attempt_no == 1
            assert source.audio_storage_path and source.audio_duration_ms == 100
            path = Path(source.audio_storage_path)
            original_pcm = path.read_bytes()
        assert len(original_pcm) == 3200
        await probe.manager.close()
        assert stream.closed and not runtime._receivers
        if missing_recording:
            path.unlink()

        fresh = MatchRuntimeManager(probe.factory, capture_writer=probe.writer)
        new_runtime = AsrRuntime(
            settings=runtime._settings, callbacks=fresh, capture_writer=probe.writer
        )
        fresh.set_speech_runtime(new_runtime)

        def new_connection(_receiver: MatchAudioReceiver) -> FunAsrConnection:
            return FunAsrConnection(
                url="wss://synthetic.invalid/asr",
                api_key="synthetic",
                model=new_runtime.model_id,
                socket_factory=factory,
            )

        monkeypatch.setattr(MatchAudioReceiver, "_new_provider_connection", new_connection)
        recovered = await fresh.recover_unfinished()
        assert recovered == len(fresh._actors) and match_id in fresh._actors
        actor = await fresh.get_actor(match_id)
        assert actor.state.status == "ERROR" and actor.state.current_speech_id == speech_id
        assert actor.state.asr_failed_speech_id == speech_id
        assert actor.state.speech_remaining_ms == remaining
        assert fresh.asr_recovery_available(actor.state) is not missing_recording
        assert len(sockets) == 2  # Startup restored only a disk owner, never a microphone/provider.
        assert not new_runtime.finalization_callback_is_current(envelope=old_owner)
        async with probe.factory() as session:
            match = await session.get(Match, match_id)
            assert match is not None
            payload = cast(
                dict[str, Any], operator_payload(actor, context_version=match.context_version)
            )
        if missing_recording:
            with pytest.raises(MatchDomainError, match="asr_audio_missing"):
                await fresh.submit(
                    match_id,
                    MatchCommand("asr.retry.begin", "missing-recording-retry", payload=payload),
                )
            assert actor.state.status == "ERROR" and actor.state.current_speech_id == speech_id
            assert actor.state.speech_remaining_ms == remaining and len(sockets) == 2
            assert not actor.state.emergency_operation
            return
        await fresh.submit(
            match_id,
            MatchCommand("asr.retry.begin", "retry-after-process-restart", payload=payload),
        )
        await wait(
            lambda: bool(
                actor.state.emergency_operation
                and actor.state.emergency_operation.get("status") == "SUCCEEDED"
            )
        )
        async with probe.factory() as session:
            source = await session.get(Speech, speech_id)
            assert source is not None and source.status == "FINALIZED"
            assert source.attempt_no == 1 and source.display_text
            assert source.audio_duration_ms == 100 and source.audio_storage_path == str(path)
            calls = list(
                (
                    await session.scalars(
                        select(ExternalCall).where(
                            ExternalCall.match_id == match_id,
                            ExternalCall.speech_id == speech_id,
                            ExternalCall.call_kind == "ASR",
                        )
                    )
                ).all()
            )
            segments = list(
                (
                    await session.scalars(
                        select(AsrSegment).where(AsrSegment.speech_id == speech_id)
                    )
                ).all()
            )
        assert len(calls) == len(segments) == 3
        assert sorted(row.status for row in calls) == ["FAILED", "FAILED", "SUCCEEDED"]
        assert sorted(row.segment_no for row in segments) == [1, 2, 3]
        assert len(sockets) == 3
        assert b"".join(sockets[-1].audio_frames) == original_pcm
        assert path.read_bytes() == original_pcm
        assert actor.state.current_action_index == 1
        assert not fresh._asr_retry_tasks
    finally:
        if fresh is not None:
            await fresh.close()
        await runtime.close()
        assert not runtime._receivers
        await asyncio.sleep(0)

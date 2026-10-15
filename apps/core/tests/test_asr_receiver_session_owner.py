"""Actual Receiver/Session callbacks must keep their creation owner on abort.

Only an already-owned Room and provider are synthetic. The actual Receiver,
speech session, abort, recording and callback dispatch run unchanged.
"""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import json
from collections.abc import AsyncIterable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

import jx_core.asr.runtime as runtime_module
from jx_core.asr.protocol import FunAsrConnection, FunAsrError, FunAsrSentence, SegmentResult
from jx_core.asr.runtime import MatchAudioReceiver
from jx_core.asr.session import PCM_FRAME_BYTES, AsrSpeechSession
from jx_core.data_capture.provider import ProviderCallCapture
from jx_core.runtime_identity import CallbackEnvelope
from test_asr_protocol_retirement_epoch import external_guard as external_guard
from test_asr_protocol_retirement_epoch import observe


@dataclass
class OwnedProvider:
    old_outcome: str
    retired_source: bool = True
    calls: int = 0
    close_calls: int = 0
    cancellations: int = 0
    old_entered: asyncio.Event = field(default_factory=asyncio.Event)
    old_release: asyncio.Event = field(default_factory=asyncio.Event)
    old_sentence: Any = None
    old_task_id: UUID | None = None
    replacements: list[OwnedProvider] = field(default_factory=lambda: list[OwnedProvider]())

    async def recognize_segment(
        self,
        chunks: AsyncIterable[bytes],
        *,
        on_sentence: Any,
        on_started: Any,
        task_id: UUID,
    ) -> SegmentResult:
        self.calls += 1
        old = self.retired_source and self.calls == 1
        async for chunk in chunks:
            assert len(chunk) == PCM_FRAME_BYTES
        if on_started is not None:
            on_started()
        if old:
            self.old_sentence, self.old_task_id = on_sentence, task_id
            self.old_entered.set()
            while not self.old_release.is_set():
                try:
                    await self.old_release.wait()
                except asyncio.CancelledError:
                    self.cancellations += 1
            if self.old_outcome == "failure":
                raise FunAsrError("asr_task_failed")
        return SegmentResult(
            task_id=task_id,
            final_text="retired text" if old else "current text",
            first_interim_latency_ms=1,
            final_latency_ms=1,
        )

    def take_capture(self, _: UUID) -> ProviderCallCapture | None:
        return None

    async def close(self) -> None:
        self.close_calls += 1


def envelope(match_id: UUID, speech_id: UUID, *, attempt: int) -> CallbackEnvelope:
    return CallbackEnvelope(
        match_id=match_id,
        speech_id=speech_id,
        attempt_no=attempt,
        generation_id=None,
        connection_epoch=attempt,
        context_version=attempt,
        opportunity_id=None,
        opportunity_generation=None,
    )


def callbacks_with_envelope(callbacks: AsyncMock) -> list[dict[str, Any]]:
    return [
        {"name": str(call[0]), **call.kwargs}
        for call in callbacks.mock_calls
        if "envelope" in call.kwargs
    ]


async def prepare(
    tmp_path: Path, outcome: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    MatchAudioReceiver, OwnedProvider, AsyncMock, UUID, CallbackEnvelope, asyncio.Task[None]
]:
    match_id, speech_id, user_id = uuid4(), uuid4(), uuid4()
    callbacks = AsyncMock()
    provider = OwnedProvider(outcome)

    def constructor(**kwargs: Any) -> FunAsrConnection:
        assert kwargs["url"] == "wss://synthetic.invalid/asr"
        assert kwargs["model"] == "synthetic-fixed-model"
        replacement = OwnedProvider(outcome, retired_source=False)
        provider.replacements.append(replacement)
        return cast(FunAsrConnection, cast(object, replacement))

    monkeypatch.setattr(runtime_module, "FunAsrConnection", constructor)
    receiver = MatchAudioReceiver(
        match_id=match_id,
        settings=cast(
            Any,
            SimpleNamespace(
                agent_audio_storage_dir=tmp_path,
                asr_ws_url="wss://synthetic.invalid/asr",
                asr_api_key=SecretStr("synthetic-unused-material"),
                asr_model="synthetic-fixed-model",
                asr_workspace_id=None,
            ),
        ),
        callbacks=callbacks,
    )
    receiver._room = cast(Any, SimpleNamespace(disconnect=AsyncMock()))
    receiver._connection = cast(FunAsrConnection, cast(object, provider))
    old_envelope = envelope(match_id, speech_id, attempt=1)
    await receiver.start_speech(speech_id, user_id, old_envelope)
    session = receiver._speech
    old_task = receiver._speech_task
    assert session is not None and old_task is not None
    receiver._write_recording(b"\x01" * PCM_FRAME_BYTES)
    session.feed_pcm(b"\x01" * PCM_FRAME_BYTES)
    # Mark the actual Session input ended while its actual Receiver speech Task
    # remains finalizing; Receiver.finish_speech itself waits that gated Task.
    await session.finish()
    await asyncio.wait_for(provider.old_entered.wait(), timeout=1)
    return receiver, provider, callbacks, user_id, old_envelope, old_task


async def cleanup(
    receiver: MatchAudioReceiver,
    provider: OwnedProvider,
    old_task: asyncio.Task[None],
    new_task: asyncio.Task[None] | None,
    baseline: set[asyncio.Task[Any]],
) -> None:
    await asyncio.wait_for(receiver.close(), timeout=0.4)
    provider.old_release.set()
    tasks = [old_task] + ([new_task] if new_task is not None else [])
    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=1)
    current = asyncio.current_task()
    assert await observe(lambda: not asyncio.all_tasks() - baseline - {current})
    print(
        "session_owner_fixture_observations="
        + json.dumps({"pending": 0, "recording_open": receiver._recording_file is not None})
    )
    assert receiver._recording_file is None


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure"])
async def test_aborted_old_actual_session_cannot_use_replacement_envelope_or_recording(
    monkeypatch: pytest.MonkeyPatch,
    external_guard: dict[str, int],
    tmp_path: Path,
    outcome: str,
) -> None:
    baseline = asyncio.all_tasks()
    receiver, provider, callbacks, user_id, old_envelope, old_task = await prepare(
        tmp_path, outcome, monkeypatch
    )
    new_task: asyncio.Task[None] | None = None
    try:
        await receiver.abort_speech()
        assert provider.cancellations >= 1 and not old_task.done()
        new_envelope = envelope(receiver.match_id, uuid4(), attempt=2)
        assert new_envelope.speech_id is not None
        await receiver.start_speech(new_envelope.speech_id, user_id, new_envelope)
        new_session, new_task = receiver._speech, receiver._speech_task
        assert isinstance(new_session, AsrSpeechSession) and new_task is not None
        receiver._write_recording(b"\x02" * PCM_FRAME_BYTES)
        new_session.feed_pcm(b"\x02" * PCM_FRAME_BYTES)
        storage = receiver._recording_storage_path
        assert storage is not None
        if outcome == "failure":
            await receiver.finish_speech(new_envelope.speech_id)
            await asyncio.wait_for(new_task, timeout=0.4)
            assert storage.is_file() and storage.stat().st_size == PCM_FRAME_BYTES
        else:
            assert receiver._recording_file is not None and not new_task.done()
            assert await observe(
                lambda: provider.calls + sum(item.calls for item in provider.replacements) == 2
            )
        before = len(callbacks.mock_calls)
        provider.old_release.set()
        assert await observe(old_task.done)
        observed = callbacks_with_envelope(callbacks)[0:]
        late = [call for call in callbacks.mock_calls[before:] if "envelope" in call.kwargs]
        wrong = [str(call[0]) for call in late if call.kwargs["envelope"] != old_envelope]
        snapshot = {
            "outcome": outcome,
            "late_callback_names": [str(call[0]) for call in late],
            "wrong_owner_names": wrong,
            "recording_open": receiver._recording_file is not None,
            "replacement_storage_exists": storage.exists(),
            "old_task_done": old_task.done(),
            "current_task_done": new_task.done(),
            "callback_count": len(observed),
            "guard": dict(external_guard),
        }
        print("session_owner_product_observations=" + json.dumps(snapshot, sort_keys=True))
        assert not wrong, snapshot
        if outcome == "failure":
            assert storage.is_file() and storage.stat().st_size == PCM_FRAME_BYTES, snapshot
        else:
            assert receiver._recording_file is not None and receiver._speech is new_session, (
                snapshot
            )
        assert not external_guard
    finally:
        await cleanup(receiver, provider, old_task, new_task, baseline)


@pytest.mark.asyncio
async def test_same_speech_id_new_attempt_cannot_adopt_old_session_interim(
    monkeypatch: pytest.MonkeyPatch, external_guard: dict[str, int], tmp_path: Path
) -> None:
    baseline = asyncio.all_tasks()
    receiver, provider, callbacks, user_id, old_envelope, old_task = await prepare(
        tmp_path, "success", monkeypatch
    )
    new_task: asyncio.Task[None] | None = None
    try:
        await receiver.abort_speech()
        assert provider.old_sentence is not None and provider.old_task_id is not None
        assert old_envelope.speech_id is not None
        new_envelope = envelope(receiver.match_id, old_envelope.speech_id, attempt=2)
        await receiver.start_speech(old_envelope.speech_id, user_id, new_envelope)
        new_task = receiver._speech_task
        callbacks.reset_mock()
        cancelled = False
        try:
            await provider.old_sentence(
                FunAsrSentence(
                    task_id=provider.old_task_id,
                    text="retired interim",
                    sentence_end=False,
                    heartbeat=False,
                    begin_time_ms=0,
                    end_time_ms=1,
                    sentence_id=1,
                )
            )
        except asyncio.CancelledError:
            cancelled = True
        calls = callbacks_with_envelope(callbacks)
        attempts = [call["envelope"].attempt_no for call in calls]
        print(
            "session_interim_product_observations="
            + json.dumps(
                {
                    "attempts": attempts,
                    "callback_names": [call["name"] for call in calls],
                    "retired_callback_cancelled": cancelled,
                    "guard": dict(external_guard),
                },
                sort_keys=True,
            )
        )
        assert cancelled, attempts
        assert all(call["envelope"] == old_envelope for call in calls), attempts
        assert not external_guard
    finally:
        await cleanup(receiver, provider, old_task, new_task, baseline)

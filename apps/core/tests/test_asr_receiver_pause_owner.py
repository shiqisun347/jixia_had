"""Abort must revoke the old public pause intent before starting a new Session."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import json
from pathlib import Path

import pytest

from jx_core.asr.session import PCM_FRAME_BYTES
from test_asr_protocol_retirement_epoch import external_guard as external_guard
from test_asr_protocol_retirement_epoch import observe
from test_asr_receiver_session_owner import cleanup, envelope, prepare


@pytest.mark.asyncio
async def test_public_old_pause_then_abort_cannot_pause_new_session_completion(
    monkeypatch: pytest.MonkeyPatch, external_guard: dict[str, int], tmp_path: Path
) -> None:
    baseline = asyncio.all_tasks()
    receiver, provider, callbacks, user_id, old_envelope, old_task = await prepare(
        tmp_path, "success", monkeypatch
    )
    assert old_envelope.speech_id is not None
    pause_task = asyncio.create_task(receiver.pause_speech(old_envelope.speech_id))
    new_task = None
    try:
        assert await observe(lambda: receiver._pause_requested)
        await receiver.abort_speech()
        assert not old_task.done() and not provider.old_release.is_set()
        new_envelope = envelope(receiver.match_id, old_envelope.speech_id, attempt=2)
        await receiver.start_speech(old_envelope.speech_id, user_id, new_envelope)
        new_task, session = receiver._speech_task, receiver._speech
        assert new_task is not None and session is not None
        receiver._write_recording(b"\x03" * PCM_FRAME_BYTES)
        session.feed_pcm(b"\x03" * PCM_FRAME_BYTES)
        await asyncio.wait_for(receiver.finish_speech(old_envelope.speech_id), timeout=0.4)
        snapshot = {
            "final_callback_count": callbacks.finalize_asr_speech.await_count,
            "current_checkpoint_created": receiver._paused_speech is not None,
            "old_task_pending": not old_task.done(),
            "old_gate_held": not provider.old_release.is_set(),
            "new_task_done": new_task.done(),
            "guard": dict(external_guard),
        }
        print("pause_owner_product_observations=" + json.dumps(snapshot, sort_keys=True))
        assert callbacks.finalize_asr_speech.await_count == 1, snapshot
        assert callbacks.finalize_asr_speech.await_args is not None
        assert callbacks.finalize_asr_speech.await_args.kwargs["envelope"] == new_envelope
        assert receiver._paused_speech is None, snapshot
        assert not old_task.done() and not provider.old_release.is_set(), snapshot
        assert not external_guard
    finally:
        await cleanup(receiver, provider, old_task, new_task, baseline)
        await asyncio.wait_for(asyncio.gather(pause_task, return_exceptions=True), timeout=0.5)

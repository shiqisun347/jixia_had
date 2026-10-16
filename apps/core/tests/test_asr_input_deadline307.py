"""Committed Core input cutoffs, independent of provider finish and SQL waits."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from dataclasses import replace
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

import pytest

from jx_core.asr.protocol import FunAsrConnection, FunAsrError
from jx_core.asr.session import FINALIZATION_SECONDS, AsrQueueFull
from test_asr import FakeSocket
from test_asr_retained_recovery import envelope, make_receiver


async def prepare(tmp_path: Path, clock: list[float]):
    receiver, callbacks = make_receiver(tmp_path)
    receiver._input_clock = lambda: clock[0]
    socket = FakeSocket()

    async def factory(_url: str, _headers: dict[str, str]) -> FakeSocket:
        return socket

    def connection() -> FunAsrConnection:
        return FunAsrConnection(url="wss://owned.test", api_key="synthetic", socket_factory=factory)

    receiver._connection = connection()
    receiver._new_provider_connection = connection
    identity = envelope(receiver)
    assert identity.speech_id is not None
    await receiver.start_speech(
        identity.speech_id,
        uuid4(),
        identity,
        max_audio_duration_ms=100,
        defer_registration=True,
    )
    session = receiver._speech
    owner = receiver.speech_callback_envelope(identity.speech_id)
    assert session is not None and owner is not None
    await session.wait_ready()
    return receiver, callbacks, socket, session, owner


@pytest.mark.parametrize("mismatch", ["clone", "epoch", "speech"])
async def test_input_admission_requires_exact_owner_and_installs_deadline_first(
    tmp_path: Path, mismatch: str
) -> None:
    baseline = set(asyncio.all_tasks())
    clock = [100.0]
    receiver, callbacks, _socket, session, owner = await prepare(tmp_path, clock)
    assert owner.speech_id is not None
    try:
        wrong = replace(
            owner,
            **(
                {"connection_epoch": 4}
                if mismatch == "epoch"
                else {"speech_id": uuid4()}
                if mismatch == "speech"
                else {}
            ),
        )
        with pytest.raises(FunAsrError, match="asr_callback_identity_mismatch"):
            receiver.commit_speech_start(
                owner.speech_id,
                envelope=wrong,
                input_deadline_mono=110,
                timer_message_id="owned-start",
            )
        assert not session.input_admitted and session.input_deadline_mono is None

        async def started(**_kwargs: Any) -> None:
            assert session.input_deadline_mono == 110 and session.input_admitted

        callbacks.start_asr_segment.side_effect = started
        deadline = receiver.commit_speech_start(
            owner.speech_id,
            envelope=owner,
            input_deadline_mono=110,
            timer_message_id="owned-start",
        )
        assert 29.8 < deadline - monotonic() <= 30
        assert FINALIZATION_SECONDS == 20
        assert session.feed_pcm(b"\x01\x00" * 1600)
        await receiver.finish_speech(owner.speech_id)
        assert callbacks.start_asr_segment.await_count == 1
        assert callbacks.finalize_asr_speech.await_count == 1
    finally:
        await receiver.close()
        await asyncio.sleep(0)
        assert not [t for t in asyncio.all_tasks() - baseline if not t.done()]


async def test_expired_input_seals_without_provider_finish_or_final_callback(
    tmp_path: Path,
) -> None:
    clock = [100.0]
    receiver, callbacks, socket, session, owner = await prepare(tmp_path, clock)
    assert owner.speech_id is not None
    try:
        receiver.commit_speech_start(
            owner.speech_id,
            envelope=owner,
            input_deadline_mono=110,
            timer_message_id="owned-start",
        )
        pcm = b"\x01\x00" * 1600
        assert session.feed_pcm(pcm)
        receiver._write_recording(pcm)
        with pytest.raises(AsrQueueFull):
            session.feed_pcm(pcm)  # truly before the authority cutoff
        clock[0] = 110
        before = monotonic()
        assert not session.feed_pcm(pcm)
        assert not session.input_admitted and not session.input_closed
        assert session.pcm_snapshot() == pcm
        assert session.finalization_deadline is not None
        assert 19.8 < session.finalization_deadline - before <= 20.01
        await asyncio.sleep(0.01)
        assert "finish-task" not in socket.sent_actions
        assert callbacks.finalize_asr_speech.await_count == 0
        assert callbacks.handle_asr_failure.await_count == 0
        await receiver.finish_speech(owner.speech_id, deadline_mono=monotonic() + 100)
        assert callbacks.finalize_asr_speech.await_count == 1
        assert (
            Path(callbacks.finalize_asr_speech.await_args.kwargs["audio_storage_path"]).read_bytes()
            == pcm
        )
    finally:
        await receiver.close()


async def test_old_deadline_cannot_seal_replacement_same_speech_new_epoch(tmp_path: Path) -> None:
    baseline = set(asyncio.all_tasks())
    clock = [100.0]
    receiver, callbacks, _socket, old, owner = await prepare(tmp_path, clock)
    assert owner.speech_id is not None
    try:
        receiver.commit_speech_start(
            owner.speech_id,
            envelope=owner,
            input_deadline_mono=101,
            timer_message_id="old-cycle",
        )
        old_watchdog = receiver._input_deadline_task
        await receiver.abort_speech()
        new_identity = replace(owner, connection_epoch=4)
        await receiver.start_speech(owner.speech_id, uuid4(), new_identity, defer_registration=True)
        current = receiver._speech
        new_owner = receiver.speech_callback_envelope(owner.speech_id)
        assert current is not None and current is not old and new_owner is not None
        receiver.commit_speech_start(
            owner.speech_id,
            envelope=new_owner,
            input_deadline_mono=111,
            timer_message_id="new-cycle",
        )
        clock[0] = 102
        await asyncio.sleep(0.01)
        assert old_watchdog is not None and old_watchdog.done()
        assert current.input_admitted and current.feed_pcm(b"\x01\x00" * 1600)
        assert receiver.expired_input_owner("old-cycle") is None
        assert callbacks.handle_asr_failure.await_count == 0
        await receiver.finish_speech(owner.speech_id)
        assert callbacks.finalize_asr_speech.await_count == 1
    finally:
        await receiver.close()
        await asyncio.sleep(0)
        assert not [t for t in asyncio.all_tasks() - baseline if not t.done()]


async def test_pause_checkpoint_resume_installs_new_cutoff_without_renewing_old_task(
    tmp_path: Path,
) -> None:
    baseline = set(asyncio.all_tasks())
    clock = [100.0]
    receiver, callbacks, _socket, session, owner = await prepare(tmp_path, clock)
    assert owner.speech_id is not None
    user = receiver._speaker_user_id
    assert user is not None
    try:
        receiver.commit_speech_start(
            owner.speech_id,
            envelope=owner,
            input_deadline_mono=101,
            timer_message_id="pause-cycle",
        )
        assert session.feed_pcm(b"\x01\x00" * 1600)
        clock[0] = 100.5
        early = monotonic()
        await receiver.pause_speech(owner.speech_id)
        assert session.finalization_deadline is not None
        assert session.finalization_deadline <= early + 20.01
        assert receiver._paused_speech is not None
        assert callbacks.finalize_asr_speech.await_count == 0
        await receiver.start_speech(
            owner.speech_id,
            user,
            replace(owner, connection_epoch=4),
            max_audio_duration_ms=100,
            defer_registration=True,
        )
        current = receiver._speech
        new_owner = receiver.speech_callback_envelope(owner.speech_id)
        assert current is not None and current is not session and new_owner is not None
        assert not current.input_admitted
        receiver.commit_speech_start(
            owner.speech_id,
            envelope=new_owner,
            input_deadline_mono=111,
            timer_message_id="resumed-cycle",
        )
        clock[0] = 102
        assert current.input_admitted and current.feed_pcm(b"\x02\x00" * 1600)
        await receiver.finish_speech(owner.speech_id)
        assert callbacks.finalize_asr_speech.await_count == 1
        assert callbacks.finalize_asr_speech.await_args.kwargs["audio_duration_ms"] == 200
    finally:
        await receiver.close()
        await asyncio.sleep(0)
        assert not [t for t in asyncio.all_tasks() - baseline if not t.done()]

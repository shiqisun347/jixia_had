"""Public lifecycle regressions for synchronous media cleanup failures."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from jx_core.agent.runtime import AgentRun, AgentRuntime, HostAudioRun
from jx_core.config import Settings
from jx_core.matches.domain import MatchAction, MatchActor, MatchCommand, MatchRuntimeState
from jx_core.matches.service import MatchRuntimeManager
from jx_core.runtime_identity import CallbackEnvelope


def make_runtime(callbacks: Any) -> AgentRuntime:
    return AgentRuntime(
        settings=cast(Any, Settings)(
            _env_file=None,
            database_url=SecretStr("postgresql+psycopg://test:test@localhost/test"),
        ),
        session_factory=cast(Any, None),
        callbacks=callbacks,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("media_kind", ["agent", "host"])
@pytest.mark.parametrize("queue_fails", [False, True])
async def test_pause_committed_event_is_broadcast_when_media_queue_clear_fails(
    media_kind: Literal["agent", "host"], queue_fails: bool, caplog: pytest.LogCaptureFixture
) -> None:
    # The real Actor invokes the real manager publish/fence path after commit.
    # The commit hook records the authority boundary without requiring a DB.
    manager = MatchRuntimeManager(cast(Any, None))
    runtime = make_runtime(manager)
    manager.set_agent_runtime(runtime)
    match_id, agent_id = uuid4(), uuid4()
    source = SimpleNamespace(
        clear_queue=Mock(
            side_effect=RuntimeError("synthetic-private-clear-failure") if queue_fails else None
        ),
        aclose=AsyncMock(),
    )
    if media_kind == "agent":
        run = AgentRun(
            match_id=match_id,
            action_key="1:1",
            agent_profile_id=agent_id,
            duration_ms=30_000,
            source=cast(Any, source),
        )
        runtime._runs[match_id] = run  # pyright: ignore[reportPrivateUsage]
    else:
        run = HostAudioRun(
            match_id=match_id,
            action_key="1:1",
            storage_path="synthetic.ogg",
            duration_ms=1000,
            context_version=0,
            source=cast(Any, source),
        )
        runtime._host_runs[match_id] = run  # pyright: ignore[reportPrivateUsage]
    state = MatchRuntimeState(
        match_id=match_id,
        status="RUNNING",
        action_state="AGENT_PREPARING",
        actions=(
            MatchAction(
                stage_position=1,
                action_position=1,
                action_kind="AGENT_SPEECH",
                duration_seconds=30,
                agent_profile_id=agent_id,
                speaker_kind="AGENT",
            ),
        ),
        current_agent_profile_id=agent_id,
        speech_remaining_ms=30_000,
    )
    commit = AsyncMock()
    actor = MatchActor(state, commit=commit, publish=manager._publish)  # pyright: ignore[reportPrivateUsage]
    await actor.start()
    queue = await manager.subscribe(match_id)
    try:
        result = await actor.submit(
            MatchCommand(
                type="match.pause",
                message_id="synthetic-media-pause",
                actor_user_id=uuid4(),
                payload={"authorized": True},
            )
        )
        commit.assert_awaited_once()
        assert commit.await_args is not None
        assert commit.await_args.args[1].status == "PAUSED"
        assert result.state.status == actor.state.status == "PAUSED"
        event = queue.get_nowait()
        assert event.type == "match.paused" and event.sequence == actor.state.sequence == 1
        assert run.media_fenced
        assert "synthetic-private-clear-failure" not in caplog.text
        if queue_fails:
            assert any(
                getattr(record, "error_code", None) == "agent_resource_cleanup_failed"
                for record in caplog.records
            )
        # Publishing must also schedule the existing interrupted-run reset.
        tasks = tuple(manager._background_tasks)  # pyright: ignore[reportPrivateUsage]
        assert tasks
        await asyncio.gather(*tasks)
    finally:
        source.clear_queue.side_effect = None
        await manager.close()
        await actor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_step", ["none", "finish_input", "clear_queue"])
async def test_public_agent_finalizer_closes_remaining_media_after_decoder_input_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failed_step: Literal["none", "finish_input", "clear_queue"],
) -> None:
    callbacks = AsyncMock()
    runtime = make_runtime(callbacks)
    spool_path, storage_path = tmp_path / "synthetic.part", tmp_path / "synthetic.ogg"
    spool_path.write_bytes(b"synthetic-audio")
    decoder = SimpleNamespace(
        finish_input=Mock(
            side_effect=(
                RuntimeError("synthetic-private-finish-failure")
                if failed_step == "finish_input"
                else None
            )
        ),
        close=AsyncMock(),
    )
    source = SimpleNamespace(
        capture_frame=AsyncMock(),
        wait_for_playout=AsyncMock(),
        clear_queue=Mock(
            side_effect=(
                RuntimeError("synthetic-private-clear-failure")
                if failed_step == "clear_queue"
                else None
            )
        ),
        aclose=AsyncMock(),
    )
    room = SimpleNamespace(disconnect=AsyncMock())
    run = AgentRun(
        match_id=uuid4(),
        action_key="1:1",
        agent_profile_id=uuid4(),
        duration_ms=1000,
        generation_id=uuid4(),
        speech_id=uuid4(),
        draft_parts=["Synthetic full transcript"],
        spool_path=spool_path,
        storage_path=storage_path,
        played_samples=48_000,
        natural_complete=failed_step != "clear_queue",
        decoder=cast(Any, decoder),
        source=cast(Any, source),
        room=cast(Any, room),
    )
    run.callback_envelope = CallbackEnvelope(
        match_id=run.match_id,
        speech_id=run.speech_id,
        attempt_no=1,
        generation_id=run.generation_id,
        connection_epoch=None,
        context_version=0,
        opportunity_id=None,
        opportunity_generation=None,
    )
    runtime._runs[run.match_id] = run  # pyright: ignore[reportPrivateUsage]
    persist = AsyncMock()
    monkeypatch.setattr(runtime, "_persist_final", persist)
    assert run.speech_id is not None
    await runtime.finalize_agent(
        run.match_id, run.speech_id, "TIME_LIMIT" if failed_step == "clear_queue" else "COMPLETED"
    )
    persist.assert_awaited_once_with(run)
    callbacks.finalize_agent_speech.assert_awaited_once()
    callbacks.handle_agent_failure.assert_not_awaited()
    assert storage_path.read_bytes() == b"synthetic-audio" and not spool_path.exists()
    decoder.close.assert_awaited_once()
    source.aclose.assert_awaited_once()
    room.disconnect.assert_awaited_once()
    assert run.decoder is None and run.source is None and run.room is None
    assert "synthetic-private-finish-failure" not in caplog.text
    assert "synthetic-private-clear-failure" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("queue_fails", [False, True])
async def test_public_host_reset_cancels_task_and_closes_media_after_queue_clear_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, queue_fails: bool
) -> None:
    runtime = make_runtime(AsyncMock())
    decoder = SimpleNamespace(close=AsyncMock())
    source = SimpleNamespace(
        clear_queue=Mock(
            side_effect=RuntimeError("synthetic-private-clear-failure") if queue_fails else None
        ),
        aclose=AsyncMock(),
    )
    room = SimpleNamespace(disconnect=AsyncMock())
    spool_path = tmp_path / "synthetic-host.ogg"
    spool_path.write_bytes(b"synthetic-host-audio")
    run = HostAudioRun(
        match_id=uuid4(),
        action_key="1:1",
        storage_path="synthetic.ogg",
        duration_ms=1000,
        context_version=0,
        decoder=cast(Any, decoder),
        source=cast(Any, source),
        room=cast(Any, room),
        spool_path=spool_path,
    )
    started = asyncio.Event()

    async def live_host_task() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(live_host_task())
    run.task = task
    runtime._host_runs[run.match_id] = run  # pyright: ignore[reportPrivateUsage]
    await started.wait()
    try:
        await runtime.reset_host(run.match_id)
        assert task.cancelled() and run.media_fenced
        assert run.match_id not in runtime._host_runs  # pyright: ignore[reportPrivateUsage]
        decoder.close.assert_awaited_once()
        source.aclose.assert_awaited_once()
        room.disconnect.assert_awaited_once()
        assert run.decoder is None and run.source is None and run.room is None
        assert run.spool_path is None and not spool_path.exists()
        assert "synthetic-private-clear-failure" not in caplog.text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_public_host_reset_preserves_external_cancel_during_native_close() -> None:
    runtime = make_runtime(AsyncMock())
    closing = asyncio.Event()

    async def native_close() -> None:
        closing.set()
        await asyncio.Event().wait()

    source = SimpleNamespace(
        clear_queue=Mock(side_effect=RuntimeError("synthetic-private-clear-failure")),
        aclose=AsyncMock(),
    )
    run = HostAudioRun(
        match_id=uuid4(),
        action_key="1:1",
        storage_path="synthetic.ogg",
        duration_ms=1000,
        context_version=0,
        source=cast(Any, source),
        decoder=cast(Any, SimpleNamespace(close=native_close)),
    )
    runtime._host_runs[run.match_id] = run  # pyright: ignore[reportPrivateUsage]
    reset = asyncio.create_task(runtime.reset_host(run.match_id))
    try:
        await asyncio.wait_for(closing.wait(), timeout=0.5)
        reset.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reset
        assert run.media_fenced and reset.cancelled()
        assert run.match_id not in runtime._host_runs  # pyright: ignore[reportPrivateUsage]
    finally:
        reset.cancel()
        await asyncio.gather(reset, return_exceptions=True)

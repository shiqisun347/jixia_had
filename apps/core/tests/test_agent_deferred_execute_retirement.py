"""Retired startup boundaries cannot allocate the next media/provider resource."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

import jx_core.agent.runtime as runtime_module
from jx_core.agent.runtime import AgentRun
from test_agent_late_result import LateResultProbe
from test_agent_late_result import late_result_probe as late_result_probe

# The original executor and retry path run; only resource edges are synthetic.
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["entry", "decoder", "publisher"])
@pytest.mark.parametrize("retirement", ["media", "generation", "registry"])
async def test_retired_executor_rechecks_before_next_resource(
    late_result_probe: LateResultProbe,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    retirement: str,
) -> None:
    probe = late_result_probe
    entered, release = asyncio.Event(), asyncio.Event()
    events: list[str] = []

    class Decoder:
        def __init__(self) -> None:
            events.append("decoder_construct")

        async def start(self, _: Path) -> None:
            events.append("decoder_start")
            if boundary == "decoder":
                entered.set()
                await release.wait()

        def finish_input(self) -> None:
            pass

        async def close(self) -> None:
            pass

    async def publisher(*_: Any, **__: Any) -> Any:
        events.append("publisher")
        if boundary == "publisher":
            entered.set()
            await release.wait()
        return (
            SimpleNamespace(disconnect=AsyncMock()),
            SimpleNamespace(clear_queue=Mock(), aclose=AsyncMock()),
        )

    def tts(**_: Any) -> SimpleNamespace:
        events.append("tts_connection")
        return SimpleNamespace(close=AsyncMock())

    monkeypatch.setattr(runtime_module, "IncrementalOpusDecoder", Decoder)
    monkeypatch.setattr(runtime_module, "QwenTtsConnection", tts)
    monkeypatch.setattr(probe.runtime, "_connect_publisher", publisher)
    generation_id = uuid4()
    run = AgentRun(
        match_id=probe.config.match_id,
        action_key=probe.config.action_key,
        agent_profile_id=probe.config.agent_profile_id,
        duration_ms=12_000,
        generation_id=generation_id,
    )
    probe.runs.append(run)
    probe.runtime._runs[run.match_id] = run

    def retire() -> None:
        if retirement == "media":
            run.media_fenced = True
        elif retirement == "generation":
            run.generation_id = uuid4()
        else:
            probe.runtime._runs.pop(run.match_id)

    if boundary == "entry":
        retire()
    task = asyncio.create_task(probe.runtime._execute(run, probe.config, generation_id))
    try:
        if boundary != "entry":
            await asyncio.wait_for(entered.wait(), 1)
            retire()
            release.set()
        outcome = (await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1))[0]
        expected = [] if boundary == "entry" else ["decoder_construct", "decoder_start"]
        if boundary == "publisher":
            expected.append("publisher")
        assert events == expected, "retired startup allocated its next media resource"
        cast(AsyncMock, probe.runtime._start_generation_call).assert_not_awaited()
        assert not probe.clients
        assert isinstance(outcome, asyncio.CancelledError)
        if boundary == "entry":
            assert run.spool_path is None and run.decoder is None and run.room is None
            assert not (
                Path(probe.runtime._settings.agent_audio_storage_dir) / str(run.match_id)
            ).exists()
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_generation_commit_return_is_rechecked_before_calling_executor(
    late_result_probe: LateResultProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = late_result_probe
    entered, release = asyncio.Event(), asyncio.Event()
    original = probe.runtime._create_generation

    async def generation(*values: Any) -> Any:
        result = await original(*values)
        entered.set()
        await release.wait()
        return result

    executor = AsyncMock()
    monkeypatch.setattr(probe.runtime, "_create_generation", generation)
    monkeypatch.setattr(probe.runtime, "_execute", executor)
    run = await probe.start()
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await probe.runtime.fence_agent(run.match_id)
        release.set()
        assert run.task is not None
        await asyncio.wait_for(asyncio.gather(run.task, return_exceptions=True), 1)
        executor.assert_not_awaited()
        assert run.spool_path is None and run.decoder is None and run.room is None
        assert not probe.clients
    finally:
        release.set()

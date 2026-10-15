"""An interruption rejects old provider callbacks before awaiting media cleanup."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

import jx_core.agent.runtime as runtime_module
from jx_core.agent.llm import LlmStreamResult
from jx_core.agent.runtime import AgentRun
from test_agent_late_result import LateResultProbe
from test_agent_late_result import late_result_probe as late_result_probe

# The provider/codec/RTC edges are synthetic; the original runtime guards run.
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
async def test_preparing_provider_delta_cannot_start_tts_while_fence_waits_for_media_lock(
    late_result_probe: LateResultProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = late_result_probe

    async def stream(**values: Any) -> LlmStreamResult:
        probe.provider_entered.set()
        await probe.release_provider.wait()
        await values["on_delta"]("这是旧代次第一次迟到文本。")
        await asyncio.Event().wait()
        raise AssertionError("this owned held provider must be retired")

    def client(**_: Any) -> SimpleNamespace:
        return SimpleNamespace(stream_chat=stream, close=AsyncMock())

    monkeypatch.setattr(runtime_module, "OpenAIStreamingClient", client)
    run = await probe.start()
    await asyncio.wait_for(probe.provider_entered.wait(), 1)
    await probe.runtime._media_lock.acquire()
    fence = asyncio.create_task(probe.runtime.fence_agent(run.match_id))
    try:
        await asyncio.sleep(0)
        assert not fence.done(), "the old media lock is intentionally still occupied"
        probe.release_provider.set()
        await asyncio.sleep(0.03)
        cast(AsyncMock, probe.runtime._start_tts_call).assert_not_awaited()
        probe.callbacks.publish_agent_text_delta.assert_not_awaited()
        assert run.draft == "" and run.media_fenced
        assert run.task is not None and run.task.done() and run.task.cancelled()
    finally:
        probe.runtime._media_lock.release()
        await asyncio.wait_for(fence, 1)


@pytest.mark.asyncio
async def test_waiting_fence_keeps_only_its_old_run_and_does_not_fence_replacement(
    late_result_probe: LateResultProbe,
) -> None:
    probe = late_result_probe
    match_id = probe.config.match_id
    old_source = SimpleNamespace(clear_queue=Mock(), aclose=AsyncMock())
    new_source = SimpleNamespace(clear_queue=Mock(), aclose=AsyncMock())
    old = AgentRun(
        match_id=match_id,
        action_key="1:1",
        agent_profile_id=probe.config.agent_profile_id,
        duration_ms=12_000,
        source=cast(Any, old_source),
    )
    replacement = AgentRun(
        match_id=match_id,
        action_key="1:1",
        agent_profile_id=uuid4(),
        duration_ms=12_000,
        source=cast(Any, new_source),
    )
    probe.runtime._runs[match_id] = old
    await probe.runtime._media_lock.acquire()
    fence = asyncio.create_task(probe.runtime.fence_agent(match_id))
    try:
        await asyncio.sleep(0)
        assert old.media_fenced, "old run must be fenced before the first cleanup await"
        probe.runtime._runs[match_id] = replacement
    finally:
        probe.runtime._media_lock.release()
        await asyncio.wait_for(fence, 1)
    assert probe.runtime._runs[match_id] is replacement
    assert not replacement.media_fenced
    old_source.clear_queue.assert_called_once()
    new_source.clear_queue.assert_not_called()

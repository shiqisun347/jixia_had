"""Retirement keeps exact persistence ownership without hiding failed generations."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import jx_core.agent.runtime as runtime_module
from test_agent_audio_tail import TailProbe
from test_agent_audio_tail import tail_probe as tail_probe

# Exercise public reset/finalize orchestration; database commit orders have PG controls.
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
@pytest.mark.parametrize("persist_first", [False, True], ids=["cancel_first", "persist_first"])
async def test_reset_cannot_steal_generation_from_live_persistence_owner(
    tail_probe: TailProbe, monkeypatch: pytest.MonkeyPatch, persist_first: bool
) -> None:
    probe = tail_probe
    entered, release = asyncio.Event(), asyncio.Event()
    row = SimpleNamespace(status="PLAYING", error_code=None)

    async def persist(_: Any) -> None:
        entered.set()
        await release.wait()
        row.status = "FINALIZED"

    async def failed(_: Any, error_code: str, **__: Any) -> None:
        if row.status not in {"FINALIZED", "FAILED"}:
            row.status, row.error_code = "FAILED", error_code

    marker = AsyncMock(side_effect=failed)
    monkeypatch.setattr(probe.runtime, "_persist_final", persist)
    monkeypatch.setattr(probe.runtime, "_mark_generation_failed", marker)
    task = asyncio.create_task(probe.finalize())
    try:
        await asyncio.wait_for(entered.wait(), 0.3)
        if persist_first:
            release.set()
            await asyncio.wait_for(task, 0.3)
        await asyncio.wait_for(probe.runtime.reset_agent(probe.run.match_id), 0.3)
        assert probe.run.match_id not in probe.runtime._runs
        assert probe.run.storage_path is not None
        assert probe.run.storage_path.read_bytes() == b"original compressed content"
        if not persist_first:
            assert probe.run.media_fenced and probe.source.close_calls >= 1
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 0.3)
            probe.callbacks.finalize_agent_speech.assert_not_awaited()
        assert row.status == "FINALIZED" and row.error_code is None
        marker.assert_not_awaited()
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["failure", "timeout", "cancel", "committed_cancel"])
async def test_retired_persistence_owner_still_terminalizes_its_generation_on_abort(
    tail_probe: TailProbe, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    probe = tail_probe
    entered, release = asyncio.Event(), asyncio.Event()
    row = SimpleNamespace(status="PLAYING", error_code=None)

    async def persist(_: Any) -> None:
        entered.set()
        await release.wait()
        if fault == "failure":
            raise RuntimeError("synthetic persistence failure")
        if fault in {"cancel", "committed_cancel"}:
            if fault == "committed_cancel":
                row.status = "FINALIZED"
            raise asyncio.CancelledError
        await asyncio.Event().wait()

    async def failed(_: Any, error_code: str, **__: Any) -> None:
        if row.status not in {"FINALIZED", "FAILED"}:
            row.status, row.error_code = "FAILED", error_code

    monkeypatch.setattr(probe.runtime, "_persist_final", persist)
    monkeypatch.setattr(probe.runtime, "_mark_generation_failed", AsyncMock(side_effect=failed))
    if fault == "timeout":
        monkeypatch.setattr(runtime_module, "AGENT_FINALIZATION_TIMEOUT_SECONDS", 0.05)
    task = asyncio.create_task(probe.finalize())
    try:
        await asyncio.wait_for(entered.wait(), 0.3)
        await asyncio.wait_for(probe.runtime.reset_agent(probe.run.match_id), 0.3)
        assert row.status == "PLAYING"
        release.set()
        if fault in {"cancel", "committed_cancel"}:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 0.3)
        else:
            await asyncio.wait_for(task, 0.3)
        expected = {
            "failure": "agent_finalization_failed",
            "timeout": "agent_finalization_timeout",
            "cancel": "agent_cancelled",
            "committed_cancel": None,
        }[fault]
        assert row.status == ("FINALIZED" if fault == "committed_cancel" else "FAILED")
        assert row.error_code == expected
        probe.callbacks.finalize_agent_speech.assert_not_awaited()
        assert probe.run.match_id not in probe.runtime._runs
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_reset_during_tail_has_not_handed_off_cancellation_persistence(
    tail_probe: TailProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = tail_probe
    probe.source.hold_drain = True
    marker = AsyncMock()
    monkeypatch.setattr(probe.runtime, "_mark_generation_failed", marker)
    task = asyncio.create_task(probe.finalize())
    try:
        await asyncio.wait_for(probe.source.entered.wait(), 0.3)
        await asyncio.wait_for(probe.runtime.reset_agent(probe.run.match_id), 0.3)
        assert marker.await_args is not None
        assert marker.await_args.args == (probe.run.generation_id, "agent_cancelled")
        probe.source.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.3)
        probe.persist.assert_not_awaited()
        probe.callbacks.finalize_agent_speech.assert_not_awaited()
    finally:
        probe.source.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_finalizing_flag_alone_cannot_suppress_cancellation_persistence(
    tail_probe: TailProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = tail_probe
    probe.run.finalizing = True
    marker = AsyncMock()
    monkeypatch.setattr(probe.runtime, "_mark_generation_failed", marker)
    await probe.runtime.reset_agent(probe.run.match_id)
    assert marker.await_args is not None
    assert marker.await_args.args == (probe.run.generation_id, "agent_cancelled")


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", ["done_task", "other_generation"])
async def test_stale_persistence_owner_cannot_suppress_cancellation(
    tail_probe: TailProbe, monkeypatch: pytest.MonkeyPatch, stale: str
) -> None:
    probe = tail_probe
    release = asyncio.Event()
    owner_task = asyncio.create_task(release.wait())
    if stale == "done_task":
        release.set()
        await owner_task
    assert probe.run.generation_id is not None
    probe.run.finalizing = True
    probe.run.finalization_owner = runtime_module._AgentFinalizationOwner(
        probe.run.generation_id if stale == "done_task" else uuid4(), owner_task
    )
    marker = AsyncMock()
    monkeypatch.setattr(probe.runtime, "_mark_generation_failed", marker)
    try:
        await probe.runtime.reset_agent(probe.run.match_id)
        assert marker.await_args is not None
        assert marker.await_args.args == (probe.run.generation_id, "agent_cancelled")
    finally:
        owner_task.cancel()
        await asyncio.gather(owner_task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["capture_failed", "drain_timeout"])
async def test_failed_tail_terminalizes_generation_without_any_persistence_owner(
    tail_probe: TailProbe, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    probe = tail_probe
    probe.source.fail_capture = fault == "capture_failed"
    probe.source.hold_drain = fault == "drain_timeout"
    monkeypatch.setattr(runtime_module, "AGENT_FINALIZATION_TIMEOUT_SECONDS", 0.03)
    marker = AsyncMock()
    monkeypatch.setattr(probe.runtime, "_mark_generation_failed", marker)
    await probe.finalize()
    expected = (
        "agent_finalization_failed" if fault == "capture_failed" else "agent_finalization_timeout"
    )
    marker.assert_awaited_once_with(probe.run.generation_id, expected)
    assert probe.run.finalization_owner is None
    probe.persist.assert_not_awaited()

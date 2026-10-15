"""A retired startup cannot register after its existing cleanup await returns."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from jx_core.agent.runtime import AgentRun, AgentRuntime, HostAudioRun
from jx_core.config import Settings

# These controls hold only the old resource cleanup boundary, never authority.
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
@pytest.mark.parametrize("start_kind", ["agent", "host"])
@pytest.mark.parametrize("boundary", ["host_cleanup", "agent_cleanup", "runtime_lock"])
@pytest.mark.parametrize(
    "retirement",
    ["reset_agent", "fence_agent", "reset_host", "fence_host", "close_match", "close"],
)
async def test_retired_pending_start_never_registers_and_resume_can_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    start_kind: str,
    boundary: str,
    retirement: str,
) -> None:
    runtime = AgentRuntime(
        settings=Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            database_url=SecretStr("postgresql+psycopg://synthetic.invalid/unused"),
            agent_audio_storage_dir=str(tmp_path),
        ),
        session_factory=cast(Any, None),
        callbacks=AsyncMock(),
    )
    match_id, agent_id = uuid4(), uuid4()
    cleanup_entered, cleanup_release = asyncio.Event(), asyncio.Event()
    executions: list[AgentRun | HostAudioRun] = []

    async def old_cleanup(_: object) -> None:
        cleanup_entered.set()
        await cleanup_release.wait()

    async def execute(run: AgentRun | HostAudioRun) -> None:
        executions.append(run)

    monkeypatch.setattr(runtime, "_run_with_retry", execute)
    monkeypatch.setattr(runtime, "_run_host_with_retry", execute)
    if boundary == "host_cleanup":
        runtime._host_runs[match_id] = HostAudioRun(
            match_id=match_id,
            action_key="1:0",
            storage_path="synthetic-unused",
            duration_ms=6000,
            context_version=0,
        )
        monkeypatch.setattr(runtime, "_cancel_host_run", old_cleanup)
    elif boundary == "agent_cleanup":
        runtime._runs[match_id] = AgentRun(
            match_id=match_id,
            action_key="0:1",
            agent_profile_id=agent_id,
            duration_ms=12_000,
        )
        monkeypatch.setattr(runtime, "_cancel_run", old_cleanup)
    else:
        await runtime._lock.acquire()

    async def start() -> None:
        if start_kind == "agent":
            await runtime.start_agent(
                match_id=match_id,
                action_key="1:1",
                agent_profile_id=agent_id,
                duration_ms=12_000,
                side="AFFIRMATIVE",
            )
        else:
            await runtime.start_host_playback(
                match_id=match_id,
                action_key="1:1",
                storage_path="synthetic-unused",
                duration_ms=6000,
                context_version=0,
            )

    startup = asyncio.create_task(start())
    try:
        if boundary == "runtime_lock":
            await asyncio.sleep(0)
        else:
            await asyncio.wait_for(cleanup_entered.wait(), timeout=1)
        # The public interruption is allowed to finish independently of an old
        # resource close. It must invalidate the not-yet-registered startup.
        retire = getattr(runtime, retirement)
        cleanup = asyncio.create_task(retire() if retirement == "close" else retire(match_id))
        await asyncio.sleep(0)
        cleanup_release.set()
        if boundary == "runtime_lock":
            runtime._lock.release()
        await asyncio.wait_for(asyncio.gather(startup, cleanup), timeout=1)
        await asyncio.sleep(0)
        assert not executions, "retired startup executed after its cleanup wait"
        assert all(run.action_key != "1:1" for run in runtime._runs.values())
        assert all(run.action_key != "1:1" for run in runtime._host_runs.values())
        assert not runtime._pending_media_starts
        if retirement not in {"close_match", "close"}:
            # A fresh authority-driven admission must not inherit a sticky
            # match fence from the retired attempt.
            await start()
            await asyncio.sleep(0)
            assert len(executions) == 1
            registered = runtime._runs if start_kind == "agent" else runtime._host_runs
            assert registered[match_id] is executions[0]
            assert not runtime._pending_media_starts
    finally:
        cleanup_release.set()
        if boundary == "runtime_lock" and runtime._lock.locked():
            runtime._lock.release()
        startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)
        monkeypatch.setattr(runtime, "_cancel_run", AsyncMock())
        monkeypatch.setattr(runtime, "_cancel_host_run", AsyncMock())
        await runtime.close()


@pytest.mark.asyncio
async def test_cancelled_older_admission_does_not_clear_its_newer_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = AgentRuntime(
        settings=Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            database_url=SecretStr("postgresql+psycopg://synthetic.invalid/unused"),
        ),
        session_factory=cast(Any, None),
        callbacks=AsyncMock(),
    )
    match_id, agent_id = uuid4(), uuid4()
    executions: list[AgentRun] = []

    async def execute(run: AgentRun) -> None:
        executions.append(run)

    monkeypatch.setattr(runtime, "_run_with_retry", execute)
    await runtime._lock.acquire()

    async def start(action_key: str) -> None:
        await runtime.start_agent(
            match_id=match_id,
            action_key=action_key,
            agent_profile_id=agent_id,
            duration_ms=12_000,
        )

    older = asyncio.create_task(start("1:1"))
    await asyncio.sleep(0)
    newer = asyncio.create_task(start("2:1"))
    await asyncio.sleep(0)
    older.cancel()
    await asyncio.gather(older, return_exceptions=True)
    runtime._lock.release()
    try:
        await asyncio.wait_for(newer, timeout=1)
        await asyncio.sleep(0)
        assert [run.action_key for run in executions] == ["2:1"]
        assert not runtime._pending_media_starts
    finally:
        monkeypatch.setattr(runtime, "_cancel_run", AsyncMock())
        await runtime.close()

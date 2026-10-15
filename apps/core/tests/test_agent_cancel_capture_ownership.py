"""Cancellation owns its bounded capture until the original failure handoff."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from jx_core.agent.runtime import AgentRun, AgentRuntime
from jx_core.config import Settings
from jx_core.data_capture.provider import ProviderCallCapture
from jx_core.data_capture.provider_budget import METADATA_BYTES, CaptureBudget

# Tests deliberately exercise the public reset and its private ownership edge.
# pyright: reportPrivateUsage=false


def capture(budget: CaptureBudget, *, sent: bool) -> ProviderCallCapture:
    value = ProviderCallCapture(
        budget=budget, reservation=budget.reserve(METADATA_BYTES), budgeted=True
    )
    if sent:
        value.capture_request(
            transport="SSE",
            url="https://synthetic.invalid/v1/chat/completions",
            body={"messages": [{"role": "system", "content": "Owned original identity."}]},
        )
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", [False, True])
@pytest.mark.parametrize("nested_cleanup", [False, True])
@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("replacement", [False, True])
async def test_public_reset_passes_owned_capture_before_either_cleanup_can_clear_it(
    tmp_path: Path,
    sent: bool,
    nested_cleanup: bool,
    failure: bool,
    replacement: bool,
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
    run = AgentRun(
        match_id=uuid4(),
        action_key="1:1",
        agent_profile_id=uuid4(),
        duration_ms=12_000,
        generation_id=uuid4(),
    )
    budget = CaptureBudget()
    owned = capture(budget, sent=sent)
    run.llm_capture = owned
    run.tts_capture = capture(budget, sent=False)
    runtime._runs[run.match_id] = run
    entered = asyncio.Event()

    async def old_attempt() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            if nested_cleanup:
                await runtime._cleanup_attempt(run)

    run.task = asyncio.create_task(old_attempt())
    await entered.wait()
    seen: list[bool] = []
    new_run: AgentRun | None = None

    async def failed(generation_id: Any, error_code: str, **values: Any) -> None:
        nonlocal new_run
        assert generation_id == run.generation_id and error_code == "agent_cancelled"
        result = values.get("llm_capture")
        assert result is owned, "reset discarded the actual provider capture owner"
        assert result is not None
        assert (result.request is not None) == sent
        assert result.reservation is not None
        seen.append(True)
        if replacement:
            new_run = AgentRun(
                match_id=run.match_id,
                action_key="2:1",
                agent_profile_id=uuid4(),
                duration_ms=12_000,
                llm_capture=capture(budget, sent=True),
            )
            runtime._runs[run.match_id] = new_run
        if failure:
            raise RuntimeError("synthetic failure persistence")

    runtime._mark_generation_failed = failed
    try:
        if failure:
            with pytest.raises(RuntimeError, match="synthetic failure persistence"):
                await runtime.reset_agent(run.match_id)
        else:
            await runtime.reset_agent(run.match_id)
        assert seen == [True]
        assert run.llm_capture is None and run.tts_capture is None
        assert budget.items == int(replacement) and not budget.workspace_bytes
        if new_run is not None:
            assert runtime._runs[run.match_id] is new_run
            assert new_run.llm_capture is not None
            assert new_run.llm_capture.request is not None
        else:
            assert not budget.retained_bytes
    finally:
        owned.close()
        await runtime.close()
        assert not budget.items and not budget.retained_bytes and not budget.workspace_bytes


@pytest.mark.asyncio
async def test_postcommit_capture_failure_cannot_reverse_success_or_leak_budget(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    runtime = AgentRuntime(
        settings=Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            database_url=SecretStr("postgresql+psycopg://synthetic.invalid/unused"),
        ),
        session_factory=cast(Any, None),
        callbacks=AsyncMock(),
    )
    budget = CaptureBudget()
    owned = capture(budget, sent=True)

    def unavailable(*_: Any) -> Any:
        raise RuntimeError("synthetic private diagnostic response")

    runtime._capture_writer = cast(Any, SimpleNamespace(prepare=unavailable))
    call = SimpleNamespace(id=uuid4(), status="SUCCEEDED", error_code=None)
    runtime._submit_committed_capture(cast(Any, call), owned)
    assert call.status == "SUCCEEDED" and call.error_code is None
    assert not budget.items and not budget.retained_bytes and not budget.workspace_bytes
    assert "synthetic private diagnostic response" not in caplog.text
    assert any(
        getattr(record, "error_code", None) == "agent_capture_unavailable"
        for record in caplog.records
    )
    await runtime.close()

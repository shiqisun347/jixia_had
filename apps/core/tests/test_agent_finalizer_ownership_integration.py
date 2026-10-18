"""Owned PG verifies a retired persistence owner still records every terminal result."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

import jx_core.agent.runtime as runtime_module
from jx_core.matches.domain import MatchCommand
from jx_core.models import AgentAudioAsset, AgentGeneration, MatchParticipant, Speech
from test_agent_audio_tail_integration import install_edges, start
from test_agent_failure_recovery_integration import wait_for_condition
from test_agent_runtime_fault_integration import AgentFaultProbe
from test_agent_runtime_fault_integration import (
    agent_fault_database_url as agent_fault_database_url,
)
from test_agent_runtime_fault_integration import agent_fault_probe as agent_fault_probe

pytestmark = pytest.mark.integration
# The real Actor, callbacks, generation/asset transactions and ordinary pause remain in use.
# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["failure", "timeout", "cancel", "committed_cancel"])
async def test_paused_persistence_owner_reaches_exact_terminal_generation(
    agent_fault_probe: AgentFaultProbe, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    probe = agent_fault_probe
    edges = install_edges(probe, monkeypatch, "unheld")
    entered, release = asyncio.Event(), asyncio.Event()
    original_persist = probe.runtime._persist_final

    async def persist(run: Any) -> None:
        entered.set()
        await release.wait()
        if fault == "failure":
            raise RuntimeError("owned persistence failure")
        if fault == "committed_cancel":
            await original_persist(run)
        if fault in {"cancel", "committed_cancel"}:
            raise asyncio.CancelledError
        await asyncio.Event().wait()

    monkeypatch.setattr(probe.runtime, "_persist_final", persist)
    if fault == "timeout":
        monkeypatch.setattr(runtime_module, "AGENT_FINALIZATION_TIMEOUT_SECONDS", 1.0)
    run = await start(probe)
    try:
        await asyncio.wait_for(entered.wait(), 2)
        async with probe.factory() as session:
            participant = await session.scalar(
                select(MatchParticipant.user_id)
                .where(MatchParticipant.match_id == run.match_id, MatchParticipant.kind == "HUMAN")
                .limit(1)
            )
        assert participant is not None
        await probe.manager.submit(
            run.match_id,
            MatchCommand(
                type="match.pause",
                message_id=str(uuid4()),
                actor_user_id=participant,
                payload={"authorized": True},
            ),
        )
        await wait_for_condition(lambda: run.match_id not in probe.runtime._runs)
        async with probe.factory() as session:
            generation = await session.get(AgentGeneration, run.generation_id)
            assert generation is not None and generation.status == "PLAYING"
            assert generation.error_code is None
        release.set()
        await wait_for_condition(lambda: not probe.manager._background_tasks, timeout=2)
        assert probe.actor.state.status == "PAUSED"
        assert run.storage_path is not None and run.storage_path.exists()
        async with probe.factory() as session:
            generation = await session.get(AgentGeneration, run.generation_id)
            speech = await session.get(Speech, run.speech_id)
            asset = await session.scalar(
                select(AgentAudioAsset).where(AgentAudioAsset.generation_id == run.generation_id)
            )
            assert speech is not None and speech.status == "RESET"
            assert generation is not None and generation.completed_at is not None
            if fault == "committed_cancel":
                assert generation.status == "FINALIZED" and generation.error_code is None
                assert asset is not None and asset.status == "FINALIZED"
            else:
                assert generation.status == "FAILED"
                assert generation.error_code == {
                    "failure": "agent_finalization_failed",
                    "timeout": "agent_finalization_timeout",
                    "cancel": "agent_cancelled",
                }[fault]
                assert asset is None
        assert run.finalization_owner is None
        assert all(source.closed for source in edges.sources)
    finally:
        release.set()
        for source in edges.sources:
            source.release_tail.set()

"""Test entry point for the voice reliability simulator (Spec 258).

Loads the simulator from scripts/qa/ and runs each scenario category.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_simulator() -> ModuleType:
    path = Path(__file__).parents[3] / "scripts/qa/voice_reliability_simulator.py"
    spec = importlib.util.spec_from_file_location("voice_reliability_simulator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("simulator_import_failed")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_asr_failure_convergence() -> None:
    """ASR failure convergence: AUDIO/CONFIG, retry budget, free debate, Bug 2/5."""
    simulator = _load_simulator()
    results = await simulator.test_asr_failure_convergence()
    failed = [r for r in results if not r.passed]
    assert not failed, f"ASR failure scenarios failed: {[r.name for r in failed]}"


@pytest.mark.asyncio
async def test_agent_failure_convergence() -> None:
    """Agent failure convergence: PREPARING/SPEAKING/FINALIZING, Bug 4."""
    simulator = _load_simulator()
    results = await simulator.test_agent_failure_convergence()
    failed = [r for r in results if not r.passed]
    assert not failed, f"Agent failure scenarios failed: {[r.name for r in failed]}"


@pytest.mark.asyncio
async def test_free_debate_race_conditions() -> None:
    """Free debate race conditions: human priority, HUMAN_ONLY_WAIT, ALL_AGENT_SKIP_RANDOM."""
    simulator = _load_simulator()
    results = await simulator.test_free_debate_race_conditions()
    failed = [r for r in results if not r.passed]
    assert not failed, f"Free debate race scenarios failed: {[r.name for r in failed]}"


@pytest.mark.asyncio
async def test_pause_resume_recovery() -> None:
    """Pause/resume recovery: all action states, Bug 1/3."""
    simulator = _load_simulator()
    results = await simulator.test_pause_resume_recovery()
    failed = [r for r in results if not r.passed]
    assert not failed, f"Pause/resume scenarios failed: {[r.name for r in failed]}"


@pytest.mark.asyncio
async def test_timeout_boundaries() -> None:
    """Timeout boundaries: human.start_timeout, human.wait_timeout, speech.deadline."""
    simulator = _load_simulator()
    results = await simulator.test_timeout_boundaries()
    failed = [r for r in results if not r.passed]
    assert not failed, f"Timeout boundary scenarios failed: {[r.name for r in failed]}"


@pytest.mark.asyncio
async def test_idempotency_boundaries() -> None:
    """Idempotency boundaries: duplicate callbacks, stale callbacks."""
    simulator = _load_simulator()
    results = await simulator.test_idempotency_boundaries()
    failed = [r for r in results if not r.passed]
    assert not failed, f"Idempotency scenarios failed: {[r.name for r in failed]}"


@pytest.mark.asyncio
async def test_full_suite_runs_without_crash() -> None:
    """Full suite runs without crashing and produces a summary."""
    simulator = _load_simulator()
    summary = await simulator.run_voice_reliability_suite()
    assert summary.total_scenarios > 0
    assert summary.passed_scenarios + summary.failed_scenarios == summary.total_scenarios

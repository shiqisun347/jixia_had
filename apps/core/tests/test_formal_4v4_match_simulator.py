from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_simulator() -> ModuleType:
    path = Path(__file__).parents[3] / "scripts/qa/formal_4v4_match_simulator.py"
    spec = importlib.util.spec_from_file_location("formal_4v4_match_simulator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("simulator_import_failed")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_formal_4v4_simulator_completes_two_humans_and_two_agents_per_side() -> None:
    simulator = _load_simulator()
    summary = await simulator.run_simulation(2)
    assert summary.completed_rounds == 2
    assert summary.human_participants == 4
    assert summary.agent_participants == 4
    assert summary.completed_speeches == 20
    assert summary.stale_callbacks_rejected == 6
    assert summary.pause_resume_verified is True


@pytest.mark.asyncio
async def test_formal_4v4_simulator_rejects_unbounded_round_count() -> None:
    simulator = _load_simulator()
    with pytest.raises(ValueError, match="rounds_must_be_between_1_and_100"):
        await simulator.run_simulation(0)

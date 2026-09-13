from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_simulator() -> ModuleType:
    path = Path(__file__).parents[3] / "scripts/qa/five_human_match_simulator.py"
    spec = importlib.util.spec_from_file_location("five_human_match_simulator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("simulator_import_failed")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_five_human_simulator_completes_and_converges_faults() -> None:
    simulator = _load_simulator()
    summary = await simulator.run_simulation(3)

    assert summary.completed_rounds == 3
    assert summary.human_participants == 5
    assert summary.completed_speeches == 15
    assert summary.stale_callbacks_rejected == 12
    assert summary.invalid_uuid_rejected is True
    assert summary.timer_failure_converged is True
    assert summary.asr_classes == {
        "asr_empty_audio": "AUDIO",
        "asr_pcm_queue_full": "AUDIO",
        "asr_task_failed": "TRANSIENT",
        "asr_not_configured": "CONFIG",
    }


@pytest.mark.asyncio
async def test_five_human_simulator_rejects_unbounded_round_count() -> None:
    simulator = _load_simulator()
    with pytest.raises(ValueError, match="rounds_must_be_between_1_and_1000"):
        await simulator.run_simulation(0)

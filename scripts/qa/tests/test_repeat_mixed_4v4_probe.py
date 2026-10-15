from __future__ import annotations

import asyncio
import importlib
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> Any:
    # Importing helpers must never load deployment settings or contact providers.
    monkeypatch.syspath_prepend(str(Path("scripts/qa/probes").resolve()))
    return importlib.import_module("server_repeat_mixed_4v4_probe")


@pytest.fixture
def finished_receipt() -> dict[str, Any]:
    return {
        "status": "passed",
        "match_status": "FINISHED",
        "fixed_speeches": 8,
        "finished_events": 1,
        "free_side_kind_counts": {
            f"{side}:{kind}": 1
            for side in ("AFFIRMATIVE", "NEGATIVE")
            for kind in ("HUMAN", "AGENT")
        },
        "ai_judge": "SUCCEEDED",
        "postmatch": {
            "phase": "AWARDS",
            "ranked_participants": 8,
            "duplicate_vote_rejected": True,
        },
        "postmatch_audio_archive": {"status": "passed", "file_status": "READY"},
        "owned_rtc_room_deleted": True,
        "owned_postgres_and_audio_removed": True,
        "manager_actors_after_close": 0,
        "asr_receivers_after_close": 0,
        "agent_runs_after_close": 0,
        "host_runs_after_close": 0,
        "postmatch_tasks_after_close": 0,
        "new_pending_tasks_after_cleanup": 0,
        "interruption_receipts": {
            "human_pause_resume": "passed",
            "agent_pause_resume": "passed",
            "human_natural_resume_ms": 3000,
            "agent_natural_resume_ms": 3000,
        },
    }


def test_complete_fixture_is_accepted(probe: Any, finished_receipt: dict[str, Any]) -> None:
    assert probe.cleanup_complete(finished_receipt)
    assert probe.completed_flow(finished_receipt)


@pytest.mark.parametrize(
    "key",
    [
        "owned_rtc_room_deleted",
        "owned_postgres_and_audio_removed",
        "manager_actors_after_close",
        "asr_receivers_after_close",
        "agent_runs_after_close",
        "host_runs_after_close",
        "postmatch_tasks_after_close",
        "new_pending_tasks_after_cleanup",
    ],
)
@pytest.mark.parametrize("missing", [True, False])
def test_cleanup_must_be_complete_and_explicit(
    probe: Any, finished_receipt: dict[str, Any], key: str, missing: bool
) -> None:
    if missing:
        finished_receipt.pop(key)
    else:
        finished_receipt[key] = False if key.startswith("owned_") else 1
    assert not probe.cleanup_complete(finished_receipt)
    assert not probe.completed_flow(finished_receipt)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("status",), "failed"),
        (("match_status",), "PAUSED"),
        (("fixed_speeches",), 6),
        (("finished_events",), 0),
        (("finished_events",), 2),
        (("ai_judge",), "FAILED"),
        (("postmatch", "phase"), "AUDIENCE_VOTE"),
        (("postmatch", "ranked_participants"), 7),
        (("postmatch", "duplicate_vote_rejected"), False),
        (("postmatch_audio_archive", "status"), "failed"),
        (("postmatch_audio_archive", "file_status"), "PENDING"),
    ],
)
def test_partial_match_cannot_pass(
    probe: Any, finished_receipt: dict[str, Any], path: tuple[str, ...], value: Any
) -> None:
    target = finished_receipt
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert not probe.completed_flow(finished_receipt)


def test_both_sides_need_human_and_agent_free_speeches(
    probe: Any, finished_receipt: dict[str, Any]
) -> None:
    finished_receipt["free_side_kind_counts"].pop("NEGATIVE:AGENT")
    assert not probe.completed_flow(finished_receipt)


def test_pause_coverage_needs_both_kinds_and_natural_countdowns(
    probe: Any, finished_receipt: dict[str, Any]
) -> None:
    assert probe.pause_covered(finished_receipt)
    assert not probe.pause_covered({})


@pytest.mark.parametrize("kind", ["human", "agent"])
@pytest.mark.parametrize("field", ["pause_resume", "natural_resume_ms"])
def test_missing_pause_evidence_cannot_pass(
    probe: Any, finished_receipt: dict[str, Any], kind: str, field: str
) -> None:
    finished_receipt["interruption_receipts"].pop(f"{kind}_{field}")
    assert not probe.pause_covered(finished_receipt)


@pytest.mark.parametrize("kind", ["human", "agent"])
@pytest.mark.parametrize("duration", [0, 2899, "3000", None])
def test_early_or_invalid_resume_cannot_pass_pause_coverage(
    probe: Any, finished_receipt: dict[str, Any], kind: str, duration: Any
) -> None:
    finished_receipt["interruption_receipts"][f"{kind}_natural_resume_ms"] = duration
    assert not probe.pause_covered(finished_receipt)


@pytest.mark.asyncio
async def test_global_baseline_detects_a_task_hidden_by_next_round_baseline(probe: Any) -> None:
    global_baseline = set(asyncio.all_tasks())
    blocker = asyncio.Event()
    leftover = asyncio.create_task(blocker.wait(), name="qa-deliberate-round-one-leftover")
    try:
        await asyncio.sleep(0)
        next_round_baseline = set(asyncio.all_tasks())
        assert probe.pending_since(next_round_baseline) == []
        pending = probe.pending_since(global_baseline)
        assert len(pending) == 1
        assert pending[0]["name"] == "qa-deliberate-round-one-leftover"
    finally:
        leftover.cancel()
        await asyncio.gather(leftover, return_exceptions=True)
    assert probe.pending_since(global_baseline) == []


def install_synthetic_batch(
    probe: Any,
    monkeypatch: pytest.MonkeyPatch,
    receipts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    class Owned:
        def start(self) -> str:
            return "synthetic-owned-database"

        def migrate(self, _url: str) -> None:
            return None

        def close(self) -> bool:
            return True

    async def run(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return deepcopy(receipts[len(calls) - 1])

    monkeypatch.setattr(probe, "load_settings", lambda: object())
    monkeypatch.setattr(probe, "OwnedPostgres", Owned)
    monkeypatch.setattr(probe, "run", run)
    monkeypatch.setattr(probe, "resource_snapshot", lambda: {"synthetic": True})
    monkeypatch.setattr(probe, "native_mappings", lambda: {})
    monkeypatch.setattr(probe.importlib.metadata, "version", lambda _name: "1.1.20")
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "judge", "pause"])
async def test_five_round_gate_requires_every_round_to_qualify(
    probe: Any,
    finished_receipt: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    receipts = [deepcopy(finished_receipt) for _ in range(5)]
    if failure == "judge":
        receipts[1]["status"] = "failed"
        receipts[1]["ai_judge"] = "FAILED"
    elif failure == "pause":
        receipts[0]["interruption_receipts"] = {}
    calls = install_synthetic_batch(probe, monkeypatch, receipts)
    summary = await probe.run_batch()
    assert summary["status"] == ("passed" if failure is None else "failed")
    assert summary["completed_rounds"] == 5
    assert summary["passed_rounds"] == (5 if failure is None else 4)
    assert len(calls) == 5
    assert [call["pause_resume"] for call in calls] == [True, False, True, False, True]
    assert all(call["topical_microphones"] for call in calls)
    assert len({row["process_id"] for row in summary["rounds"]}) == 1
    assert len({row["event_loop_id"] for row in summary["rounds"]}) == 1


@pytest.mark.asyncio
async def test_incomplete_cleanup_stops_before_second_round(
    probe: Any, finished_receipt: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    finished_receipt["agent_runs_after_close"] = 1
    calls = install_synthetic_batch(probe, monkeypatch, [finished_receipt])
    summary = await probe.run_batch()
    assert summary["status"] == "failed"
    assert summary["stopped_reason"] == "qa_cleanup_incomplete"
    assert summary["completed_rounds"] == 1
    assert summary["passed_rounds"] == 0
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_outer_pending_tasks_stop_the_batch_even_if_local_cleanup_claims_zero(
    probe: Any, finished_receipt: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = install_synthetic_batch(probe, monkeypatch, [finished_receipt])
    monkeypatch.setattr(probe, "pending_since", lambda _baseline: [{"name": "old-round-task"}])
    summary = await probe.run_batch()
    assert summary["status"] == "failed"
    assert summary["completed_rounds"] == 1
    assert summary["rounds"][0]["global_new_pending_tasks"] == [{"name": "old-round-task"}]
    assert not summary["rounds"][0]["complete_passed"]
    assert len(calls) == 1

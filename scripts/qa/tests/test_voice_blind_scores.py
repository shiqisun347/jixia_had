from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from scripts.qa.build_voice_blind_pack import load_previous_metrics, previous_latency
from scripts.qa.summarize_voice_blind_scores import (
    EXPECTED_CODES,
    load_mapping,
    load_scorecards,
    summarize,
)


def test_existing_audio_metrics_keep_latency_on_idempotent_rebuild(tmp_path: Path) -> None:
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(
        json.dumps(
            {
                "metrics": [
                    {
                        "filename": "V01-T1-low.opus",
                        "duration_ms": 12000,
                        "byte_count": 42000,
                        "first_audio_latency_ms": 380,
                        "completed_latency_ms": 1400,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    previous = load_previous_metrics(metrics_path)["V01-T1-low.opus"]
    assert (
        previous_latency(
            previous,
            duration_ms=12000,
            byte_count=42000,
            key="first_audio_latency_ms",
        )
        == 380
    )
    assert (
        previous_latency(
            previous,
            duration_ms=12000,
            byte_count=999,
            key="first_audio_latency_ms",
        )
        is None
    )


def write_mapping(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(["code", "name", "kind", "provider_voice"])
        for index, code in enumerate(EXPECTED_CODES):
            writer.writerow([code, f"音色{index + 1}", "HOST" if index == 0 else "AGENT", "voice"])


def write_scorecard(path: Path, listener_id: str, score: float = 5.0) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(
            [
                "listener_id",
                "voice_code",
                "clarity_1_5",
                "naturalness_1_5",
                "consistency_1_5",
                "comments",
            ]
        )
        for code in EXPECTED_CODES:
            writer.writerow([listener_id, code, score, score, score, ""])


def test_complete_ten_listener_scores_pass(tmp_path: Path) -> None:
    mapping_path = tmp_path / "mapping.csv"
    write_mapping(mapping_path)
    scorecards = []
    for index in range(10):
        path = tmp_path / f"listener-{index}.csv"
        write_scorecard(path, f"L{index + 1:02d}")
        scorecards.append(path)
    result = summarize(load_scorecards(scorecards), load_mapping(mapping_path))
    assert result["listener_count"] == 10
    assert result["response_count"] == 110
    assert result["passed_agent_voices"] == 10
    assert result["passed_host_voices"] == 1
    assert result["overall_pass"] is True


def test_incomplete_listener_is_rejected(tmp_path: Path) -> None:
    paths = []
    for index in range(10):
        path = tmp_path / f"listener-{index}.csv"
        write_scorecard(path, f"L{index + 1:02d}")
        paths.append(path)
    lines = paths[-1].read_text(encoding="utf-8").splitlines()
    paths[-1].write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="listener_incomplete:L10:missing=V11"):
        load_scorecards(paths)


def test_scores_below_threshold_are_valid_but_fail(tmp_path: Path) -> None:
    mapping_path = tmp_path / "mapping.csv"
    write_mapping(mapping_path)
    paths = []
    for index in range(10):
        path = tmp_path / f"listener-{index}.csv"
        write_scorecard(path, f"L{index + 1:02d}", score=3.0)
        paths.append(path)
    result = summarize(load_scorecards(paths), load_mapping(mapping_path))
    assert result["overall_pass"] is False
    assert result["passed_agent_voices"] == 0
    assert result["passed_host_voices"] == 0

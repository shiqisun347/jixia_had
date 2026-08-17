"""Validate and summarize anonymized voice-listening scorecards."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

EXPECTED_CODES = tuple(f"V{index:02d}" for index in range(1, 12))
SCORE_FIELDS = ("clarity_1_5", "naturalness_1_5", "consistency_1_5")
THRESHOLDS = {
    "clarity_1_5": 4.0,
    "naturalness_1_5": 3.5,
    "consistency_1_5": 4.0,
}


def parse_score(value: str, *, path: Path, row_number: int, field: str) -> float:
    try:
        score = float(value)
    except ValueError as error:
        raise ValueError(f"{path.name}:row_{row_number}:{field}_invalid") from error
    if not 1 <= score <= 5:
        raise ValueError(f"{path.name}:row_{row_number}:{field}_out_of_range")
    return score


def load_scorecards(paths: list[Path], *, minimum_listeners: int = 10) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    listener_codes: dict[str, set[str]] = defaultdict(set)
    for path in paths:
        with path.open(newline="", encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            required = {"listener_id", "voice_code", *SCORE_FIELDS}
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                raise ValueError(f"{path.name}:columns_invalid")
            for row_number, row in enumerate(reader, start=2):
                listener_id = (row.get("listener_id") or "").strip()
                voice_code = (row.get("voice_code") or "").strip().upper()
                if not listener_id:
                    raise ValueError(f"{path.name}:row_{row_number}:listener_id_missing")
                if voice_code not in EXPECTED_CODES:
                    raise ValueError(f"{path.name}:row_{row_number}:voice_code_invalid")
                key = (listener_id, voice_code)
                if key in seen:
                    raise ValueError(f"duplicate_score:{listener_id}:{voice_code}")
                seen.add(key)
                listener_codes[listener_id].add(voice_code)
                rows.append(
                    {
                        "listener_id": listener_id,
                        "voice_code": voice_code,
                        **{
                            field: parse_score(
                                row.get(field) or "",
                                path=path,
                                row_number=row_number,
                                field=field,
                            )
                            for field in SCORE_FIELDS
                        },
                    }
                )
    if len(listener_codes) < minimum_listeners:
        raise ValueError(f"listeners_insufficient:{len(listener_codes)}<{minimum_listeners}")
    expected = set(EXPECTED_CODES)
    for listener_id, codes in listener_codes.items():
        if codes != expected:
            missing = ",".join(sorted(expected - codes)) or "none"
            raise ValueError(f"listener_incomplete:{listener_id}:missing={missing}")
    return rows


def load_mapping(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    mapping = {
        str(row.get("code", "")).strip(): {
            "name": str(row.get("name", "")).strip(),
            "kind": str(row.get("kind", "")).strip().upper(),
        }
        for row in rows
    }
    if set(mapping) != set(EXPECTED_CODES):
        raise ValueError("mapping_codes_invalid")
    invalid_values = any(
        value["kind"] not in {"AGENT", "HOST"} or not value["name"] for value in mapping.values()
    )
    if invalid_values:
        raise ValueError("mapping_values_invalid")
    return mapping


def summarize(rows: list[dict[str, Any]], mapping: dict[str, dict[str, str]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["voice_code"])].append(row)
    voices: list[dict[str, Any]] = []
    for code in EXPECTED_CODES:
        code_rows = grouped[code]
        means = {
            field: round(statistics.fmean(float(row[field]) for row in code_rows), 3)
            for field in SCORE_FIELDS
        }
        passed = all(means[field] >= threshold for field, threshold in THRESHOLDS.items())
        voices.append({"code": code, **mapping[code], **means, "passed": passed})
    passed_agents = sum(voice["passed"] and voice["kind"] == "AGENT" for voice in voices)
    passed_hosts = sum(voice["passed"] and voice["kind"] == "HOST" for voice in voices)
    return {
        "listener_count": len({str(row["listener_id"]) for row in rows}),
        "response_count": len(rows),
        "thresholds": THRESHOLDS,
        "passed_agent_voices": passed_agents,
        "passed_host_voices": passed_hosts,
        "overall_pass": passed_agents >= 4 and passed_hosts >= 1,
        "voices": voices,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("scorecards", nargs="+", type=Path)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--minimum-listeners", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        rows = load_scorecards(args.scorecards, minimum_listeners=args.minimum_listeners)
        result = summarize(rows, load_mapping(args.mapping))
    except (OSError, ValueError) as error:
        rendered_error = json.dumps({"valid": False, "error": str(error)}, ensure_ascii=False)
        print(rendered_error, file=sys.stderr)
        raise SystemExit(2) from None
    rendered = json.dumps({"valid": True, **result}, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    raise SystemExit(0 if result["overall_pass"] else 1)


if __name__ == "__main__":
    main()

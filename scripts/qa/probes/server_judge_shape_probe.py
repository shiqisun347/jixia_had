"""Server-only current rule judge repro; output parsed shape, never response text."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from server_full_match_probe import load_resources
from server_provider_media_probe import error_receipt

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ops"))
from provider_chain_probe import _judge_snapshot, _stream, load_material  # noqa: E402

from jx_core.config import load_settings  # noqa: E402
from jx_core.database import Database  # noqa: E402
from jx_core.postmatch import _parse_result  # noqa: E402


def shape(response: str, participants: list[dict[str, Any]]) -> dict[str, Any]:
    receipt: dict[str, Any] = {"response_chars": len(response)}
    value = response.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        receipt.update(
            json_valid=False, json_error_line=error.lineno, json_error_column=error.colno
        )
        return receipt
    receipt["json_valid"] = True
    receipt["json_object"] = isinstance(parsed, dict)
    if not isinstance(parsed, dict):
        return receipt
    expected = {"winner", "team_scores", "participants", "team_comments"}
    receipt["known_top_keys_present"] = sorted(expected & parsed.keys())
    receipt["other_top_key_count"] = len(parsed.keys() - expected)
    scores = parsed.get("team_scores") or parsed.get("scores")
    score_out_of_range: list[dict[str, Any]] = []
    score_key_mismatch = 0
    if isinstance(scores, dict):
        limits = {"argument": 30, "rebuttal": 25, "evidence": 20, "teamwork": 15, "expression": 10}
        aliases = {
            "论点": "argument",
            "立论": "argument",
            "反驳": "rebuttal",
            "证据": "evidence",
            "事实与证据": "evidence",
            "团队协作": "teamwork",
            "协作": "teamwork",
            "表达与规则": "expression",
            "表达": "expression",
        }
        for side_value in scores.values():
            if not isinstance(side_value, dict):
                score_key_mismatch += 1
                continue
            normalized = {aliases.get(key, key): value for key, value in side_value.items()}
            if set(normalized) != set(limits):
                score_key_mismatch += 1
            for key, maximum in limits.items():
                value = normalized.get(key)
                if isinstance(value, (float, int)) and not 0 <= value <= maximum:
                    score_out_of_range.append({"field": key, "maximum": maximum, "observed": value})
    receipt["team_score_key_mismatch_count"] = score_key_mismatch
    receipt["team_scores_out_of_range"] = score_out_of_range
    items = parsed.get("participants")
    receipt["participant_count"] = len(items) if isinstance(items, list) else None
    if isinstance(items, list):
        receipt["participant_score_missing_or_out_of_range"] = sum(
            not isinstance(item, dict)
            or not isinstance(item.get("score"), (int, float))
            or not 0 <= item["score"] <= 20
            for item in items
        )
    try:
        _parse_result(response, {p["participant_id"] for p in participants}, participants)
    except Exception as error:
        receipt.update(error_receipt(error))
        tb = error.__traceback__
        while tb is not None:
            if tb.tb_frame.f_code.co_name == "_parse_result":
                receipt["parser_failure_line"] = tb.tb_lineno
            tb = tb.tb_next
    else:
        receipt["status"] = "passed"
    return receipt


async def run() -> dict[str, Any]:
    settings = load_settings()
    database = Database(settings.database_url_value)
    try:
        legacy = await load_material(database)
        current, _ = await load_resources(database)
        snapshot, participants = _judge_snapshot()
        snapshot.pop("transcript", None)
        snapshot["history"] = [
            {
                "stage": "合成正式发言",
                "speaker": f"{side}-{seat}",
                "side": side,
                "seat_no": seat,
                "content": "过程塑造能力，结果检验实践价值，二者共同支持奋斗。",
            }
            for side in ("AFFIRMATIVE", "NEGATIVE")
            for seat in (1, 2, 3, 4)
        ]
        receipt = {
            "probe": "judge_shape_only",
            "llm_model": current.model_id,
            "synthetic_history_entries": len(snapshot["history"]),
        }
        for label, material, system in (
            ("current_enabled_rule", current, "你是客观的中文辩论裁判。"),
            ("legacy_global_control", legacy, legacy.judge_system_prompt),
        ):
            response, metrics = await _stream(
                material,
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": material.judge_prompt
                        + "\n"
                        + json.dumps(snapshot, ensure_ascii=False),
                    },
                ],
                max_tokens=1600,
                generation_params=current.model_params,
                database=database,
                call_kind="JUDGE",
            )
            receipt[label] = {**metrics, **shape(response, participants)}
        return receipt
    finally:
        await database.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    try:
        result = asyncio.run(run())
    except Exception as error:
        result = error_receipt(error)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)

"""Compare bounded judge request contracts using synthetic grouped history only."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from server_full_match_probe import load_resources
from server_judge_shape_probe import shape
from server_provider_media_probe import error_receipt

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ops"))
from provider_chain_probe import _judge_snapshot, _stream  # noqa: E402

from jx_core.config import load_settings  # noqa: E402
from jx_core.database import Database  # noqa: E402
from jx_core.experiments.prompts import render_history  # noqa: E402


def object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def score_schema(participants: list[dict[str, Any]]) -> dict[str, Any]:
    team = object_schema(
        {
            name: {"type": "number", "minimum": 0, "maximum": maximum}
            for name, maximum in {
                "argument": 30,
                "rebuttal": 25,
                "evidence": 20,
                "teamwork": 15,
                "expression": 10,
            }.items()
        }
    )
    comment = {"type": "string", "maxLength": 40}
    return object_schema(
        {
            "winner": {"type": "string", "enum": ["AFFIRMATIVE", "NEGATIVE", "DRAW"]},
            "team_scores": object_schema({side: team for side in ("AFFIRMATIVE", "NEGATIVE")}),
            "participants": {
                "type": "array",
                "minItems": 8,
                "maxItems": 8,
                "items": object_schema(
                    {
                        "participant_id": {
                            "type": "string",
                            "enum": [item["participant_id"] for item in participants],
                        },
                        "score": {"type": "number", "minimum": 0, "maximum": 20},
                        "comment": comment,
                    }
                ),
            },
            "team_comments": object_schema({side: comment for side in ("AFFIRMATIVE", "NEGATIVE")}),
        }
    )


async def run() -> dict[str, Any]:
    settings = load_settings()
    database = Database(settings.database_url_value)
    try:
        material, _ = await load_resources(database)
        snapshot, participants = _judge_snapshot()
        snapshot.pop("transcript", None)
        for item in participants:
            item["kind"] = "HUMAN" if item["seat_no"] <= 2 else "AGENT"
        turns = (
            [("固定发言", side, seat) for seat in (1, 2, 3) for side in ("AFFIRMATIVE", "NEGATIVE")]
            + [
                ("自由辩论", side, seat)
                for seat in (1, 3, 2, 4)
                for side in ("AFFIRMATIVE", "NEGATIVE")
            ]
            + [("总结陈词", "NEGATIVE", 4), ("总结陈词", "AFFIRMATIVE", 4)]
        )
        history = [
            {
                "stage": stage,
                "side": side,
                "seat_no": seat,
                "speaker": f"{side}-{seat}",
                "content": "过程塑造能力，持续成长体现奋斗价值。"
                if side == "AFFIRMATIVE"
                else "结果检验实践，创造实际价值体现奋斗价值。",
            }
            for stage, side, seat in turns
        ]
        snapshot["history"] = json.loads(render_history(history))
        schema = score_schema(participants)
        contract = (
            "你是客观的中文辩论裁判，必须输出评分JSON。"
            "双方side为AFFIRMATIVE/NEGATIVE，winner允许DRAW。"
            "team_scores每方argument上限30、rebuttal25、evidence20、teamwork15、expression10，"
            "所有分数下限0。participants必须恰好包含输入的8个participant_id各一次，"
            "score范围0到20，comment一句话不超过40字。team_comments每方一句话不超过40字。"
            "只返回winner、team_scores、participants、team_comments。"
        )
        receipt: dict[str, Any] = {
            "probe": "synthetic_grouped_judge_contract_comparison",
            "model": material.model_id,
            "synthetic_history_entries": len(history),
            "max_tokens": 1600,
            "production_config_changed": False,
            "production_matches_created": 0,
            "cases": [],
        }
        for label, response_format in (
            (
                "strict_json_schema",
                {
                    "type": "json_schema",
                    "json_schema": {"name": "jixia_judge_probe", "strict": True, "schema": schema},
                },
            ),
            ("explicit_contract_json_object", {"type": "json_object"}),
        ):
            parameters = dict(material.model_params)
            parameters.pop("max_tokens", None)
            parameters["response_format"] = response_format
            case: dict[str, Any] = {"case": label}
            try:
                text, metrics = await _stream(
                    material,
                    messages=[
                        {"role": "system", "content": contract},
                        {
                            "role": "user",
                            "content": material.judge_prompt
                            + "\n"
                            + json.dumps(snapshot, ensure_ascii=False),
                        },
                    ],
                    max_tokens=1600,
                    generation_params=parameters,
                    database=database,
                    call_kind="JUDGE",
                )
                case.update(metrics)
                case.update(shape(text, participants))
                raw = json.loads(text)
                ids = [item.get("participant_id") for item in raw.get("participants", [])]
                case["raw_participant_ids_exact_and_unique"] = len(ids) == 8 and set(ids) == {
                    item["participant_id"] for item in participants
                }
                if not case["raw_participant_ids_exact_and_unique"]:
                    case["status"] = "failed"
            except Exception as error:
                case.update(error_receipt(error))
            receipt["cases"].append(case)
        receipt["status"] = (
            "passed" if all(item["status"] == "passed" for item in receipt["cases"]) else "failed"
        )
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
    raise SystemExit(0 if result["status"] == "passed" else 1)

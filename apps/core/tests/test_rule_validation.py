import pytest

from jx_core.rules.formal_4v4 import (
    build_formal_4v4_draft,
    expected_formal_4v4_host_copy,
)
from jx_core.rules.validation import RuleValidationError, validate_rule_draft


def _valid_rule() -> dict[str, object]:
    return {
        "name": "线性练习规则",
        "side_size": 1,
        "stages": [
            {
                "name": "正方立论",
                "stage_kind": "FIXED_SPEECH",
                "actions": [{"side": "AFFIRMATIVE", "seat_no": 1, "duration_seconds": 120}],
            },
            {"name": "准备", "stage_kind": "PREPARATION", "duration_seconds": 30},
            {"name": "结束", "stage_kind": "END"},
        ],
    }


def test_rule_validation_compiles_finite_snapshot_and_duration() -> None:
    snapshot = validate_rule_draft(_valid_rule())
    assert snapshot["estimated_seconds"] == 150
    assert snapshot["stages"][-1]["stage_kind"] == "END"


def test_formal_4v4_counts_both_free_debate_clocks() -> None:
    fixed_stages = [
        ("正方一辩立论", "AFFIRMATIVE", 1, 180),
        ("反方一辩立论", "NEGATIVE", 1, 180),
        ("正方二辩陈词", "AFFIRMATIVE", 2, 90),
        ("反方二辩陈词", "NEGATIVE", 2, 90),
        ("正方三辩陈词", "AFFIRMATIVE", 3, 90),
        ("反方三辩陈词", "NEGATIVE", 3, 90),
        ("反方四辩总结", "NEGATIVE", 4, 180),
        ("正方四辩总结", "AFFIRMATIVE", 4, 180),
    ]
    stages = [
        {
            "name": name,
            "stage_kind": "FIXED_SPEECH",
            "actions": [{"side": side, "seat_no": seat_no, "duration_seconds": duration}],
        }
        for name, side, seat_no, duration in fixed_stages[:6]
    ]
    stages.append(
        {
            "name": "自由辩论",
            "stage_kind": "FREE_DEBATE",
            "duration_seconds": 180,
            "parameters": {"starting_side": "AFFIRMATIVE", "max_speech_seconds": 30},
        }
    )
    stages.extend(
        {
            "name": name,
            "stage_kind": "FIXED_SPEECH",
            "actions": [{"side": side, "seat_no": seat_no, "duration_seconds": duration}],
        }
        for name, side, seat_no, duration in fixed_stages[6:]
    )
    stages.append({"name": "比赛结束", "stage_kind": "END"})

    snapshot = validate_rule_draft({"name": "4v4 正式辩论赛", "side_size": 4, "stages": stages})

    assert snapshot["estimated_seconds"] == 1440
    free_stage = snapshot["stages"][6]
    assert free_stage["duration_seconds"] == 180
    assert free_stage["parameters"] == {
        "max_speech_seconds": 30,
        "starting_side": "AFFIRMATIVE",
    }


def test_formal_4v4_host_copy_has_welcome_transitions_and_closing() -> None:
    draft = build_formal_4v4_draft()
    stages = draft["stages"]
    assert isinstance(stages, list)
    host_texts = [
        str(stage.get(key, ""))
        for stage in stages
        if isinstance(stage, dict)
        for key in ("start_host_text", "end_host_text")
        if stage.get(key)
    ]

    assert "欢迎来到本场四对四辩论" in host_texts[0]
    assert any("感谢正方一辩" in text for text in host_texts)
    assert any("自由辩论结束，感谢双方辩手" in text for text in host_texts)
    assert host_texts[-1] == "本场辩论到此结束，感谢各位辩手。"
    assert all("辩手姓名" not in text for text in host_texts)
    assert len(expected_formal_4v4_host_copy()) == len(stages)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["stages"].append({"name": "后置", "stage_kind": "FIXED_SPEECH"}),
        lambda value: value["stages"][0]["actions"][0].update({"seat_no": 2}),
        lambda value: value["stages"][-1].update({"duration_seconds": 1}),
        lambda value: value["stages"][1].update(
            {"stage_kind": "FREE_DEBATE", "actions": [{"duration_seconds": 10}]}
        ),
    ],
)
def test_rule_validation_rejects_invalid_linear_rules(mutator) -> None:
    payload = _valid_rule()
    mutator(payload)
    with pytest.raises(RuleValidationError):
        validate_rule_draft(payload)

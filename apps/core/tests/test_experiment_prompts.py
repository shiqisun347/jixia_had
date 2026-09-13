from __future__ import annotations

import json

from jx_core.experiments.prompts import (
    EXPERIMENT_PROMPT_VERSION,
    render_experiment_decision_prompt,
    render_experiment_speech_prompt,
    render_history,
)


def _arguments() -> dict[str, object]:
    return {
        "side": "NEGATIVE",
        "seat_no": 3,
        "topic": "过程还是结果更能体现奋斗的价值",
        "affirmative_stance": "过程更能体现奋斗的价值",
        "negative_stance": "结果更能体现奋斗的价值",
        "side_remaining_ms": 321_000,
        "opponent_remaining_ms": 298_000,
        "history": [
            {
                "side": "AFFIRMATIVE",
                "seat_no": 1,
                "speaker_kind": "HUMAN",
                "content": "第一条完整发言。",
            },
            {
                "side": "NEGATIVE",
                "seat_no": 2,
                "speaker_kind": "AGENT",
                "content": "第二条完整发言。",
            },
        ],
    }


def test_decision_prompt_matches_approved_contract_without_hand_or_identity_data() -> None:
    prompt = render_experiment_decision_prompt(**_arguments())  # type: ignore[arg-type]

    assert EXPERIMENT_PROMPT_VERSION == "paper-v2.1-json-history-2026-08-30"
    assert "你的席位：三辩" in prompt
    assert "本方剩余时间：321000" in prompt
    assert "对方剩余时间：298000" in prompt
    assert "第一条完整发言。" in prompt and "第二条完整发言。" in prompt
    assert '{"should_speak": true|false}' in prompt
    assert "HUMAN_HAND_STATE" not in prompt
    assert "willingness" not in prompt
    assert "音色" not in prompt and "头像" not in prompt


def test_history_is_grouped_json_and_preserves_empty_content() -> None:
    value = json.loads(
        render_history(
            [
                {"stage": "自由辩论", "speaker": "正方二辩", "content": ""},
                {"stage": "自由辩论", "speaker": "反方二辩", "content": None},
            ]
        )
    )
    assert value == [
        {
            "stage": "自由辩论",
            "message": [
                {"speaker": "正方二辩", "content": ""},
                {"speaker": "反方二辩", "content": ""},
            ],
        }
    ]


def test_speech_prompt_includes_seat_limit_and_full_history() -> None:
    prompt = render_experiment_speech_prompt(
        **_arguments(),  # type: ignore[arg-type]
        max_speech_seconds=30,
    )

    assert "你是结果更能体现奋斗的价值反方的三辩" in prompt
    assert "本次发言最多 30 秒" in prompt
    assert prompt.index("第一条完整发言。") < prompt.index("第二条完整发言。")
    assert "只输出将被朗读的发言正文" in prompt

from __future__ import annotations

import pytest

from jx_core.auth.errors import APIError
from jx_core.survey_definitions import (
    AI_APPROPRIATENESS,
    AI_MATCH_REASONS,
    AI_ACCEPTABLE_ACTIONS,
    AI_ACTION_REALIZATION,
    HUMAN_GOAL_DESCRIPTIONS,
    HUMAN_GOALS,
    HUMAN_PRIMARY_REASONS,
    OVERALL_OPTIONS,
    OVERALL_QUESTIONS,
    PAPER_LIKERT_OPTIONS,
    PAPER_OVERALL_QUESTIONS,
    POSTMATCH_QUESTIONNAIRE,
)
from jx_core.survey_service import (
    PERSONAL_DEFAULT_QUESTIONS,
    POSTMATCH_SCOPE_FREE_DEBATE,
    POSTMATCH_SCOPE_LEGACY,
    POSTMATCH_WORKFLOW_KEY,
    POSTMATCH_WORKFLOW_VERSION,
    _annotation_scope,
    _participant_answers_projection,
    _review_projection,
    _validate_answers,
    _validate_strict_overall,
    _validate_strict_prespeech_answers_immutable,
    _validate_strict_progression,
    _validate_strict_speech_answer,
)


def test_personal_survey_requires_all_scale_answers_on_submit() -> None:
    assert len(PERSONAL_DEFAULT_QUESTIONS) == 5
    assert [item["text"] for item in PERSONAL_DEFAULT_QUESTIONS] == list(PAPER_OVERALL_QUESTIONS)
    assert all(
        item["type"] == "scale" and item["scale_max"] == 5 for item in PERSONAL_DEFAULT_QUESTIONS
    )
    answers = {item["key"]: 4 for item in PERSONAL_DEFAULT_QUESTIONS if item["type"] == "scale"}
    _validate_answers(PERSONAL_DEFAULT_QUESTIONS, answers)
    _validate_answers(PERSONAL_DEFAULT_QUESTIONS, {"q1": 4}, require_complete=False)
    with pytest.raises(APIError, match="survey_answer_incomplete"):
        _validate_answers(PERSONAL_DEFAULT_QUESTIONS, {"q1": 4})


def test_personal_survey_rejects_out_of_range_and_unknown_answers() -> None:
    with pytest.raises(APIError, match="survey_answer_invalid"):
        _validate_answers(PERSONAL_DEFAULT_QUESTIONS, {"q1": 6})
    with pytest.raises(APIError, match="survey_answer_invalid"):
        _validate_answers(PERSONAL_DEFAULT_QUESTIONS, {"unexpected": 1})


def test_strict_postmatch_questionnaire_matches_approved_questions() -> None:
    assert list(HUMAN_PRIMARY_REASONS) == [
        "自己更适合处理这个问题。",
        "担心AI 不适合或者处理不好这个问题。",
        "没人回答或者其他担忧被迫回答",
        "其他。",
    ]
    assert list(HUMAN_GOALS) == [
        "回应对手",
        "接续队友",
        "补足缺口",
        "主动推进",
        "调整方向",
        "其他",
    ]
    assert HUMAN_GOAL_DESCRIPTIONS == {
        "回应对手": "对手刚提出了需要处理的攻击、质疑或追问",
        "接续队友": "队友的内容需要补充、澄清或继续推进",
        "补足缺口": "我注意到己方有一个重要问题还没人处理",
        "主动推进": "我认为应该推进新的或已有的己方论证",
        "调整方向": "我认为当前讨论需要转向更重要的问题",
    }
    assert list(AI_APPROPRIATENESS) == [
        "不合适，比如有此时其他人更合适发言：",
        "发言不发言都合适：",
        "很适合 AI 发言",
        "无法判断：",
    ]
    assert list(AI_MATCH_REASONS) == [
        "很好，处理了当时团队真正需要处理的问题",
        "一般，内容有点冗余，重复表达，新增价值很小",
        "一般，有点跑偏或者钻牛角尖，内容可能有价值，但不是当时最需要处理的问题",
        "不好，为后续带来了额外的修复负担",
        "其他不好或者一般的原因",
        "无法判断",
    ]
    assert list(OVERALL_QUESTIONS) == [
        "这场比赛里，AI 更像一个会和队友配合的辩手，还是只顾自己对抗的辩手？",
        "AI 的发言通常有没有接住队友刚才说的内容和场上的情况？",
        "AI 的发言对我们团队有多大帮助？",
        (
            "这场比赛里，AI 有没有给你带来额外负担？比如它说得不清楚、有漏洞、"
            "和队友重复，你还要花力气去理解、补充或修正。"
        ),
        "如果下一场还要和这个 AI 一起辩，你愿意继续把它当作队友吗？",
    ]
    assert [list(options) for options in OVERALL_OPTIONS] == [
        [
            "完全只顾自己，几乎不管队友",
            "大多只顾自己，偶尔配合队友",
            "两方面差不多",
            "大多能配合队友",
            "很像人类队友，会主动补充、接续和配合",
        ],
        [
            "几乎没有，常常各说各的",
            "很少接得上",
            "有时接得上，有时接不上",
            "大多数时候接得上",
            "几乎总能接住并继续推进",
        ],
        ["明显没帮助，甚至添乱", "帮助很少", "有一点帮助", "比较有帮助", "帮助很大"],
        ["几乎没有", "很少", "有一点", "不少", "很多"],
        ["完全不愿意", "不太愿意", "说不上", "比较愿意", "非常愿意"],
    ]
    assert POSTMATCH_QUESTIONNAIRE["human_self"]["q1"]["text"] == (
        "当时你为什么选择自己发言，而不是把这个机会留给 AI 或其他队友？请选择最主要的原因。"
    )
    assert POSTMATCH_QUESTIONNAIRE["human_self"]["q1"]["options"] == list(HUMAN_PRIMARY_REASONS)
    assert POSTMATCH_QUESTIONNAIRE["human_self"]["q2"]["text"] == ("你这次发言最主要想完成什么？")
    assert POSTMATCH_QUESTIONNAIRE["human_self"]["q2"]["options"] == list(HUMAN_GOALS)
    assert POSTMATCH_QUESTIONNAIRE["human_self"]["q2"]["descriptions"] == (HUMAN_GOAL_DESCRIPTIONS)
    assert "其他" not in POSTMATCH_QUESTIONNAIRE["human_self"]["q2"]["descriptions"]
    assert POSTMATCH_QUESTIONNAIRE["team_ai"]["q1"]["options"] == list(AI_ACCEPTABLE_ACTIONS)
    assert POSTMATCH_QUESTIONNAIRE["team_ai"]["q1"]["type"] == "multiple_choice"
    assert POSTMATCH_QUESTIONNAIRE["team_ai"]["q2"]["options"] == list(AI_ACTION_REALIZATION)
    assert [item["text"] for item in POSTMATCH_QUESTIONNAIRE["overall"]] == list(
        PAPER_OVERALL_QUESTIONS
    )
    assert [
        [option["text"] for option in item["options"]]
        for item in POSTMATCH_QUESTIONNAIRE["overall"]
    ] == [list(PAPER_LIKERT_OPTIONS)] * len(PAPER_OVERALL_QUESTIONS)


def test_strict_postmatch_speech_answers_enforce_question_types() -> None:
    _validate_strict_speech_answer(
        subject_kind="HUMAN_SELF",
        value={"q1": HUMAN_PRIMARY_REASONS[0], "q2": [HUMAN_GOALS[0], HUMAN_GOALS[3]]},
    )
    _validate_strict_speech_answer(
        subject_kind="TEAM_AI",
        value={"q1": [AI_ACCEPTABLE_ACTIONS[0]], "q2": AI_ACTION_REALIZATION[0]},
    )
    _validate_strict_speech_answer(
        subject_kind="TEAM_AI", value={"q1": [AI_ACCEPTABLE_ACTIONS[2]], "q2": None}, submit=False
    )
    with pytest.raises(APIError, match="survey_answer_incomplete"):
        _validate_strict_speech_answer(
            subject_kind="TEAM_AI", value={"q1": [AI_ACCEPTABLE_ACTIONS[2]]}, submit=True
        )
    with pytest.raises(APIError, match="survey_answer_invalid"):
        _validate_strict_speech_answer(
            subject_kind="TEAM_AI",
            value={"q1": [AI_ACCEPTABLE_ACTIONS[2]], "q2": [AI_ACTION_REALIZATION[0]]},
        )
    with pytest.raises(APIError, match="survey_answer_invalid"):
        _validate_strict_speech_answer(
            subject_kind="HUMAN_SELF",
            value={"q1": HUMAN_PRIMARY_REASONS[0], "q2": []},
        )
    with pytest.raises(APIError, match="survey_answer_incomplete"):
        _validate_strict_speech_answer(
            subject_kind="HUMAN_SELF", value={"q1": None, "q2": [HUMAN_GOALS[0]]}
        )
    with pytest.raises(APIError, match="survey_answer_incomplete"):
        _validate_strict_speech_answer(
            subject_kind="TEAM_AI", value={"q1": [AI_ACCEPTABLE_ACTIONS[0]], "q2": None}
        )


def test_strict_postmatch_overall_requires_exact_five_point_answers_on_submit() -> None:
    _validate_strict_overall({"q1": 1, "q2": 2, "q3": 3, "q4": 4, "q5": 5}, submit=True)
    with pytest.raises(APIError, match="survey_answer_incomplete"):
        _validate_strict_overall({"q1": 1}, submit=True)
    with pytest.raises(APIError, match="survey_answer_invalid"):
        _validate_strict_overall({"q1": 6}, submit=False)


def test_strict_ai_postspeech_answer_requires_saved_prespeech_choice() -> None:
    with pytest.raises(APIError, match="survey_answer_invalid"):
        _validate_strict_speech_answer(
            subject_kind="TEAM_AI",
            value={"q1": None, "q2": AI_ACTION_REALIZATION[0]},
            submit=False,
        )


def test_strict_postmatch_prespeech_answer_cannot_change_after_reveal() -> None:
    previous = {"speeches": {"ai-1": {"q1": AI_APPROPRIATENESS[0]}}}
    subject_kinds = {"human-1": "HUMAN_SELF", "ai-1": "TEAM_AI"}
    _validate_strict_prespeech_answers_immutable(
        previous_answers=previous,
        next_speeches={"ai-1": {"q1": AI_APPROPRIATENESS[0], "q2": AI_MATCH_REASONS[0]}},
        subject_kinds=subject_kinds,
    )
    with pytest.raises(APIError, match="survey_locked"):
        _validate_strict_prespeech_answers_immutable(
            previous_answers=previous,
            next_speeches={"ai-1": {"q1": AI_APPROPRIATENESS[1]}},
            subject_kinds=subject_kinds,
        )
    with pytest.raises(APIError, match="survey_locked"):
        _validate_strict_prespeech_answers_immutable(
            previous_answers=previous,
            next_speeches={},
            subject_kinds=subject_kinds,
        )


def test_strict_postmatch_requires_q1_save_before_q2_and_blocks_future_speeches() -> None:
    with pytest.raises(APIError, match="survey_answer_invalid"):
        _validate_strict_progression(
            previous_answers={},
            next_speeches={
                "human-1": {
                    "q1": HUMAN_PRIMARY_REASONS[0],
                    "q2": [HUMAN_GOALS[0]],
                }
            },
            ordered_speech_ids=["human-1", "ai-1"],
        )
    with pytest.raises(APIError, match="survey_answer_invalid"):
        _validate_strict_progression(
            previous_answers={},
            next_speeches={"ai-1": {"q1": AI_APPROPRIATENESS[0]}},
            ordered_speech_ids=["human-1", "ai-1"],
        )


def test_strict_postmatch_advances_only_after_current_speech_is_complete() -> None:
    q1_saved = {"speeches": {"human-1": {"q1": HUMAN_PRIMARY_REASONS[0]}}}
    _validate_strict_progression(
        previous_answers=q1_saved,
        next_speeches={
            "human-1": {
                "q1": HUMAN_PRIMARY_REASONS[0],
                "q2": [HUMAN_GOALS[0]],
            }
        },
        ordered_speech_ids=["human-1", "ai-1"],
    )
    completed = {
        "speeches": {
            "human-1": {
                "q1": HUMAN_PRIMARY_REASONS[0],
                "q2": [HUMAN_GOALS[0]],
            }
        }
    }
    _validate_strict_progression(
        previous_answers=completed,
        next_speeches={
            **completed["speeches"],
            "ai-1": {"q1": AI_APPROPRIATENESS[0]},
        },
        ordered_speech_ids=["human-1", "ai-1"],
    )


def test_postmatch_projection_reveals_context_then_current_text_after_q1() -> None:
    speech_ids = ["opponent", "self", "team-ai", "future-opponent"]
    subject_kinds = ["OPPONENT_HUMAN", "HUMAN_SELF", "TEAM_AI", "OPPONENT_AI"]
    assert _review_projection(
        answers={},
        status="PENDING",
        speech_ids=speech_ids,
        subject_kinds=subject_kinds,
    ) == [
        (True, "CONTEXT"),
        (False, "CURRENT_PRE"),
        (False, "FUTURE"),
        (False, "FUTURE"),
    ]
    answers = {"speeches": {"self": {"q1": HUMAN_PRIMARY_REASONS[0]}}}
    assert _review_projection(
        answers=answers,
        status="IN_PROGRESS",
        speech_ids=speech_ids,
        subject_kinds=subject_kinds,
    ) == [
        (True, "CONTEXT"),
        (True, "CURRENT_POST"),
        (False, "FUTURE"),
        (False, "FUTURE"),
    ]


def test_postmatch_projection_only_targets_free_debate_and_waits_for_confirmation() -> None:
    speech_ids = ["opening-self", "free-self", "free-ai", "future-opponent"]
    subject_kinds = ["HUMAN_SELF", "HUMAN_SELF", "TEAM_AI", "OPPONENT_AI"]
    annotatable = [False, True, True, False]
    answers = {
        "speeches": {
            "opening-self": {
                "q1": HUMAN_PRIMARY_REASONS[0],
                "q2": [HUMAN_GOALS[0]],
            },
            "free-self": {
                "q1": HUMAN_PRIMARY_REASONS[0],
                "q2": [HUMAN_GOALS[0]],
            },
        },
        POSTMATCH_WORKFLOW_KEY: {
            "version": POSTMATCH_WORKFLOW_VERSION,
            "scope": POSTMATCH_SCOPE_FREE_DEBATE,
            "confirmed_speech_ids": [],
        },
    }

    assert _review_projection(
        answers=answers,
        status="IN_PROGRESS",
        speech_ids=speech_ids,
        subject_kinds=subject_kinds,
        annotatable=annotatable,
    ) == [
        (True, "CONTEXT"),
        (True, "CURRENT_POST"),
        (False, "FUTURE"),
        (False, "FUTURE"),
    ]

    answers[POSTMATCH_WORKFLOW_KEY]["confirmed_speech_ids"] = ["free-self"]
    assert _review_projection(
        answers=answers,
        status="IN_PROGRESS",
        speech_ids=speech_ids,
        subject_kinds=subject_kinds,
        annotatable=annotatable,
    ) == [
        (True, "CONTEXT"),
        (True, "COMPLETE"),
        (False, "CURRENT_PRE"),
        (False, "FUTURE"),
    ]


def test_postmatch_scope_keeps_submitted_legacy_tasks_readable() -> None:
    assert _annotation_scope({}, status="PENDING") == POSTMATCH_SCOPE_FREE_DEBATE
    assert _annotation_scope({}, status="SUBMITTED") == POSTMATCH_SCOPE_LEGACY
    assert (
        _annotation_scope(
            {
                POSTMATCH_WORKFLOW_KEY: {
                    "version": POSTMATCH_WORKFLOW_VERSION,
                    "scope": POSTMATCH_SCOPE_FREE_DEBATE,
                }
            },
            status="SUBMITTED",
        )
        == POSTMATCH_SCOPE_FREE_DEBATE
    )


def test_free_debate_participant_answers_hide_retained_and_future_speech_keys() -> None:
    answers = {
        "speeches": {
            "opening-self": {"q1": HUMAN_PRIMARY_REASONS[0], "q2": [HUMAN_GOALS[0]]},
            "visible-free-self": {
                "q1": HUMAN_PRIMARY_REASONS[0],
                "q2": [HUMAN_GOALS[0]],
            },
            "future-free-ai": {"q1": AI_APPROPRIATENESS[2]},
        },
        "overall": {"q1": 5},
        POSTMATCH_WORKFLOW_KEY: {
            "version": POSTMATCH_WORKFLOW_VERSION,
            "scope": POSTMATCH_SCOPE_FREE_DEBATE,
            "revision": 3,
            "confirmed_speech_ids": ["visible-free-self"],
        },
    }

    assert _participant_answers_projection(
        answers,
        scope=POSTMATCH_SCOPE_FREE_DEBATE,
        visible_target_ids={"visible-free-self"},
    ) == {
        "speeches": {
            "visible-free-self": {
                "q1": HUMAN_PRIMARY_REASONS[0],
                "q2": [HUMAN_GOALS[0]],
            }
        }
    }
    assert (
        _participant_answers_projection(
            answers,
            scope=POSTMATCH_SCOPE_LEGACY,
            visible_target_ids=set(),
        )
        is answers
    )

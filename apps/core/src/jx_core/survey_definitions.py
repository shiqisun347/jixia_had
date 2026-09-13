"""Published question text and answer choices for the current AI debate survey."""

from __future__ import annotations

HUMAN_PRIMARY_REASONS = (
    "自己更适合处理这个问题。",
    "担心AI 不适合或者处理不好这个问题。",
    "没人回答或者其他担忧被迫回答",
    "其他。",
)

HUMAN_GOALS = (
    "回应对手",
    "接续队友",
    "补足缺口",
    "主动推进",
    "调整方向",
    "其他",
)

HUMAN_GOAL_DESCRIPTIONS = {
    "回应对手": "对手刚提出了需要处理的攻击、质疑或追问",
    "接续队友": "队友的内容需要补充、澄清或继续推进",
    "补足缺口": "我注意到己方有一个重要问题还没人处理",
    "主动推进": "我认为应该推进新的或已有的己方论证",
    "调整方向": "我认为当前讨论需要转向更重要的问题",
}

AI_APPROPRIATENESS = (
    "不合适，比如有此时其他人更合适发言：",
    "发言不发言都合适：",
    "很适合 AI 发言",
    "无法判断：",
)

AI_MATCH_REASONS = (
    "很好，处理了当时团队真正需要处理的问题",
    "一般，内容有点冗余，重复表达，新增价值很小",
    "一般，有点跑偏或者钻牛角尖，内容可能有价值，但不是当时最需要处理的问题",
    "不好，为后续带来了额外的修复负担",
    "其他不好或者一般的原因",
    "无法判断",
)

AI_ACCEPTABLE_ACTIONS = (
    "反驳挑战",
    "修补防守",
    "澄清区分",
    "延伸团队论证",
    "比较竞争考量",
    "综合/重构立场",
    "填补协调缺口",
)

AI_ACTION_REALIZATION = ("完全实现", "基本实现", "部分实现", "未实现", "相矛盾")

OVERALL_QUESTIONS = (
    "这场比赛里，AI 更像一个会和队友配合的辩手，还是只顾自己对抗的辩手？",
    "AI 的发言通常有没有接住队友刚才说的内容和场上的情况？",
    "AI 的发言对我们团队有多大帮助？",
    (
        "这场比赛里，AI 有没有给你带来额外负担？比如它说得不清楚、有漏洞、"
        "和队友重复，你还要花力气去理解、补充或修正。"
    ),
    "如果下一场还要和这个 AI 一起辩，你愿意继续把它当作队友吗？",
)

OVERALL_OPTIONS = (
    (
        "完全只顾自己，几乎不管队友",
        "大多只顾自己，偶尔配合队友",
        "两方面差不多",
        "大多能配合队友",
        "很像人类队友，会主动补充、接续和配合",
    ),
    (
        "几乎没有，常常各说各的",
        "很少接得上",
        "有时接得上，有时接不上",
        "大多数时候接得上",
        "几乎总能接住并继续推进",
    ),
    ("明显没帮助，甚至添乱", "帮助很少", "有一点帮助", "比较有帮助", "帮助很大"),
    ("几乎没有", "很少", "有一点", "不少", "很多"),
    ("完全不愿意", "不太愿意", "说不上", "比较愿意", "非常愿意"),
)

PAPER_LIKERT_OPTIONS = (
    "非常不同意",
    "不同意",
    "既不同意也不反对",
    "同意",
    "非常同意",
)

# The paper treats these as five separate descriptive measures, not a composite score.
PAPER_OVERALL_QUESTIONS = (
    "AI 队友给人的感觉像是一位会与队友协调的辩手。",
    "AI 队友通常能够理解比赛中正在发生什么。",
    "AI 队友对我们团队很有帮助。",
    (
        "AI 队友给我增加了额外负担，例如它表达不清、论证留有缺口或重复队友内容，"
        "使我不得不花精力理解、补充或纠正它。"
    ),
    "我愿意继续让它作为队友。",
)

POSTMATCH_QUESTIONNAIRE_VERSION = "postmatch-v4-action-set-2026-09-10"

POSTMATCH_QUESTIONNAIRE = {
    "human_self": {
        "q1": {
            "type": "single_choice",
            "text": (
                "当时你为什么选择自己发言，而不是把这个机会留给 AI 或其他队友？"
                "请选择最主要的原因。"
            ),
            "options": list(HUMAN_PRIMARY_REASONS),
        },
        "q2": {
            "type": "multiple_choice",
            "text": "你这次发言最主要想完成什么？",
            "options": list(HUMAN_GOALS),
            "descriptions": HUMAN_GOAL_DESCRIPTIONS,
        },
    },
    "team_ai": {
        "q1": {
            "type": "multiple_choice",
            "text": "在看到 AI 的实际发言之前，根据当时可见的团队状态，团队此刻需要哪些合适的行为？请选择 1–3 项。",
            "max_selections": 3,
            "options": list(AI_ACCEPTABLE_ACTIONS),
        },
        "q2": {
            "type": "single_choice",
            "text": "看完 AI 的实际发言后，这组必要行为整体被实现到什么程度？",
            "options": list(AI_ACTION_REALIZATION),
        },
    },
    "overall": [
        {
            "key": f"q{index + 1}",
            "type": "scale",
            "text": question,
            "options": [
                {"value": score + 1, "text": option}
                for score, option in enumerate(PAPER_LIKERT_OPTIONS)
            ],
        }
        for index, question in enumerate(PAPER_OVERALL_QUESTIONS)
    ],
}

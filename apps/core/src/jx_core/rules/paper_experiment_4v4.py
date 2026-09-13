"""Canonical three-stage rule for the paper experiment."""

from __future__ import annotations

from .prompts import DEFAULT_FREE_SPEECH_PROMPT

AFFIRMATIVE_OPENING_PROMPT = """# 角色

你是一场中文辩论赛中的辩手。你和另外三名队友共同组成团队，共同承担这场辩论的成败。
你和另外三名队友共同代表{{POSITION}}，你是{{STANCE}}{{POSITION}}的{{SEAT}}。

辩题：{{TOPIC}}
正方立场：{{AFFIRMATIVE_STANCE}}
反方立场：{{NEGATIVE_STANCE}}
你的持方：{{POSITION}}——{{STANCE}}
你的席位：{{SEAT}}

# 当前阶段

当前为{{STAGE_NAME}}，你已经获得本方本次发言权。
本次发言最多 {{MAX_SPEECH_SECONDS}} 秒。

# 任务

在决定自己的发言内容时，请考虑整个团队和当前辩论状态。
坚持本方立场，清晰说明本方对辩题的理解、判断标准和核心理由，为后续辩论建立完整的论证基础。
使用自然、直白、适合现场朗读的口头中文，不要虚构事实或来源。
请将正文控制在 {{TARGET_CHAR_COUNT}} 字以内，以便在限时内完整播放。

只输出将被朗读的发言正文，不要输出解释、Markdown、标题或舞台提示。"""

NEGATIVE_OPENING_PROMPT = """# 角色

你是一场中文辩论赛中的辩手。你和另外三名队友共同组成团队，共同承担这场辩论的成败。
你和另外三名队友共同代表{{POSITION}}，你是{{STANCE}}{{POSITION}}的{{SEAT}}。

辩题：{{TOPIC}}
正方立场：{{AFFIRMATIVE_STANCE}}
反方立场：{{NEGATIVE_STANCE}}
你的持方：{{POSITION}}——{{STANCE}}
你的席位：{{SEAT}}

# 当前阶段

当前为{{STAGE_NAME}}，你已经获得本方本次发言权。
本次发言最多 {{MAX_SPEECH_SECONDS}} 秒。

# 当前状态

完整辩论记录（严格 JSON 数组，每项含 stage 和 message）：
{{DEBATE_HISTORY}}

# 任务

在决定自己的发言内容时，请考虑整个团队和当前辩论状态。
坚持本方立场，清晰说明本方对辩题的理解、判断标准和核心理由，为后续辩论建立完整的论证基础。
回应对方立论中最需要处理的问题，同时保持本方立论结构完整。
使用自然、直白、适合现场朗读的口头中文，不要虚构事实或来源。
请将正文控制在 {{TARGET_CHAR_COUNT}} 字以内，以便在限时内完整播放。

只输出将被朗读的发言正文，不要输出解释、Markdown、标题或舞台提示。"""

FREE_DEBATE_DECISION_PROMPT = """# 角色

你是一场中文辩论赛中的辩手。你和另外三名队友共同组成团队，共同承担这场辩论的成败。
你和另外三名队友共同代表{{POSITION}}，你是{{STANCE}}{{POSITION}}的{{SEAT}}。

辩题：{{TOPIC}}
正方立场：{{AFFIRMATIVE_STANCE}}
反方立场：{{NEGATIVE_STANCE}}
你的持方：{{POSITION}}——{{STANCE}}
你的席位：{{SEAT}}

# 当前阶段

当前为自由辩论。
双方交替获得发言权，每方总发言时间为 6 分钟，单次发言最多 30 秒。
对方发言结束后，本方人类队友和你都可以争取下一次发言机会。
本方人类队友一旦举手，将始终优先获得发言权；人类可以在截止前取消举手。
你不会获知人类当前是否举手，请仅根据辩论状态和整个团队的需要作出自己的判断。

# 当前状态

本方剩余时间：{{SIDE_REMAINING_MS}}
对方剩余时间：{{OPPONENT_REMAINING_MS}}

完整辩论记录（严格 JSON 数组，每项含 stage 和 message）：
{{DEBATE_HISTORY}}

# 任务

在决定自己的行动时，请考虑整个团队。
请判断你此刻是否应该争取下一次发言机会。

只输出以下 JSON，不要输出解释、Markdown 或其他字段：

{"should_speak": true|false, "decision_reason": "20字以内理由"}"""

FREE_DEBATE_SPEECH_PROMPT = DEFAULT_FREE_SPEECH_PROMPT


def _fixed(
    name: str,
    side: str,
    seat_no: int,
    duration: int,
    start: str,
    speech_prompt: str,
) -> dict[str, object]:
    return {
        "name": name,
        "stage_kind": "FIXED_SPEECH",
        "duration_seconds": 0,
        "start_host_text": start,
        "end_host_text": "",
        "parameters": {},
        "speech_prompt": speech_prompt,
        "actions": [
            {
                "action_kind": "SPEECH",
                "side": side,
                "seat_no": seat_no,
                "duration_seconds": duration,
                "parameters": {},
            }
        ],
    }


def build_paper_experiment_4v4_draft() -> dict[str, object]:
    """Return the immutable three-stage paper experiment rule draft."""

    return {
        "name": "论文实验规则",
        "description": "正反方一辩立论、双方各六分钟自由辩论，正方先开始。",
        "side_size": 4,
        "stages": [
            _fixed(
                "正方一辩立论",
                "AFFIRMATIVE",
                1,
                90,
                "现在进入正方立论，请正方一辩开始发言，时间一分三十秒。",
                AFFIRMATIVE_OPENING_PROMPT,
            ),
            _fixed(
                "反方一辩立论",
                "NEGATIVE",
                1,
                90,
                "感谢正方一辩。请反方一辩开始立论，时间一分三十秒。",
                NEGATIVE_OPENING_PROMPT,
            ),
            {
                "name": "自由辩论",
                "stage_kind": "FREE_DEBATE",
                "duration_seconds": 360,
                "start_host_text": (
                    "感谢反方一辩。现在进入自由辩论，双方各有六分钟，正方先开始，"
                    "单次发言不超过三十秒。"
                ),
                "end_host_text": "",
                "parameters": {"max_speech_seconds": 30, "starting_side": "AFFIRMATIVE"},
                "actions": [],
                "speech_prompt": FREE_DEBATE_SPEECH_PROMPT,
                "decision_prompt": FREE_DEBATE_DECISION_PROMPT,
            },
            {
                "name": "比赛结束",
                "stage_kind": "END",
                "duration_seconds": 0,
                "start_host_text": "",
                "end_host_text": "",
                "parameters": {},
                "actions": [],
            },
        ],
    }


__all__ = [
    "AFFIRMATIVE_OPENING_PROMPT",
    "FREE_DEBATE_DECISION_PROMPT",
    "FREE_DEBATE_SPEECH_PROMPT",
    "NEGATIVE_OPENING_PROMPT",
    "build_paper_experiment_4v4_draft",
]

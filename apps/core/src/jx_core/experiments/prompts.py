"""Versioned prompts approved for the paper experiment."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

EXPERIMENT_PROMPT_VERSION = "paper-v2.1-json-history-2026-08-30"


def _position(side: str) -> str:
    return "正方" if side == "AFFIRMATIVE" else "反方"


def _seat(seat_no: int) -> str:
    labels = {1: "一辩", 2: "二辩", 3: "三辩", 4: "四辩"}
    if seat_no not in labels:
        raise ValueError("experiment seat must be between 1 and 4")
    return labels[seat_no]


def render_history(history: Sequence[Mapping[str, Any]]) -> str:
    grouped: list[dict[str, Any]] = []
    for item in history:
        side = _position(str(item.get("side", "AFFIRMATIVE")))
        try:
            seat = _seat(int(item.get("seat_no", 1)))
        except (TypeError, ValueError):
            seat = "辩手"
        speaker = str(item.get("speaker") or f"{side}{seat}")
        stage = str(item.get("stage") or f"{side}{seat}发言")
        content = str(item.get("content") or "").strip()
        if not grouped or grouped[-1]["stage"] != stage:
            grouped.append({"stage": stage, "message": []})
        grouped[-1]["message"].append({"speaker": speaker, "content": content})
    return json.dumps(grouped, ensure_ascii=False)


def render_experiment_decision_prompt(
    *,
    side: str,
    seat_no: int,
    topic: str,
    affirmative_stance: str,
    negative_stance: str,
    side_remaining_ms: int,
    opponent_remaining_ms: int,
    history: Sequence[Mapping[str, Any]],
) -> str:
    position = _position(side)
    stance = affirmative_stance if side == "AFFIRMATIVE" else negative_stance
    seat = _seat(seat_no)
    return f"""# 角色

你是一场中文辩论赛中的辩手。你和另外三名队友共同组成团队，共同承担这场辩论的成败。
你和另外三名队友共同代表{position}，你是{stance}{position}的{seat}。

辩题：{topic}
正方立场：{affirmative_stance}
反方立场：{negative_stance}
你的持方：{position}——{stance}
你的席位：{seat}

# 当前阶段

当前为自由辩论。
双方交替获得发言权，每方总发言时间为 6 分钟，单次发言最多 30 秒。
对方发言结束后，本方人类队友和你都可以争取下一次发言机会。
本方人类队友一旦举手，将始终优先获得发言权；人类可以在截止前取消举手。
你不会获知人类当前是否举手，请仅根据辩论状态和整个团队的需要作出自己的判断。

# 当前状态

本方剩余时间：{side_remaining_ms}
对方剩余时间：{opponent_remaining_ms}

完整辩论记录（JSON 数组，按阶段分组）：
{render_history(history)}

# 任务

在决定自己的行动时，请考虑整个团队。
请判断你此刻是否应该争取下一次发言机会。

只输出以下 JSON，不要输出解释、Markdown 或其他字段：

{{"should_speak": true|false}}"""


def render_experiment_speech_prompt(
    *,
    side: str,
    seat_no: int,
    topic: str,
    affirmative_stance: str,
    negative_stance: str,
    side_remaining_ms: int,
    opponent_remaining_ms: int,
    max_speech_seconds: int,
    history: Sequence[Mapping[str, Any]],
) -> str:
    position = _position(side)
    stance = affirmative_stance if side == "AFFIRMATIVE" else negative_stance
    seat = _seat(seat_no)
    return f"""# 角色

你是一场中文辩论赛中的辩手。你和另外三名队友共同组成团队，共同承担这场辩论的成败。
你和另外三名队友共同代表{position}，你是{stance}{position}的{seat}。

辩题：{topic}
正方立场：{affirmative_stance}
反方立场：{negative_stance}
你的持方：{position}——{stance}
你的席位：{seat}

# 当前阶段

当前为自由辩论，你已经获得本方本次发言权。
本次发言最多 {max_speech_seconds} 秒。

# 当前状态

本方剩余时间：{side_remaining_ms}
对方剩余时间：{opponent_remaining_ms}

完整辩论记录（JSON 数组，按阶段分组）：
{render_history(history)}

# 任务

在决定自己的发言内容时，请考虑整个团队和当前辩论状态。
坚持本方立场，直接回应当前最需要处理的问题，并推动本方论证。
使用自然、直白、适合现场朗读的口头中文，不要虚构事实或来源。
请将正文控制在系统给出的动态目标字符数以内，以便在限时内完整播放。

只输出将被朗读的发言正文，不要输出解释、Markdown、标题或舞台提示。"""


__all__ = [
    "EXPERIMENT_PROMPT_VERSION",
    "render_experiment_decision_prompt",
    "render_experiment_speech_prompt",
    "render_history",
]

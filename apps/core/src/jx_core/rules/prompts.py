"""Validation and rendering for rule-owned Agent Prompt templates."""

from __future__ import annotations

import re
from collections.abc import Mapping

PROMPT_VARIABLE_RE = re.compile(r"{{\s*([A-Z][A-Z0-9_]*)\s*}}")
KNOWN_PROMPT_VARIABLES = frozenset(
    {
        "TOPIC",
        "POSITION",
        "STANCE",
        "AFFIRMATIVE_STANCE",
        "NEGATIVE_STANCE",
        "SEAT",
        "STAGE_NAME",
        "MAX_SPEECH_SECONDS",
        "TARGET_CHAR_COUNT",
        "DEBATE_HISTORY",
        "SIDE_REMAINING_MS",
        "OPPONENT_REMAINING_MS",
    }
)
ROLE_VARIABLES = frozenset(
    {"TOPIC", "POSITION", "STANCE", "AFFIRMATIVE_STANCE", "NEGATIVE_STANCE", "SEAT"}
)
FIXED_SPEECH_VARIABLES = ROLE_VARIABLES | {
    "STAGE_NAME",
    "MAX_SPEECH_SECONDS",
    "TARGET_CHAR_COUNT",
}
FREE_DECISION_VARIABLES = ROLE_VARIABLES | {
    "SIDE_REMAINING_MS",
    "OPPONENT_REMAINING_MS",
    "DEBATE_HISTORY",
}
FREE_SPEECH_VARIABLES = ROLE_VARIABLES | {
    "MAX_SPEECH_SECONDS",
    "TARGET_CHAR_COUNT",
    "DEBATE_HISTORY",
}

DEFAULT_FIXED_SPEECH_PROMPT = """# 角色

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

坚持本方立场，完成当前阶段要求并推动本方论证。
使用自然、直白、适合现场朗读的口头中文，不要虚构事实或来源。
请将正文控制在 {{TARGET_CHAR_COUNT}} 字以内。

只输出将被朗读的发言正文，不要输出解释、Markdown、标题或舞台提示。"""

DEFAULT_FREE_DECISION_PROMPT = """# 角色

你是一场中文辩论赛中的辩手。你和另外三名队友共同组成团队。
你和另外三名队友共同代表{{POSITION}}，你是{{STANCE}}{{POSITION}}的{{SEAT}}。

辩题：{{TOPIC}}
正方立场：{{AFFIRMATIVE_STANCE}}
反方立场：{{NEGATIVE_STANCE}}
本方剩余时间：{{SIDE_REMAINING_MS}}
对方剩余时间：{{OPPONENT_REMAINING_MS}}
完整辩论记录（严格 JSON 数组，每项含 stage 和 message）：
{{DEBATE_HISTORY}}

在决定自己的行动时，请考虑整个团队。
请判断你此刻是否应该争取下一次发言机会。
请同时用 20 字以内说明最主要的决策理由。
只输出：{"should_speak": true|false, "decision_reason": "20字以内理由"}"""

DEFAULT_FREE_SPEECH_PROMPT = """# 角色

你是当前辩论赛中的{{POSITION}}{{SEAT}}。你和另外三名队友共同组成团队，共同承担这场辩论的成败。
你和另外三名队友共同代表{{POSITION}}。

辩题：{{TOPIC}}
正方立场：{{AFFIRMATIVE_STANCE}}
反方立场：{{NEGATIVE_STANCE}}
你的持方：{{POSITION}}——{{STANCE}}
你的席位：{{SEAT}}

# 当前阶段

当前为自由辩论，你已经获得本方本次发言权。
本次发言最多 {{MAX_SPEECH_SECONDS}} 秒。

完整辩论记录（严格 JSON 数组，每项含 stage 和 message）：
{{DEBATE_HISTORY}}

# 任务

请根据辩论状态、自身个性化和团队协作给出合适的发言。

使用自然、直白、适合现场朗读的口头中文。

请将正文控制在 {{TARGET_CHAR_COUNT}} 字以内，以便在限时内完整播放。

只输出将被朗读的发言正文，不要输出解释、Markdown、标题或舞台提示。"""


class PromptTemplateError(ValueError):
    def __init__(self, code: str, variables: set[str] | frozenset[str]) -> None:
        self.code = code
        self.variables = tuple(sorted(variables))
        super().__init__(f"{code}: {', '.join(self.variables)}")


def extract_prompt_variables(template_text: str) -> frozenset[str]:
    return frozenset(PROMPT_VARIABLE_RE.findall(template_text))


def validate_prompt_template(
    template_text: str, *, required_variables: frozenset[str]
) -> frozenset[str]:
    variables = extract_prompt_variables(template_text)
    unknown = variables - KNOWN_PROMPT_VARIABLES
    if unknown:
        raise PromptTemplateError("prompt_unknown_variables", unknown)
    missing = required_variables - variables
    if missing:
        raise PromptTemplateError("prompt_missing_variables", missing)
    return variables


def render_prompt_template(template_text: str, values: Mapping[str, object]) -> str:
    variables = extract_prompt_variables(template_text)
    unknown = variables - KNOWN_PROMPT_VARIABLES
    if unknown:
        raise PromptTemplateError("prompt_unknown_variables", unknown)
    missing = variables - values.keys()
    if missing:
        raise PromptTemplateError("prompt_render_values_missing", set(missing))
    return PROMPT_VARIABLE_RE.sub(lambda match: str(values[match.group(1)]), template_text)


def target_char_count(seconds: float, chars_per_second: float) -> int:
    if seconds <= 0 or chars_per_second <= 0:
        raise ValueError("seconds and chars_per_second must be positive")
    return int(seconds * chars_per_second * 0.85)


__all__ = [
    "FIXED_SPEECH_VARIABLES",
    "DEFAULT_FIXED_SPEECH_PROMPT",
    "DEFAULT_FREE_DECISION_PROMPT",
    "DEFAULT_FREE_SPEECH_PROMPT",
    "FREE_DECISION_VARIABLES",
    "FREE_SPEECH_VARIABLES",
    "KNOWN_PROMPT_VARIABLES",
    "PromptTemplateError",
    "extract_prompt_variables",
    "render_prompt_template",
    "target_char_count",
    "validate_prompt_template",
]

from __future__ import annotations

import pytest

from jx_core.rules.prompts import (
    DEFAULT_FREE_SPEECH_PROMPT,
    FIXED_SPEECH_VARIABLES,
    FREE_SPEECH_VARIABLES,
    PromptTemplateError,
    render_prompt_template,
    target_char_count,
    validate_prompt_template,
)

EXPECTED_FREE_SPEECH_PROMPT = """# 角色

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


def _template(variables: frozenset[str]) -> str:
    return "\n".join(f"{name}: {{{{{name}}}}}" for name in sorted(variables))


def test_prompt_validation_accepts_required_variables_and_optional_history() -> None:
    variables = validate_prompt_template(
        _template(FIXED_SPEECH_VARIABLES | {"DEBATE_HISTORY"}),
        required_variables=FIXED_SPEECH_VARIABLES,
    )
    assert variables == FIXED_SPEECH_VARIABLES | {"DEBATE_HISTORY"}


def test_approved_free_speech_prompt_omits_optional_remaining_time_variables() -> None:
    assert DEFAULT_FREE_SPEECH_PROMPT == EXPECTED_FREE_SPEECH_PROMPT
    variables = validate_prompt_template(
        DEFAULT_FREE_SPEECH_PROMPT,
        required_variables=FREE_SPEECH_VARIABLES,
    )
    assert variables == FREE_SPEECH_VARIABLES
    assert "SIDE_REMAINING_MS" not in variables
    assert "OPPONENT_REMAINING_MS" not in variables


def test_free_speech_prompt_still_accepts_optional_remaining_time_variables() -> None:
    variables = validate_prompt_template(
        DEFAULT_FREE_SPEECH_PROMPT
        + "\n本方剩余：{{SIDE_REMAINING_MS}}\n对方剩余：{{OPPONENT_REMAINING_MS}}",
        required_variables=FREE_SPEECH_VARIABLES,
    )
    assert variables == FREE_SPEECH_VARIABLES | {
        "SIDE_REMAINING_MS",
        "OPPONENT_REMAINING_MS",
    }


@pytest.mark.parametrize("variable", sorted(FREE_SPEECH_VARIABLES))
def test_free_speech_prompt_rejects_each_missing_required_variable(variable: str) -> None:
    template = DEFAULT_FREE_SPEECH_PROMPT.replace(f"{{{{{variable}}}}}", "")
    with pytest.raises(PromptTemplateError) as raised:
        validate_prompt_template(template, required_variables=FREE_SPEECH_VARIABLES)
    assert raised.value.code == "prompt_missing_variables"
    assert raised.value.variables == (variable,)


@pytest.mark.parametrize(
    ("template", "code", "variable"),
    [
        ("{{TOPIC}} {{UNKNOWN}}", "prompt_unknown_variables", "UNKNOWN"),
        (_template(FIXED_SPEECH_VARIABLES - {"SEAT"}), "prompt_missing_variables", "SEAT"),
    ],
)
def test_prompt_validation_rejects_unknown_and_missing_variables(
    template: str, code: str, variable: str
) -> None:
    with pytest.raises(PromptTemplateError) as raised:
        validate_prompt_template(template, required_variables=FIXED_SPEECH_VARIABLES)
    assert raised.value.code == code
    assert variable in raised.value.variables


def test_prompt_rendering_is_whitelist_based_and_requires_every_value() -> None:
    assert render_prompt_template("辩题：{{ TOPIC }}", {"TOPIC": "测试题"}) == "辩题：测试题"
    with pytest.raises(PromptTemplateError) as raised:
        render_prompt_template("{{TOPIC}} {{SEAT}}", {"TOPIC": "测试题"})
    assert raised.value.code == "prompt_render_values_missing"
    assert raised.value.variables == ("SEAT",)


def test_target_char_count_uses_calibrated_rate_and_safety_factor() -> None:
    assert target_char_count(30, 4.0) == 102
    assert target_char_count(90, 4.5) == 344
    with pytest.raises(ValueError):
        target_char_count(0, 4.0)

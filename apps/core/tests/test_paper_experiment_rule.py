import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from jx_core.rules.paper_experiment_4v4 import (
    AFFIRMATIVE_OPENING_PROMPT,
    FREE_DEBATE_DECISION_PROMPT,
    FREE_DEBATE_SPEECH_PROMPT,
    NEGATIVE_OPENING_PROMPT,
    build_paper_experiment_4v4_draft,
)
from jx_core.rules.prompts import DEFAULT_FREE_SPEECH_PROMPT
from jx_core.rules.validation import validate_rule_draft

_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts/ops/ensure_paper_experiment_rule.py"
_SPEC = importlib.util.spec_from_file_location("ensure_paper_experiment_rule", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_archive_previous_rules = _MODULE._archive_previous_rules
_prompt_templates_match = _MODULE._prompt_templates_match


def test_paper_experiment_rule_is_exactly_three_stage_schedule() -> None:
    draft = build_paper_experiment_4v4_draft()
    stages = draft["stages"]
    assert [stage["stage_kind"] for stage in stages] == [
        "FIXED_SPEECH",
        "FIXED_SPEECH",
        "FREE_DEBATE",
        "END",
    ]
    assert stages[0]["actions"][0]["duration_seconds"] == 90
    assert stages[1]["actions"][0]["duration_seconds"] == 90
    assert stages[2]["duration_seconds"] == 360
    assert stages[2]["parameters"] == {"max_speech_seconds": 30, "starting_side": "AFFIRMATIVE"}
    assert "正方先开始" in stages[2]["start_host_text"]
    assert validate_rule_draft(draft)["estimated_seconds"] == 900


def test_paper_experiment_rule_has_no_extra_fixed_speeches_or_summary() -> None:
    draft = build_paper_experiment_4v4_draft()
    names = [stage["name"] for stage in draft["stages"]]
    assert names == ["正方一辩立论", "反方一辩立论", "自由辩论", "比赛结束"]


def test_paper_experiment_prompts_preserve_confirmed_history_rules() -> None:
    assert "# 当前状态" not in AFFIRMATIVE_OPENING_PROMPT
    assert "{{DEBATE_HISTORY}}" not in AFFIRMATIVE_OPENING_PROMPT
    assert "# 当前状态" in NEGATIVE_OPENING_PROMPT
    assert "{{DEBATE_HISTORY}}" in NEGATIVE_OPENING_PROMPT
    assert '"should_speak": true|false' in FREE_DEBATE_DECISION_PROMPT
    assert '"decision_reason": "20字以内理由"' in FREE_DEBATE_DECISION_PROMPT
    assert "其他 Agent" not in FREE_DEBATE_DECISION_PROMPT
    assert "{{TARGET_CHAR_COUNT}}" in FREE_DEBATE_SPEECH_PROMPT
    assert FREE_DEBATE_SPEECH_PROMPT == DEFAULT_FREE_SPEECH_PROMPT
    assert "{{SIDE_REMAINING_MS}}" not in FREE_DEBATE_SPEECH_PROMPT
    assert "{{OPPONENT_REMAINING_MS}}" not in FREE_DEBATE_SPEECH_PROMPT


@pytest.mark.asyncio
async def test_existing_paper_rule_archives_previous_versions() -> None:
    previous = SimpleNamespace(status="GENERATING_AUDIO")
    archived = SimpleNamespace(status="ARCHIVED")
    scalar_result = SimpleNamespace(all=lambda: [previous, archived])
    session = SimpleNamespace(
        scalars=AsyncMock(return_value=scalar_result),
        commit=AsyncMock(),
    )

    await _archive_previous_rules(session, uuid4())

    assert previous.status == "ARCHIVED"
    assert archived.status == "ARCHIVED"
    session.commit.assert_awaited_once()


def test_paper_rule_match_rejects_stale_prompt_text() -> None:
    stale = SimpleNamespace(purpose="SPEECH", template_text="旧通用 Prompt")
    exact = SimpleNamespace(purpose="SPEECH", template_text=AFFIRMATIVE_OPENING_PROMPT)

    assert not _prompt_templates_match(
        [stale], speech_prompt=AFFIRMATIVE_OPENING_PROMPT, decision_prompt=None
    )
    assert _prompt_templates_match(
        [exact], speech_prompt=AFFIRMATIVE_OPENING_PROMPT, decision_prompt=None
    )

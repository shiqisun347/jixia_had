from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from jx_core.admin_routes import agent_generation_detail, agent_generation_view
from jx_core.agent.llm import LlmProviderError
from jx_core.models import AgentGeneration
from jx_core.postmatch import _parse_result
from jx_core.postmatch_routes import can_retry_judge_for_viewer, public_leaderboard_avatar_key


def _result() -> dict[str, object]:
    dimensions = {
        "argument": 30,
        "rebuttal": 25,
        "evidence": 20,
        "teamwork": 15,
        "expression": 10,
    }
    return {
        "winner": "AFFIRMATIVE",
        "team_scores": {"AFFIRMATIVE": dimensions, "NEGATIVE": dimensions},
        "participants": [
            {"participant_id": "p1", "score": 18, "comment": "清晰"},
            {"participant_id": "p2", "score": 17, "comment": "扎实"},
        ],
        "team_comments": {"AFFIRMATIVE": "有效", "NEGATIVE": "完整"},
    }


def test_parse_judge_result_accepts_complete_stable_ids() -> None:
    parsed = _parse_result(json.dumps(_result()), {"p1", "p2"})
    assert parsed["winner"] == "AFFIRMATIVE"


def test_parse_judge_result_rejects_missing_participant() -> None:
    with pytest.raises(LlmProviderError, match="judge_result_invalid"):
        _parse_result(json.dumps(_result()), {"p1", "p2", "p3"})


def test_parse_judge_result_rejects_dimension_above_maximum() -> None:
    result = _result()
    team_scores = result["team_scores"]
    assert isinstance(team_scores, dict)
    affirmative = team_scores["AFFIRMATIVE"]
    assert isinstance(affirmative, dict)
    affirmative["argument"] = 31
    with pytest.raises(LlmProviderError, match="judge_result_invalid"):
        _parse_result(json.dumps(result), {"p1", "p2"})


def test_parse_judge_result_normalizes_chinese_labels_and_participant_order() -> None:
    result = _result()
    result["winner"] = "正方"
    result["team_scores"] = {
        "正方": {"立论": 25, "反驳": 20, "事实与证据": 18, "团队协作": 13, "表达与规则": 9},
        "反方": {"立论": 24, "反驳": 19, "事实与证据": 17, "团队协作": 12, "表达与规则": 8},
    }
    result["participants"] = [
        {"participant_id": "甲", "score": 18, "comment": "清晰"},
        {"participant_id": "乙", "score": 17, "comment": "扎实"},
    ]
    parsed = _parse_result(
        json.dumps(result, ensure_ascii=False),
        {"p1", "p2"},
        [
            {"participant_id": "p1", "name": "甲", "side": "AFFIRMATIVE", "seat_no": 1},
            {"participant_id": "p2", "name": "乙", "side": "NEGATIVE", "seat_no": 1},
        ],
    )
    assert parsed["winner"] == "AFFIRMATIVE"
    assert parsed["participants"][0]["participant_id"] == "p1"


def test_public_leaderboard_avatar_key_only_exposes_allowlisted_presets() -> None:
    human_id = uuid4()
    agent_id = uuid4()
    assert (
        public_leaderboard_avatar_key(
            "HUMAN", human_id, {human_id: "human-03"}, {agent_id: "agent-04"}
        )
        == "human-03"
    )
    assert (
        public_leaderboard_avatar_key(
            "AGENT", agent_id, {human_id: "human-03"}, {agent_id: "agent-04"}
        )
        == "agent-04"
    )
    assert (
        public_leaderboard_avatar_key("HUMAN", human_id, {human_id: "../../private.webp"}, {})
        is None
    )
    assert public_leaderboard_avatar_key("HUMAN", agent_id, {}, {agent_id: "agent-04"}) is None


def test_judge_retry_is_limited_to_failed_finished_match_owner_or_admin() -> None:
    owner = uuid4()
    other = uuid4()
    assert can_retry_judge_for_viewer(
        match_status="FINISHED",
        judge_status="FAILED",
        role="USER",
        user_id=owner,
        organizer_user_id=owner,
    )
    assert can_retry_judge_for_viewer(
        match_status="FINISHED",
        judge_status="FAILED",
        role="ADMIN",
        user_id=other,
        organizer_user_id=owner,
    )
    assert not can_retry_judge_for_viewer(
        match_status="FINISHED",
        judge_status="FAILED",
        role="USER",
        user_id=other,
        organizer_user_id=owner,
    )
    assert not can_retry_judge_for_viewer(
        match_status="TERMINATED",
        judge_status="FAILED",
        role="ADMIN",
        user_id=other,
        organizer_user_id=owner,
    )


def test_agent_generation_diagnostic_exposes_only_persisted_safe_fields() -> None:
    generation = AgentGeneration(
        id=uuid4(),
        match_id=uuid4(),
        action_key="1:1",
        agent_profile_id=uuid4(),
        context_version=7,
        attempt_no=2,
        status="FAILED",
        input_snapshot={"messages": [{"role": "user", "content": "脱敏输入"}]},
        llm_draft_text="部分草稿",
        first_token_latency_ms=510,
        completed_latency_ms=None,
        completion_tokens=12,
        error_code="llm_stream_stalled",
        created_at=datetime.now(UTC),
    )
    summary = agent_generation_view(generation, "乾元").model_dump(mode="json")
    assert "input_snapshot" not in summary
    assert "llm_draft_text" not in summary
    payload = agent_generation_detail(generation, "乾元").model_dump(mode="json")
    assert payload["agent_name"] == "乾元"
    assert payload["input_snapshot"]["messages"][0]["content"] == "脱敏输入"
    assert payload["llm_draft_text"] == "部分草稿"
    assert "api_key" not in payload
    assert "provider_response" not in payload

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from jx_core.admin_data_routes import _call_explanation, _call_view, _timeline_at
from jx_core.models import ExternalCall


def test_call_explanation_translates_failure_into_actionable_chinese() -> None:
    explanation = _call_explanation("LLM_SPEECH", "FAILED", "llm_first_token_timeout")

    assert explanation["what"] == "Agent 发言稿失败"
    assert "llm_first_token_timeout" in explanation["why"]
    assert "新的调用记录" in explanation["impact"]


def test_call_view_keeps_summary_small_and_exposes_blob_presence_only() -> None:
    call = ExternalCall(
        id=uuid4(),
        call_kind="LLM_DECISION",
        provider="openai-compatible",
        operation="chat.completions",
        model="test-model",
        attempt_no=1,
        status="SUCCEEDED",
        match_id=uuid4(),
        request_blob_id=uuid4(),
        response_blob_id=uuid4(),
        context_version=4,
        started_at=datetime.now(UTC),
        first_result_latency_ms=220,
        completed_latency_ms=480,
        prompt_tokens=120,
        completion_tokens=18,
    )

    view = _call_view(call)

    assert view["kind_label"] == "Agent 发言决策"
    assert view["has_request"] is True
    assert view["has_response"] is True
    assert "request" not in view
    assert "response" not in view


def test_timeline_at_normalizes_legacy_naive_timestamps() -> None:
    naive = datetime(2026, 8, 17, 8, 30, 0)
    aware = datetime(2026, 8, 17, 8, 30, 0, tzinfo=UTC)

    assert _timeline_at(naive) == _timeline_at(aware)

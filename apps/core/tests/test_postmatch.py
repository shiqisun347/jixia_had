from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import SecretStr

import jx_core.postmatch as postmatch_module
from jx_core.admin_routes import agent_generation_detail, agent_generation_view
from jx_core.agent.llm import LlmCapacityLimiter, LlmProviderError, LlmStreamResult
from jx_core.config import Settings
from jx_core.models import AgentGeneration, ExternalCall, JudgeResult, ModelProfile
from jx_core.postmatch import (
    PostmatchService,
    _bound_judge_history,  # pyright: ignore[reportPrivateUsage]
    _parse_result,  # pyright: ignore[reportPrivateUsage]
    _snapshot_judge,  # pyright: ignore[reportPrivateUsage]
)
from jx_core.postmatch_routes import can_retry_judge_for_viewer, public_leaderboard_avatar_key

JudgeExecution = tuple[PostmatchService, SimpleNamespace, list[ExternalCall], LlmCapacityLimiter]


def _client_factory(stream: Any, close: Any) -> Callable[..., SimpleNamespace]:
    def construct(**_: Any) -> SimpleNamespace:
        return SimpleNamespace(stream_chat=stream, close=close)

    return construct


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


@pytest.fixture
def judge_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> JudgeExecution:
    result_id, model_id = uuid4(), uuid4()
    row = SimpleNamespace(
        id=result_id,
        model_key=str(model_id),
        match_id=uuid4(),
        judge_profile_id=None,
        context_version=1,
        input_snapshot={
            "_judge_runtime": {"model": {"id": str(model_id)}},
            "participants": [{"participant_id": "p1"}, {"participant_id": "p2"}],
        },
    )
    model = SimpleNamespace(
        id=model_id,
        name="synthetic-model",
        max_concurrency=1,
        base_url="https://synthetic.invalid",
        model_id="synthetic-model",
        api_key_ciphertext=b"synthetic",
        api_key_nonce=b"synthetic",
    )
    calls: list[ExternalCall] = []

    class Context:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_: Any) -> bool:
            return False

    class Session(Context):
        def begin(self) -> Context:
            return Context()

        async def get(self, model_type: type[Any], identity: Any, **_: Any) -> Any:
            if model_type is JudgeResult:
                return row
            if model_type is ModelProfile:
                return model
            if model_type is ExternalCall:
                return next((call for call in calls if call.id == identity), None)
            return None

        def add(self, entity: Any) -> None:
            if isinstance(entity, ExternalCall):
                calls.append(entity)

        async def flush(self) -> None:
            return

    limiter = LlmCapacityLimiter(2)
    service = PostmatchService(
        settings=Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            database_url=SecretStr("postgresql+psycopg://test:test@localhost/test"),
            llm_key_encryption_key=SecretStr("synthetic"),
        ),
        session_factory=cast(Any, Session),
        limiter=limiter,
    )

    def decrypt(*_: Any) -> str:
        return "synthetic"

    monkeypatch.setattr(postmatch_module, "decrypt_secret", decrypt)
    monkeypatch.setattr(postmatch_module, "store_content_blob", AsyncMock(return_value=uuid4()))
    return service, row, calls, limiter


async def _assert_judge_capacity_available(limiter: LlmCapacityLimiter, model_key: str) -> None:
    leases = await limiter.acquire(model_key, 1, timeout_seconds=0.1)
    try:
        second = await limiter.acquire("capacity-reuse-probe", 1, timeout_seconds=0.1)
        limiter.release(second)
    finally:
        limiter.release(leases)


@pytest.mark.asyncio
async def test_judge_close_failure_does_not_mask_success_or_retain_capacity(
    monkeypatch: pytest.MonkeyPatch,
    judge_execution: JudgeExecution,
) -> None:
    service, row, calls, limiter = judge_execution
    stream = AsyncMock(return_value=LlmStreamResult(json.dumps(_result()), 1, 1, 1))
    close = AsyncMock(side_effect=RuntimeError("synthetic-close-failed"))
    monkeypatch.setattr(
        postmatch_module,
        "OpenAIStreamingClient",
        _client_factory(stream, close),
    )

    await service._execute(row.id)  # pyright: ignore[reportPrivateUsage]

    assert row.status == "SUCCEEDED"
    assert len(calls) == 1 and calls[0].status == "SUCCEEDED"
    stream.assert_awaited_once()
    close.assert_awaited_once()
    await _assert_judge_capacity_available(limiter, row.model_key)


@pytest.mark.asyncio
async def test_judge_constructor_failure_releases_capacity_and_retries(
    monkeypatch: pytest.MonkeyPatch,
    judge_execution: JudgeExecution,
) -> None:
    service, row, calls, limiter = judge_execution
    constructors = 0

    def constructor(**_: Any) -> None:
        nonlocal constructors
        constructors += 1
        raise RuntimeError("synthetic-constructor-failed")

    monkeypatch.setattr(postmatch_module, "OpenAIStreamingClient", constructor)

    execution = service._execute(row.id)  # pyright: ignore[reportPrivateUsage]
    await asyncio.wait_for(execution, timeout=0.5)

    assert constructors == 2
    assert row.status == "FAILED"
    assert len(calls) == 2 and all(call.status == "FAILED" for call in calls)
    await _assert_judge_capacity_available(limiter, row.model_key)


@pytest.mark.asyncio
async def test_judge_cancel_during_close_releases_capacity_and_propagates_cancel(
    monkeypatch: pytest.MonkeyPatch,
    judge_execution: JudgeExecution,
) -> None:
    service, row, _, limiter = judge_execution
    close_started = asyncio.Event()
    close_cancelled = asyncio.Event()

    async def close() -> None:
        close_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            close_cancelled.set()

    monkeypatch.setattr(
        postmatch_module,
        "OpenAIStreamingClient",
        _client_factory(
            AsyncMock(return_value=LlmStreamResult(json.dumps(_result()), 1, 1, 1)),
            close,
        ),
    )
    execution = service._execute(row.id)  # pyright: ignore[reportPrivateUsage]
    task = asyncio.create_task(execution)
    await asyncio.wait_for(close_started.wait(), timeout=0.5)
    await _assert_judge_capacity_available(limiter, row.model_key)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(close_cancelled.wait(), timeout=0.5)
    await _assert_judge_capacity_available(limiter, row.model_key)


@pytest.mark.asyncio
async def test_judge_close_timeout_releases_capacity_even_if_close_ignores_cancel(
    monkeypatch: pytest.MonkeyPatch,
    judge_execution: JudgeExecution,
) -> None:
    service, row, _, limiter = judge_execution
    monkeypatch.setattr(postmatch_module, "JUDGE_CLIENT_CLOSE_TIMEOUT_SECONDS", 0.01)
    release_close = asyncio.Event()
    close_cancelled = asyncio.Event()
    close_tasks: list[asyncio.Task[Any]] = []

    async def close() -> None:
        task = asyncio.current_task()
        assert task is not None
        close_tasks.append(task)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            close_cancelled.set()
            await release_close.wait()
            raise RuntimeError("synthetic-late-cleanup-error") from None

    monkeypatch.setattr(
        postmatch_module,
        "OpenAIStreamingClient",
        _client_factory(
            AsyncMock(return_value=LlmStreamResult(json.dumps(_result()), 1, 1, 1)),
            close,
        ),
    )
    try:
        execution = service._execute(row.id)  # pyright: ignore[reportPrivateUsage]
        await asyncio.wait_for(execution, timeout=0.5)
        assert row.status == "SUCCEEDED"
        await asyncio.wait_for(close_cancelled.wait(), timeout=0.5)
        assert len(close_tasks) == 1 and not close_tasks[0].done()
        await _assert_judge_capacity_available(limiter, row.model_key)
    finally:
        release_close.set()
        await asyncio.gather(*close_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_judge_provider_failure_retains_retry_semantics_when_close_fails(
    monkeypatch: pytest.MonkeyPatch,
    judge_execution: JudgeExecution,
) -> None:
    service, row, calls, limiter = judge_execution
    stream = AsyncMock(side_effect=LlmProviderError("llm_first_token_timeout"))
    monkeypatch.setattr(
        postmatch_module,
        "OpenAIStreamingClient",
        _client_factory(
            stream,
            AsyncMock(side_effect=RuntimeError("synthetic-close-failed")),
        ),
    )

    await service._execute(row.id)  # pyright: ignore[reportPrivateUsage]

    assert stream.await_count == 2
    assert row.status == "FAILED" and row.error_code == "llm_first_token_timeout"
    assert len(calls) == 2 and all(call.error_code == "llm_first_token_timeout" for call in calls)
    await _assert_judge_capacity_available(limiter, row.model_key)


@pytest.mark.asyncio
async def test_judge_shares_agent_model_id_capacity_and_recovers_after_release(
    monkeypatch: pytest.MonkeyPatch,
    judge_execution: JudgeExecution,
) -> None:
    service, row, calls, limiter = judge_execution
    agent_leases = await limiter.acquire(row.model_key, 1)
    acquire = limiter.acquire

    async def fast_acquire(
        model_key: str, model_limit: int, timeout_seconds: float = 3.0
    ) -> tuple[asyncio.Semaphore, asyncio.Semaphore]:
        return await acquire(model_key, model_limit, timeout_seconds=0.01)

    monkeypatch.setattr(limiter, "acquire", fast_acquire)
    stream = AsyncMock(return_value=LlmStreamResult(json.dumps(_result()), 1, 1, 1))
    constructor = Mock()

    def client(**kwargs: Any) -> SimpleNamespace:
        constructor(**kwargs)
        return SimpleNamespace(stream_chat=stream, close=AsyncMock())

    # A frozen label differs from the live label; only the ModelProfile ID is stable.
    row.input_snapshot["_judge_runtime"]["model"]["name"] = "different-frozen-label"
    monkeypatch.setattr(postmatch_module, "OpenAIStreamingClient", client)
    try:
        await service._execute(row.id)  # pyright: ignore[reportPrivateUsage]
        assert row.status == "FAILED" and row.error_code == "llm_capacity_full"
        assert len(calls) == 2 and all(call.error_code == "llm_capacity_full" for call in calls)
        assert constructor.call_count == 0
        stream.assert_not_awaited()
    finally:
        limiter.release(agent_leases)

    await service._execute(row.id)  # pyright: ignore[reportPrivateUsage]
    assert row.status == "SUCCEEDED"
    assert constructor.call_count == 1
    stream.assert_awaited_once()
    await _assert_judge_capacity_available(limiter, row.model_key)


def test_judge_history_is_bounded_to_recent_entries() -> None:
    history = [{"content": "论据" * 2_000, "speaker": str(index)} for index in range(20)]
    bounded = _bound_judge_history(history)
    assert bounded[0]["speaker"] == "SYSTEM"
    assert bounded[-1]["speaker"] == "19"
    assert len(bounded) < len(history)


def test_parse_judge_result_accepts_complete_stable_ids() -> None:
    parsed = _parse_result(json.dumps(_result()), {"p1", "p2"})
    assert parsed["winner"] == "AFFIRMATIVE"


@pytest.mark.parametrize(
    ("dimension", "invalid_score"),
    [
        ("argument", 31),
        ("rebuttal", 26),
        ("evidence", 21),
        ("teamwork", 16),
        ("expression", 14),
        ("expression", 16),
        ("expression", -1),
        ("expression", True),
    ],
)
def test_judge_rejects_invalid_team_scores_without_clamping(
    dimension: str, invalid_score: object
) -> None:
    result = cast(dict[str, Any], _result())
    result["team_scores"]["NEGATIVE"][dimension] = invalid_score
    with pytest.raises(LlmProviderError, match="judge_result_invalid"):
        _parse_result(json.dumps(result), {"p1", "p2"})
    assert result["team_scores"]["NEGATIVE"][dimension] == invalid_score


@pytest.mark.parametrize("score", [-1, 21, True])
def test_judge_rejects_invalid_personal_scores(score: object) -> None:
    result = cast(dict[str, Any], _result())
    result["participants"][0]["score"] = score
    with pytest.raises(LlmProviderError, match="judge_result_invalid"):
        _parse_result(json.dumps(result), {"p1", "p2"})


def test_judge_rejects_extra_duplicate_even_when_stable_id_set_matches() -> None:
    result = cast(dict[str, Any], _result())
    result["participants"].append(dict(result["participants"][0]))
    with pytest.raises(LlmProviderError, match="judge_result_invalid"):
        _parse_result(json.dumps(result), {"p1", "p2"})


@pytest.mark.asyncio
async def test_judge_sends_fixed_contract_and_retains_normal_retry(
    monkeypatch: pytest.MonkeyPatch, judge_execution: JudgeExecution
) -> None:
    service, row, calls, _ = judge_execution
    runtime = row.input_snapshot["_judge_runtime"]
    runtime.update(
        system_prompt="冻结系统提示",
        judge_prompt="冻结评分提示",
        generation_params={"temperature": 0.3, "response_format": {"type": "json_object"}},
    )
    frozen_input = json.dumps(row.input_snapshot, sort_keys=True)
    invalid = cast(dict[str, Any], _result())
    invalid["team_scores"]["NEGATIVE"]["expression"] = 16
    stream = AsyncMock(
        side_effect=[
            LlmStreamResult(json.dumps(invalid), 1, 1, 1081),
            LlmStreamResult(json.dumps(_result()), 1, 1, 756),
        ]
    )
    monkeypatch.setattr(
        postmatch_module, "OpenAIStreamingClient", _client_factory(stream, AsyncMock())
    )

    await service._execute(row.id)  # pyright: ignore[reportPrivateUsage]

    assert row.status == "SUCCEEDED"
    assert [call.status for call in calls] == ["FAILED", "SUCCEEDED"]
    assert calls[0].error_code == "judge_result_invalid"
    assert stream.await_count == 2
    for invocation in stream.await_args_list:
        kwargs = invocation.kwargs
        assert kwargs["max_tokens"] == 1600
        assert kwargs["messages"][0]["content"].startswith("冻结系统提示")
        assert "expression上限10" in kwargs["messages"][0]["content"]
        assert kwargs["messages"][1]["content"].startswith("冻结评分提示\n")
        assert kwargs["generation_params"]["temperature"] == 0.3
        response_format = kwargs["generation_params"]["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["strict"] is True
        schema = response_format["json_schema"]["schema"]["properties"]
        for side in ("AFFIRMATIVE", "NEGATIVE"):
            dimensions = schema["team_scores"]["properties"][side]["properties"]
            assert {name: value["maximum"] for name, value in dimensions.items()} == {
                "argument": 30,
                "rebuttal": 25,
                "evidence": 20,
                "teamwork": 15,
                "expression": 10,
            }
        participants = schema["participants"]
        assert participants["minItems"] == participants["maxItems"] == 2
        assert participants["items"]["properties"]["participant_id"]["enum"] == ["p1", "p2"]
        assert participants["items"]["properties"]["score"]["maximum"] == 20
    assert json.dumps(row.input_snapshot, sort_keys=True) == frozen_input


def test_snapshot_judge_requires_enabled_version_module() -> None:
    judge = {"system_prompt": "frozen", "model": {"id": str(uuid4())}}
    assert (
        _snapshot_judge({"modules": [{"module_key": "JUDGE", "enabled": True}], "judge": judge})
        == judge
    )
    assert (
        _snapshot_judge({"modules": [{"module_key": "JUDGE", "enabled": False}], "judge": judge})
        is None
    )
    assert _snapshot_judge({"modules": [], "judge": judge}) is None


def test_snapshot_judge_supports_rule_owned_enabled_flag() -> None:
    judge = {"enabled": True, "judge_prompt": "frozen", "model": {"id": str(uuid4())}}
    assert _snapshot_judge({"schema": "rule-config-v1", "judge": judge}) == judge
    assert (
        _snapshot_judge({"schema": "rule-config-v1", "judge": {**judge, "enabled": False}}) is None
    )


def test_parse_judge_result_rejects_missing_participant() -> None:
    with pytest.raises(LlmProviderError, match="judge_result_invalid"):
        _parse_result(json.dumps(_result()), {"p1", "p2", "p3"})


def test_parse_judge_result_rejects_dimension_above_maximum() -> None:
    result = _result()
    team_scores = result["team_scores"]
    assert isinstance(team_scores, dict)
    affirmative = cast(dict[str, Any], team_scores["AFFIRMATIVE"])
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


@pytest.mark.asyncio
async def test_postmatch_close_bounds_judge_tasks_that_ignore_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(postmatch_module, "JUDGE_TASK_CANCEL_TIMEOUT_SECONDS", 0.01)
    release = asyncio.Event()

    async def stubborn_task() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    service = PostmatchService(
        settings=Settings(database_url=SecretStr("postgresql+psycopg://test:test@localhost/test")),
        session_factory=cast(Any, None),
    )
    task = asyncio.create_task(stubborn_task())
    await asyncio.sleep(0)
    service._tasks.add(task)  # pyright: ignore[reportPrivateUsage]

    await asyncio.wait_for(service.close(), timeout=0.2)

    assert not task.done()
    release.set()
    await task


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

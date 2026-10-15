"""Deterministic pair-tool controls, no settings, real DB or external API."""

from __future__ import annotations

import asyncio
import hashlib
import json
import stat
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from scripts.qa.probes import server_spec304_prompt_compare as probe

from jx_core.agent.llm import OpenAIStreamingClient


def scene() -> dict[str, Any]:
    now = datetime(2026, 10, 15, tzinfo=UTC)
    roster = [
        {"id": uuid4(), "agent_profile_id": uuid4(), "side": side, "seat_no": seat}
        for side in ("AFFIRMATIVE", "NEGATIVE")
        for seat in range(1, 5)
    ]
    participant = roster[-1]
    rule = {
        "stages": [
            {"position": 1, "name": "开篇"},
            {"position": 7, "name": "自由辩论"},
            {"position": 8, "name": "反方四辩总结"},
            {"position": 9, "name": "比赛结束"},
        ]
    }
    rows = []
    for index, (side, seat, content) in enumerate(
        (
            ("NEGATIVE", 1, "队友此前完整论点"),
            ("AFFIRMATIVE", 1, "对方实际说过的具体命题"),
            ("NEGATIVE", 4, "本人此前完整论点"),
        ),
        1,
    ):
        owner = next(item for item in roster if item["side"] == side and item["seat_no"] == seat)
        rows.append(
            {
                "id": uuid4(),
                "side": side,
                "seat_no": seat,
                "agent_profile_id": owner["agent_profile_id"],
                "speaker_kind": "AGENT",
                "status": "FINALIZED",
                "display_text": content,
                "action_key": "1:1",
                "created_at": now + timedelta(seconds=index),
                "finalized_at": now + timedelta(seconds=index, milliseconds=500),
            }
        )
    groups = [
        {
            "stage": "开篇",
            "message": [
                {
                    "speaker": ("正方" if row["side"] == "AFFIRMATIVE" else "反方")
                    + {1: "一", 4: "四"}[row["seat_no"]]
                    + "辩",
                    "content": row["display_text"],
                }
                for row in rows
            ],
        }
    ]
    content = (
        "原定自定义任务，不改下一阶段。\n"
        + json.dumps(groups, ensure_ascii=False)
        + "\n原定输出任务。"
    )
    body = {
        "model": "synthetic-model",
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 160,
        "temperature": 0.75,
        "top_p": 0.9,
        "enable_thinking": False,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    generation = SimpleNamespace(
        action_key="7:0", created_at=now + timedelta(seconds=10), context_version=7
    )
    room = SimpleNamespace(
        rule_snapshot=rule,
        topic_snapshot={
            "title": "合成主题",
            "affirmative_text": "支持甲",
            "negative_text": "反对甲",
        },
    )
    assemble, reference, _ = probe.candidate_assembler(Path(__file__).resolve().parents[3])
    return {
        "rows": rows,
        "roster": roster,
        "participant": participant,
        "rule": rule,
        "body": body,
        "groups": groups,
        "generation": generation,
        "room": room,
        "assemble": assemble,
        "history_reference": reference,
    }


def build(values: dict[str, Any], source_id: Any = None) -> tuple[Any, Any, Any]:
    return probe.candidate_messages(
        values["body"],
        generation=values["generation"],
        participant=values["participant"],
        rows=values["rows"],
        roster=values["roster"],
        room=values["room"],
        assemble=values["assemble"],
        history_reference=values["history_reference"],
        source_id=source_id,
    )


def test_actual_candidate_assembler_preserves_task_and_exact_single_typed_history() -> None:
    values = scene()
    before = deepcopy(values["body"])
    messages, history, identity = build(values)
    assert values["body"] == before
    assert [message["role"] for message in messages] == ["system", "user", "user"]
    assert (
        messages[1]["content"]
        == "原定自定义任务，不改下一阶段。\n" + values["history_reference"] + "\n原定输出任务。"
    )
    assert [item["relation"] for item in history] == ["TEAMMATE", "OPPONENT", "SELF"]
    assert [item["speech_id"] for item in history] == [str(row["id"]) for row in values["rows"]]
    assert identity["side"] == "NEGATIVE" and identity["stance"] == "反对甲"
    assert identity["stage"] == "自由辩论" and identity["next_stage"] == "反方四辩总结"
    text = json.dumps(messages, ensure_ascii=False)
    assert text.count("队友此前完整论点") == 1
    assert text.count("对方实际说过的具体命题") == 1
    assert json.loads(messages[-1]["content"])["history"] == history


def test_exact_repeat_by_different_seats_keeps_both_real_ids() -> None:
    values = scene()
    values["rows"][2]["display_text"] = values["rows"][0]["display_text"]
    values["groups"][0]["message"][2]["content"] = values["rows"][0]["display_text"]
    values["body"]["messages"][0]["content"] = json.dumps(values["groups"], ensure_ascii=False)
    _, history, _ = build(values)
    assert history[0]["content"] == history[2]["content"]
    assert history[0]["speech_id"] != history[2]["speech_id"]


@pytest.mark.parametrize("fault", ["duplicate_owner", "future", "invalid_speaker"])
def test_history_identity_ambiguity_or_future_content_is_rejected(fault: str) -> None:
    values = scene()
    if fault == "duplicate_owner":
        duplicate = {**values["rows"][0], "id": uuid4()}
        values["rows"].insert(1, duplicate)
    elif fault == "future":
        values["rows"][0]["created_at"] = values["generation"].created_at + timedelta(seconds=1)
    else:
        values["groups"][0]["message"][0]["speaker"] = "未知辩手"
        values["body"]["messages"][0]["content"] = json.dumps(values["groups"], ensure_ascii=False)
    with pytest.raises(probe.CompareError):
        build(values)


def test_provisional_prefix_does_not_import_its_future_final_text() -> None:
    values = scene()
    prefix = "尚未完成但真实已经进入原始请求的二十字以上识别文字"
    values["rows"][0]["display_text"] = prefix + "这是随后才完成的未来补充，不能加入原请求。"
    values["rows"][0]["finalized_at"] = values["generation"].created_at + timedelta(seconds=1)
    values["groups"][0]["message"][0]["content"] = prefix
    values["body"]["messages"][0]["content"] = json.dumps(values["groups"], ensure_ascii=False)
    messages, history, _ = build(values)
    assert history[0]["content"] == prefix
    assert history[0]["content_state"] == "PROVISIONAL"
    assert "未来补充" not in json.dumps(messages, ensure_ascii=False)


def test_missing_inflight_source_is_explicit_pending_and_not_future_full_text() -> None:
    values = scene()
    source = {
        **values["rows"][1],
        "id": uuid4(),
        "created_at": values["generation"].created_at - timedelta(seconds=1),
        "finalized_at": values["generation"].created_at + timedelta(seconds=1),
        "display_text": "只能在未来final后才知道的完整正文",
    }
    values["rows"].append(source)
    messages, history, _ = build(values, source["id"])
    assert history[-1]["content"] == "" and history[-1]["content_state"] == "PENDING"
    assert json.loads(messages[-1]["content"])["source_speech"]["speech_id"] == str(source["id"])
    assert source["display_text"] not in json.dumps(messages, ensure_ascii=False)


def test_closing_adds_only_already_finalized_history_and_allows_summary() -> None:
    values = scene()
    values["body"]["messages"][0]["content"] = "原始四辩总结任务，不含历史。"
    values["generation"].action_key = "8:1"
    for status in ("RESET", "FAILED"):
        values["rows"].append({**values["rows"][0], "id": uuid4(), "status": status})
    values["rows"].append(
        {
            **values["rows"][0],
            "id": uuid4(),
            "finalized_at": values["generation"].created_at + timedelta(seconds=1),
        }
    )
    messages, history, identity = build(values)
    assert len(history) == 3
    assert identity["stage"] == "反方四辩总结"
    assert "不要求新增论据" in messages[0]["content"]
    assert messages[1]["content"] == "原始四辩总结任务，不含历史。"


def test_multiple_embedded_histories_are_not_silently_replayed_twice() -> None:
    values = scene()
    with pytest.raises(probe.CompareError, match="multiple_histories"):
        probe.grouped_history(values["body"]["messages"][0]["content"] * 2)


def test_schema_0046_speech_query_never_selects_new_origin_column() -> None:
    statement = probe.speech_statement()
    assert "origin" not in statement.selected_columns.keys()
    assert "speeches.origin" not in str(statement)
    assert "speeches.finalized_at" in str(statement)


def test_private_artifacts_are_exclusive_and_never_store_known_credentials(tmp_path: Path) -> None:
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    probe.private_json(root, "safe.json", {"content": "受控内容"}, credential="fake-credential")
    assert stat.S_IMODE((root / "safe.json").stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        probe.private_json(root, "safe.json", {}, credential="fake-credential")
    with pytest.raises(probe.CompareError, match="sensitive"):
        probe.private_json(
            root, "secret.json", {"content": "fake-credential"}, credential="fake-credential"
        )
    assert not (root / "secret.json").exists()


def test_metrics_distinguish_self_teammate_and_opponent_repetition() -> None:
    _, history, _ = build(scene())
    own = probe.repetition_metrics("本人此前完整论点", history)
    teammate = probe.repetition_metrics("队友此前完整论点", history)
    opponent = probe.repetition_metrics("对方实际说过的具体命题", history)
    assert own["self_exact_repeat"] and not own["teammate_exact_repeat"]
    assert teammate["teammate_exact_repeat"] and not teammate["self_exact_repeat"]
    assert not opponent["self_exact_repeat"] and not opponent["teammate_exact_repeat"]
    assert own["max_overlap_fraction_of_output"] == 1


def test_long_review_excerpt_keeps_the_conclusion_within_the_shared_limit() -> None:
    output = "开头" + "中间" * 150 + "最后坚持本方立场"
    excerpt = probe.review_excerpt(output)
    assert len(excerpt) == 180
    assert excerpt.startswith("开头") and excerpt.endswith("最后坚持本方立场")
    assert "…" in excerpt


@pytest.mark.asyncio
async def test_readonly_transaction_is_first_database_operation() -> None:
    observed = []

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def execute(self, value: Any) -> None:
            observed.append(str(value))

        async def get(self, *_: Any) -> None:
            observed.append("read-match")
            return None

    with pytest.raises(probe.CompareError, match="not_terminal"):
        await probe.load_cases(SimpleNamespace(session_factory=Session), None, None, "")
    assert observed[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert observed[1:] == ["read-match"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_one", [False, True])
async def test_twenty_four_actual_adapter_pairs_preserve_parameters_and_serial_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_one: bool,
) -> None:
    values = scene()
    candidate, history, identity = build(values)
    params = {
        key: value
        for key, value in values["body"].items()
        if key not in {"messages", "model", "stream", "stream_options", "max_tokens"}
    }
    cases = [
        {
            "case": f"case-{index}",
            "call_id": str(uuid4()),
            "speech_id": str(uuid4()),
            "access": probe.Access(
                "https://synthetic.invalid/v1", "synthetic-model", "owned-synthetic-key"
            ),
            "body": values["body"],
            "candidate": candidate,
            "history": history,
            "identity": identity,
            "params": params,
            "cutoff": values["generation"].created_at.isoformat(),
        }
        for index in range(4)
    ]
    requests, clients = [], []
    active, maximum = 0, 0

    async def http(request: httpx.Request) -> httpx.Response:
        nonlocal active, maximum
        active += 1
        maximum = max(active, maximum)
        body = json.loads(request.content)
        requests.append(body)
        await asyncio.sleep(0.001)
        active -= 1
        if fail_one and len(requests) == 3:
            return httpx.Response(503, json={"code": "synthetic_failure"})
        output = "队友此前完整论点" if len(body["messages"]) == 1 else "反对甲并给出新的具体回应。"
        event = {"choices": [{"delta": {"content": output}}], "usage": {"completion_tokens": 12}}
        return httpx.Response(200, content=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")

    def client(**kwargs: Any) -> OpenAIStreamingClient:
        transport = httpx.AsyncClient(transport=httpx.MockTransport(http))
        clients.append(transport)
        return OpenAIStreamingClient(**kwargs, client=transport)

    monkeypatch.setattr(probe, "OpenAIStreamingClient", client)
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    try:
        result = await probe.run_pairs(cases, root)
    finally:
        for item in clients:
            await item.aclose()
    assert maximum == 1 and len(requests) == 24
    assert result["requests_completed"] == 24
    assert result["status"] == ("failed" if fail_one else "passed")
    assert result["production_rows_written"] == 0
    assert not result["semantic_acceptance_automatic"] and result["manual_side_review_required"]
    baseline = [
        row
        for row in result["trials"]
        if row["variant"] == "baseline" and row["status"] == "succeeded"
    ]
    assert all(row["teammate_exact_repeat"] for row in baseline)
    assert all(row["max_tokens"] == 160 for row in result["trials"])
    assert len({row["params_sha256"] for row in result["trials"]}) == 1
    assert all(len(row.get("output_excerpt", "")) <= 180 for row in result["trials"])
    for request in requests:
        assert {key: value for key, value in request.items() if key != "messages"} == {
            key: value for key, value in values["body"].items() if key != "messages"
        }
    for path in root.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert "owned-synthetic-key" not in path.read_text()
        assert '"headers"' not in path.read_text()
    assert all(
        row["output_sha256"] == hashlib.sha256("队友此前完整论点".encode()).hexdigest()
        for row in baseline
    )

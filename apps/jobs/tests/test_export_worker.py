from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from jx_jobs import export_worker


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class _Session:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> _Result:
        query = str(statement)
        self.queries.append((query, parameters))
        for marker, rows in self.rows.items():
            if marker in query:
                return _Result(rows)
        raise AssertionError(f"unexpected export query: {query}")


def _rows(match_id: UUID, request_blob_id: UUID, response_blob_id: UUID) -> dict[str, Any]:
    now = datetime(2026, 8, 24, 4, 0, tzinfo=UTC)
    return {
        "FROM matches": [
            {
                "id": match_id,
                "status": "FINISHED",
                "sequence": 42,
                "context_version": 7,
                "created_at": now,
                "label": "研究比赛",
                "topic_snapshot": {"title": "测试辩题"},
            }
        ],
        "FROM speeches": [
            {
                "id": uuid4(),
                "action_key": "free_debate",
                "speaker_kind": "AGENT",
                "user_id": None,
                "agent_profile_id": uuid4(),
                "side": "AFFIRMATIVE",
                "seat_no": 2,
                "status": "FINALIZED",
                "asr_raw_final_text": None,
                "display_text": "=HYPERLINK(\"https://example.invalid\")",
                "finalized_at": now,
                "audio_duration_ms": 1234,
                "audio_storage_path": None,
            }
        ],
        "FROM match_events": [
            {
                "sequence": 41,
                "event_type": "agent.completed",
                "payload": {"speech_id": "speech-1"},
                "created_at": now,
            }
        ],
        "FROM external_calls": [
            {
                "id": uuid4(),
                "call_kind": "LLM_SPEECH",
                "provider": "openai-compatible",
                "operation": "chat.completions",
                "model": "test-model",
                "voice": None,
                "attempt_no": 1,
                "status": "SUCCEEDED",
                "speech_id": uuid4(),
                "generation_id": uuid4(),
                "decision_round_id": None,
                "context_version": 7,
                "started_at": now,
                "first_result_latency_ms": 200,
                "completed_latency_ms": 800,
                "error_code": None,
                "capture_version": 1,
                "captured_at": now,
                "source_kind": "MATCH",
                "source_resource_id": str(match_id),
                "logical_call_id": uuid4(),
                "provider_request_id": "provider-request-1",
                "request_capture_status": "COMPLETE",
                "response_capture_status": "COMPLETE",
                "request_original_bytes": 100,
                "response_original_bytes": 120,
                "request_stored_bytes": 110,
                "response_stored_bytes": 130,
                "request_sha256": "a" * 64,
                "response_sha256": "b" * 64,
                "capture_error_code": None,
                "request_blob_id": request_blob_id,
                "response_blob_id": response_blob_id,
            }
        ],
    }


@pytest.mark.asyncio
async def test_match_package_contains_provider_input_output_and_frozen_scope(monkeypatch) -> None:
    match_id, request_blob_id, response_blob_id = uuid4(), uuid4(), uuid4()
    session = _Session(_rows(match_id, request_blob_id, response_blob_id))
    request = {"capture_schema_version": 1, "body": {"messages": [{"content": "输入"}]}}
    response = {"capture_schema_version": 1, "body": {"content": "输出"}}

    async def load_blob(_session: Any, blob_id: UUID) -> dict[str, Any]:
        return request if blob_id == request_blob_id else response

    monkeypatch.setattr(export_worker, "load_content_blob", load_blob)
    cutoff = datetime(2026, 8, 24, 5, 0, tzinfo=UTC)
    files = await export_worker._match_package(
        session,  # type: ignore[arg-type]
        match_id=match_id,
        cutoff_sequence=42,
        cutoff_context_version=7,
        cutoff_at=cutoff,
        include_audio=False,
        audio_roots=[],
    )

    manifest = json.loads(files["manifest.json"])
    assert manifest["match_id"] == str(match_id)
    assert manifest["cutoff_sequence"] == 42
    assert manifest["cutoff_context_version"] == 7
    assert manifest["provider_call_capture_schema_version"] == 1

    calls = [json.loads(line) for line in files["agent-calls.jsonl"].splitlines()]
    assert len(calls) == 1
    assert calls[0]["request"] == request
    assert calls[0]["response"] == response
    assert calls[0]["content_errors"] == []
    assert "request_blob_id" not in calls[0]
    assert "response_blob_id" not in calls[0]

    speech_rows = list(csv.DictReader(io.StringIO(files["speeches.csv"].decode("utf-8-sig"))))
    assert speech_rows[0]["display_text"].startswith("'=")

    call_query, call_parameters = next(
        item for item in session.queries if "FROM external_calls" in item[0]
    )
    assert "source_kind='MATCH'" in call_query
    assert call_parameters == {"id": match_id, "cutoff": cutoff}
    event_parameters = next(
        parameters for query, parameters in session.queries if "FROM match_events" in query
    )
    assert event_parameters == {"id": match_id, "sequence": 42}


@pytest.mark.asyncio
async def test_match_package_marks_unreadable_provider_content(monkeypatch) -> None:
    match_id, request_blob_id, response_blob_id = uuid4(), uuid4(), uuid4()
    session = _Session(_rows(match_id, request_blob_id, response_blob_id))

    async def load_blob(_session: Any, blob_id: UUID) -> dict[str, Any]:
        if blob_id == response_blob_id:
            raise ValueError("call_content_blob_checksum_mismatch")
        return {"capture_schema_version": 1, "body": {"messages": []}}

    monkeypatch.setattr(export_worker, "load_content_blob", load_blob)
    files = await export_worker._match_package(
        session,  # type: ignore[arg-type]
        match_id=match_id,
        cutoff_sequence=42,
        cutoff_context_version=7,
        cutoff_at=datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
        include_audio=False,
        audio_roots=[],
    )

    call = json.loads(files["agent-calls.jsonl"].splitlines()[0])
    assert call["request"]["capture_schema_version"] == 1
    assert call["response"] is None
    assert call["content_errors"] == ["response"]
    assert call["response_capture_status"] == "COMPLETE"
    assert call["response_sha256"] == "b" * 64

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
        self.captures: list[Any] = []

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> _Result:
        query = str(statement)
        self.queries.append((query, parameters))
        for marker, rows in self.rows.items():
            if marker in query:
                return _Result(rows)
        raise AssertionError(f"unexpected export query: {query}")

    async def scalars(self, statement: Any) -> list[Any]:
        assert "provider_call_captures" in str(statement)
        return self.captures


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
                "display_text": '=HYPERLINK("https://example.invalid")',
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


def test_bounded_rows_rejects_sentinel_row_before_export_materializes_it() -> None:
    with pytest.raises(export_worker.ExportError, match="export_match_rows_too_large") as raised:
        export_worker._bounded_rows(
            ({"id": 1}, {"id": 2}),
            max_rows=1,
        )
    assert raised.value.code == "export_match_rows_too_large"


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
async def test_match_package_enforces_per_file_audio_budget(monkeypatch, tmp_path) -> None:
    match_id, request_blob_id, response_blob_id = uuid4(), uuid4(), uuid4()
    rows = _rows(match_id, request_blob_id, response_blob_id)
    audio_path = tmp_path / "speech.pcm"
    audio_path.write_bytes(b"12345")
    rows["FROM speeches"][0]["audio_storage_path"] = str(audio_path)
    session = _Session(rows)

    async def load_blob(_session: Any, blob_id: UUID) -> dict[str, Any]:
        return {"capture_schema_version": 1, "body": {"blob_id": str(blob_id)}}

    monkeypatch.setattr(export_worker, "load_content_blob", load_blob)
    monkeypatch.setattr(export_worker, "MATCH_EXPORT_MAX_AUDIO_FILE_BYTES", 4)
    monkeypatch.setattr(export_worker, "MATCH_EXPORT_MAX_AUDIO_BYTES", 16)

    with pytest.raises(export_worker.ExportError, match="export_audio_file_too_large") as raised:
        await export_worker._match_package(
            session,  # type: ignore[arg-type]
            match_id=match_id,
            cutoff_sequence=42,
            cutoff_context_version=7,
            cutoff_at=datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
            include_audio=True,
            audio_roots=[tmp_path],
        )

    assert raised.value.code == "export_audio_file_too_large"


@pytest.mark.asyncio
async def test_match_package_enforces_total_audio_budget(monkeypatch, tmp_path) -> None:
    match_id, request_blob_id, response_blob_id = uuid4(), uuid4(), uuid4()
    rows = _rows(match_id, request_blob_id, response_blob_id)
    first_path = tmp_path / "first.pcm"
    second_path = tmp_path / "second.pcm"
    first_path.write_bytes(b"1234")
    second_path.write_bytes(b"567")
    rows["FROM speeches"][0]["audio_storage_path"] = str(first_path)
    second_speech = rows["FROM speeches"][0].copy()
    second_speech["id"] = uuid4()
    second_speech["audio_storage_path"] = str(second_path)
    rows["FROM speeches"].append(second_speech)
    session = _Session(rows)

    async def load_blob(_session: Any, blob_id: UUID) -> dict[str, Any]:
        return {"capture_schema_version": 1, "body": {"blob_id": str(blob_id)}}

    monkeypatch.setattr(export_worker, "load_content_blob", load_blob)
    monkeypatch.setattr(export_worker, "MATCH_EXPORT_MAX_AUDIO_FILE_BYTES", 4)
    monkeypatch.setattr(export_worker, "MATCH_EXPORT_MAX_AUDIO_BYTES", 6)

    with pytest.raises(export_worker.ExportError, match="export_audio_total_too_large") as raised:
        await export_worker._match_package(
            session,  # type: ignore[arg-type]
            match_id=match_id,
            cutoff_sequence=42,
            cutoff_context_version=7,
            cutoff_at=datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
            include_audio=True,
            audio_roots=[tmp_path],
        )

    assert raised.value.code == "export_audio_total_too_large"


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


@pytest.mark.asyncio
async def test_match_package_rejects_provider_payload_budget_before_loading_blobs(
    monkeypatch,
) -> None:
    match_id, request_blob_id, response_blob_id = uuid4(), uuid4(), uuid4()
    session = _Session(_rows(match_id, request_blob_id, response_blob_id))

    async def load_blob(*_: Any) -> dict[str, Any]:
        raise AssertionError("provider blobs must not be loaded after the budget check")

    monkeypatch.setattr(export_worker, "load_content_blob", load_blob)
    monkeypatch.setattr(export_worker, "MATCH_EXPORT_MAX_PROVIDER_PAYLOAD_BYTES", 219)

    with pytest.raises(export_worker.ExportError, match="export_provider_payload_too_large"):
        await export_worker._match_package(
            session,  # type: ignore[arg-type]
            match_id=match_id,
            cutoff_sequence=42,
            cutoff_context_version=7,
            cutoff_at=datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
            include_audio=False,
            audio_roots=[],
        )


@pytest.mark.asyncio
async def test_frozen_package_uses_snapshot_and_never_reads_mutated_live_call(monkeypatch) -> None:
    match_id, request_id, response_id, item_id = uuid4(), uuid4(), uuid4(), uuid4()
    rows = _rows(match_id, request_id, response_id)
    frozen_call = {**rows.pop("FROM external_calls")[0], "capture_revision": 1}
    rows["FROM export_provider_capture_snapshots"] = [frozen_call]
    session = _Session(rows)

    async def load_blob(_session: Any, blob_id: UUID) -> dict[str, Any]:
        return {"immutable": str(blob_id)}

    monkeypatch.setattr(export_worker, "load_content_blob", load_blob)
    files = await export_worker._match_package(
        session,  # type: ignore[arg-type]
        match_id=match_id,
        cutoff_sequence=42,
        cutoff_context_version=7,
        cutoff_at=datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
        include_audio=False,
        audio_roots=[],
        export_item_id=item_id,
        capture_snapshot_version=1,
    )
    call = json.loads(files["agent-calls.jsonl"].splitlines()[0])
    assert call["request"] == {"immutable": str(request_id)}
    assert call["response"] == {"immutable": str(response_id)}
    assert call["capture_revision"] == 1
    assert not any("FROM external_calls" in query for query, _ in session.queries)
    assert json.loads(files["manifest.json"])["provider_capture_snapshot_version"] == 1


@pytest.mark.asyncio
async def test_explicit_zero_row_snapshot_does_not_fall_back_to_live_calls() -> None:
    match_id = uuid4()
    rows = _rows(match_id, uuid4(), uuid4())
    rows["FROM export_provider_capture_snapshots"] = []
    session = _Session(rows)
    files = await export_worker._match_package(
        session,  # type: ignore[arg-type]
        match_id=match_id,
        cutoff_sequence=42,
        cutoff_context_version=7,
        cutoff_at=datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
        include_audio=False,
        audio_roots=[],
        export_item_id=uuid4(),
        capture_snapshot_version=1,
    )
    assert files["agent-calls.jsonl"].strip() == b""
    assert not any("FROM external_calls" in query for query, _ in session.queries)


@pytest.mark.asyncio
async def test_legacy_export_async_gap_never_loads_authority_business_blobs(monkeypatch) -> None:
    match_id = uuid4()
    rows = _rows(match_id, uuid4(), uuid4())
    row = rows["FROM external_calls"][0]
    row["capture_storage_mode"] = "ASYNC_V1"
    row["capture_version"] = None
    session = _Session(rows)

    async def forbidden_load(*_: Any) -> Any:
        raise AssertionError("authority business blob is not raw provider capture")

    monkeypatch.setattr(export_worker, "load_content_blob", forbidden_load)
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
    assert call["request"] is None and call["response"] is None
    assert call["request_capture_status"] == call["response_capture_status"] == "UNAVAILABLE"
    assert call["capture_error_code"] == "capture_not_persisted"
    assert json.loads(files["manifest.json"])["provider_capture_snapshot_version"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("version,item_id", [(1, None), (2, uuid4())])
async def test_unsupported_or_missing_frozen_input_fails_closed(version, item_id) -> None:
    match_id = uuid4()
    session = _Session(_rows(match_id, uuid4(), uuid4()))
    with pytest.raises(export_worker.ExportError, match="export_capture_snapshot_unsupported"):
        await export_worker._match_package(
            session,  # type: ignore[arg-type]
            match_id=match_id,
            cutoff_sequence=42,
            cutoff_context_version=7,
            cutoff_at=datetime(2026, 8, 24, 5, 0, tzinfo=UTC),
            include_audio=False,
            audio_roots=[],
            export_item_id=item_id,
            capture_snapshot_version=version,
        )
    assert not any("FROM external_calls" in query for query, _ in session.queries)

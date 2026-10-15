from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from jx_jobs import experiment_worker
from jx_jobs.experiment_worker import (
    _EXPORT_COLUMNS,
    ExperimentTaskError,
    _bounded_context_json,
    _check_postmatch_database_budget,
    _check_postmatch_payload_budget,
    _csv_bytes,
    _frozen_history,
)


def _read_csv(content: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))


def test_export_columns_are_stable_for_empty_datasets() -> None:
    for name, columns in _EXPORT_COLUMNS.items():
        assert _read_csv(_csv_bytes([], columns)) == [columns], name
    assert {"source_text", "cedar_id"}.issubset(_EXPORT_COLUMNS["schedule.csv"])


def test_csv_export_neutralizes_spreadsheet_formulas() -> None:
    content = _csv_bytes(
        [
            {
                "a": '=HYPERLINK("https://example.invalid")',
                "b": "+1+1",
                "c": "-2+3",
                "d": "@SUM(1,2)",
                "e": "plain",
            }
        ],
        ["a", "b", "c", "d", "e"],
    )

    assert _read_csv(content)[1] == [
        '\'=HYPERLINK("https://example.invalid")',
        "'+1+1",
        "'-2+3",
        "'@SUM(1,2)",
        "plain",
    ]


def test_frozen_history_stops_at_source_speech_without_future_information() -> None:
    now = datetime.now(UTC)
    speech_ids = [uuid4(), uuid4(), uuid4()]
    speeches = [
        {
            "id": speech_id,
            "speaker_kind": "HUMAN",
            "side": "AFFIRMATIVE" if index % 2 == 0 else "NEGATIVE",
            "seat_no": index + 1,
            "display_text": f"speech-{index}",
            "finalized_at": now + timedelta(seconds=index),
        }
        for index, speech_id in enumerate(speech_ids)
    ]

    history = _frozen_history(
        speeches,
        {"source_speech_id": speech_ids[1], "opened_at": now - timedelta(seconds=5)},
    )

    assert [item["speech_id"] for item in history] == [str(speech_ids[0]), str(speech_ids[1])]
    assert all(
        set(item) == {"speech_id", "speaker_kind", "side", "seat_no", "text"} for item in history
    )


def test_initial_opportunity_history_uses_only_already_finalized_speeches() -> None:
    now = datetime.now(UTC)
    speeches = [
        {
            "id": uuid4(),
            "speaker_kind": "HUMAN",
            "side": "AFFIRMATIVE",
            "seat_no": 1,
            "display_text": str(index),
            "finalized_at": now + timedelta(seconds=index),
        }
        for index in (-1, 1)
    ]

    history = _frozen_history(speeches, {"source_speech_id": None, "opened_at": now})

    assert [item["text"] for item in history] == ["-1"]


class _BatchResult:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self.row = row
        self.closed = False

    def mappings(self) -> _BatchResult:
        return self

    def one_or_none(self) -> dict[str, object] | None:
        return self.row


class _StreamResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.closed = False

    def mappings(self) -> _StreamResult:
        return self

    def __aiter__(self):
        async def rows():
            for row in self.rows:
                yield row

        return rows()

    async def close(self) -> None:
        self.closed = True


class _BatchSession:
    def __init__(self) -> None:
        self.stream_calls: list[str] = []
        self.results: list[_StreamResult] = []

    async def execute(self, statement: object, _parameters: dict[str, object]) -> _BatchResult:
        assert "FROM experiment_batches" in str(statement)
        return _BatchResult(
            {
                "id": uuid4(),
                "code": "B-1",
                "title": "批次",
                "status": "DRAFT",
                "schedule_version": 1,
            }
        )

    async def stream(self, statement: object, _parameters: dict[str, object]) -> _StreamResult:
        query = str(statement)
        self.stream_calls.append(query)
        if "FROM agent_free_debate_decisions" in query:
            rows = [{"status": "SKIP"}]
            result = _StreamResult(rows)
            self.results.append(result)
            return result
        columns_marker = next(
            (
                name
                for name, (_, columns) in experiment_worker._BATCH_EXPORT_QUERIES.items()
                if columns and columns[0] in query
            ),
            None,
        )
        assert columns_marker is not None
        columns = experiment_worker._BATCH_EXPORT_QUERIES[columns_marker][1]
        assert columns is not None
        rows = [{columns[0]: str(uuid4())}]
        result = _StreamResult(rows)
        self.results.append(result)
        return result


@pytest.mark.asyncio
async def test_stream_batch_files_writes_rows_without_materializing_files(tmp_path) -> None:
    session = _BatchSession()
    archive_path = tmp_path / "batch.zip"
    batch_id = uuid4()
    cutoff = datetime.now(UTC)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = await experiment_worker._stream_batch_files(
            session, archive, batch_id=batch_id, cutoff_at=cutoff
        )

    assert len(session.stream_calls) == len(experiment_worker._BATCH_EXPORT_QUERIES)
    assert all(result.closed for result in session.results)
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        assert names == set(experiment_worker._BATCH_EXPORT_QUERIES) | {"manifest.json"}
        schedule = archive.read("schedule.csv")
        assert schedule.startswith(b"\xef\xbb\xbf")
        assert json.loads(archive.read("agent_decisions.jsonl")) == {"status": "SKIP"}
        manifest_file = json.loads(archive.read("manifest.json"))
    assert manifest_file == manifest
    assert manifest["files"]["schedule.csv"]["row_count"] == 1
    assert manifest["files"]["agent_decisions.jsonl"]["row_count"] == 1


@pytest.mark.asyncio
async def test_stream_batch_files_rejects_oversized_entry(monkeypatch, tmp_path) -> None:
    session = _BatchSession()
    monkeypatch.setattr(experiment_worker, "EXPERIMENT_EXPORT_MAX_ROW_BYTES", 16)
    monkeypatch.setattr(experiment_worker, "_csv_row_bytes", lambda *_args: b"x" * 17)
    with zipfile.ZipFile(tmp_path / "too-large.zip", "w") as archive:
        with pytest.raises(experiment_worker.ExperimentTaskError, match="row_too_large"):
            await experiment_worker._stream_batch_files(
                session, archive, batch_id=uuid4(), cutoff_at=datetime.now(UTC)
            )


def test_postmatch_payload_budget_rejects_oversized_rows(monkeypatch) -> None:
    monkeypatch.setattr(experiment_worker, "EXPERIMENT_POSTMATCH_MAX_SPEECH_ROWS", 1)
    speeches = [{"id": uuid4(), "display_text": "a"}, {"id": uuid4(), "display_text": "b"}]
    with pytest.raises(ExperimentTaskError, match="experiment_postmatch_payload_too_large"):
        _check_postmatch_payload_budget(speeches=speeches, opportunities=[])


def test_postmatch_payload_budget_rejects_oversized_context(monkeypatch) -> None:
    monkeypatch.setattr(experiment_worker, "EXPERIMENT_POSTMATCH_MAX_CONTEXT_BYTES", 8)
    with pytest.raises(ExperimentTaskError, match="experiment_postmatch_payload_too_large"):
        _bounded_context_json({"history": ["x" * 32]})


def test_postmatch_payload_budget_rejects_non_object_context() -> None:
    with pytest.raises(ExperimentTaskError, match="experiment_postmatch_payload_too_large"):
        _bounded_context_json(["unexpected-json-array"])


class _BudgetResult:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row

    def mappings(self) -> _BudgetResult:
        return self

    def one(self) -> dict[str, object]:
        return self.row


class _BudgetSession:
    def __init__(self, *rows: dict[str, object]) -> None:
        self.rows = list(rows)
        self.statements: list[str] = []

    async def execute(self, statement: object, _parameters: dict[str, object]) -> _BudgetResult:
        self.statements.append(str(statement))
        return _BudgetResult(self.rows.pop(0))


@pytest.mark.asyncio
async def test_postmatch_database_budget_preflights_logical_payload_bytes() -> None:
    session = _BudgetSession(
        {"row_count": 1, "text_bytes": 16},
        {"row_count": 1, "context_bytes": 32, "max_context_bytes": 32},
    )

    await _check_postmatch_database_budget(session, match_id=uuid4(), attempt_id=uuid4())

    assert "octet_length(frozen_context::text)" in session.statements[1]
    assert "pg_column_size" not in session.statements[1]
    assert "opportunity_id IS NOT NULL" in session.statements[0]


@pytest.mark.asyncio
async def test_postmatch_database_budget_rejects_before_materialization() -> None:
    session = _BudgetSession(
        {"row_count": 4_097, "text_bytes": 0},
        {"row_count": 0, "context_bytes": 0, "max_context_bytes": 0},
    )

    with pytest.raises(ExperimentTaskError, match="experiment_postmatch_payload_too_large"):
        await _check_postmatch_database_budget(session, match_id=uuid4(), attempt_id=uuid4())

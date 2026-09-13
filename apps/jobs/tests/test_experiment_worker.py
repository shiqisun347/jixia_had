from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from jx_jobs.experiment_worker import _EXPORT_COLUMNS, _csv_bytes, _frozen_history


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

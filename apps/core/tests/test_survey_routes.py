# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from jx_core.survey_routes import (
    _decode_postmatch_cursor,
    _encode_postmatch_cursor,
    _personal_response_export_row,
    _stream_personal_admin_payload,
    _stream_survey_export,
)


class _Stream:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows
        self.closed = False

    def __aiter__(self) -> _Stream:
        self._iterator = iter(self._rows)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._iterator)
        except StopIteration as error:
            raise StopAsyncIteration from error

    async def close(self) -> None:
        self.closed = True


class _Session:
    def __init__(self, rows: list[Any]) -> None:
        self.result = _Stream(rows)
        self.statement: Any = None

    async def stream_scalars(self, statement: Any) -> _Stream:
        self.statement = statement
        return self.result


def _async_session(session: _Session) -> AsyncSession:
    return cast(AsyncSession, session)


def _personal_row() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        version_id=uuid4(),
        status="SUBMITTED",
        answers={"q1": 5},
        updated_at=datetime(2026, 10, 13, 12, 0, tzinfo=UTC),
        submitted_at=datetime(2026, 10, 13, 12, 1, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_admin_personal_payload_streams_legacy_json_shape_and_closes_cursor() -> None:
    row = _personal_row()
    session = _Session([row])
    chunks = [
        chunk
        async for chunk in _stream_personal_admin_payload(
            _async_session(session),
            {
                "id": str(uuid4()),
                "version": 1,
                "title": "AI 辩论感受",
                "description": "desc",
                "questions": [],
                "status": "DRAFT",
            },
        )
    ]

    payload = json.loads(b"".join(chunks))
    assert payload["version"]["version"] == 1
    assert payload["responses"][0]["id"] == str(row.id)
    assert payload["responses"][0]["updated_at"] == row.updated_at.isoformat()
    assert session.result.closed is True


@pytest.mark.asyncio
async def test_survey_json_export_streams_rows_without_all_materialization() -> None:
    first = _personal_row()
    second = _personal_row()
    session = _Session([first, second])

    chunks = [
        chunk
        async for chunk in _stream_survey_export(
            _async_session(session),
            statement=object(),
            format="json",
            fields=["response_id", "answers"],
            row_encoder=_personal_response_export_row,
        )
    ]

    rows = json.loads(b"".join(chunks))
    assert [item["response_id"] for item in rows] == [str(first.id), str(second.id)]
    assert session.result.closed is True


@pytest.mark.asyncio
async def test_survey_csv_export_emits_header_and_one_row_at_a_time() -> None:
    row = _personal_row()
    session = _Session([row])
    chunks = [
        chunk
        async for chunk in _stream_survey_export(
            _async_session(session),
            statement=object(),
            format="csv",
            fields=[
                "response_id",
                "user_id",
                "version_id",
                "status",
                "answers",
                "updated_at",
                "submitted_at",
            ],
            row_encoder=_personal_response_export_row,
        )
    ]

    output = b"".join(chunks).decode("utf-8")
    assert output.splitlines()[0] == (
        "response_id,user_id,version_id,status,answers,updated_at,submitted_at"
    )
    assert str(row.id) in output
    assert session.result.closed is True


def test_postmatch_cursor_round_trip_preserves_order_key() -> None:
    timestamp = datetime(2026, 10, 13, 12, 0, 0, 123456, tzinfo=UTC)
    task_id = uuid4()
    encoded = _encode_postmatch_cursor((timestamp, task_id))
    assert _decode_postmatch_cursor(encoded) == (timestamp, task_id)


def test_postmatch_cursor_rejects_malformed_values() -> None:
    from jx_core.auth.errors import APIError

    with pytest.raises(APIError, match="survey_query_invalid"):
        _decode_postmatch_cursor("not-a-cursor")

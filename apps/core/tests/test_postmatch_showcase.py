from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from jx_core.postmatch_showcase_routes import _audience_totals


class _StreamingRows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def __aiter__(self) -> _StreamingRows:
        self._iterator = iter(self._rows)
        return self

    async def __anext__(self) -> tuple[Any, ...]:
        try:
            return next(self._iterator)
        except StopIteration as error:
            raise StopAsyncIteration from error


class _Session:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.stream_called = False

    async def stream(self, statement: object) -> _StreamingRows:
        self.stream_called = True
        return _StreamingRows(self.rows)


@pytest.mark.asyncio
async def test_audience_totals_streams_rows_without_loading_vote_list() -> None:
    first, second = uuid4(), uuid4()
    voter = uuid4()
    rows = [(voter, "AFFIRMATIVE", [str(first), str(second)]) for _ in range(2_000)]
    session = _Session(rows)
    participants = [
        SimpleNamespace(id=first),
        SimpleNamespace(id=second),
    ]

    total, has_voted, winners, points, ranking_votes = await _audience_totals(
        cast(Any, session), voter, voter, cast(Any, participants)
    )

    assert session.stream_called is True
    assert total == 2_000
    assert has_voted is True
    assert winners["AFFIRMATIVE"] == 2_000
    assert points[first] == 4_000
    assert points[second] == 2_000
    assert ranking_votes[first] == ranking_votes[second] == 2_000

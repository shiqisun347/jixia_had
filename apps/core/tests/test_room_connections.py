from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect

from jx_core.room_connections import RoomConnectionService, reconcile_terminal_room_end_times


@pytest.mark.asyncio
async def test_reconcile_terminal_room_end_times_uses_set_based_update_count() -> None:
    class Transaction:
        async def __aenter__(self) -> Transaction:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Result:
        rowcount = 2

    statements: list[object] = []

    class Session:
        def begin(self) -> Transaction:
            return Transaction()

        async def execute(self, statement: object) -> Result:
            statements.append(statement)
            return Result()

    repaired = await reconcile_terminal_room_end_times(cast(Any, Session()))

    assert repaired == 2
    assert len(statements) == 1
    sql = str(statements[0].compile(dialect=postgresql_dialect()))
    assert sql.startswith("UPDATE rooms SET updated_at=now(), ended_at=coalesce")
    assert "FROM matches" in sql
    assert "rooms.status IN" in sql


@pytest.mark.asyncio
async def test_clear_active_leases_returns_one_bounded_batch() -> None:
    rows = [(uuid4(), uuid4(), uuid4()), (uuid4(), uuid4(), uuid4())]

    class Transaction:
        async def __aenter__(self) -> Transaction:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    class SelectResult:
        def all(self) -> list[tuple[object, object, object]]:
            return rows

    class DeleteResult:
        rowcount = len(rows)

    statements: list[object] = []

    class Session:
        def in_transaction(self) -> bool:
            return False

        def begin(self) -> Transaction:
            return Transaction()

        async def execute(self, statement: object) -> SelectResult | DeleteResult:
            statements.append(statement)
            return SelectResult() if len(statements) == 1 else DeleteResult()

    result = await RoomConnectionService().clear_active_leases(
        cast(Any, Session()), batch_size=2
    )

    assert result == [(row[1], row[2]) for row in rows]
    assert len(statements) == 2
    select_sql = str(statements[0].compile(dialect=postgresql_dialect()))
    assert "LIMIT %(param_1)s" in select_sql

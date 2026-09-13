from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jx_core.admin_diagnostic_routes import (
    _parse_runtime_log_cursor,
    _runtime_log_cursor,
)
from jx_core.auth.errors import APIError


def test_runtime_log_cursor_round_trips_stable_order_keys() -> None:
    happened_at = datetime.now(UTC)
    row_id = uuid4()
    cursor = _runtime_log_cursor(SimpleNamespace(happened_at=happened_at, id=row_id))

    assert _parse_runtime_log_cursor(cursor) == (happened_at, row_id)


@pytest.mark.parametrize("cursor", ["not-base64!", "bm8tc2VwYXJhdG9y", "MjAyNi0wMS0wMXxiYWQ="])
def test_runtime_log_cursor_rejects_malformed_values(cursor: str) -> None:
    with pytest.raises(APIError):
        _parse_runtime_log_cursor(cursor)

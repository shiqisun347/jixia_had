"""Owned QA fixture teardown must run despite an independent close exception."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import test_agent_runtime_fault_integration as fixture_module
from test_agent_runtime_fault_integration import AgentFaultProbe, finish_owned_fault_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failing_close", ["manager", "seed-runtime", "host-runtime", "http", "writer"]
)
async def test_owned_terminalization_and_dispose_survive_any_resource_close_error(
    monkeypatch: pytest.MonkeyPatch, failing_close: str
) -> None:
    calls: list[str] = []

    def close(name: str) -> AsyncMock:
        async def action() -> None:
            calls.append(name)
            if name == failing_close:
                raise RuntimeError(f"synthetic_{name}_close_failed")

        return AsyncMock(side_effect=action)

    seed_runtime = SimpleNamespace(close=close("seed-runtime"))
    probe = cast(
        AgentFaultProbe,
        SimpleNamespace(
            manager=SimpleNamespace(close=close("manager")),
            runtime=SimpleNamespace(close=close("host-runtime")),
            http_clients=[SimpleNamespace(aclose=close("http"))],
            writer=SimpleNamespace(close=close("writer")),
            engine=SimpleNamespace(dispose=close("dispose")),
            run=None,
            factory=object(),
        ),
    )
    match_id, room_id = uuid4(), uuid4()

    async def terminalize(*_: Any, **__: Any) -> None:
        calls.append("terminalize")

    committed = AsyncMock(side_effect=terminalize)
    monkeypatch.setattr(fixture_module, "terminalize_owned_fault_fixture", committed)
    with pytest.raises(ExceptionGroup) as raised:
        await finish_owned_fault_fixture(
            probe, seed_runtime=cast(Any, seed_runtime), match_id=match_id, room_id=room_id
        )
    assert len(raised.value.exceptions) == 1
    assert str(raised.value.exceptions[0]) == f"synthetic_{failing_close}_close_failed"
    committed.assert_awaited_once_with(probe.factory, match_id=match_id, room_id=room_id)
    assert calls == [
        "manager",
        "seed-runtime",
        "host-runtime",
        "http",
        "writer",
        "terminalize",
        "dispose",
    ]

from __future__ import annotations

from uuid import uuid4

from jx_core.matches.domain import MatchRuntimeState
from jx_core.matches.service import _state_from_snapshot, state_snapshot


def test_runtime_snapshot_round_trips_independent_offline_markers() -> None:
    first = uuid4()
    second = uuid4()
    state = MatchRuntimeState(
        match_id=uuid4(),
        status="RUNNING",
        action_state="FREE_SELECTING",
        actions=(),
        offline_user_id=second,
        offline_since_ms=((first, 1000), (second, 2000)),
        connection_epochs=((first, 3), (second, 4)),
    )

    restored = _state_from_snapshot(state.match_id, state_snapshot(state))

    assert dict(restored.offline_since_ms) == {first: 1000, second: 2000}
    assert dict(restored.connection_epochs) == {first: 3, second: 4}
    assert restored.offline_user_id == second

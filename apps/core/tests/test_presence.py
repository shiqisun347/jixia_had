from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from jx_core.presence import MatchPresenceCoordinator

# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false


def test_presence_aggregates_complete_epochs_and_ignores_duplicate_media_events() -> None:
    coordinator = MatchPresenceCoordinator(cast(Any, None), cast(Any, None))
    match_id = uuid4()
    user_id = uuid4()
    first_connection = uuid4()
    second_connection = uuid4()

    coordinator._websocket[(match_id, user_id, 1)].add(first_connection)
    assert coordinator._apply_media_event(
        match_id=match_id,
        user_id=user_id,
        epoch=1,
        sid="first-sid",
        event_id="first-joined",
        joined=True,
    )
    assert coordinator._complete_epochs(match_id, user_id, {1}) == {1}

    coordinator._websocket[(match_id, user_id, 2)].add(second_connection)
    assert coordinator._complete_epochs(match_id, user_id, {1, 2}) == {1}
    assert coordinator._apply_media_event(
        match_id=match_id,
        user_id=user_id,
        epoch=2,
        sid="second-sid",
        event_id="second-joined",
        joined=True,
    )
    assert not coordinator._apply_media_event(
        match_id=match_id,
        user_id=user_id,
        epoch=2,
        sid="second-sid",
        event_id="second-joined",
        joined=True,
    )
    assert coordinator._complete_epochs(match_id, user_id, {1, 2}) == {1, 2}

    assert coordinator._apply_media_event(
        match_id=match_id,
        user_id=user_id,
        epoch=1,
        sid="first-sid",
        event_id="first-left-late",
        joined=False,
    )
    assert coordinator._complete_epochs(match_id, user_id, {1, 2}) == {2}


def test_presence_uses_injected_wall_clock_for_offline_timestamp() -> None:
    coordinator = MatchPresenceCoordinator(
        cast(Any, None), cast(Any, None), wall_clock=lambda: 1234.567
    )

    assert coordinator._offline_timestamp_ms() == 1_234_567


@pytest.mark.asyncio
async def test_revoke_user_removes_all_socket_and_media_epochs() -> None:
    coordinator = MatchPresenceCoordinator(cast(Any, None), cast(Any, None))
    coordinator._transition = AsyncMock()  # type: ignore[method-assign]
    match_id = uuid4()
    user_id = uuid4()
    other_user = uuid4()
    connection_id = uuid4()
    coordinator._websocket[(match_id, user_id, 3)].add(connection_id)
    coordinator._media[(match_id, user_id, 3)].add("sid")
    coordinator._websocket[(match_id, other_user, 1)].add(uuid4())

    await coordinator.revoke_user(user_id)

    assert not any(key[1] == user_id for key in coordinator._websocket)
    assert not any(key[1] == user_id for key in coordinator._media)
    coordinator._transition.assert_awaited_once_with(match_id, user_id, 0)


@pytest.mark.asyncio
async def test_presence_rolls_back_socket_join_when_transition_fails() -> None:
    coordinator = MatchPresenceCoordinator(cast(Any, None), cast(Any, None))
    coordinator._transition = AsyncMock(side_effect=RuntimeError("database unavailable"))  # type: ignore[method-assign]
    match_id = uuid4()
    user_id = uuid4()
    connection_id = uuid4()

    with pytest.raises(RuntimeError, match="database unavailable"):
        await coordinator.websocket_joined(match_id, user_id, 4, connection_id)

    assert (match_id, user_id, 4) not in coordinator._websocket


@pytest.mark.asyncio
async def test_presence_rolls_back_media_event_and_allows_retry() -> None:
    coordinator = MatchPresenceCoordinator(cast(Any, None), cast(Any, None))
    transition = AsyncMock(side_effect=[RuntimeError("actor unavailable"), None])
    coordinator._transition = transition  # type: ignore[method-assign]
    match_id = uuid4()
    user_id = uuid4()

    with pytest.raises(RuntimeError, match="actor unavailable"):
        await coordinator.media_event(
            match_id=match_id,
            user_id=user_id,
            epoch=2,
            sid="media-1",
            event_id="event-1",
            joined=True,
        )

    assert (match_id, user_id, 2) not in coordinator._media
    assert "event-1" not in coordinator._seen_events[match_id]

    await coordinator.media_event(
        match_id=match_id,
        user_id=user_id,
        epoch=2,
        sid="media-1",
        event_id="event-1",
        joined=True,
    )

    assert coordinator._media[(match_id, user_id, 2)] == {"media-1"}
    assert transition.await_count == 2

"""Short business-link disruption must not retire an active audio owner."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
from typing import Any, cast
from uuid import uuid4

import pytest

from jx_core.matches.domain import MatchActor, MatchCommand
from jx_core.presence import MatchPresenceCoordinator
from test_match_asr_recovery307 import active_state


def test_known_audio_continues_while_business_websocket_reconnects() -> None:
    coordinator = MatchPresenceCoordinator(cast(Any, None), cast(Any, None))
    match, user = uuid4(), uuid4()
    coordinator._media[(match, user, 5)].add("exact-old-live-media")
    coordinator._online_state[(match, user)] = True
    coordinator._announced_epoch[(match, user)] = 5
    assert coordinator._complete_epochs(match, user, set()) == {5}
    coordinator._media.clear()
    assert coordinator._complete_epochs(match, user, set()) == set()


@pytest.mark.asyncio
async def test_online_does_not_resume_clock_until_exact_media_ready() -> None:
    state = active_state()
    actor = MatchActor(state, clock=lambda: 120, sleep=lambda _: asyncio.sleep(60))
    await actor.start()
    try:
        user = state.current_speaker_user_id
        await actor.submit(
            MatchCommand(
                "member.offline", "offline", actor_user_id=user, payload={"connection_epoch": 5}
            )
        )
        frozen = actor.state.speech_remaining_ms
        await actor.submit(
            MatchCommand(
                "member.online", "online", actor_user_id=user, payload={"connection_epoch": 6}
            )
        )
        assert actor.state.speech_deadline_mono is None
        await actor.submit(
            MatchCommand(
                "speech.media_ready",
                "actual-ready",
                actor_user_id=user,
                payload={"speech_id": str(state.current_speech_id), "connection_epoch": 6},
            )
        )
        assert actor.state.speech_remaining_ms == frozen
        assert actor.state.speech_deadline_mono == 120 + (frozen or 0) / 1000
    finally:
        await actor.close()

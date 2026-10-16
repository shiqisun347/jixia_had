"""Resume accepts only a current required speaker's unexpired device check."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from jx_core.matches.resume import required_resume_reasons
from test_match_asr_recovery307 import active_state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expires_in", "blocked"),
    [
        ("PASS", 60, False),
        ("WARN", 60, False),
        ("PASS", -60, True),
        ("WARN", -60, True),
        ("FAIL", 60, True),
    ],
)
async def test_required_speaker_device_expiration(
    status: str, expires_in: int, blocked: bool
) -> None:
    state = replace(active_state(), status="PAUSED", paused_from_action_state="HUMAN_SPEAKING")
    member = SimpleNamespace(user_id=state.current_speaker_user_id, online=True)

    class Session:
        async def execute(self, _query: Any) -> Any:
            return SimpleNamespace(all=lambda: [(member, "测试辩手")])

        async def scalar(self, query: Any) -> Any:
            if "device_checks" in str(query):
                return SimpleNamespace(
                    status=status,
                    valid_until=datetime.now(UTC) + timedelta(seconds=expires_in),
                )
            return SimpleNamespace()

    reasons = await required_resume_reasons(cast(Any, Session()), uuid4(), state)
    assert bool(reasons) is blocked
    if blocked:
        assert reasons == ["辩手 测试辩手 的麦克风或扬声器检测已失效"]

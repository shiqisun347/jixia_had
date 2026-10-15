from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from jx_core.auth.errors import AuthError
from jx_core.models import ModelProfile
from jx_core.rules import service as catalog
from jx_core.rules.host_voice import is_host_reserved_voice
from jx_core.rules.schemas import VoiceProfileCreate


@pytest.mark.parametrize("voice", ["longanwen_v3.1", "longyixuanjun_v3.1"])
def test_canonical_and_legacy_alias_reserve_same_host_sound(voice: str) -> None:
    assert is_host_reserved_voice(voice)
    assert not is_host_reserved_voice("yuxiaoyun_v3.1")


@pytest.mark.asyncio
async def test_an_existing_agent_alias_cannot_be_used_for_new_agent() -> None:
    class Session:
        async def get(self, model: Any, identity: Any) -> Any:
            if model is ModelProfile:
                return SimpleNamespace(status="ENABLED")
            return SimpleNamespace(
                kind="AGENT", status="ENABLED", provider_voice="longyixuanjun_v3.1"
            )

    with pytest.raises(AuthError, match="host_voice_reserved"):
        await catalog.CatalogService()._validate_agent_dependencies(
            cast(Any, Session()), model_profile_id=uuid4(), voice_profile_id=uuid4()
        )


@pytest.mark.asyncio
async def test_creating_reserved_host_as_debater_fails_before_writes(monkeypatch: Any) -> None:
    async def lock(_: Any) -> None:
        return

    monkeypatch.setattr(catalog, "lock_agent_pool", lock)

    class Session:
        @asynccontextmanager
        async def begin(self) -> Any:
            yield

        def add(self, _: Any) -> None:
            pytest.fail("a host sound must never be inserted into the Agent pool")

    payload = VoiceProfileCreate(
        name="新别名", kind="AGENT", provider_voice="longanwen_v3.1", avatar_key="agent-01"
    )
    with pytest.raises(AuthError, match="host_voice_reserved"):
        await catalog.CatalogService().create_voice(cast(Any, Session()), payload=payload)

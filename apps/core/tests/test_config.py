from __future__ import annotations

import pytest
from pydantic import ValidationError

from jx_core.config import CORE_INSTANCE_LOCK_KEY, Settings


def test_core_instance_lock_key_is_fixed_and_not_environment_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INSTANCE_LOCK_ID", "bypass-attempt")
    settings = Settings(database_url="postgresql+psycopg://jx:secret@127.0.0.1:5432/jx_debate")

    assert -(2**63) <= CORE_INSTANCE_LOCK_KEY < 2**63
    assert not hasattr(settings, "instance_lock_id")
    assert "jx:secret@" not in repr(settings)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://jx:secret@127.0.0.1:5432/jx_debate",
        "sqlite+aiosqlite:///jx.db",
        "postgresql+psycopg://jx:secret@127.0.0.1:5432",
        " postgresql+psycopg://jx:secret@127.0.0.1:5432/jx_debate",
    ],
)
def test_settings_reject_database_url_without_exact_async_driver(
    database_url: str,
) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=database_url)

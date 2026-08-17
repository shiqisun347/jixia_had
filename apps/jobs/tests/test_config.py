from __future__ import annotations

import pytest
from pydantic import ValidationError

from jx_jobs.config import Settings


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://jx:secret@127.0.0.1:5432/jx_debate",
        "sqlite+aiosqlite:///jx.db",
        "postgresql+psycopg://jx:secret@127.0.0.1:5432",
    ],
)
def test_settings_require_exact_async_psycopg_url(database_url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=database_url)


def test_settings_keep_database_url_secret() -> None:
    settings = Settings(database_url="postgresql+psycopg://jx:secret@127.0.0.1:5432/jx_debate")

    assert "secret" not in repr(settings)

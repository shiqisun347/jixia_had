from __future__ import annotations

import pytest

from jx_jobs.cli import main
from jx_jobs.config import Settings


class FakeDatabase:
    def __init__(self, _: str) -> None:
        self.disposed = False

    async def ping(self) -> bool:
        return True

    async def dispose(self) -> None:
        self.disposed = True


def test_once_cli_exits_zero_after_configuration_check() -> None:
    settings = Settings(database_url="postgresql+psycopg://jx:secret@127.0.0.1:5432/jx_debate")

    with pytest.raises(SystemExit) as result:
        main(
            ["--once"],
            settings_factory=lambda: settings,
            database_factory=FakeDatabase,
        )

    assert result.value.code == 0


def test_once_cli_accepts_pnpm_argument_separator() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://jx:secret@127.0.0.1:5432/jx_debate"
    )

    with pytest.raises(SystemExit) as result:
        main(
            ["--", "--once"],
            settings_factory=lambda: settings,
            database_factory=FakeDatabase,
        )

    assert result.value.code == 0


def test_database_construction_failure_is_normalized_and_redacted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(database_url="postgresql+psycopg://jx:secret@127.0.0.1:5432/jx_debate")

    def fail(database_url: str) -> FakeDatabase:
        raise RuntimeError(f"cannot construct {database_url} password=hunter2")

    with pytest.raises(SystemExit) as result:
        main(
            ["--once"],
            settings_factory=lambda: settings,
            database_factory=fail,
        )

    output = capsys.readouterr().out
    assert result.value.code == 1
    assert '"error_code":"database_unavailable"' in output
    assert "secret" not in output
    assert "hunter2" not in output
    assert "postgresql" not in output
    assert "Traceback" not in output

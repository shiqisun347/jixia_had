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
    settings = Settings(database_url="postgresql+psycopg://jx:secret@127.0.0.1:5432/jx_debate")

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


@pytest.mark.parametrize("fault", ["heartbeat", "tts_close"])
def test_process_capture_writer_is_closed_after_startup_or_provider_cleanup_error(
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    import jx_jobs.cli as cli

    class SessionContext:
        async def __aenter__(self):
            if fault == "heartbeat":
                raise RuntimeError("synthetic_heartbeat_failed")
            return self

        async def __aexit__(self, *_args):
            return None

        def begin(self):
            return self

        async def get(self, *_args, **_kwargs):
            return None

        def add(self, _row):
            pass

    class ConcreteDatabase(FakeDatabase):
        def session_factory(self):
            return SessionContext()

    database = ConcreteDatabase("synthetic")
    writer = SimpleNamespace(start=AsyncMock(), close=AsyncMock(), new_capture=Mock())
    logs = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    client = SimpleNamespace(
        close=AsyncMock(side_effect=RuntimeError("synthetic_tts_close_failed"))
    )
    monkeypatch.setattr(cli, "Database", ConcreteDatabase)
    monkeypatch.setattr(cli, "RuntimeLogWriter", Mock(return_value=logs))
    monkeypatch.setattr(cli, "ProviderCaptureWriter", Mock(return_value=writer))
    monkeypatch.setattr(cli, "DashScopeTTSClient", Mock(return_value=client))
    monkeypatch.setattr(cli, "run_jobs", AsyncMock(return_value=0))
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://jx:synthetic@localhost/jx_debate_qa_agent_faults",
        TTS_API_KEY="synthetic",
        tts_ws_url="wss://synthetic.invalid/tts",
    )
    with pytest.raises(SystemExit) as result:
        cli.main(["--once"], settings_factory=lambda: settings, database_factory=lambda _: database)
    assert result.value.code == 1
    writer.start.assert_awaited_once()
    writer.close.assert_awaited_once()
    logs.stop.assert_awaited_once()
    if fault == "heartbeat":
        assert database.disposed


@pytest.mark.parametrize("enabled", [True, False])
def test_cli_injects_one_process_writer_or_disabled_capture_without_double_database_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    import jx_jobs.cli as cli
    from jx_core.data_capture.provider import ProviderCallCapture

    class SessionContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def begin(self):
            return self

        async def get(self, *_args, **_kwargs):
            return None

        def add(self, _row):
            pass

    class ConcreteDatabase(FakeDatabase):
        dispose_count = 0

        def session_factory(self):
            return SessionContext()

        async def dispose(self) -> None:
            self.dispose_count += 1
            await super().dispose()

    database = ConcreteDatabase("synthetic")
    capture = ProviderCallCapture()
    writer = SimpleNamespace(
        start=AsyncMock(), close=AsyncMock(), new_capture=Mock(return_value=capture)
    )
    writer_factory = Mock(return_value=writer)
    logs = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    client = SimpleNamespace(close=AsyncMock())
    client_factory = Mock(return_value=client)

    async def owned_run_jobs(**kwargs):
        await kwargs["database"].dispose()
        return 0

    monkeypatch.setattr(cli, "Database", ConcreteDatabase)
    monkeypatch.setattr(cli, "RuntimeLogWriter", Mock(return_value=logs))
    monkeypatch.setattr(cli, "ProviderCaptureWriter", writer_factory)
    monkeypatch.setattr(cli, "DashScopeTTSClient", client_factory)
    monkeypatch.setattr(cli, "run_jobs", owned_run_jobs)
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://jx:synthetic@localhost/jx_debate_qa_agent_faults",
        TTS_API_KEY="synthetic",
        tts_ws_url="wss://synthetic.invalid/tts",
        provider_capture_enabled=enabled,
    )
    with pytest.raises(SystemExit) as result:
        cli.main(["--once"], settings_factory=lambda: settings, database_factory=lambda _: database)
    assert result.value.code == 0 and database.dispose_count == 1
    factory = client_factory.call_args.kwargs["capture_factory"]
    captured = factory()
    if enabled:
        writer_factory.assert_called_once()
        writer.start.assert_awaited_once()
        writer.close.assert_awaited_once()
        assert captured is capture
    else:
        writer_factory.assert_not_called()
        assert captured.can_capture is False
        captured.capture_request(transport="websocket", body={"synthetic": True})
        assert captured.request.status == "UNAVAILABLE"
    captured.close()
    client.close.assert_awaited_once()
    logs.stop.assert_awaited_once()

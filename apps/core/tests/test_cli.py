from __future__ import annotations

from jx_core import cli
from jx_core.config import Settings


def test_cli_uses_bounded_graceful_shutdown(monkeypatch) -> None:
    settings = Settings(
        database_url="postgresql+psycopg://jx:secret@127.0.0.1:5432/jx_debate",
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_app", lambda resolved: object())

    def fake_run(app, **kwargs):
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main()

    assert captured["host"] == settings.core_host
    assert captured["port"] == settings.core_port
    assert captured["workers"] == 1
    assert captured["timeout_graceful_shutdown"] == 2
    assert captured["log_config"] is None

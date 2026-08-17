"""Validated configuration for the no-op ``jx-jobs`` process."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, cast
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
AppEnvironment = Literal["development", "test", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnvironment = "development"
    log_level: LogLevel = "INFO"
    database_url: SecretStr
    tts_ws_url: str = ""
    dashscope_api_key: SecretStr | None = None
    dashscope_workspace: str = ""
    host_audio_storage_dir: str = "./data/host-audio"
    match_audio_storage_dir: str = "./data/match-audio"
    human_audio_storage_dir: str = "./data/agent-audio"
    export_storage_dir: str = "./data/exports"

    @field_validator("database_url", mode="before")
    @classmethod
    def validate_database_url(cls, value: object) -> SecretStr:
        if isinstance(value, SecretStr):
            raw = value.get_secret_value()
        elif isinstance(value, str):
            raw = value
        else:
            raise ValueError("DATABASE_URL must be a PostgreSQL URL")
        if raw.strip() != raw or any(character.isspace() for character in raw):
            raise ValueError("DATABASE_URL must not contain whitespace")
        parsed = urlsplit(raw)
        if parsed.scheme != "postgresql+psycopg":
            raise ValueError("DATABASE_URL must use postgresql+psycopg")
        if not parsed.hostname or not parsed.path or parsed.path == "/":
            raise ValueError("DATABASE_URL must include a database name")
        return SecretStr(raw)

    @property
    def database_url_value(self) -> str:
        return self.database_url.get_secret_value()


def load_settings() -> Settings:
    """Load settings from the configured environment and dotenv file."""

    settings_factory = cast(Callable[[], Settings], Settings)
    return settings_factory()

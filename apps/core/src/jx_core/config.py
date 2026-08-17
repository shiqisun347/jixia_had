"""Validated process configuration for :mod:`jx_core`.

The foundation slice deliberately keeps the configuration surface small.  A
later slice may add business settings, but it must not bypass this validation
boundary or expose secrets in logs and health responses.
"""

from __future__ import annotations

from collections.abc import Callable
from hashlib import blake2b
from typing import Final, Literal, cast
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
AppEnvironment = Literal["development", "test", "production"]

_LOCK_NAMESPACE: Final[bytes] = b"jx-core-lock-v1"
_LOCK_IDENTITY: Final[bytes] = b"jx-core-singleton"


def _stable_signed_bigint(value: bytes) -> int:
    digest = blake2b(value, digest_size=8, key=_LOCK_NAMESPACE).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


# The core process must have exactly one owner per database.  This key is part
# of the service identity, not deployment configuration: exposing it through
# the environment would let a second process bypass the singleton guarantee.
CORE_INSTANCE_LOCK_KEY: Final[int] = _stable_signed_bigint(_LOCK_IDENTITY)


class Settings(BaseSettings):
    """Settings loaded once at the process boundary.

    ``DATABASE_URL`` is intentionally a ``SecretStr``.  SQLAlchemy receives
    the unwrapped value only inside the database adapter; representations and
    validation errors therefore cannot accidentally print credentials.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnvironment = "development"
    log_level: LogLevel = "INFO"
    database_url: SecretStr
    core_host: str = "127.0.0.1"
    core_port: int = 8000
    cors_origins: str = "http://localhost:3000"
    avatar_storage_dir: str = "./data/avatars"
    session_ttl_seconds: int = 7 * 24 * 60 * 60
    session_rolling_refresh_seconds: int = 15 * 60
    livekit_url: str | None = None
    livekit_api_key: SecretStr | None = None
    livekit_api_secret: SecretStr | None = None
    asr_ws_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    asr_api_key: SecretStr | None = None
    asr_model: str = "fun-asr-realtime"
    asr_workspace_id: str | None = None
    tts_ws_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    tts_api_key: SecretStr | None = None
    tts_model: str = "qwen-audio-3.0-tts-flash"
    tts_workspace_id: str | None = None
    host_audio_storage_dir: str = "./data/host-audio"
    agent_audio_storage_dir: str = "./data/agent-audio"
    match_audio_storage_dir: str = "./data/match-audio"
    export_storage_dir: str = "./data/exports"
    llm_key_encryption_key: SecretStr | None = None
    llm_global_concurrency: int = 50
    diagnostic_queue_size: int = 1024
    diagnostic_batch_size: int = 50
    diagnostic_flush_interval_ms: int = 250

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

    @field_validator("core_host")
    @classmethod
    def validate_core_host(cls, value: str) -> str:
        value = value.strip()
        if not value or any(char.isspace() for char in value):
            raise ValueError("CORE_HOST must be a non-empty host")
        return value

    @field_validator("core_port")
    @classmethod
    def validate_core_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("CORE_PORT must be between 1 and 65535")
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: str) -> str:
        origins = [item.strip() for item in value.split(",") if item.strip()]
        if not origins:
            raise ValueError("CORS_ORIGINS must contain at least one origin")
        for origin in origins:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("CORS_ORIGINS must contain HTTP(S) origins")
            if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError("CORS_ORIGINS must contain origins, not paths")
        return ",".join(origins)

    @field_validator("session_ttl_seconds", "session_rolling_refresh_seconds")
    @classmethod
    def validate_session_durations(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("session durations must be positive")
        return value

    @field_validator(
        "avatar_storage_dir",
        "host_audio_storage_dir",
        "agent_audio_storage_dir",
        "match_audio_storage_dir",
        "export_storage_dir",
    )
    @classmethod
    def validate_avatar_storage_dir(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("storage directory must be non-empty")
        return value

    @field_validator("livekit_url")
    @classmethod
    def validate_livekit_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        parsed = urlsplit(value)
        if parsed.scheme not in {"ws", "wss"} or not parsed.netloc or parsed.path not in {"", "/"}:
            raise ValueError("LIVEKIT_URL must be a ws:// or wss:// origin")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_livekit_credentials(self) -> Settings:
        values = (self.livekit_url, self.livekit_api_key, self.livekit_api_secret)
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            raise ValueError(
                "LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be configured together"
            )
        return self

    @model_validator(mode="after")
    def validate_asr_credentials(self) -> Settings:
        if not self.asr_ws_url.startswith("wss://"):
            raise ValueError("ASR_WS_URL must use wss://")
        if not self.tts_ws_url.startswith("wss://"):
            raise ValueError("TTS_WS_URL must use wss://")
        return self

    @field_validator("llm_global_concurrency")
    @classmethod
    def validate_llm_global_concurrency(cls, value: int) -> int:
        if not 1 <= value <= 50:
            raise ValueError("LLM_GLOBAL_CONCURRENCY must be between 1 and 50")
        return value

    @field_validator("diagnostic_queue_size")
    @classmethod
    def validate_diagnostic_queue_size(cls, value: int) -> int:
        if not 64 <= value <= 100_000:
            raise ValueError("DIAGNOSTIC_QUEUE_SIZE must be between 64 and 100000")
        return value

    @field_validator("diagnostic_batch_size")
    @classmethod
    def validate_diagnostic_batch_size(cls, value: int) -> int:
        if not 1 <= value <= 1000:
            raise ValueError("DIAGNOSTIC_BATCH_SIZE must be between 1 and 1000")
        return value

    @field_validator("diagnostic_flush_interval_ms")
    @classmethod
    def validate_diagnostic_flush_interval_ms(cls, value: int) -> int:
        if not 10 <= value <= 5000:
            raise ValueError("DIAGNOSTIC_FLUSH_INTERVAL_MS must be between 10 and 5000")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        """Return origins in the shape expected by Starlette middleware."""

        return self.cors_origins.split(",")

    @property
    def database_url_value(self) -> str:
        """Return the database URL only to the internal DB adapter."""

        return self.database_url.get_secret_value()


def load_settings() -> Settings:
    """Load settings from the configured environment and dotenv file."""

    settings_factory = cast(Callable[[], Settings], Settings)
    return settings_factory()

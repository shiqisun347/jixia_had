from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from jx_core.admin_routes import SystemSettingsPatch


def test_system_settings_use_a_fixed_whitelist() -> None:
    payload = SystemSettingsPatch.model_validate(
        {
            "log_retention_days": 45,
            "debug_enabled": True,
            "debug_expires_at": datetime.now(UTC) + timedelta(hours=1),
            "max_upload_bytes": 4 * 1024 * 1024,
        }
    )
    assert set(payload.model_dump()) == {
        "log_retention_days",
        "debug_enabled",
        "debug_expires_at",
        "max_upload_bytes",
    }
    with pytest.raises(ValidationError):
        SystemSettingsPatch.model_validate({"unknown_setting": True})


@pytest.mark.parametrize(
    "expires_at",
    [None, datetime.now(UTC) - timedelta(minutes=1), datetime.now(UTC) + timedelta(hours=25)],
)
def test_temporary_debug_requires_a_short_future_window(expires_at: datetime | None) -> None:
    with pytest.raises(ValidationError):
        SystemSettingsPatch(debug_enabled=True, debug_expires_at=expires_at)


def test_expired_persisted_debug_is_read_as_disabled() -> None:
    settings = SystemSettingsPatch.from_rows(
        [
            SimpleNamespace(key="debug_enabled", value={"value": True}),
            SimpleNamespace(
                key="debug_expires_at",
                value={"value": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()},
            ),
        ]
    )
    assert settings.debug_enabled is False
    assert settings.debug_expires_at is None

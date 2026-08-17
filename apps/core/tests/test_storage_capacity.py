from types import SimpleNamespace

import pytest

from jx_core.auth.errors import AuthError
from jx_core.rooms.service import ensure_storage_capacity


def test_storage_capacity_allows_below_ninety_percent(tmp_path) -> None:
    ensure_storage_capacity(
        str(tmp_path),
        disk_usage=lambda _: SimpleNamespace(total=1000, used=899),
    )


def test_storage_capacity_blocks_at_ninety_percent(tmp_path) -> None:
    with pytest.raises(AuthError, match="disk_capacity_full"):
        ensure_storage_capacity(
            str(tmp_path),
            disk_usage=lambda _: SimpleNamespace(total=1000, used=900),
        )


def test_storage_capacity_normalizes_filesystem_failure(tmp_path) -> None:
    def unavailable(_):
        raise OSError("probe failed")

    with pytest.raises(AuthError, match="storage_unavailable"):
        ensure_storage_capacity(str(tmp_path), disk_usage=unavailable)

from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path

import pytest


def _load_ops_module():
    path = Path(__file__).parents[3] / "scripts/ops/prepare_experiment_simulation.py"
    spec = importlib.util.spec_from_file_location("prepare_experiment_simulation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ops = _load_ops_module()


def test_simulation_batch_code_is_explicitly_isolated() -> None:
    assert ops.RULE_KEY == "paper-experiment-4v4"
    assert ops.validate_batch_code(" sim_20260822 ") == "SIM_20260822"
    with pytest.raises(ValueError, match="SIM_"):
        ops.validate_batch_code("PAPER_20260822")


def test_credentials_path_is_restricted_to_root(monkeypatch) -> None:
    monkeypatch.setattr(ops.os, "geteuid", lambda: 0)
    assert ops.validate_root_credentials_path(Path("/root/jixia-credentials.json")) == Path(
        "/root/jixia-credentials.json"
    )
    with pytest.raises(RuntimeError, match="under_root"):
        ops.validate_root_credentials_path(Path("/tmp/jixia-credentials.json"))

    monkeypatch.setattr(ops.os, "geteuid", lambda: 501)
    with pytest.raises(RuntimeError, match="requires_root"):
        ops.validate_root_credentials_path(Path("/root/jixia-credentials.json"))


def test_credentials_are_written_without_weak_permissions(tmp_path) -> None:
    path = tmp_path / "credentials.json"
    accounts = [
        {"code": code, "username": code, "temporary_password": f"secret-{code}"}
        for code in ops.ACCOUNT_CODES
    ]
    payload = ops.build_credential_payload(accounts)
    ops.write_credentials(path, payload)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = ops.load_existing_credentials(path)
    assert loaded == payload
    assert loaded is not None
    assert loaded["accounts"][0]["team_code"] == "T01"
    assert loaded["accounts"][17]["team_code"] == "T06"
    assert loaded["accounts"][18]["team_code"] is None


def test_credentials_with_group_access_are_rejected(tmp_path) -> None:
    path = tmp_path / "credentials.json"
    path.write_text('{"accounts": []}', encoding="utf-8")
    path.chmod(0o640)
    with pytest.raises(RuntimeError, match="0600"):
        ops.load_existing_credentials(path)

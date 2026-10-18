"""Historical recovery regression AST with owned files and no external effects."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "fixtures/spec304/release_recovery_ast.py"


def execute_recovery(
    tmp_path: Path,
    *,
    installed: bool,
    fault: str = "none",
    revision: str = "0047_speech_origin",
) -> dict[str, Any]:
    """Compile only helper functions and the outer exception handler body.

    The public fixture has no deployment body or live dependencies. Systemctl
    and subprocess are injected fakes; pathlib/shutil touch only owned tmp_path.
    """
    tree = ast.parse(SOURCE.read_text())
    outer = next(
        node
        for node in tree.body
        if isinstance(node, ast.Try) and any(child.name == "error" for child in node.handlers)
    )
    handler = outer.handlers[0]
    helpers = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"sha", "gate", "safe_relative", "web_manifest", "recovery_emit"}
    ]
    code = compile(ast.Module(body=[*helpers, *handler.body], type_ignores=[]), str(SOURCE), "exec")
    base, backup, web = tmp_path / "base", tmp_path / "backup", tmp_path / "web"
    base.mkdir()
    backup.mkdir()
    web.mkdir()
    originals: dict[str, Any] = {}
    for relative in ("first.py", "second.py"):
        (base / relative).write_text("candidate-" + relative)
        old = backup / "source" / relative
        old.parent.mkdir(exist_ok=True)
        old.write_text("old-" + relative)
        originals[relative] = {
            "existed": True,
            "sha256": hashlib.sha256(old.read_bytes()).hexdigest(),
        }
    migration = "migrations/versions/0047_speech_origin.py"
    (base / migration).parent.mkdir(parents=True)
    (base / migration).write_text("candidate migration")
    originals[migration] = {"existed": False, "sha256": None}
    (base / "new.py").write_text("candidate new")
    originals["new.py"] = {"existed": False, "sha256": None}
    if fault == "corrupt_backup":
        (backup / "source/second.py").write_text("corrupted old input")
    if fault in {"web_backup", "corrupt_web_backup"}:
        old_web = backup / "web/apps/web"
        (old_web / ".next").mkdir(parents=True)
        (old_web / "server.js").write_text("owned old web")
        (old_web / ".next/BUILD_ID").write_text("old-build")
        (old_web / "static.js").write_text("owned old static")
        (web / "candidate.txt").write_text("candidate-web")
        snapshot = {
            path.relative_to(backup / "web").as_posix(): {
                "type": "file",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in (backup / "web").rglob("*")
            if path.is_file()
        }
        (backup / "web-source-rollback.json").write_text(json.dumps(snapshot))
        if fault == "corrupt_web_backup":
            (old_web / "static.js").write_text("corrupted old static")
    events: list[dict[str, Any]] = []
    commands: list[list[str]] = []
    fallback: list[list[str]] = []
    before = {
        "revisions": [revision],
        "nonterminal_matches": 0,
        "nonterminal_rooms": 0,
        "leases": 0,
        "matches_total": 7,
        "rooms_total": 7,
        "speeches_total": 30,
    }
    inspections = 0

    async def inspect() -> dict[str, Any]:
        nonlocal inspections
        inspections += 1
        result = dict(before)
        if fault == "active" or (fault == "new_during_drain" and inspections > 1):
            result["nonterminal_matches"] = 1
        return result

    def run(command: list[str], label: str, **_: Any) -> None:
        commands.append(command)
        if fault == "stop_fails" and command[:2] == ["systemctl", "stop"]:
            raise RuntimeError("synthetic stop failure")

    def fallback_run(command: list[str], **_: Any) -> Any:
        fallback.append(command)
        if fault == "fallback_timeout" and command[-1] == "jx-core":
            raise subprocess.TimeoutExpired(command, 0.01)
        return SimpleNamespace(returncode=0)

    def emit(phase: str, **values: Any) -> None:
        if fault == "report_write_fails":
            raise OSError("synthetic owned report failure")
        events.append({"phase": phase, **values})

    report: dict[str, Any] = {
        "stop_attempted": True,
        "installation_started": installed,
        "backup_ready": installed,
        "web_backup_ready": fault in {"missing_web_backup", "web_backup", "corrupt_web_backup"},
        "resource_configuration_started": False,
    }
    if fault in {"fallback_timeout", "fallback_log_write_fails"}:
        # Force the recovery gate to fail, then test independent service start.
        before["nonterminal_matches"] = 1
    if fault == "fallback_log_write_fails":
        for service in ("jx-core", "jx-jobs", "jx-web"):
            (backup / ("forward_start_" + service + ".log")).mkdir()
    namespace = {
        "REPORT": report,
        "BACKUP": backup,
        "BASE": base,
        "WEB": web,
        "before": before,
        "original_files": originals,
        "error": RuntimeError("owned synthetic initial failure"),
        "emit": emit,
        "inspect": inspect,
        "run": run,
        "hashlib": hashlib,
        "json": json,
        "Path": Path,
        "asyncio": asyncio,
        "shutil": shutil,
        "subprocess": SimpleNamespace(
            run=fallback_run,
            STDOUT=subprocess.STDOUT,
            PIPE=subprocess.PIPE,
            TimeoutExpired=subprocess.TimeoutExpired,
        ),
    }
    escaped: str | None = None
    try:
        exec(code, namespace)
    except SystemExit as result:
        assert result.code == 1
    except BaseException as error:
        escaped = type(error).__name__
    return {
        "base": base,
        "web": web,
        "backup": backup,
        "events": events,
        "commands": commands,
        "fallback": fallback,
        "migration": migration,
        "escaped": escaped,
    }


def test_public_recovery_fixture_has_no_executable_installation_body() -> None:
    tree = ast.parse(SOURCE.read_text())
    outer = next(node for node in tree.body if isinstance(node, ast.Try))
    assert len(outer.body) == 1 and isinstance(outer.body[0], ast.Pass)
    assert not any(
        isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) for node in tree.body
    )


def test_partial_stop_before_install_restarts_all_without_backup(tmp_path: Path) -> None:
    result = execute_recovery(tmp_path, installed=False)
    assert result["escaped"] is None
    assert result["commands"] == [
        ["systemctl", "start", item] for item in ("jx-core", "jx-jobs", "jx-web")
    ]
    assert (result["base"] / "first.py").read_text() == "candidate-first.py"
    assert not result["fallback"]


@pytest.mark.parametrize(
    "fault", ["active", "new_during_drain", "corrupt_backup", "missing_web_backup", "stop_fails"]
)
def test_failed_rollback_gate_never_partially_restores_source_and_starts_current_services(
    tmp_path: Path, fault: str
) -> None:
    result = execute_recovery(tmp_path, installed=True, fault=fault)
    assert result["escaped"] is None
    assert (result["base"] / "first.py").read_text() == "candidate-first.py"
    assert (result["base"] / "second.py").read_text() == "candidate-second.py"
    assert (result["base"] / "new.py").exists()
    assert result["fallback"] == [
        ["systemctl", "start", item] for item in ("jx-core", "jx-jobs", "jx-web")
    ]
    assert any(row["phase"] == "recovery_requires_forward_resolution" for row in result["events"])
    if fault in {"active", "corrupt_backup", "missing_web_backup"}:
        assert not result["commands"], "prevalidate all inputs before stopping/restoring"


def test_forward_restart_timeout_still_attempts_every_service(tmp_path: Path) -> None:
    result = execute_recovery(tmp_path, installed=True, fault="fallback_timeout")
    assert result["escaped"] is None, "one start timeout must not abort remaining services"
    assert result["fallback"] == [
        ["systemctl", "start", item] for item in ("jx-core", "jx-jobs", "jx-web")
    ]
    report = next(
        row for row in result["events"] if row["phase"] == "forward_start_attempts_complete"
    )
    assert report["services"]["jx-core"] == {"error_class": "TimeoutExpired"}
    assert report["services"]["jx-jobs"] == {"returncode": 0}
    assert report["services"]["jx-web"] == {"returncode": 0}


@pytest.mark.parametrize("revision", ["0046_provider_capture_snapshots", "0047_speech_origin"])
def test_exact_source_restore_preserves_only_an_applied_additive_migration(
    tmp_path: Path, revision: str
) -> None:
    result = execute_recovery(tmp_path, installed=True, revision=revision)
    assert result["escaped"] is None
    assert (result["base"] / "first.py").read_text() == "old-first.py"
    assert (result["base"] / "second.py").read_text() == "old-second.py"
    assert not (result["base"] / "new.py").exists()
    assert (result["base"] / result["migration"]).exists() == (revision == "0047_speech_origin")
    assert all("pg_restore" not in command for command in result["commands"])
    assert any(row["phase"] == "compatible_source_and_web_recovered" for row in result["events"])


def test_valid_source_and_web_backup_restores_exact_owned_files(tmp_path: Path) -> None:
    result = execute_recovery(tmp_path, installed=True, fault="web_backup")
    assert result["escaped"] is None
    assert (result["web"] / "apps/web/server.js").read_text() == "owned old web"
    assert (result["web"] / "apps/web/.next/BUILD_ID").read_text() == "old-build"
    assert (result["backup"] / "failed-web/candidate.txt").read_text() == "candidate-web"
    assert not result["fallback"]

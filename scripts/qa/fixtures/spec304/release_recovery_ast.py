"""Historical recovery AST fixture; not a deployment or recovery command.

Tests compile selected nodes with owned temporary files and injected fake services.
Dependencies below are inert None placeholders; importing this file performs no IO.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

hashlib: Any = None
json: Any = None
asyncio: Any = None
shutil: Any = None
subprocess: Any = None
REPORT: dict[str, Any] = None
BACKUP: Path = None
BASE: Path = None
WEB: Path = None
before: dict[str, Any] = None
original_files: dict[str, Any] = None
emit: Any = None
inspect: Any = None
run: Any = None


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recovery_emit(phase, **values):
    try:
        emit(phase, **values)
    except Exception as logging_error:
        REPORT.update(phase=phase, **values)
        try:
            print(
                json.dumps(
                    {"phase": phase, "recovery_report_error_class": type(logging_error).__name__}
                ),
                flush=True,
            )
        except Exception:
            pass


def safe_relative(relative):
    path = Path(relative)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and all(not part.startswith(".env") and part != "api.md" for part in path.parts)
    )


def web_manifest(root):
    assert root.is_dir() and (not root.is_symlink())
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        assert safe_relative(relative), "forbidden_web_backup_path"
        if path.is_symlink():
            assert path.resolve().is_relative_to(root.resolve()), "external_web_backup_link"
            result[relative] = {"type": "link", "target": path.readlink().as_posix()}
        elif path.is_file():
            result[relative] = {"type": "file", "sha256": sha(path)}
    assert "apps/web/server.js" in result and "apps/web/.next/BUILD_ID" in result
    return result


def gate(value):
    return value["nonterminal_matches"] == value["nonterminal_rooms"] == value["leases"] == 0


try:
    pass
except BaseException as error:
    recovery_emit("release_requires_recovery", error_class=type(error).__name__, published=False)
    if REPORT["stop_attempted"]:
        try:
            if REPORT["installation_started"]:
                state = asyncio.run(inspect())
                assert gate(state)
                assert before is not None and all(
                    state[key] == before[key]
                    for key in ["matches_total", "rooms_total", "speeches_total"]
                )
                assert REPORT["backup_ready"]
                for relative, value in original_files.items():
                    if value["existed"]:
                        assert sha(BACKUP / "source" / relative) == value["sha256"]
                if REPORT.get("web_backup_ready"):
                    expected_web = json.loads((BACKUP / "web-source-rollback.json").read_text())
                    assert web_manifest(BACKUP / "web") == expected_web, (
                        "web_backup_manifest_mismatch"
                    )
                for service in ["jx-web", "jx-jobs", "jx-core"]:
                    run(["systemctl", "stop", service], "rollback_stop_" + service)
                assert gate(asyncio.run(inspect())), "new_state_during_rollback_drain"
                for relative, value in original_files.items():
                    if value["existed"]:
                        shutil.copy2(BACKUP / "source" / relative, BASE / relative)
                    elif (BASE / relative).is_file():
                        keep_applied_migration = (
                            relative == "migrations/versions/0047_speech_origin.py"
                            and state["revisions"] == ["0047_speech_origin"]
                        )
                        if not keep_applied_migration:
                            (BASE / relative).unlink()
                if REPORT.get("web_backup_ready"):
                    if WEB.exists():
                        WEB.rename(BACKUP / "failed-web")
                    (BACKUP / "web").rename(WEB)
            for service in ["jx-core", "jx-jobs", "jx-web"]:
                run(["systemctl", "start", service], "rollback_start_" + service)
            recovery_emit(
                "compatible_source_and_web_recovered",
                rollback_completed=True,
                production_history_restored=False,
                resources_require_inspection=REPORT["resource_configuration_started"],
            )
        except BaseException as recovery_error:
            recovery_emit(
                "recovery_requires_forward_resolution",
                recovery_error_class=type(recovery_error).__name__,
                rollback_completed=False,
                production_history_restored=False,
            )
            forward_starts = {}
            for service in ["jx-core", "jx-jobs", "jx-web"]:
                try:
                    started = subprocess.run(
                        ["systemctl", "start", service],
                        cwd=BASE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        timeout=90,
                    )
                    forward_starts[service] = {"returncode": started.returncode}
                    try:
                        (BACKUP / ("forward_start_" + service + ".log")).write_bytes(
                            getattr(started, "stdout", b"") or b""
                        )
                    except OSError as log_error:
                        forward_starts[service]["log_error_class"] = type(log_error).__name__
                except Exception as start_error:
                    forward_starts[service] = {"error_class": type(start_error).__name__}
            recovery_emit(
                "forward_start_attempts_complete", services=forward_starts, rollback_completed=False
            )
    raise SystemExit(1) from None

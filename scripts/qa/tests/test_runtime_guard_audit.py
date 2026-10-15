from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess, run

import pytest
from scripts.ops import runtime_guard_audit
from scripts.ops.runtime_guard_audit import (
    RuntimeGuardReport,
    guard_failures,
    inspect_service,
    parse_memory_events,
    parse_proc_limits,
    parse_systemd_show,
)


def test_systemd_parser_keeps_only_non_sensitive_guard_fields() -> None:
    values = parse_systemd_show(
        "MainPID=123\nLimitNOFILE=524288\nLimitNOFILESoft=1024\nMemoryHigh=1G\nMemoryMax=2G\n"
        "ControlGroup=/system.slice/jx-core.service\n"
        "Environment=SECRET=must-not-be-retained\n"
    )

    assert values == {
        "MainPID": "123",
        "LimitNOFILE": "524288",
        "LimitNOFILESoft": "1024",
        "MemoryHigh": "1G",
        "MemoryMax": "2G",
        "ControlGroup": "/system.slice/jx-core.service",
    }


def test_memory_event_parser_keeps_only_numeric_oom_kill() -> None:
    assert parse_memory_events("low 0\nome 3\noom_kill\t2\nsecret 99\n") == {"oom_kill": 2}


def test_proc_limits_parser_reads_soft_and_hard_open_file_limits() -> None:
    soft, hard = parse_proc_limits(
        "Limit                     Soft Limit           Hard Limit           Units\n"
        "Max open files            1024                 524288               files\n"
    )

    assert (soft, hard) == (1024, 524288)


def test_inspect_service_reads_main_pid_and_proc_limits_without_mutation(tmp_path: Path) -> None:
    limits = tmp_path / "123" / "limits"
    limits.parent.mkdir()
    limits.write_text(
        "Max open files            524288               524288               files\n",
        encoding="utf-8",
    )

    def runner(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(
            args=["systemctl"],
            returncode=0,
            stdout="MainPID=123\nLimitNOFILE=524288\nMemoryHigh=1G\nMemoryMax=2G\n",
            stderr="",
        )

    report = inspect_service("jx-core", proc_root=tmp_path, runner=runner)

    assert report.main_pid == 123
    assert report.systemd_nofile == 524288
    assert report.systemd_nofile_soft is None
    assert report.process_nofile_soft == 524288
    assert report.process_nofile_hard == 524288
    assert guard_failures(report) == []


def test_inspect_service_distinguishes_systemd_high_hard_from_low_soft(tmp_path: Path) -> None:
    limits = tmp_path / "123" / "limits"
    limits.parent.mkdir()
    limits.write_text(
        "Max open files            1024                 524288               files\n",
        encoding="utf-8",
    )

    def runner(args: list[str], **_kwargs: object) -> CompletedProcess[str]:
        assert "--property=LimitNOFILESoft" in args
        return CompletedProcess(
            args=args,
            returncode=0,
            stdout="MainPID=123\nLimitNOFILE=524288\nLimitNOFILESoft=1024\n",
            stderr="",
        )

    report = inspect_service("jx-core", proc_root=tmp_path, runner=runner)

    assert report.systemd_nofile == 524288
    assert report.systemd_nofile_soft == 1024
    assert report.process_nofile_soft == 1024
    assert guard_failures(report) == ["jx-core: process soft nofile=1024 < 524288"]


@pytest.mark.parametrize("systemd_soft", [1024, None])
def test_cli_reports_systemd_soft_without_changing_effective_process_gate(
    systemd_soft: int | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = RuntimeGuardReport(
        service="jx-core",
        main_pid=123,
        systemd_nofile=524288,
        process_nofile_soft=524288,
        process_nofile_hard=524288,
        memory_high="infinity",
        memory_max="infinity",
        systemd_nofile_soft=systemd_soft,
    )

    def audit(*_args: object, **_kwargs: object):
        return [report], guard_failures(report)

    monkeypatch.setattr(runtime_guard_audit, "audit", audit)

    assert runtime_guard_audit.main(["--service", "jx-core"]) == 0
    output = capsys.readouterr()
    expected_soft = "unavailable" if systemd_soft is None else str(systemd_soft)
    assert "systemd_nofile=524288" in output.out
    assert f"systemd_nofile_soft={expected_soft}" in output.out
    assert "soft_nofile=524288 hard_nofile=524288" in output.out
    assert output.err == ""


def test_inspect_service_reads_cgroup_telemetry_without_retaining_unknown_files(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    limits = proc_root / "123" / "limits"
    limits.parent.mkdir(parents=True)
    limits.write_text(
        "Max open files            524288               524288               files\n",
        encoding="utf-8",
    )
    cgroup_root = tmp_path / "cgroup"
    cgroup_dir = cgroup_root / "system.slice" / "jx-core.service"
    cgroup_dir.mkdir(parents=True)
    for name, value in {
        "memory.current": "1234\n",
        "memory.peak": "5678\n",
        "memory.swap.current": "100\n",
        "memory.swap.peak": "200\n",
        "memory.events": "low 0\noom_kill 0\nunknown 999\n",
        "pids.current": "19\n",
        "pids.max": "max\n",
    }.items():
        (cgroup_dir / name).write_text(value, encoding="utf-8")

    def runner(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(
            args=["systemctl"],
            returncode=0,
            stdout=(
                "MainPID=123\nLimitNOFILE=524288\nMemoryHigh=1G\nMemoryMax=2G\n"
                "ControlGroup=/system.slice/jx-core.service\n"
            ),
            stderr="",
        )

    report = inspect_service(
        "jx-core",
        proc_root=proc_root,
        cgroup_root=cgroup_root,
        runner=runner,
    )

    assert report.cgroup_memory_current_bytes == 1234
    assert report.cgroup_memory_peak_bytes == 5678
    assert report.cgroup_swap_current_bytes == 100
    assert report.cgroup_swap_peak_bytes == 200
    assert report.cgroup_oom_kill == 0
    assert report.cgroup_pids_current == 19
    assert report.cgroup_pids_max == "max"


def test_cgroup_symlink_loop_is_reported_as_unavailable(tmp_path: Path) -> None:
    cgroup_root = tmp_path / "cgroup"
    cgroup_root.mkdir()
    loop = cgroup_root / "system.slice"
    loop.symlink_to(loop)

    def runner(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(
            args=["systemctl"],
            returncode=0,
            stdout="MainPID=0\nControlGroup=/system.slice/jx-core.service\n",
            stderr="",
        )

    report = inspect_service(
        "jx-core",
        cgroup_root=cgroup_root,
        runner=runner,
    )

    assert report.cgroup_memory_peak_bytes is None
    assert report.cgroup_oom_kill is None


def test_guard_failures_fail_closed_for_soft_limit_and_optional_memory_guards() -> None:
    report = RuntimeGuardReport(
        service="jx-core",
        main_pid=123,
        systemd_nofile=524288,
        process_nofile_soft=1024,
        process_nofile_hard=524288,
        memory_high="infinity",
        memory_max="infinity",
    )

    failures = guard_failures(report, require_memory_guards=True)

    assert any("soft nofile=1024" in failure for failure in failures)
    assert any("MemoryHigh is unlimited" in failure for failure in failures)
    assert any("MemoryMax is unlimited" in failure for failure in failures)


def test_cgroup_telemetry_gate_fails_closed_for_missing_and_nonzero_oom() -> None:
    missing = RuntimeGuardReport(
        service="jx-core",
        main_pid=123,
        systemd_nofile=524288,
        process_nofile_soft=524288,
        process_nofile_hard=524288,
        memory_high="1G",
        memory_max="2G",
    )
    missing_failures = guard_failures(missing, require_cgroup_telemetry=True)
    assert any("memory.peak is unavailable" in failure for failure in missing_failures)
    assert any("oom_kill is unavailable" in failure for failure in missing_failures)

    oom = RuntimeGuardReport(
        service="jx-core",
        main_pid=123,
        systemd_nofile=524288,
        process_nofile_soft=524288,
        process_nofile_hard=524288,
        memory_high="1G",
        memory_max="2G",
        cgroup_memory_current_bytes=1,
        cgroup_memory_peak_bytes=2,
        cgroup_swap_current_bytes=3,
        cgroup_swap_peak_bytes=4,
        cgroup_oom_kill=1,
        cgroup_pids_current=1,
        cgroup_pids_max="max",
    )
    assert guard_failures(oom, require_cgroup_telemetry=True) == [
        "jx-core: cgroup memory.events oom_kill=1"
    ]


def test_preflight_is_read_only_and_checks_room_cleanup_guards() -> None:
    preflight = Path("scripts/ops/preflight_v2_1.sh").read_text(encoding="utf-8")
    wrapper = Path("scripts/ops/runtime_guard_audit.sh").read_text(encoding="utf-8")

    assert "runtime_guard_audit.sh" in preflight
    assert "terminal_rooms_missing_end_at" in preflight
    assert "active_statuses" in preflight
    assert "nonterminal_rooms" in preflight
    assert "nonterminal_room_statuses" in preflight
    assert "active_leases" in preflight
    assert "JX_PREFLIGHT_REQUIRE_CGROUP_TELEMETRY" in preflight
    assert "systemctl restart" not in wrapper
    assert "systemctl daemon-reload" not in wrapper
    for service in ("jx-core", "jx-jobs"):
        drop_in = Path(f"ops/systemd/{service}.service.d/resource-limits.conf")
        assert "LimitNOFILE=524288:524288" in drop_in.read_text(encoding="utf-8")


def _run_service_check(tmp_path: Path, *, docker_output: str | None, livekit_unit: bool = False):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    systemctl = bin_dir / "systemctl"
    systemctl.write_text(
        "#!/bin/bash\n"
        'if [[ "$1" == is-active ]]; then\n'
        '  case "${@: -1}" in\n'
        "    jx-core|jx-jobs|jx-web) exit 0 ;;\n"
        f"    jx-livekit|livekit.service) exit {'0' if livekit_unit else '3'} ;;\n"
        "    *) exit 3 ;;\n"
        "  esac\n"
        "fi\n"
        "exit 3\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    if docker_output is not None:
        docker = bin_dir / "docker"
        docker.write_text(
            f"#!/bin/bash\nprintf '%s\\n' {docker_output!r}\n",
            encoding="utf-8",
        )
        docker.chmod(0o755)
    result = run(
        [
            "/bin/bash",
            "-c",
            'source "$1"; check_services; exit "$failures"',
            "bash",
            str(Path("scripts/ops/preflight_v2_1.sh").resolve()),
        ],
        env={"PATH": str(bin_dir)},
        capture_output=True,
        text=True,
        check=False,
    )
    return result


def test_preflight_accepts_running_pinned_livekit_container(tmp_path: Path) -> None:
    result = _run_service_check(
        tmp_path,
        docker_output="true|livekit/livekit-server:v1.13.5",
    )

    assert result.returncode == 0
    assert "container active: jx-livekit" in result.stdout


def test_preflight_fails_closed_for_stopped_or_unexpected_livekit_container(
    tmp_path: Path,
) -> None:
    stopped = _run_service_check(
        tmp_path / "stopped",
        docker_output="false|livekit/livekit-server:v1.13.5",
    )
    unexpected = _run_service_check(
        tmp_path / "unexpected",
        docker_output="true|example/other:latest",
    )

    assert stopped.returncode != 0
    assert "container is not running" in stopped.stderr
    assert unexpected.returncode != 0
    assert "container image is unexpected" in unexpected.stderr

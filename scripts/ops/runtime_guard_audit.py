"""Read-only audit of the effective systemd and process resource guards.

This command is deliberately diagnostic.  It never writes a unit file, reloads
systemd, or restarts a service.  The release preflight may run it before a
protected rollout so a unit that only raises the hard ``nofile`` limit (while
leaving the process soft limit at 1024) is caught.  cgroup current/peak values
are read-only evidence; ``--require-cgroup-telemetry`` makes missing values or
non-zero ``oom_kill`` fail closed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_SERVICES = ("jx-core", "jx-jobs")
DEFAULT_MIN_NOFILE = 524_288
_UNLIMITED = {"infinity", "max", "unlimited", "-1"}


@dataclass(frozen=True, slots=True)
class RuntimeGuardReport:
    service: str
    main_pid: int | None
    systemd_nofile: int | None
    process_nofile_soft: int | None
    process_nofile_hard: int | None
    memory_high: str | None
    memory_max: str | None
    cgroup_memory_current_bytes: int | None = None
    cgroup_memory_peak_bytes: int | None = None
    cgroup_swap_current_bytes: int | None = None
    cgroup_swap_peak_bytes: int | None = None
    cgroup_oom_kill: int | None = None
    cgroup_pids_current: int | None = None
    cgroup_pids_max: int | str | None = None
    systemd_nofile_soft: int | None = None


def parse_systemd_show(output: str) -> dict[str, str]:
    """Parse ``systemctl show`` key/value output without retaining unknown data."""

    allowed = {
        "MainPID",
        "LimitNOFILE",
        "LimitNOFILESoft",
        "MemoryHigh",
        "MemoryMax",
        "ControlGroup",
    }
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in allowed:
            values[key] = value.strip()
    return values


def parse_proc_limits(output: str) -> tuple[int | None, int | None]:
    """Return soft/hard ``Max open files`` values from ``/proc/$pid/limits``."""

    for line in output.splitlines():
        if not line.startswith("Max open files"):
            continue
        fields = line.split()
        # Label has three words, then soft and hard limits.
        if len(fields) < 5:
            return None, None
        return _parse_limit(fields[3]), _parse_limit(fields[4])
    return None, None


def _parse_limit(value: str) -> int | None:
    normalized = value.strip().lower()
    if normalized in _UNLIMITED:
        return None
    try:
        return int(normalized)
    except ValueError:
        return None


def _parse_pid(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        pid = int(value)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _parse_optional_limit(value: str | None) -> int | None:
    if value is None:
        return None
    return _parse_limit(value)


def _parse_counter(value: str | None) -> int | None:
    """Parse a non-negative cgroup counter without exposing raw file text."""

    if value is None:
        return None
    try:
        parsed = int(value.strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _parse_cgroup_limit(value: str | None) -> int | str | None:
    """Parse a cgroup limit while preserving the safe ``max`` sentinel."""

    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"max", "infinity", "unlimited"}:
        return "max"
    return _parse_counter(normalized)


def parse_memory_events(output: str) -> dict[str, int]:
    """Keep only the numeric ``oom_kill`` cgroup memory event counter."""

    events: dict[str, int] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[0] != "oom_kill":
            continue
        parsed = _parse_counter(fields[1])
        if parsed is not None:
            events["oom_kill"] = parsed
    return events


def _read_cgroup_file(
    *,
    cgroup_root: Path,
    control_group: str | None,
    filename: str,
) -> str | None:
    """Read one whitelisted cgroup file without allowing path escape or raw output."""

    if not control_group or not control_group.startswith("/"):
        return None
    relative_group = Path(control_group.lstrip("/"))
    if any(part in {"", ".", ".."} for part in relative_group.parts):
        return None
    if filename not in {
        "memory.current",
        "memory.peak",
        "memory.swap.current",
        "memory.swap.peak",
        "memory.events",
        "pids.current",
        "pids.max",
    }:
        return None
    try:
        root = cgroup_root.resolve()
        path = (root / relative_group / filename).resolve()
        path.relative_to(root)
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeError, ValueError, RuntimeError):
        return None


def _read_cgroup_snapshot(
    *,
    cgroup_root: Path,
    control_group: str | None,
) -> dict[str, Any]:
    def read(filename: str) -> str | None:
        return _read_cgroup_file(
            cgroup_root=cgroup_root,
            control_group=control_group,
            filename=filename,
        )

    events = parse_memory_events(read("memory.events") or "")
    return {
        "cgroup_memory_current_bytes": _parse_counter(read("memory.current")),
        "cgroup_memory_peak_bytes": _parse_counter(read("memory.peak")),
        "cgroup_swap_current_bytes": _parse_counter(read("memory.swap.current")),
        "cgroup_swap_peak_bytes": _parse_counter(read("memory.swap.peak")),
        "cgroup_oom_kill": events.get("oom_kill"),
        "cgroup_pids_current": _parse_counter(read("pids.current")),
        "cgroup_pids_max": _parse_cgroup_limit(read("pids.max")),
    }


def inspect_service(
    service: str,
    *,
    systemctl: str = "systemctl",
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    runner: Callable[..., Any] = subprocess.run,
) -> RuntimeGuardReport:
    """Read one service's effective unit and process limits."""

    try:
        result = runner(
            [
                systemctl,
                "show",
                service,
                "--no-pager",
                "--property=MainPID",
                "--property=LimitNOFILE",
                "--property=LimitNOFILESoft",
                "--property=MemoryHigh",
                "--property=MemoryMax",
                "--property=ControlGroup",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        result = None
    output = result.stdout if result is not None and result.returncode == 0 else ""
    values = parse_systemd_show(output)
    pid = _parse_pid(values.get("MainPID"))
    soft: int | None = None
    hard: int | None = None
    if pid is not None:
        try:
            limits = (proc_root / str(pid) / "limits").read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            limits = ""
        soft, hard = parse_proc_limits(limits)
    return RuntimeGuardReport(
        service=service,
        main_pid=pid,
        systemd_nofile=_parse_optional_limit(values.get("LimitNOFILE")),
        process_nofile_soft=soft,
        process_nofile_hard=hard,
        memory_high=values.get("MemoryHigh"),
        memory_max=values.get("MemoryMax"),
        systemd_nofile_soft=_parse_optional_limit(values.get("LimitNOFILESoft")),
        **_read_cgroup_snapshot(
            cgroup_root=cgroup_root,
            control_group=values.get("ControlGroup"),
        ),
    )


def guard_failures(
    report: RuntimeGuardReport,
    *,
    min_nofile: int = DEFAULT_MIN_NOFILE,
    require_memory_guards: bool = False,
    require_cgroup_telemetry: bool = False,
) -> list[str]:
    """Return stable, credential-free failures for one report."""

    failures: list[str] = []
    if report.main_pid is None:
        failures.append(f"{report.service}: active MainPID is unavailable")
    if report.systemd_nofile is not None and report.systemd_nofile < min_nofile:
        failures.append(
            f"{report.service}: systemd LimitNOFILE={report.systemd_nofile} < {min_nofile}"
        )
    if report.process_nofile_soft is None:
        failures.append(f"{report.service}: process soft nofile limit is unavailable")
    elif report.process_nofile_soft < min_nofile:
        failures.append(
            f"{report.service}: process soft nofile={report.process_nofile_soft} < {min_nofile}"
        )
    if report.process_nofile_hard is not None and report.process_nofile_hard < min_nofile:
        failures.append(
            f"{report.service}: process hard nofile={report.process_nofile_hard} < {min_nofile}"
        )
    if require_memory_guards:
        if _is_unlimited(report.memory_high):
            failures.append(f"{report.service}: MemoryHigh is unlimited")
        if _is_unlimited(report.memory_max):
            failures.append(f"{report.service}: MemoryMax is unlimited")
    if require_cgroup_telemetry:
        if report.cgroup_memory_current_bytes is None:
            failures.append(f"{report.service}: cgroup memory.current is unavailable")
        if report.cgroup_memory_peak_bytes is None:
            failures.append(f"{report.service}: cgroup memory.peak is unavailable")
        if report.cgroup_swap_current_bytes is None:
            failures.append(f"{report.service}: cgroup memory.swap.current is unavailable")
        if report.cgroup_swap_peak_bytes is None:
            failures.append(f"{report.service}: cgroup memory.swap.peak is unavailable")
        if report.cgroup_oom_kill is None:
            failures.append(f"{report.service}: cgroup memory.events oom_kill is unavailable")
        elif report.cgroup_oom_kill > 0:
            failures.append(
                f"{report.service}: cgroup memory.events oom_kill={report.cgroup_oom_kill}"
            )
        if report.cgroup_pids_current is None:
            failures.append(f"{report.service}: cgroup pids.current is unavailable")
        if report.cgroup_pids_max is None:
            failures.append(f"{report.service}: cgroup pids.max is unavailable")
    return failures


def _is_unlimited(value: str | None) -> bool:
    return value is None or value.strip().lower() in _UNLIMITED


def audit(
    services: Iterable[str] = DEFAULT_SERVICES,
    *,
    min_nofile: int = DEFAULT_MIN_NOFILE,
    require_memory_guards: bool = False,
    require_cgroup_telemetry: bool = False,
    systemctl: str = "systemctl",
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> tuple[list[RuntimeGuardReport], list[str]]:
    reports = [
        inspect_service(
            service,
            systemctl=systemctl,
            proc_root=proc_root,
            cgroup_root=cgroup_root,
        )
        for service in services
    ]
    failures = [
        failure
        for report in reports
        for failure in guard_failures(
            report,
            min_nofile=min_nofile,
            require_memory_guards=require_memory_guards,
            require_cgroup_telemetry=require_cgroup_telemetry,
        )
    ]
    return reports, failures


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", action="append", dest="services")
    parser.add_argument("--min-nofile", type=_positive_int, default=DEFAULT_MIN_NOFILE)
    parser.add_argument("--require-memory-guards", action="store_true")
    parser.add_argument("--require-cgroup-telemetry", action="store_true")
    args = parser.parse_args(argv)
    services = tuple(args.services or DEFAULT_SERVICES)
    reports, failures = audit(
        services,
        min_nofile=args.min_nofile,
        require_memory_guards=args.require_memory_guards,
        require_cgroup_telemetry=args.require_cgroup_telemetry,
    )

    def display(value: object) -> str:
        return "unavailable" if value is None else str(value)

    for report in reports:
        print(
            f"INFO {report.service} pid={display(report.main_pid)} "
            f"systemd_nofile={display(report.systemd_nofile)} "
            f"systemd_nofile_soft={display(report.systemd_nofile_soft)} "
            f"soft_nofile={display(report.process_nofile_soft)} "
            f"hard_nofile={display(report.process_nofile_hard)} "
            f"MemoryHigh={display(report.memory_high)} "
            f"MemoryMax={display(report.memory_max)} "
            f"cgroup_memory_current_bytes={display(report.cgroup_memory_current_bytes)} "
            f"cgroup_memory_peak_bytes={display(report.cgroup_memory_peak_bytes)} "
            f"cgroup_swap_current_bytes={display(report.cgroup_swap_current_bytes)} "
            f"cgroup_swap_peak_bytes={display(report.cgroup_swap_peak_bytes)} "
            f"cgroup_oom_kill={display(report.cgroup_oom_kill)} "
            f"cgroup_pids_current={display(report.cgroup_pids_current)} "
            f"cgroup_pids_max={display(report.cgroup_pids_max)}"
        )
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    if not failures:
        print("PASS runtime resource guards satisfy the release floor")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

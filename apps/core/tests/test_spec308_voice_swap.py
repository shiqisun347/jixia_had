from __future__ import annotations

import subprocess

import pytest
from scripts.ops import spec308_voice_swap as swap


def test_service_gate_requires_exact_inactive_for_both_services(monkeypatch) -> None:
    calls = []

    def run(command, **options):
        calls.append((command, options))
        return subprocess.CompletedProcess(command, 3, "inactive\n", "")

    monkeypatch.setattr(swap.subprocess, "run", run)
    swap.require_services_stopped()
    assert [command for command, _ in calls] == [
        ["systemctl", "is-active", "jx-core"],
        ["systemctl", "is-active", "jx-jobs"],
    ]
    assert all(options["timeout"] == 5 and options["check"] is False for _, options in calls)


@pytest.mark.parametrize("blocked_service", ["jx-core", "jx-jobs"])
@pytest.mark.parametrize(
    ("code", "state"),
    [
        (0, "active"),
        (3, "failed"),
        (4, "unknown"),
        (0, "inactive"),
        (3, ""),
        (3, "inactive\nactive"),
    ],
)
def test_service_gate_rejects_active_failed_unknown_and_inconsistent_results(
    monkeypatch, blocked_service: str, code: int, state: str
) -> None:
    def run(command, **_options):
        if command[-1] == blocked_service:
            return subprocess.CompletedProcess(command, code, state, "")
        return subprocess.CompletedProcess(command, 3, "inactive\n", "")

    monkeypatch.setattr(swap.subprocess, "run", run)
    with pytest.raises(swap.SwapConflict, match="room_creating_services_must_be_inactive"):
        swap.require_services_stopped()


@pytest.mark.parametrize(
    "error", [subprocess.TimeoutExpired("systemctl", 5), OSError("synthetic systemctl failure")]
)
def test_service_gate_fails_closed_if_systemctl_cannot_be_observed(monkeypatch, error) -> None:
    def run(*_args, **_options):
        raise error

    monkeypatch.setattr(swap.subprocess, "run", run)
    with pytest.raises((swap.SwapConflict, subprocess.TimeoutExpired, OSError)):
        swap.require_services_stopped()

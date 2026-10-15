from __future__ import annotations

import asyncio
import json
import logging
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from test_spec304_match_probe import probe as probe


def exception_record(exception: type[Exception], depth: int = 0) -> logging.LogRecord:
    def nested(remaining: int) -> None:
        if remaining:
            nested(remaining - 1)
        else:
            raise exception("never-export-this-secret-response-or-path")

    try:
        nested(depth)
    except Exception:
        record = logging.LogRecord(
            "jx-core.agent", logging.ERROR, "/private/never-export.py", 9,
            "never-export-this-secret-response-or-path", (), sys.exc_info(),
        )
        record.error_code = "agent_pipeline_failed"
        record.attempt_no = 2
        return record
    raise AssertionError("fixture must capture an exception")


@pytest.mark.parametrize("depth", [0, 20])
def test_warning_diagnostic_keeps_only_safe_exception_class_and_last_eight_frames(
    probe: Any, depth: int
) -> None:
    handler = probe.full.StableWarnings()
    handler.emit(exception_record(TimeoutError, depth))
    assert len(handler.rows) == 1
    row = handler.rows[0]
    assert row["code"] == "agent_pipeline_failed" and row["attempt_no"] == 2
    assert row["exception_class"] == "TimeoutError"
    assert 1 <= len(row["exception_frames"]) <= 8
    assert all(set(frame) == {"function", "line"} for frame in row["exception_frames"])
    assert all(isinstance(frame["line"], int) for frame in row["exception_frames"])
    serialized = json.dumps(handler.rows)
    assert "secret-response" not in serialized and "private" not in serialized
    assert "never-export.py" not in serialized


def test_warning_diagnostic_rejects_unlisted_exception_names_and_preserves_row_bound(
    probe: Any,
) -> None:
    class UnlistedSecretResponseError(Exception):
        pass

    handler = probe.full.StableWarnings()
    record = exception_record(UnlistedSecretResponseError)
    for _ in range(101):
        handler.emit(record)
    assert len(handler.rows) == 100
    assert all(row["exception_class"] is None for row in handler.rows)
    assert "UnlistedSecretResponseError" not in json.dumps(handler.rows)
    assert "secret-response" not in json.dumps(handler.rows)


def test_host_warning_without_attached_exc_info_uses_only_active_safe_exception_shape(
    probe: Any,
) -> None:
    handler = probe.full.StableWarnings()
    record = logging.LogRecord("jx-core.agent", logging.WARNING, "private-path", 1, "raw", (), None)
    record.error_code = "agent_pipeline_failed"
    try:
        raise TimeoutError("never-export-this-secret-response-or-path")
    except TimeoutError:
        handler.emit(record)
    assert handler.rows[0]["exception_class"] == "TimeoutError"
    assert handler.rows[0]["exception_frames"]
    assert "secret-response" not in json.dumps(handler.rows)


def test_runtime_telemetry_keeps_one_record_per_state_and_only_queue_counts(probe: Any) -> None:
    state = SimpleNamespace(
        status="RUNNING", action_state="HOST_ANNOUNCING", current_action_index=9
    )
    counts = {"ffi_subscribers": 2, "ffi_queued_events": 3, "rss_kib": 123}
    telemetry = probe.full.RuntimeTelemetry(lambda: state, counts=lambda: dict(counts))
    telemetry.record()
    counts["ffi_queued_events"] = 0
    telemetry.record()
    assert len(telemetry.rows) == 1 and telemetry.rows[0]["samples"] == 2
    assert telemetry.rows[0]["ffi_queued_events_peak"] == 3
    assert telemetry.rows[0]["counts"]["ffi_queued_events"] == 0
    assert "event_payload" not in json.dumps(telemetry.rows)


def test_runtime_telemetry_bounds_state_history_and_reports_truncation(probe: Any) -> None:
    state = SimpleNamespace(
        status="RUNNING", action_state="HOST_ANNOUNCING", current_action_index=0
    )
    telemetry = probe.full.RuntimeTelemetry(
        lambda: state, counts=lambda: {"ffi_subscribers": 0, "ffi_queued_events": 0}
    )
    for index in range(130):
        state.current_action_index = index
        telemetry.record()
    assert len(telemetry.rows) == 128 and telemetry.dropped == 2
    assert telemetry.rows[0]["marker"][2] == 2 and telemetry.rows[-1]["marker"][2] == 129


@pytest.mark.asyncio
async def test_runtime_telemetry_sampler_is_cancelled_before_owner_gate(probe: Any) -> None:
    baseline = set(asyncio.all_tasks())
    telemetry = probe.full.RuntimeTelemetry(
        lambda: None, counts=lambda: {"ffi_subscribers": 0, "ffi_queued_events": 0}, period=0.001
    )
    telemetry.start()
    await asyncio.sleep(0.005)
    receipt = await telemetry.close()
    assert receipt["rows"] and receipt["max_loop_lag_ms"] >= 0
    assert receipt["state_row_limit"] == 128 and receipt["observer_ffi_subscriptions_created"] == 0
    assert not (asyncio.all_tasks() - baseline)

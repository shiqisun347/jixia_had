"""Five real mixed 4v4 matches in one process/loop, only in owned QA databases."""

from __future__ import annotations

import asyncio
import gc
import hashlib
import importlib.metadata
import json
import logging
import os
import resource
from pathlib import Path
from time import monotonic
from typing import Any

from livekit import rtc
from server_full_match_probe import OwnedPostgres, run
from server_provider_media_probe import error_receipt

from jx_core.config import load_settings


def resource_snapshot() -> dict[str, Any]:
    values: dict[str, Any] = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        key = line.split(":", 1)[0]
        if key in {"VmRSS", "VmHWM", "VmSwap", "Threads"}:
            values[key] = int(line.split()[1])
    values["open_fds"] = len(list(Path("/proc/self/fd").iterdir()))
    usage = resource.getrusage(resource.RUSAGE_SELF)
    values["cpu_user_seconds"] = round(usage.ru_utime, 3)
    values["cpu_system_seconds"] = round(usage.ru_stime, 3)
    values["cumulative_peak_rss_kib"] = usage.ru_maxrss
    values["own_direct_child_pids"] = (
        Path(f"/proc/self/task/{os.getpid()}/children").read_text().split()
    )
    return values


def pending_since(baseline: set[asyncio.Task[Any]]) -> list[dict[str, Any]]:
    result = []
    for task in asyncio.all_tasks() - baseline:
        frames = [
            {"function": frame.f_code.co_qualname, "line": frame.f_lineno}
            for frame in task.get_stack(limit=6)
        ]
        result.append({"name": task.get_name(), "await_frames": frames})
    return result


def cleanup_complete(receipt: dict[str, Any]) -> bool:
    owners = (
        "manager_actors_after_close",
        "asr_receivers_after_close",
        "agent_runs_after_close",
        "host_runs_after_close",
        "postmatch_tasks_after_close",
        "new_pending_tasks_after_cleanup",
    )
    return (
        receipt.get("owned_rtc_room_deleted") is True
        and receipt.get("owned_postgres_and_audio_removed") is True
        and all(receipt.get(key) == 0 for key in owners)
    )


def completed_flow(receipt: dict[str, Any]) -> bool:
    archive = receipt.get("postmatch_audio_archive", {})
    postmatch = receipt.get("postmatch", {})
    return (
        receipt.get("status") == "passed"
        and receipt.get("match_status") == "FINISHED"
        and receipt.get("fixed_speeches") == 8
        and receipt.get("finished_events") == 1
        and set(receipt.get("free_side_kind_counts", {}))
        == {f"{side}:{kind}" for side in ("AFFIRMATIVE", "NEGATIVE") for kind in ("HUMAN", "AGENT")}
        and receipt.get("ai_judge") == "SUCCEEDED"
        and postmatch.get("phase") == "AWARDS"
        and postmatch.get("ranked_participants") == 8
        and postmatch.get("duplicate_vote_rejected") is True
        and archive.get("status") == "passed"
        and archive.get("file_status") == "READY"
        and cleanup_complete(receipt)
    )


def native_mappings() -> dict[str, str]:
    paths = {
        line.split()[-1]
        for line in Path("/proc/self/maps").read_text().splitlines()
        if "livekit" in line and ".so" in line
    }
    return {
        path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for path in sorted(paths)
        if Path(path).is_file()
    }


def pause_covered(receipt: dict[str, Any]) -> bool:
    checkpoints = receipt.get("interruption_receipts", {})
    return all(
        checkpoints.get(f"{kind}_pause_resume") == "passed"
        and isinstance(checkpoints.get(f"{kind}_natural_resume_ms"), (int, float))
        and checkpoints[f"{kind}_natural_resume_ms"] >= 2900
        for kind in ("human", "agent")
    )


async def run_batch() -> dict[str, Any]:
    settings = load_settings()
    baseline = set(asyncio.all_tasks())
    loop_id = id(asyncio.get_running_loop())
    process_id = os.getpid()
    sdk_version = importlib.metadata.version("livekit")
    if sdk_version != "1.1.20":
        raise RuntimeError("qa_unexpected_deployed_sdk")
    summary: dict[str, Any] = {
        "scope": "five_serial_full_mixed_4v4_one_process_one_event_loop",
        "requested_rounds": 5,
        "same_process_id": process_id,
        "same_event_loop_id": loop_id,
        "sdk_version": sdk_version,
        "sdk_module": rtc.__file__,
        "runtime_objects_recreated_each_match": True,
        "production_rooms_matches_users_created": 0,
        "production_config_or_services_changed": False,
        "initial_resources": resource_snapshot(),
        "rounds": [],
    }
    print(json.dumps({"batch_started": summary}, sort_keys=True), flush=True)
    for number in range(1, 6):
        owned = OwnedPostgres()
        started = monotonic()
        pause_resume = number in {1, 3, 5}
        receipt: dict[str, Any] = {"status": "failed"}
        print(json.dumps({"round_started": number, "pause_resume": pause_resume}), flush=True)
        try:
            url = owned.start()
            owned.migrate(url)
            receipt = await run(
                settings, owned, url, pause_resume=pause_resume, topical_microphones=True
            )
        except Exception as error:
            receipt = error_receipt(error)
        finally:
            try:
                receipt["owned_postgres_and_audio_removed"] = owned.close()
            except Exception as error:
                receipt["owned_postgres_and_audio_removed"] = False
                receipt["owned_cleanup_error"] = error_receipt(error)
            gc.collect()
            await asyncio.sleep(0.5)
        pending = pending_since(baseline)
        row = {
            "round": number,
            "pause_resume": pause_resume,
            "elapsed_ms": round((monotonic() - started) * 1000),
            "process_id": os.getpid(),
            "event_loop_id": id(asyncio.get_running_loop()),
            "resources": resource_snapshot(),
            "global_new_pending_tasks": pending,
            "native_library_mappings": native_mappings(),
            "receipt": receipt,
        }
        row["complete_passed"] = (
            completed_flow(receipt)
            and not pending
            and row["process_id"] == process_id
            and row["event_loop_id"] == loop_id
            and (not pause_resume or pause_covered(receipt))
        )
        summary["rounds"].append(row)
        print(json.dumps({"round_terminal": row}, ensure_ascii=False, sort_keys=True), flush=True)
        if pending or not cleanup_complete(receipt):
            summary["stopped_reason"] = "qa_cleanup_incomplete"
            break
    summary["completed_rounds"] = len(summary["rounds"])
    summary["passed_rounds"] = sum(row["complete_passed"] for row in summary["rounds"])
    summary["status"] = "passed" if summary["passed_rounds"] == 5 else "failed"
    summary["final_resources"] = resource_snapshot()
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    try:
        result = asyncio.run(run_batch())
    except Exception as error:
        result = error_receipt(error)
    print(json.dumps({"batch_receipt": result}, ensure_ascii=False, sort_keys=True), flush=True)
    raise SystemExit(0 if result.get("status") == "passed" else 1)

"""Bounded aggregate process/participant telemetry for the sole owned QA instance."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path

import psycopg
from livekit import api

from jx_core.config import load_settings


async def run() -> None:
    candidates = []
    for pid in subprocess.check_output(["pgrep", "-x", "postgres"], text=True).split():
        try:
            args = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")
        except FileNotFoundError:
            continue
        if (
            "-D" in args
            and "-p" in args
            and args[args.index("-D") + 1].startswith("/tmp/jx-full-match-qa-")
        ):
            candidates.append(int(args[args.index("-p") + 1]))
    if len(candidates) != 1:
        print(
            json.dumps({"status": "unavailable", "reason": "sole_owned_instance_missing"}),
            flush=True,
        )
        return
    processes = []
    for pid in subprocess.check_output(
        ["pgrep", "-f", "server_full_match_probe.py"], text=True
    ).split():
        try:
            args = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")
        except FileNotFoundError:
            continue
        if (
            len(args) > 1
            and Path(args[0]).name == "python3"
            and args[1].endswith("scripts/qa/probes/server_full_match_probe.py")
        ):
            processes.append(pid)
    if len(processes) != 1:
        print(
            json.dumps({"status": "unavailable", "reason": "sole_owned_python_missing"}), flush=True
        )
        return
    process = Path(f"/proc/{processes[0]}")
    settings = load_settings()
    client = api.LiveKitAPI(
        settings.livekit_url,
        settings.livekit_api_key.get_secret_value(),
        settings.livekit_api_secret.get_secret_value(),
    )
    try:
        with psycopg.connect(
            host="127.0.0.1",
            port=candidates[0],
            user="jx_qa",
            dbname="jx_debate_qa_agent_faults",
            autocommit=True,
        ) as conn:
            for _ in range(36):
                row = conn.execute(
                    "SELECT id, status, runtime_snapshot FROM matches LIMIT 1"
                ).fetchone()
                if row is None:
                    await asyncio.sleep(5)
                    continue
                status = {}
                for line in (process / "status").read_text().splitlines():
                    if ":" in line:
                        key, value = line.split(":", 1)
                        if key in {"Threads", "VmRSS", "VmHWM", "FDSize"}:
                            status[key] = int(value.strip().split()[0])
                fd_count = len(list((process / "fd").iterdir()))
                nofile = next(
                    line.split()[3:5]
                    for line in (process / "limits").read_text().splitlines()
                    if line.startswith("Max open files")
                )
                try:
                    participants = await client.room.list_participants(
                        api.ListParticipantsRequest(room=f"jx-match-{row[0]}")
                    )
                    participant_count = len(participants.participants)
                except Exception:
                    participant_count = None
                print(
                    json.dumps(
                        {
                            "telemetry": {
                                "match_status": row[1],
                                "action_index": row[2].get("current_action_index"),
                                "fd_count": fd_count,
                                "nofile_soft": nofile[0],
                                "nofile_hard": nofile[1],
                                "process": status,
                                "owned_rtc_participants": participant_count,
                            }
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                if row[1] in {"FINISHED", "ERROR", "TERMINATED"}:
                    return
                await asyncio.sleep(5)
    finally:
        await client.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    try:
        asyncio.run(run())
    except Exception as error:
        print(
            json.dumps({"status": "unavailable", "error_class": type(error).__name__}), flush=True
        )

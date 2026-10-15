"""Retain one failed owned synthetic host asset on server, then decode that same file."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from time import monotonic
from typing import Any

import psycopg

from jx_core.agent.audio import IncrementalOpusDecoder


def retain() -> dict[str, Any]:
    candidates = []
    for pid in subprocess.check_output(["pgrep", "-x", "postgres"], text=True).split():
        try:
            args = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")
        except FileNotFoundError:
            continue
        if "-D" not in args or "-p" not in args:
            continue
        data = Path(args[args.index("-D") + 1])
        if str(data).startswith("/tmp/jx-full-match-qa-") and data.name == "postgres":
            candidates.append((data.parent, int(args[args.index("-p") + 1])))
    if len(candidates) != 1:
        return {"status": "not_retained", "reason": "sole_owned_instance_missing"}
    root, port = candidates[0]
    with psycopg.connect(
        host="127.0.0.1",
        port=port,
        user="jx_qa",
        dbname="jx_debate_qa_agent_faults",
        autocommit=True,
    ) as conn:
        guard = conn.execute(
            "SELECT count(*) FILTER (WHERE username LIKE 'qa-%'), "
            "count(*) FILTER (WHERE username='admin' AND role='ADMIN'), "
            "bool_and(username LIKE 'qa-%' OR (username='admin' AND role='ADMIN')) FROM users"
        ).fetchone()
        if guard != (5, 1, True):
            return {"status": "not_retained", "reason": "synthetic_ownership_guard"}
        deadline = monotonic() + 240
        while monotonic() < deadline:
            row = conn.execute("SELECT status FROM matches LIMIT 1").fetchone()
            if row and row[0] == "FINISHED":
                return {"status": "not_retained", "reason": "match_finished"}
            if row and row[0] == "ERROR":
                asset = conn.execute(
                    "SELECT storage_path, byte_count FROM host_audio_assets "
                    "WHERE segment_key='stage-9-end' AND status='READY' LIMIT 1"
                ).fetchone()
                if asset is None:
                    return {"status": "not_retained", "reason": "closing_asset_missing"}
                source = (root / "host-audio" / asset[0]).resolve()
                if (
                    not source.is_relative_to(root.resolve())
                    or not 0 < source.stat().st_size <= 1024 * 1024
                ):
                    return {"status": "not_retained", "reason": "asset_ownership_or_size_guard"}
                directory = Path(tempfile.mkdtemp(prefix="jx-host-diagnostic-"))
                target = directory / "failed-closing-host.ogg"
                shutil.copyfile(source, target)
                target.chmod(0o600)
                return {
                    "status": "retained",
                    "retained_server_directory": str(directory),
                    "filename": target.name,
                    "byte_count": target.stat().st_size,
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
            time.sleep(0.1)
    return {"status": "not_retained", "reason": "observation_timeout"}


async def decode(receipt: dict[str, Any]) -> None:
    decoder = IncrementalOpusDecoder()
    path = Path(receipt["retained_server_directory"]) / receipt["filename"]
    samples = 0
    try:
        await decoder.start(path)
        decoder.notify_written(path.stat().st_size)
        decoder.finish_input()
        async with asyncio.timeout(10):
            async for pcm in decoder.frames():
                samples += len(pcm) // 2
    except Exception as error:
        receipt["same_file_decode"] = "failed"
        receipt["decoder_exception_class"] = type(error).__name__
        frames = []
        tb = error.__traceback__
        while tb:
            frames.append({"function": tb.tb_frame.f_code.co_name, "line": tb.tb_lineno})
            tb = tb.tb_next
        receipt["exception_frames"] = frames[-8:]
    else:
        receipt["same_file_decode"] = "passed"
        receipt["decoded_duration_ms"] = samples // 48
    finally:
        await asyncio.wait_for(decoder.close(), 3)


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    try:
        result = retain()
        if result["status"] == "retained":
            asyncio.run(decode(result))
    except Exception as error:
        result = {"status": "failed", "error_class": type(error).__name__}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)

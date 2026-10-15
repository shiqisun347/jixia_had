"""Capture the sole running owned QA judge snapshot in server memory; no raw output."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from time import monotonic
from typing import Any

import psycopg
from server_judge_shape_probe import shape
from server_provider_media_probe import error_receipt

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ops"))
from provider_chain_probe import _stream, load_material  # noqa: E402

from jx_core.config import load_settings  # noqa: E402
from jx_core.database import Database  # noqa: E402


def owned_snapshot() -> dict[str, Any]:
    candidates = []
    pids = subprocess.check_output(["pgrep", "-x", "postgres"], text=True).split()
    for pid in pids:
        try:
            arguments = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")
        except FileNotFoundError:
            continue
        if "-D" not in arguments or "-p" not in arguments:
            continue
        data = arguments[arguments.index("-D") + 1]
        if data.startswith("/tmp/jx-full-match-qa-") and data.endswith("/postgres"):
            candidates.append((data, int(arguments[arguments.index("-p") + 1])))
    if len(candidates) != 1:
        raise RuntimeError("qa_sole_owned_postgres_required")
    _, port = candidates[0]
    with psycopg.connect(
        host="127.0.0.1",
        port=port,
        user="jx_qa",
        dbname="jx_debate_qa_agent_faults",
        autocommit=True,
    ) as connection:
        own = connection.execute(
            "SELECT count(*) FILTER (WHERE username LIKE 'qa-%'), "
            "count(*) FILTER (WHERE username = 'admin' AND role = 'ADMIN'), "
            "bool_and(username LIKE 'qa-%' OR (username = 'admin' AND role = 'ADMIN')) FROM users"
        ).fetchone()
        # Migration 0016 installs one default admin into this new empty owned cluster.
        if own != (5, 1, True):
            raise RuntimeError("qa_synthetic_user_ownership_required")
        deadline = monotonic() + 80
        while monotonic() < deadline:
            row = connection.execute(
                "SELECT input_snapshot FROM judge_results ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                return row[0]
            import time

            time.sleep(0.1)
    raise RuntimeError("qa_owned_judge_snapshot_missing")


async def run(snapshot: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    database = Database(settings.database_url_value)
    try:
        material = await load_material(database)
        frozen = snapshot["_judge_runtime"]
        if frozen["model"]["model_id"] != material.model_id:
            raise RuntimeError("qa_current_model_identity_mismatch")
        material = replace(material, judge_prompt=frozen["judge_prompt"])
        public_snapshot = {key: value for key, value in snapshot.items() if key != "_judge_runtime"}
        receipt: dict[str, Any] = {
            "probe": "same_owned_match_judge_token_control",
            "history_entries": len(public_snapshot["history"]),
            "llm_model": material.model_id,
        }
        messages = [
            {
                "role": "system",
                "content": frozen.get("system_prompt") or "你是客观的中文辩论裁判。",
            },
            {
                "role": "user",
                "content": material.judge_prompt
                + "\n"
                + json.dumps(public_snapshot, ensure_ascii=False),
            },
        ]
        for tokens in (1600, 4096):
            response, metrics = await _stream(
                material,
                messages=messages,
                max_tokens=tokens,
                generation_params=frozen["model"]["generation_params"],
                database=database,
                call_kind="JUDGE",
            )
            receipt[f"max_tokens_{tokens}"] = {
                **metrics,
                **shape(response, public_snapshot["participants"]),
            }
        return receipt
    finally:
        await database.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    try:
        result = asyncio.run(run(owned_snapshot()))
    except Exception as error:
        result = error_receipt(error)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)

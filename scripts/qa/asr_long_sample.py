"""Run a paced long-form Fun-ASR acceptance sample without exposing credentials."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import wave
from pathlib import Path
from typing import Any
from uuid import uuid4

from jx_core.asr.protocol import FunAsrConnection, SegmentResult
from jx_core.asr.session import AsrSpeechSession
from jx_core.config import load_settings

FRAME_BYTES = 3200
FRAME_SECONDS = 0.1
NORMALIZE_PATTERN = re.compile(r"[^\u3400-\u9fffA-Za-z0-9]")


def normalize_transcript(value: str) -> str:
    return NORMALIZE_PATTERN.sub("", value).casefold()


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def read_pcm(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as source:
        if source.getframerate() != 16000 or source.getnchannels() != 1:
            raise ValueError("sample_must_be_16khz_mono")
        if source.getsampwidth() != 2 or source.getcomptype() != "NONE":
            raise ValueError("sample_must_be_pcm_s16le")
        frames = source.readframes(source.getnframes())
        duration_ms = source.getnframes() // 16
    return frames, duration_ms


async def run_sample(wav_path: Path, reference_path: Path) -> dict[str, Any]:
    settings = load_settings()
    if settings.asr_api_key is None:
        raise RuntimeError("asr_api_key_missing")
    pcm, expected_duration_ms = read_pcm(wav_path)
    reference = reference_path.read_text(encoding="utf-8")
    speech_id = uuid4()
    segments: list[dict[str, Any]] = []

    async def on_interim(_: object, __: int, ___: str) -> None:
        return

    async def on_segment(
        _: object,
        segment_no: int,
        result: SegmentResult,
        sample_count: int,
    ) -> None:
        segments.append(
            {
                "segment_no": segment_no,
                "sample_count": sample_count,
                "first_interim_latency_ms": result.first_interim_latency_ms,
                "final_latency_ms": result.final_latency_ms,
                "text_chars": len(normalize_transcript(result.final_text)),
            }
        )

    connection = FunAsrConnection(
        url=settings.asr_ws_url,
        api_key=settings.asr_api_key.get_secret_value(),
        model=settings.asr_model,
        workspace_id=settings.asr_workspace_id,
    )
    session = AsrSpeechSession(
        speech_id=speech_id,
        connection=connection,
        on_interim=on_interim,
        on_segment=on_segment,
    )
    started = asyncio.get_running_loop().time()
    task = asyncio.create_task(session.run(), name="qa-asr-long-sample")
    try:
        await session.wait_ready()
        for offset in range(0, len(pcm), FRAME_BYTES):
            session.feed_pcm(pcm[offset : offset + FRAME_BYTES])
            await asyncio.sleep(FRAME_SECONDS)
        await session.finish()
        result = await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await connection.close()
    elapsed_ms = int((asyncio.get_running_loop().time() - started) * 1000)
    normalized_reference = normalize_transcript(reference)
    normalized_prediction = normalize_transcript(result.final_text)
    edits = edit_distance(normalized_reference, normalized_prediction)
    return {
        "source": "AISHELL-1 S0770, single-speaker real Mandarin",
        "speech_id": str(speech_id),
        "expected_duration_ms": expected_duration_ms,
        "reported_duration_ms": result.audio_duration_ms,
        "wall_elapsed_ms": elapsed_ms,
        "segment_count": len(result.segments),
        "first_interim_latency_ms": result.first_interim_latency_ms,
        "segments": segments,
        "reference_chars": len(normalized_reference),
        "prediction_chars": len(normalized_prediction),
        "edit_distance": edits,
        "cer": edits / max(1, len(normalized_reference)),
        "reference_sha256": hashlib.sha256(normalized_reference.encode()).hexdigest(),
        "prediction_sha256": hashlib.sha256(normalized_prediction.encode()).hexdigest(),
        "reference_text": reference,
        "prediction_text": result.final_text,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = asyncio.run(run_sample(args.wav, args.reference))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {key: value for key, value in result.items() if not key.endswith("_text")}
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Generate an anonymized 11-voice listening pack through the production TTS adapter."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import shutil
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy import text

from jx_core.agent.tts import QwenTtsConnection, TtsProviderError
from jx_jobs.config import load_settings
from jx_jobs.database import Database

VOICE_NAMES = (
    "龙安灵希",
    "龙弦星岚",
    "龙昕蕊璇",
    "龙晴霄湘",
    "龙涟霓蓉",
    "龙漪暄珺",
    "龙璨竹月",
    "龙翼暮凌",
    "龙莹松柳",
    "龙蓉桃涟",
    "龙露柳澈",
)
TEXTS = {
    "T1": (
        "人工智能降低了表达门槛，却没有替创作者回答为什么要表达。工具扩展能力，价值判断仍然来自人。"
    ),
    "T2": (
        "对方把效率提升等同于意义削弱，这混淆了作品生产与创作动机。"
        "被节省的时间，恰好能用于更深入的观察和选择。"
    ),
    "T3": (
        "今天的分歧不在于人工智能是否强大，而在于人是否仍承担判断、责任与审美。"
        "只要答案是肯定的，创作者的意义就没有消失。"
    ),
}
RATES = {"low": 0.9, "high": 1.1}
WORKERS = 5


def load_previous_metrics(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    values = payload.get("metrics") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        if isinstance(value, dict) and isinstance(value.get("filename"), str):
            result[str(value["filename"])] = value
    return result


def previous_latency(
    previous: dict[str, Any] | None,
    *,
    duration_ms: int,
    byte_count: int,
    key: str,
) -> int | None:
    if (
        previous is None
        or previous.get("duration_ms") != duration_ms
        or previous.get("byte_count") != byte_count
    ):
        return None
    value = previous.get(key)
    return int(value) if isinstance(value, int) else None


async def load_voices(database: Database) -> list[dict[str, str]]:
    async with database.session_factory() as session:
        rows = list(
            (
                await session.execute(
                    text(
                        "SELECT name, kind, provider_voice FROM voice_profiles "
                        "WHERE name = ANY(:names) AND status = 'ENABLED'"
                    ),
                    {"names": list(VOICE_NAMES)},
                )
            ).mappings()
        )
    indexed = {str(row["name"]): row for row in rows}
    missing = [name for name in VOICE_NAMES if name not in indexed]
    if missing:
        raise RuntimeError(f"voice_profiles_missing:{','.join(missing)}")
    return [
        {
            "name": name,
            "kind": str(indexed[name]["kind"]),
            "provider_voice": str(indexed[name]["provider_voice"]),
        }
        for name in VOICE_NAMES
    ]


def audio_duration_ms(path: Path) -> int:
    output = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        text=True,
    )
    return round(float(output.strip()) * 1000)


def create_tts_client(settings: Any) -> QwenTtsConnection:
    return QwenTtsConnection(
        url=settings.tts_ws_url,
        api_key=settings.dashscope_api_key.get_secret_value(),
        workspace_id=settings.dashscope_workspace or None,
    )


async def synthesize_with_retry(
    client: QwenTtsConnection,
    settings: Any,
    *,
    text_value: str,
    provider_voice: str,
    rate: float,
    output_path: Path,
) -> tuple[int, int | None, int | None, QwenTtsConnection]:
    if output_path.is_file() and output_path.stat().st_size > 0:
        return audio_duration_ms(output_path), None, None, client
    for attempt in (1, 2):
        audio = bytearray()

        async def chunks():
            yield text_value

        async def on_audio(chunk: bytes, target: bytearray = audio) -> None:
            target.extend(chunk)

        try:
            result = await client.synthesize(
                chunks(), voice=provider_voice, rate=rate, on_audio=on_audio
            )
            temporary_path = output_path.with_suffix(f".attempt-{attempt}.part")
            temporary_path.write_bytes(audio)
            os.replace(temporary_path, output_path)
            return (
                audio_duration_ms(output_path),
                result.first_audio_latency_ms,
                result.completed_latency_ms,
                client,
            )
        except (TtsProviderError, OSError, subprocess.SubprocessError):
            output_path.unlink(missing_ok=True)
            output_path.with_suffix(f".attempt-{attempt}.part").unlink(missing_ok=True)
            await client.close()
            if attempt == 2:
                raise
            client = create_tts_client(settings)
    raise RuntimeError("tts_retry_exhausted")


async def generate(output_dir: Path) -> dict[str, Any]:
    settings = load_settings()
    if settings.dashscope_api_key is None or not settings.tts_ws_url:
        raise RuntimeError("tts_configuration_missing")
    database = Database(settings.database_url_value)
    try:
        voices = await load_voices(database)
    finally:
        await database.dispose()

    shuffled = list(voices)
    random.Random(20260804).shuffle(shuffled)
    mapping = [{"code": f"V{index:02d}", **voice} for index, voice in enumerate(shuffled, start=1)]
    audio_dir = output_dir / "listener-pack" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    previous_metrics = load_previous_metrics(output_dir / "metrics.json")
    jobs: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    metrics: list[dict[str, Any]] = []
    for voice in mapping:
        for text_id, text_value in TEXTS.items():
            for rate_name, rate in RATES.items():
                jobs.put_nowait(
                    {
                        **voice,
                        "text_id": text_id,
                        "text": text_value,
                        "rate_name": rate_name,
                        "rate": rate,
                    }
                )

    async def worker() -> None:
        client = create_tts_client(settings)
        try:
            while True:
                try:
                    item = jobs.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    filename = f"{item['code']}-{item['text_id']}-{item['rate_name']}.opus"
                    path = audio_dir / filename
                    (
                        duration_ms,
                        first_audio_latency_ms,
                        completed_latency_ms,
                        client,
                    ) = await synthesize_with_retry(
                        client,
                        settings,
                        text_value=str(item["text"]),
                        provider_voice=str(item["provider_voice"]),
                        rate=float(item["rate"]),
                        output_path=path,
                    )
                    byte_count = path.stat().st_size
                    previous = previous_metrics.get(filename)
                    if first_audio_latency_ms is None:
                        first_audio_latency_ms = previous_latency(
                            previous,
                            duration_ms=duration_ms,
                            byte_count=byte_count,
                            key="first_audio_latency_ms",
                        )
                    if completed_latency_ms is None:
                        completed_latency_ms = previous_latency(
                            previous,
                            duration_ms=duration_ms,
                            byte_count=byte_count,
                            key="completed_latency_ms",
                        )
                    metrics.append(
                        {
                            "filename": filename,
                            "code": item["code"],
                            "text_id": item["text_id"],
                            "rate_name": item["rate_name"],
                            "rate": item["rate"],
                            "duration_ms": duration_ms,
                            "first_audio_latency_ms": first_audio_latency_ms,
                            "completed_latency_ms": completed_latency_ms,
                            "byte_count": byte_count,
                        }
                    )
                finally:
                    jobs.task_done()
        finally:
            await client.close()

    await asyncio.gather(*(worker() for _ in range(WORKERS)))
    if len(metrics) != len(mapping) * len(TEXTS) * len(RATES):
        raise RuntimeError("blind_pack_incomplete")
    metrics.sort(key=lambda item: str(item["filename"]))

    listener_dir = output_dir / "listener-pack"
    scorecard_path = listener_dir / "scorecard-template.csv"
    with scorecard_path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.writer(output)
        writer.writerow(
            [
                "listener_id",
                "voice_code",
                "clarity_1_5",
                "naturalness_1_5",
                "consistency_1_5",
                "comments",
            ]
        )
        for voice in mapping:
            writer.writerow(["", voice["code"], "", "", "", ""])
    (listener_dir / "instructions.md").write_text(
        "# 稷下音色盲听\n\n"
        "1. 每名听众使用耳机，按随机顺序试听 `audio/` 中的文件。\n"
        "2. 每个 V 编号包含三段辩论文本和低/高两种语速。\n"
        "3. 对每个 V 编号整体评价：清晰度、自然度、跨文本与语速一致性，均为 1–5 分。\n"
        "4. 不讨论或猜测音色名称；独立填写一份 scorecard。\n"
        "5. listener_id 使用不含姓名的唯一编号；每名听众另存一份 CSV，不合并或删除行。\n"
        "6. 至少 10 名中文听众。通过线：清晰度 ≥4.0、自然度 ≥3.5、一致性 ≥4.0。\n",
        encoding="utf-8",
    )
    (listener_dir / "texts.json").write_text(
        json.dumps(TEXTS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "mapping.csv").open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=["code", "name", "kind", "provider_voice"])
        writer.writeheader()
        writer.writerows(mapping)
    (output_dir / "mapping.csv").chmod(0o600)
    summary: dict[str, Any] = {
        "voice_count": len(mapping),
        "sample_count": len(metrics),
        "total_bytes": sum(int(item["byte_count"]) for item in metrics),
        "duration_ms": sum(int(item["duration_ms"]) for item in metrics),
        "first_audio_latency_count": sum(
            item["first_audio_latency_ms"] is not None for item in metrics
        ),
        "completed_latency_count": sum(
            item["completed_latency_ms"] is not None for item in metrics
        ),
        "metrics": metrics,
    }
    (output_dir / "metrics.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "metrics.json").chmod(0o600)
    archive = shutil.make_archive(str(output_dir / "jixia-voice-blind-pack"), "zip", listener_dir)
    summary["archive"] = archive
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = asyncio.run(generate(args.output_dir))
    print(
        json.dumps(
            {key: value for key, value in summary.items() if key != "metrics"},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

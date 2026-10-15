"""Compare documented Bailian candidates using synthetic, redacted probes only."""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import replace
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

import httpx
from scripts.ops.provider_chain_probe import ProviderMaterial, load_material

from jx_core.agent.audio import decode_ogg_opus_pcm
from jx_core.agent.tts import QwenTtsConnection
from jx_core.asr.protocol import FunAsrConnection
from jx_core.asr.session import AsrSpeechSession
from jx_core.config import load_settings
from jx_core.database import Database

TEXT = "稷下平台语音链路测试，表达清晰，连接正常。"
SNAPSHOT = "qwen3.7-flash-2026-07-15"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    passed = [row for row in rows if row["status"] == "passed"]
    errors: dict[str, int] = {}
    for row in rows:
        if row["status"] != "passed":
            code = row["error_code"]
            errors[code] = errors.get(code, 0) + 1
    result: dict[str, Any] = {"requested": len(rows), "passed": len(passed), "errors": errors}
    for key in ("first_ms", "complete_ms"):
        values = sorted(row[key] for row in passed if row.get(key) is not None)
        result[key] = (
            {
                "p50": values[math.ceil(len(values) * 0.5) - 1],
                "p95": values[math.ceil(len(values) * 0.95) - 1],
                "max": values[-1],
            }
            if values
            else None
        )
    return result


def messages_for(index: int) -> list[dict[str, str]]:
    if index % 3 == 0:
        prompt = '只输出 JSON：{"speak":true,"reason":"需要回应对方观点"}'
    else:
        context = "对方认为结果是奋斗价值的唯一尺度。我方认为过程塑造能力并产生长期价值。" * (
            120 if index % 3 == 2 else 1
        )
        prompt = context + "\n请用约100字中文回应，立场清晰，不要标题。"
    return [
        {"role": "system", "content": "你是中文辩论助手。直接输出答案。"},
        {"role": "user", "content": prompt},
    ]


async def llm_probe(
    client: httpx.AsyncClient, material: ProviderMaterial, protocol: str, index: int
) -> dict[str, Any]:
    messages = messages_for(index)
    headers = {"Authorization": f"Bearer {material.api_key}"}
    if protocol == "chat":
        url = material.base_url.rstrip("/") + "/chat/completions"
        body: dict[str, Any] = {
            "model": material.model_id,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": 180,
            "temperature": 0,
            "enable_thinking": False,
        }
    else:
        origin = urlsplit(material.base_url)
        url = f"{origin.scheme}://{origin.netloc}/api/v1/services/aigc/text-generation/generation"
        headers["X-DashScope-SSE"] = "enable"
        body = {
            "model": material.model_id,
            "input": {"messages": messages},
            "parameters": {
                "incremental_output": True,
                "result_format": "message",
                "max_tokens": 180,
                "temperature": 0,
                "enable_thinking": False,
            },
        }
    started = monotonic()
    first: int | None = None
    parts: list[str] = []
    try:
        async with asyncio.timeout(30):
            async with client.stream("POST", url, json=body, headers=headers) as response:
                if response.status_code != 200:
                    return {"status": "failed", "error_code": f"http_{response.status_code}"}
                deadline = started + 10
                lines = response.aiter_lines()
                while True:
                    try:
                        async with asyncio.timeout(max(0, deadline - monotonic())):
                            line = await anext(lines)
                    except StopAsyncIteration:
                        break
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    event = json.loads(payload)
                    if event.get("error") or event.get("code"):
                        raise RuntimeError("provider_error_event")
                    if protocol == "chat":
                        choices = event.get("choices") or []
                        delta = choices[0].get("delta", {}).get("content", "") if choices else ""
                    else:
                        choices = event.get("output", {}).get("choices") or []
                        content = (
                            choices[0].get("message", {}).get("content", []) if choices else []
                        )
                        delta = "".join(item.get("text", "") for item in content)
                    if delta:
                        if first is None:
                            first = round((monotonic() - started) * 1000)
                        parts.append(delta)
                        deadline = monotonic() + 10
        text = "".join(parts)
        if not text.strip():
            raise RuntimeError("empty_stream")
        if index % 3 == 0 and json.loads(text).get("speak") is not True:
            raise RuntimeError("invalid_json_result")
        return {
            "status": "passed",
            "first_ms": first,
            "complete_ms": round((monotonic() - started) * 1000),
        }
    except Exception as error:
        return {
            "status": "failed",
            "error_code": (str(error) if type(error) is RuntimeError else type(error).__name__),
        }


async def audio_tts(model: str, voice: str) -> tuple[dict[str, Any], bytes]:
    settings = load_settings()
    assert settings.tts_api_key is not None
    client = QwenTtsConnection(
        url=settings.tts_ws_url,
        api_key=settings.tts_api_key.get_secret_value(),
        model=model,
        workspace_id=settings.tts_workspace_id,
    )
    audio = bytearray()

    async def chunks():
        yield TEXT

    async def receive(chunk: bytes) -> None:
        audio.extend(chunk)

    started = monotonic()
    try:
        async with asyncio.timeout(25):
            result = await client.synthesize(chunks(), voice=voice, rate=1.0, on_audio=receive)
        pcm = decode_ogg_opus_pcm(bytes(audio))
        if not pcm:
            raise RuntimeError("empty_audio")
        return {
            "status": "passed",
            "first_ms": result.first_audio_latency_ms,
            "complete_ms": round((monotonic() - started) * 1000),
        }, pcm
    finally:
        await client.close()


async def audio_asr(model: str, pcm: bytes) -> dict[str, Any]:
    from uuid import uuid4

    settings = load_settings()
    assert settings.asr_api_key is not None
    client = FunAsrConnection(
        url=settings.asr_ws_url,
        api_key=settings.asr_api_key.get_secret_value(),
        model=model,
        workspace_id=settings.asr_workspace_id,
    )
    session = AsrSpeechSession(
        speech_id=uuid4(),
        connection=client,
        on_interim=lambda *args: asyncio.sleep(0),
        on_segment=lambda *args: asyncio.sleep(0),
    )
    task = asyncio.create_task(session.run())

    async def feed() -> None:
        for offset in range(0, len(pcm), 3200):
            session.feed_pcm(pcm[offset : offset + 3200])
            await asyncio.sleep(0.1)
        await session.finish()

    feeder = asyncio.create_task(feed())
    started = monotonic()
    try:
        async with asyncio.timeout(25):
            await session.wait_ready()
            await feeder
            result = await task
        if not result.final_text.strip():
            raise RuntimeError("empty_transcript")
        return {
            "status": "passed",
            "first_ms": result.first_interim_latency_ms,
            "complete_ms": round((monotonic() - started) * 1000),
            "transcript_chars": len(result.final_text),
        }
    finally:
        for pending in (task, feeder):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, feeder, return_exceptions=True)
        await client.close()


async def main() -> None:
    database = Database(load_settings().database_url_value)
    try:
        material = await load_material(database)
        async with httpx.AsyncClient(timeout=httpx.Timeout(10), trust_env=False) as client:
            semaphore = asyncio.Semaphore(4)

            async def one(protocol: str, model: str, index: int):
                async with semaphore:
                    return await llm_probe(
                        client, replace(material, model_id=model), protocol, index
                    )

            for protocol in ("chat", "native"):
                for model in (material.model_id, SNAPSHOT):
                    rows = await asyncio.gather(
                        *[one(protocol, model, index) for index in range(20)]
                    )
                    print(
                        json.dumps(
                            {
                                "kind": "llm",
                                "protocol": protocol,
                                "model": model,
                                "metrics": summarize(rows),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
        pcm = b""
        for model, voice in (
            ("qwen-audio-3.1-tts-flash", "yuxiaoyun_v3.1"),
            ("qwen-audio-3.0-tts-flash", "longanhuan_v3.6"),
        ):
            rows = []
            for _ in range(3):
                try:
                    row, audio = await audio_tts(model, voice)
                    if not pcm:
                        pcm = audio
                except Exception as error:
                    row = {
                        "status": "failed",
                        "error_code": getattr(error, "code", type(error).__name__),
                    }
                rows.append(row)
            print(
                json.dumps(
                    {"kind": "tts", "model": model, "voice": voice, "metrics": summarize(rows)}
                ),
                flush=True,
            )
        for model in ("qwen-audio-3.1-asr-flash-streaming", "qwen-audio-3.0-asr-flash-streaming"):
            rows = []
            for _ in range(3):
                try:
                    row = await audio_asr(model, pcm)
                except Exception as error:
                    row = {
                        "status": "failed",
                        "error_code": getattr(error, "code", type(error).__name__),
                    }
                rows.append(row)
            print(
                json.dumps({"kind": "asr", "model": model, "metrics": summarize(rows)}), flush=True
            )
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())

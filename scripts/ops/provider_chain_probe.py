"""Run redacted, read-only probes against the configured production providers."""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from uuid import uuid4

from livekit import api, rtc
from sqlalchemy import select

from jx_core.agent.audio import decode_ogg_opus_pcm
from jx_core.agent.llm import OpenAIStreamingClient
from jx_core.agent.tts import QwenTtsConnection
from jx_core.asr.protocol import FunAsrConnection, SegmentResult
from jx_core.asr.session import AsrSpeechSession
from jx_core.config import load_settings
from jx_core.data_capture.provider import PROVIDER_CAPTURE_VERSION, ProviderCallCapture
from jx_core.data_capture.provider_persistence import persist_provider_capture
from jx_core.database import Database
from jx_core.models import AgentProfile, ExternalCall, JudgeProfile, ModelProfile, VoiceProfile
from jx_core.postmatch import _parse_result  # pyright: ignore[reportPrivateUsage]
from jx_core.security.crypto import decrypt_secret

PROBE_TEXT = "稷下平台语音链路测试，表达清晰，连接正常。"


@dataclass(frozen=True, slots=True)
class ProviderMaterial:
    model_name: str
    base_url: str
    model_id: str
    api_key: str
    model_params: dict[str, Any]
    voice: str
    voice_rate: float
    judge_system_prompt: str
    judge_prompt: str
    judge_params: dict[str, Any]


async def persist_probe_capture(
    database: Database | None,
    *,
    call_kind: str,
    provider: str,
    operation: str,
    model: str | None,
    voice: str | None,
    capture: ProviderCallCapture,
    started_at: datetime,
    error: Exception | None,
) -> None:
    if database is None:
        return
    async with database.session_factory() as session:
        async with session.begin():
            call = ExternalCall(
                call_kind=call_kind,
                provider=provider,
                operation=operation,
                model=model,
                voice=voice,
                attempt_no=1,
                status="FAILED" if error else "SUCCEEDED",
                capture_version=PROVIDER_CAPTURE_VERSION,
                captured_at=datetime.now(UTC),
                source_kind="OPS_PROBE",
                logical_call_id=uuid4(),
                started_at=started_at,
                completed_at=datetime.now(UTC),
                error_code=(str(getattr(error, "code", type(error).__name__)) if error else None),
            )
            session.add(call)
            await persist_provider_capture(session, call, capture)


async def load_material(database: Database) -> ProviderMaterial:
    settings = load_settings()
    if settings.llm_key_encryption_key is None:
        raise RuntimeError("llm_encryption_key_missing")
    async with database.session_factory() as session:
        agent = await session.scalar(
            select(AgentProfile)
            .where(AgentProfile.status == "ENABLED")
            .order_by(AgentProfile.name)
            .limit(1)
        )
        judge = await session.scalar(
            select(JudgeProfile)
            .where(JudgeProfile.status == "ENABLED")
            .order_by(JudgeProfile.created_at)
            .limit(1)
        )
        if agent is None or judge is None:
            raise RuntimeError("provider_profiles_missing")
        model = await session.get(ModelProfile, agent.model_profile_id)
        judge_model = await session.get(ModelProfile, judge.model_profile_id)
        voice = await session.get(VoiceProfile, agent.voice_profile_id)
        if model is None or judge_model is None or voice is None:
            raise RuntimeError("provider_profile_reference_missing")
        if model.id != judge_model.id:
            raise RuntimeError("probe_requires_shared_experiment_model")
        if (
            model.status != "ENABLED"
            or not model.base_url
            or not model.model_id
            or model.api_key_ciphertext is None
            or model.api_key_nonce is None
            or voice.status != "ENABLED"
        ):
            raise RuntimeError("provider_profile_incomplete")
        key = decrypt_secret(
            model.api_key_ciphertext,
            model.api_key_nonce,
            settings.llm_key_encryption_key.get_secret_value(),
        )
        return ProviderMaterial(
            model_name=model.name,
            base_url=model.base_url,
            model_id=model.model_id,
            api_key=key,
            model_params=dict(model.generation_params),
            voice=voice.provider_voice,
            voice_rate=voice.rate,
            judge_system_prompt=judge.system_prompt or "你是客观的中文辩论裁判。",
            judge_prompt=judge.judge_prompt or "请严格按 JSON 评分。",
            judge_params=dict(judge.generation_params),
        )


async def probe_tts(
    material: ProviderMaterial, database: Database | None = None
) -> tuple[dict[str, Any], bytes]:
    settings = load_settings()
    if settings.tts_api_key is None:
        raise RuntimeError("tts_api_key_missing")
    audio = bytearray()
    capture = ProviderCallCapture()
    started_at = datetime.now(UTC)
    error: Exception | None = None

    async def chunks():
        yield PROBE_TEXT

    async def on_audio(chunk: bytes) -> None:
        audio.extend(chunk)

    client = QwenTtsConnection(
        url=settings.tts_ws_url,
        api_key=settings.tts_api_key.get_secret_value(),
        model=settings.tts_model,
        workspace_id=settings.tts_workspace_id,
    )
    try:
        try:
            result = await client.synthesize(
                chunks(),
                voice=material.voice,
                rate=material.voice_rate,
                on_audio=on_audio,
                capture=capture,
            )
        except Exception as caught:
            error = caught
            raise
    finally:
        await client.close()
        await persist_probe_capture(
            database,
            call_kind="TTS",
            provider="BAILIAN",
            operation="duplex.server_commit",
            model=settings.tts_model,
            voice=material.voice,
            capture=capture,
            started_at=started_at,
            error=error,
        )
    pcm = decode_ogg_opus_pcm(bytes(audio))
    if not pcm:
        raise RuntimeError("tts_audio_decode_empty")
    return (
        {
            "status": "passed",
            "audio_bytes": result.byte_count,
            "decoded_duration_ms": len(pcm) // 32,
            "first_audio_latency_ms": result.first_audio_latency_ms,
            "completed_latency_ms": result.completed_latency_ms,
        },
        pcm,
    )


async def probe_asr(pcm: bytes, database: Database | None = None) -> dict[str, Any]:
    settings = load_settings()
    if settings.asr_api_key is None:
        raise RuntimeError("asr_api_key_missing")
    segment_count = 0
    captures: list[ProviderCallCapture] = []
    started_at = datetime.now(UTC)
    error: Exception | None = None

    async def on_interim(_: object, __: int, ___: str) -> None:
        return

    async def on_segment(_: object, __: int, ___: SegmentResult, ____: int) -> None:
        nonlocal segment_count
        segment_count += 1

    async def on_capture(_: object, capture: ProviderCallCapture) -> None:
        captures.append(capture)

    connection = FunAsrConnection(
        url=settings.asr_ws_url,
        api_key=settings.asr_api_key.get_secret_value(),
        model=settings.asr_model,
        workspace_id=settings.asr_workspace_id,
    )
    session = AsrSpeechSession(
        speech_id=uuid4(),
        connection=connection,
        on_interim=on_interim,
        on_segment=on_segment,
        on_capture=on_capture,
    )
    task = asyncio.create_task(session.run(), name="provider-probe-asr")
    started = monotonic()
    try:
        await session.wait_ready()
        for offset in range(0, len(pcm), 3_200):
            session.feed_pcm(pcm[offset : offset + 3_200])
            await asyncio.sleep(0.1)
        await session.finish()
        result = await task
    except Exception as caught:
        error = caught
        raise
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await connection.close()
        for capture in captures:
            await persist_probe_capture(
                database,
                call_kind="ASR",
                provider="BAILIAN",
                operation="duplex.transcribe",
                model=settings.asr_model,
                voice=None,
                capture=capture,
                started_at=started_at,
                error=error,
            )
    if not result.final_text.strip():
        raise RuntimeError("asr_transcript_empty")
    return {
        "status": "passed",
        "audio_duration_ms": result.audio_duration_ms,
        "wall_elapsed_ms": round((monotonic() - started) * 1_000),
        "segment_count": segment_count,
        "transcript_chars": len(result.final_text.strip()),
        "first_interim_latency_ms": result.first_interim_latency_ms,
    }


async def _stream(
    material: ProviderMaterial,
    *,
    messages: list[dict[str, str]],
    max_tokens: int,
    generation_params: dict[str, Any],
    database: Database | None = None,
    call_kind: str = "LLM_SPEECH",
) -> tuple[str, dict[str, Any]]:
    capture = ProviderCallCapture()
    started_at = datetime.now(UTC)
    error: Exception | None = None
    client = OpenAIStreamingClient(
        base_url=material.base_url,
        api_key=material.api_key,
        model=material.model_id,
    )
    try:
        try:
            result = await client.stream_chat(
                messages=messages,
                max_tokens=max_tokens,
                generation_params=generation_params,
                on_delta=lambda _: asyncio.sleep(0),
                capture=capture,
            )
        except Exception as caught:
            error = caught
            raise
    finally:
        await client.close()
        await persist_probe_capture(
            database,
            call_kind=call_kind,
            provider="OPENAI_COMPATIBLE",
            operation="chat.completions.stream",
            model=material.model_id,
            voice=None,
            capture=capture,
            started_at=started_at,
            error=error,
        )
    return result.text, {
        "status": "passed",
        "response_chars": len(result.text),
        "first_token_latency_ms": result.first_token_latency_ms,
        "completed_latency_ms": result.completed_latency_ms,
        "completion_tokens": result.completion_tokens,
    }


async def probe_llm(material: ProviderMaterial, database: Database | None = None) -> dict[str, Any]:
    text, metrics = await _stream(
        material,
        messages=[
            {"role": "system", "content": "你是服务健康检查助手。"},
            {"role": "user", "content": '只输出 JSON：{"ok":true}'},
        ],
        max_tokens=32,
        generation_params={"temperature": 0, "enable_thinking": False},
        database=database,
    )
    try:
        parsed = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
    except json.JSONDecodeError as error:
        raise RuntimeError("llm_probe_json_invalid") from error
    if parsed != {"ok": True}:
        raise RuntimeError("llm_probe_result_invalid")
    return metrics


def _judge_snapshot() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    participants: list[dict[str, Any]] = []
    for side in ("AFFIRMATIVE", "NEGATIVE"):
        for seat_no in range(1, 5):
            participants.append(
                {
                    "participant_id": str(uuid4()),
                    "name": f"{side}-{seat_no}",
                    "side": side,
                    "seat_no": seat_no,
                }
            )
    snapshot = {
        "topic": "过程还是结果更能体现奋斗的价值",
        "affirmative_stance": "过程更能体现奋斗的价值",
        "negative_stance": "结果更能体现奋斗的价值",
        "participants": participants,
        "transcript": [
            {"side": "AFFIRMATIVE", "seat_no": 1, "text": "奋斗的过程塑造能力与人格。"},
            {"side": "NEGATIVE", "seat_no": 1, "text": "结果检验奋斗是否真正创造价值。"},
        ],
    }
    return snapshot, participants


async def probe_judge(
    material: ProviderMaterial, database: Database | None = None
) -> dict[str, Any]:
    snapshot, participants = _judge_snapshot()
    text, metrics = await _stream(
        material,
        messages=[
            {"role": "system", "content": material.judge_system_prompt},
            {
                "role": "user",
                "content": material.judge_prompt + "\n" + json.dumps(snapshot, ensure_ascii=False),
            },
        ],
        max_tokens=1_600,
        generation_params=material.judge_params,
        database=database,
        call_kind="JUDGE",
    )
    participant_ids = {str(item["participant_id"]) for item in participants}
    parsed = _parse_result(text, participant_ids, participants)
    metrics["winner"] = parsed["winner"]
    metrics["participant_count"] = len(parsed["participants"])
    return metrics


async def probe_livekit() -> dict[str, Any]:
    settings = load_settings()
    if not settings.livekit_url or not settings.livekit_api_key or not settings.livekit_api_secret:
        raise RuntimeError("livekit_configuration_missing")
    room_name = f"jx-provider-probe-{uuid4().hex[:12]}"
    token = (
        api.AccessToken(
            settings.livekit_api_key.get_secret_value(),
            settings.livekit_api_secret.get_secret_value(),
        )
        .with_identity(f"probe-{uuid4().hex[:12]}")
        .with_grants(api.VideoGrants(room_join=True, room=room_name, hidden=True))
        .to_jwt()
    )
    room = rtc.Room()
    started = monotonic()
    try:
        await asyncio.wait_for(room.connect(settings.livekit_url, token), timeout=10)
        connected_ms = round((monotonic() - started) * 1_000)
    finally:
        await room.disconnect()
    # Release the native handle while the asyncio/FFI runtimes are both alive.
    del room
    gc.collect()
    return {"status": "passed", "connect_latency_ms": connected_ms}


def summarize_latencies(results: list[tuple[bool, int, str | None]]) -> dict[str, Any]:
    latencies = sorted(elapsed for passed, elapsed, _ in results if passed)
    errors: dict[str, int] = {}
    for passed, _, error_code in results:
        if not passed and error_code is not None:
            errors[error_code] = errors.get(error_code, 0) + 1

    def percentile(fraction: float) -> int | None:
        if not latencies:
            return None
        return latencies[max(0, math.ceil(len(latencies) * fraction) - 1)]

    return {
        "requested": len(results),
        "succeeded": len(latencies),
        "failed": len(results) - len(latencies),
        "latency_ms": {
            "p50": percentile(0.50),
            "p95": percentile(0.95),
            "max": latencies[-1] if latencies else None,
        },
        "errors": errors,
    }


async def _measure(operation: Callable[[], Awaitable[Any]]) -> tuple[bool, int, str | None]:
    started = monotonic()
    try:
        await operation()
    except Exception as error:  # Provider messages are intentionally redacted.
        return False, round((monotonic() - started) * 1_000), type(error).__name__
    return True, round((monotonic() - started) * 1_000), None


async def _staggered_measure(
    delay_seconds: float, operation: Callable[[], Awaitable[Any]]
) -> tuple[bool, int, str | None]:
    if delay_seconds:
        await asyncio.sleep(delay_seconds)
    return await _measure(operation)


async def run_load(
    material: ProviderMaterial,
    *,
    database: Database | None = None,
    llm_count: int = 50,
    tts_count: int = 5,
    asr_count: int = 5,
    livekit_count: int = 5,
) -> dict[str, Any]:
    if min(llm_count, tts_count, asr_count, livekit_count) < 1:
        raise ValueError("load probe counts must be positive")

    tts_results = await asyncio.gather(
        *[
            _staggered_measure(index * 0.4, lambda: probe_tts(material, database))
            for index in range(tts_count)
        ]
    )
    _, pcm = await probe_tts(material, database)
    asr_results = await asyncio.gather(
        *[_measure(lambda: probe_asr(pcm, database)) for _ in range(asr_count)]
    )
    llm_results = await asyncio.gather(
        *[_measure(lambda: probe_llm(material, database)) for _ in range(llm_count)]
    )
    livekit_results = await asyncio.gather(*[_measure(probe_livekit) for _ in range(livekit_count)])
    return {
        "probe_version": "2",
        "mode": "concurrent_load",
        "tts": summarize_latencies(list(tts_results)),
        "asr": summarize_latencies(list(asr_results)),
        "llm": summarize_latencies(list(llm_results)),
        "livekit": summarize_latencies(list(livekit_results)),
    }


async def run() -> dict[str, Any]:
    settings = load_settings()
    database = Database(settings.database_url_value)
    try:
        material = await load_material(database)
        tts, pcm = await probe_tts(material, database)
        return {
            "probe_version": "1",
            "model_profile": material.model_name,
            "tts": tts,
            "asr": await probe_asr(pcm, database),
            "llm": await probe_llm(material, database),
            "judge": await probe_judge(material, database),
            "livekit": await probe_livekit(),
        }
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run redacted provider probes without writing application data."
    )
    parser.add_argument("--load", action="store_true", help="run redacted concurrent load probes")
    parser.add_argument("--llm-count", type=int, default=50)
    parser.add_argument("--tts-count", type=int, default=5)
    parser.add_argument("--asr-count", type=int, default=5)
    parser.add_argument("--livekit-count", type=int, default=5)
    args = parser.parse_args()
    if args.load:

        async def load() -> dict[str, Any]:
            settings = load_settings()
            database = Database(settings.database_url_value)
            try:
                material = await load_material(database)
                return await run_load(
                    material,
                    database=database,
                    llm_count=args.llm_count,
                    tts_count=args.tts_count,
                    asr_count=args.asr_count,
                    livekit_count=args.livekit_count,
                )
            finally:
                await database.dispose()

        result = asyncio.run(load())
    else:
        result = asyncio.run(run())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Bounded, distinct public speeches rendered in short real provider tasks.

The resulting long PCM is never looped, sped up or replaced with local speech.
Only fully transmitted block boundaries count as complete reference text.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.engine import make_url

from jx_core.agent.audio import decode_ogg_opus_pcm
from jx_core.agent.tts import QwenTtsConnection
from jx_core.config import Settings
from jx_core.data_capture.provider import ProviderCallCapture
from jx_core.data_capture.provider_persistence import persist_provider_capture
from jx_core.models import ExternalCall
from jx_core.rooms.roster import CONFERENCE_ROLES

ARGUMENTS = (
    ("评价公共决策需要先讲清楚标准", "预算编制公开每项支出，让居民理解方案为何这样选择"),
    ("公共资源的分配必须回应真实需要", "社区调查应同时记录老年人、上班族和学生的不同出行困难"),
    (
        "参与讨论可以发现设计者没有注意的问题",
        "公交线路调整前听取乘客意见，可以避免忽略医院附近的站点",
    ),
    ("证据的质量决定争论能否继续推进", "比较两项政策时，应使用同一时间段和相同人口范围的数据"),
    ("面对不确定性应当允许及时修正", "先在一个街区试行垃圾分类，记录问题后再完善全市实施方案"),
    ("公平意味着认真考虑少数人的处境", "便捷的线上服务也应保留必要的线下窗口，照顾不熟悉手机的人"),
    (
        "效率不能只看处理速度这一项数字",
        "一次办理虽然更快，仍要检查申请人是否清楚材料要求和申诉办法",
    ),
    (
        "责任需要清晰的记录和可检验的承诺",
        "项目负责人定期公布进度，解释延期原因，并给出能够核对的改进措施",
    ),
    (
        "不同价值之间的取舍需要明确说明",
        "建设公园可能改善生活，也会占用其他设施的预算，应让公众看到比较依据",
    ),
    (
        "政策评价应当考虑长期影响",
        "教育投入的效果不会立刻全部显现，短期数字与多年后的变化都需要记录",
    ),
    (
        "纠错机制能够减少一次错误的影响",
        "投诉渠道应记录受理时间、处理结果以及当事人是否得到明确回应",
    ),
    (
        "形成共识需要尊重分歧背后的理由",
        "面对环境治理争议，应区分事实判断、利益分配与价值偏好，再逐项讨论",
    ),
    (
        "公开信息需要让普通人真正读懂",
        "除了发布长篇报告，还可以用清楚的表格解释主要选择及其可能后果",
    ),
    (
        "跨部门合作必须避免责任相互推诿",
        "处理道路积水时，应明确排水、道路维护和预警部门各自承担的工作",
    ),
    (
        "比较方案时需要承认各自的限制",
        "一个成本较低的选择可能覆盖人数较少，应将节省的资源与服务差距同时呈现",
    ),
    (
        "总结辩论应回到最初的问题和标准",
        "判断哪项公共政策更好，应说明受益人、实施成本、监督方式和实际改变",
    ),
)


@dataclass(repr=False)
class HumanMaterial:
    side: str
    seat: int
    voice: str
    pcm: bytes = field(repr=False)
    blocks: list[dict[str, Any]] = field(repr=False)

    def completed_text(self, sent_samples: int) -> str:
        return "".join(
            block["text"] for block in self.blocks if block["end_sample"] <= sent_samples
        )

    def receipt(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "seat": self.seat,
            "voice": self.voice,
            "sample_rate": 48_000,
            "duration_ms": len(self.pcm) // 96,
            "pcm_sha256": hashlib.sha256(self.pcm).hexdigest(),
            "blocks": [
                {key: value for key, value in block.items() if key != "text"}
                for block in self.blocks
            ],
            "distinct_source": True,
            "looped": False,
            "accelerated": False,
        }


async def build_human_material(
    settings: Settings, factory: Any, *, side: str, seat: int, voice: str, target_seconds: int
) -> HumanMaterial:
    if settings.app_env == "production" or not 90 <= target_seconds <= 240:
        raise RuntimeError("qa_speech_material_owned_bounds_required")
    target = make_url(settings.database_url_value)
    async with factory() as session:
        if (
            target.host != "127.0.0.1"
            or target.database != "jx_debate_qa_agent_faults"
            or session.bind.url != target
        ):
            raise RuntimeError("qa_speech_material_database_not_owned")
    if settings.tts_api_key is None:
        raise RuntimeError("qa_tts_key_unavailable")
    frames: list[bytes] = []
    blocks: list[dict[str, Any]] = []
    total_samples = 0
    client = QwenTtsConnection(
        url=settings.tts_ws_url,
        api_key=settings.tts_api_key.get_secret_value(),
        model=settings.tts_model,
        workspace_id=settings.tts_workspace_id,
    )
    try:
        for index, (principle, example) in enumerate(ARGUMENTS):
            perspective = (
                "我方重视过程，因为公开讨论与清楚的规则能够提高决策的正当性。"
                if side == "AFFIRMATIVE"
                else "我方重视结果，因为最终需要检验公共服务是否真正改善了人们的生活。"
            )
            text_value = (
                f"我是{'正' if side == 'AFFIRMATIVE' else '反'}方{'一' if seat == 1 else '三'}辩。"
                f"第{index + 1}个论点是，{principle}。例如，{example}。{perspective}"
            )
            capture = ProviderCallCapture()
            started = datetime.now(UTC)
            audio = bytearray()
            error: BaseException | None = None

            async def chunks(value: str = text_value):
                yield value

            async def on_audio(chunk: bytes, target: bytearray = audio) -> None:
                if len(target) + len(chunk) > 1024 * 1024:
                    raise RuntimeError("qa_material_block_byte_budget")
                target.extend(chunk)

            try:
                result = await client.synthesize(
                    chunks(), voice=voice, rate=1.0, on_audio=on_audio, capture=capture
                )
            except BaseException as caught:
                error = caught
                raise
            finally:
                async with factory() as session:
                    async with session.begin():
                        call = ExternalCall(
                            call_kind="TTS",
                            provider="BAILIAN",
                            operation="duplex.server_commit",
                            model=settings.tts_model,
                            voice=voice,
                            attempt_no=1,
                            status=(
                                "CANCELLED"
                                if isinstance(error, asyncio.CancelledError)
                                else "FAILED"
                                if error
                                else "SUCCEEDED"
                            ),
                            source_kind="OPS_PROBE",
                            started_at=started,
                            completed_at=datetime.now(UTC),
                            error_code=type(error).__name__ if error else None,
                        )
                        session.add(call)
                        await persist_provider_capture(session, call, capture)
            pcm = decode_ogg_opus_pcm(bytes(audio), sample_rate=48_000)
            duration_ms = len(pcm) // 96
            if not 5_000 <= duration_ms <= 60_000:
                raise RuntimeError("qa_material_block_duration_invalid")
            total_samples += len(pcm) // 2
            frames.append(pcm)
            blocks.append(
                {
                    "index": index,
                    "text": text_value,
                    "text_sha256": hashlib.sha256(text_value.encode()).hexdigest(),
                    "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
                    "end_sample": total_samples,
                    "duration_ms": duration_ms,
                    "tts_completed_latency_ms": result.completed_latency_ms,
                }
            )
            if total_samples >= target_seconds * 48_000:
                return HumanMaterial(side, seat, voice, b"".join(frames), blocks)
        raise RuntimeError("qa_material_distinct_source_too_short")
    finally:
        await client.close()


async def calibrate_conference_voices(clients: Any) -> list[dict[str, Any]]:
    """Use the actual admin preview API and decode all four returned previews."""
    if clients.admin is None or clients.settings.app_env == "production":
        raise RuntimeError("qa_voice_calibration_owned_admin_required")
    catalog = await clients.request(clients.admin, "GET", "/api/admin/catalog")
    voices = {voice["provider_voice"]: voice for voice in catalog["voices"]}
    receipt: list[dict[str, Any]] = []
    for side, seat, name, provider_voice in CONFERENCE_ROLES:
        voice = voices.get(provider_voice)
        if voice is None or voice["name"] != name or voice["status"] != "ENABLED":
            raise RuntimeError("qa_conference_voice_binding_invalid")
        path = f"/api/admin/voices/{voice['id']}/preview"
        generated = await clients.request(clients.admin, "POST", path, {})
        response = await clients.admin.http.get(path)
        if generated.get("status") != "ready" or response.status_code != 200:
            raise RuntimeError("qa_voice_preview_not_ready")
        pcm = decode_ogg_opus_pcm(response.content, sample_rate=48_000)
        if not pcm or not any(pcm):
            raise RuntimeError("qa_voice_preview_empty_audio")
        receipt.append(
            {
                "side": side,
                "seat": seat,
                "name": name,
                "provider_voice": provider_voice,
                "voice_id": voice["id"],
                "duration_ms": len(pcm) // 96,
                "encoded_sha256": hashlib.sha256(response.content).hexdigest(),
                "actual_provider_call": True,
                "calibration_api_status": "ready",
            }
        )
    return receipt


__all__ = ["HumanMaterial", "build_human_material", "calibrate_conference_voices"]

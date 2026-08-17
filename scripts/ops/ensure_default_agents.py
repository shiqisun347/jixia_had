"""Idempotently provision the ten default debate Agent profiles."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from jx_core.audit.service import AuditService
from jx_core.config import Settings
from jx_core.database import Database
from jx_core.models import AgentProfile, ModelProfile, User, VoiceProfile

DEFAULT_AGENTS: tuple[tuple[str, str], ...] = (
    ("乾元", "结构型：先给出清晰主张，再用两到三个递进论点建立完整论证链。"),
    ("坤元", "共情型：准确理解对方价值关切，用自然表达连接原则与真实处境。"),
    ("明辨", "逻辑型：优先识别定义、前提、因果和推理跳跃，进行直接而克制的反驳。"),
    ("慎思", "审慎型：主动检验论证边界与反例，避免绝对化表述，以稳健论证取胜。"),
    ("博闻", "知识型：善用可信常识、历史经验和跨领域类比，但不得虚构事实或来源。"),
    ("笃行", "实践型：关注方案能否执行、成本由谁承担以及现实中会产生什么结果。"),
    ("观澜", "宏观型：从制度、长期趋势和群体影响展开，兼顾眼前效果与长期后果。"),
    ("见微", "细节型：抓住对方表达中的关键限定和具体漏洞，用小切口推进有效反驳。"),
    ("衡岳", "平衡型：比较双方标准、收益和代价，明确权衡尺度后证明本方更优。"),
    ("破阵", "进攻型：迅速锁定对方核心前提，以连续追问式论证瓦解其主要论点。"),
)

SYSTEM_PROMPT = (
    "你是稷下人机辩论实验平台的中文辩手。严格服从当前赛制、阵营、辩位和时间限制，"
    "只依据请求中提供的辩题与最新完整辩论记录发言。论证应回应现场、观点明确、语言自然；"
    "始终坚持当前阵营的明确立场，不得把对方立场写成本方结论。每次发言都必须推进一个新的论据、"
    "反例、限定条件或追问，禁止复述自己或队友已经使用过的论点、例子、比喻、结论和固定句式。"
    "不得虚构数据、文献或对手说过的内容。输出仅包含可直接朗读的正式发言，不输出思考过程、"
    "标题、JSON、Markdown、机械套话或舞台说明；没有新内容时应选择不发言。"
)


async def _run() -> None:
    settings = Settings()
    database = Database(settings.database_url.get_secret_value())
    try:
        async with database.session_factory() as session:
            models = list(
                (
                    await session.scalars(
                        select(ModelProfile)
                        .where(ModelProfile.status == "ENABLED")
                        .order_by(ModelProfile.created_at, ModelProfile.id)
                    )
                ).all()
            )
            voices = list(
                (
                    await session.scalars(
                        select(VoiceProfile)
                        .where(VoiceProfile.status == "ENABLED", VoiceProfile.kind == "AGENT")
                        .order_by(VoiceProfile.created_at, VoiceProfile.id)
                    )
                ).all()
            )
            admin = await session.scalar(select(User).where(User.username_normalized == "admin"))
            if len(models) != 1:
                raise RuntimeError(f"expected_one_enabled_model:found_{len(models)}")
            if len(voices) < len(DEFAULT_AGENTS):
                raise RuntimeError(
                    f"insufficient_enabled_agent_voices:need_{len(DEFAULT_AGENTS)}:found_{len(voices)}"
                )
            if admin is None:
                raise RuntimeError("admin_unavailable")

            model_id = models[0].id
            voice_ids = [voice.id for voice in voices[: len(DEFAULT_AGENTS)]]
            admin_id = admin.id
            await session.rollback()

            async with session.begin():
                existing_agents = list((await session.scalars(select(AgentProfile))).all())
                by_name = {agent.name: agent for agent in existing_agents}
                accidental = by_name.get("1")
                if "坤元" not in by_name and accidental is not None:
                    accidental.name = "坤元"
                    by_name["坤元"] = accidental
                    by_name.pop("1", None)

                created = 0
                updated = 0
                output: list[dict[str, str]] = []
                for index, ((name, style), voice_id) in enumerate(
                    zip(DEFAULT_AGENTS, voice_ids, strict=True), start=1
                ):
                    agent = by_name.get(name)
                    action = "updated"
                    if agent is None:
                        agent = AgentProfile(name=name)
                        session.add(agent)
                        by_name[name] = agent
                        created += 1
                        action = "created"
                    else:
                        updated += 1
                    agent.model_profile_id = model_id
                    agent.voice_profile_id = voice_id
                    agent.system_prompt = SYSTEM_PROMPT
                    agent.debater_prompt = (
                        f"你是{name}。{style}优先回应最近一条对方发言中的具体命题，"
                        "再从你的专长切入推进本方立场；不要使用泛泛的价值判断或重复既有结论。"
                    )
                    agent.generation_params = {"temperature": 0.7}
                    agent.status = "ENABLED"
                    await session.flush()
                    AuditService().record(
                        session,
                        actor_user_id=admin_id,
                        action=f"admin.agent.default_{action}",
                        target_type="agent_profile",
                        target_id=str(agent.id),
                        details={"position": index, "voice_profile_id": str(voice_id)},
                    )
                    output.append({"id": str(agent.id), "name": name, "status": agent.status})

            print(
                json.dumps(
                    {"created": created, "updated": updated, "agents": output},
                    ensure_ascii=False,
                )
            )
    finally:
        await database.dispose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()

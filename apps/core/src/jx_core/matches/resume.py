"""Shared necessary-participant checks for explicit and automatic continuation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DeviceCheck, RoomConnectionLease, RoomMember, User
from .domain import MatchRuntimeState


async def required_resume_reasons(
    session: AsyncSession, room_id: UUID, state: MatchRuntimeState | None = None
) -> list[str]:
    query = (
        select(RoomMember, User.real_name)
        .join(User, User.id == RoomMember.user_id)
        .where(
            RoomMember.room_id == room_id,
            RoomMember.member_role == "DEBATER",
            RoomMember.left_at.is_(None),
        )
    )
    if state is not None:
        action_state = (
            state.paused_from_action_state if state.status != "RUNNING" else state.action_state
        )
        if state.current_speaker_user_id is None or action_state not in {
            "HUMAN_READY_TO_START",
            "HUMAN_SPEAKING",
            "SPEECH_FINALIZING",
        }:
            return []
        query = query.where(RoomMember.user_id == state.current_speaker_user_id)
    members = list((await session.execute(query)).all())
    if state is not None and not members:
        return ["当前辩手尚未连接比赛"]
    reasons: list[str] = []
    for member, name in members:
        label = f"辩手 {name}"
        if not member.online:
            reasons.append(f"{label} 当前麦克风尚未恢复")
        lease = await session.scalar(
            select(RoomConnectionLease).where(
                RoomConnectionLease.user_id == member.user_id,
                RoomConnectionLease.room_id == room_id,
            )
        )
        if lease is None:
            reasons.append(f"{label} 尚未连接比赛")
        check = await session.scalar(
            select(DeviceCheck)
            .where(DeviceCheck.room_id == room_id, DeviceCheck.user_id == member.user_id)
            .order_by(DeviceCheck.checked_at.desc())
            .limit(1)
        )
        if check is None or check.status == "FAIL" or check.valid_until <= datetime.now(UTC):
            reasons.append(f"{label} 的麦克风或扬声器检测已失效")
    return reasons

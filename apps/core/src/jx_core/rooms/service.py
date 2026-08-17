"""Transactional public-room and seat rules for the 004 slice."""

from __future__ import annotations

import re
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, not_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.errors import AuthError
from ..legal.terms import get_current_human_participation_terms
from ..models import (
    AgentProfile,
    CapacityGuard,
    DeviceCheck,
    HostAudioAsset,
    Match,
    ModelProfile,
    Room,
    RoomMember,
    Rule,
    RuleStage,
    Seat,
    SeatSwapRequest,
    StageAction,
    Topic,
    User,
    UserConsent,
    VoiceProfile,
)
from .schemas import (
    DeviceCheckRequest,
    RoleChangeRequest,
    RoomCreateRequest,
    RoomJoinRequest,
    SeatSelectRequest,
    SeatSwapCreateRequest,
    SeatSwapRespondRequest,
)


def ensure_storage_capacity(
    storage_path: str,
    *,
    disk_usage: Callable[[Path], Any] = shutil.disk_usage,
) -> None:
    try:
        path = Path(storage_path)
        path.mkdir(parents=True, exist_ok=True)
        usage = disk_usage(path)
    except OSError as error:
        raise AuthError("storage_unavailable") from error
    if usage.total <= 0 or usage.used / usage.total >= 0.9:
        raise AuthError("disk_capacity_full")


ROOM_CODE_ALPHABET = "0123456789"
ROOM_CODE_PATTERN = re.compile(r"^\d{6}$")
LEGACY_ROOM_CODE_PATTERN = re.compile(r"^[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{6}$")
DEVICE_CHECK_TTL = timedelta(minutes=30)
ACTIVE_ROOM_STATUSES = ("START_PENDING_RUNTIME", "RUNNING", "PAUSED")


@dataclass(frozen=True)
class RoomViewerContext:
    """Viewer-specific entry facts layered onto the shared room snapshot."""

    member: RoomMember | None
    latest_device_check: DeviceCheck | None
    match_id: UUID | None


def _room_code() -> str:
    return "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(6))


def normalize_room_code(value: str) -> str:
    normalized = value.strip().upper()
    if not (
        ROOM_CODE_PATTERN.fullmatch(normalized) or LEGACY_ROOM_CODE_PATTERN.fullmatch(normalized)
    ):
        raise AuthError("room_code_invalid")
    return normalized


def ensure_unique_agent_ids(agent_ids: list[UUID]) -> None:
    """Reject a room layout that would let one Agent speak from two seats."""
    if len(agent_ids) != len(set(agent_ids)):
        raise AuthError("agent_duplicate_in_room")


class RoomService:
    async def _cancel_user_seat_swaps(
        self, database_session: AsyncSession, *, room_id: UUID, user_id: UUID
    ) -> None:
        await database_session.execute(
            update(SeatSwapRequest)
            .where(
                SeatSwapRequest.room_id == room_id,
                SeatSwapRequest.status == "PENDING",
                (SeatSwapRequest.requester_user_id == user_id)
                | (SeatSwapRequest.target_user_id == user_id),
            )
            .values(status="CANCELLED", responded_at=datetime.now(UTC))
        )

    async def create_seat_swap_request(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        room_id: UUID,
        payload: SeatSwapCreateRequest,
    ) -> SeatSwapRequest:
        async with database_session.begin():
            room = await database_session.get(Room, room_id, with_for_update=True)
            if room is None or room.status != "WAITING" or room.is_all_agent:
                raise AuthError("seat_swap_forbidden")
            members = list(
                (
                    await database_session.scalars(
                        select(RoomMember)
                        .where(
                            RoomMember.room_id == room_id,
                            RoomMember.user_id.in_([user_id, payload.target_user_id]),
                            RoomMember.left_at.is_(None),
                        )
                        .with_for_update()
                    )
                ).all()
            )
            if len(members) != 2 or any(item.member_role != "DEBATER" for item in members):
                raise AuthError("seat_swap_forbidden")
            seats = list(
                (
                    await database_session.scalars(
                        select(Seat)
                        .where(
                            Seat.room_id == room_id,
                            Seat.user_id.in_([user_id, payload.target_user_id]),
                        )
                        .with_for_update()
                    )
                ).all()
            )
            requester_seat = next((item for item in seats if item.user_id == user_id), None)
            target_seat = next(
                (item for item in seats if item.user_id == payload.target_user_id), None
            )
            if requester_seat is None or target_seat is None or user_id == payload.target_user_id:
                raise AuthError("seat_swap_forbidden")
            pending = await database_session.scalar(
                select(SeatSwapRequest)
                .where(
                    SeatSwapRequest.room_id == room_id,
                    SeatSwapRequest.status == "PENDING",
                    (
                        SeatSwapRequest.requester_user_id.in_([user_id, payload.target_user_id])
                        | SeatSwapRequest.target_user_id.in_([user_id, payload.target_user_id])
                    ),
                )
                .with_for_update()
            )
            if pending is not None:
                if (
                    pending.requester_user_id == user_id
                    and pending.target_user_id == payload.target_user_id
                ):
                    return pending
                raise AuthError("seat_swap_pending")
            request = SeatSwapRequest(
                room_id=room_id,
                requester_user_id=user_id,
                target_user_id=payload.target_user_id,
                requester_seat_id=requester_seat.id,
                target_seat_id=target_seat.id,
            )
            database_session.add(request)
            room.sequence += 1
            await database_session.flush()
        return request

    async def list_seat_swap_requests(
        self, database_session: AsyncSession, *, user_id: UUID, room_id: UUID
    ) -> list[SeatSwapRequest]:
        return list(
            (
                await database_session.scalars(
                    select(SeatSwapRequest)
                    .where(
                        SeatSwapRequest.room_id == room_id,
                        (SeatSwapRequest.requester_user_id == user_id)
                        | (SeatSwapRequest.target_user_id == user_id),
                        SeatSwapRequest.status.in_(("PENDING", "ACCEPTED", "REJECTED")),
                    )
                    .order_by(SeatSwapRequest.created_at.desc())
                    .limit(10)
                )
            ).all()
        )

    async def respond_seat_swap_request(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        room_id: UUID,
        request_id: UUID,
        payload: SeatSwapRespondRequest,
    ) -> SeatSwapRequest:
        stale = False
        async with database_session.begin():
            room = await database_session.get(Room, room_id, with_for_update=True)
            request = await database_session.scalar(
                select(SeatSwapRequest)
                .where(SeatSwapRequest.id == request_id, SeatSwapRequest.room_id == room_id)
                .with_for_update()
            )
            if (
                room is None
                or room.status != "WAITING"
                or request is None
                or request.status != "PENDING"
            ):
                raise AuthError("seat_swap_not_found")
            if request.target_user_id != user_id:
                raise AuthError("seat_swap_forbidden")
            if payload.decision == "REJECT":
                request.status = "REJECTED"
            else:
                seats = list(
                    (
                        await database_session.scalars(
                            select(Seat)
                            .where(
                                Seat.id.in_([request.requester_seat_id, request.target_seat_id]),
                                Seat.room_id == room_id,
                            )
                            .with_for_update()
                        )
                    ).all()
                )
                requester_seat = next(
                    (item for item in seats if item.id == request.requester_seat_id), None
                )
                target_seat = next(
                    (item for item in seats if item.id == request.target_seat_id), None
                )
                if (
                    requester_seat is None
                    or target_seat is None
                    or requester_seat.user_id != request.requester_user_id
                    or target_seat.user_id != request.target_user_id
                ):
                    request.status = "CANCELLED"
                    stale = True
                else:
                    requester_seat.user_id, target_seat.user_id = (
                        target_seat.user_id,
                        requester_seat.user_id,
                    )
                    request.status = "ACCEPTED"
                if stale:
                    request.responded_at = datetime.now(UTC)
                    room.sequence += 1
                    await database_session.flush()
                else:
                    request.status = "ACCEPTED"
            request.responded_at = datetime.now(UTC)
            room.sequence += 1
            await database_session.flush()
        if stale:
            raise AuthError("seat_swap_stale")
        return request

    async def _assert_spectator_capacity(self, database_session: AsyncSession) -> None:
        await database_session.execute(
            select(CapacityGuard).where(CapacityGuard.id == 1).with_for_update()
        )
        spectator_count = await database_session.scalar(
            select(func.count())
            .select_from(RoomMember)
            .join(Room, Room.id == RoomMember.room_id)
            .where(
                RoomMember.member_role == "SPECTATOR",
                RoomMember.left_at.is_(None),
                Room.status.not_in(("FINISHED", "TERMINATED")),
            )
        )
        if int(spectator_count or 0) >= 10:
            raise AuthError("spectator_capacity_full")

    async def lookup_room_by_code(self, database_session: AsyncSession, *, code: str) -> Room:
        normalized = normalize_room_code(code)
        room = await database_session.scalar(select(Room).where(Room.code == normalized))
        if room is None:
            raise AuthError("room_code_not_found")
        return room

    async def _restore_agent_or_empty(
        self,
        database_session: AsyncSession,
        *,
        room: Room,
        seat: Seat,
    ) -> None:
        agent_id = seat.configured_agent_profile_id
        used_agent_ids = set(
            (
                await database_session.scalars(
                    select(Seat.agent_profile_id).where(
                        Seat.room_id == room.id,
                        Seat.occupant_type == "AGENT",
                        Seat.agent_profile_id.is_not(None),
                        Seat.id != seat.id,
                    )
                )
            ).all()
        )
        if agent_id in used_agent_ids:
            agent_id = None
        if agent_id is None and room.auto_fill_agents:
            agent_id = await database_session.scalar(
                select(AgentProfile.id)
                .join(ModelProfile, ModelProfile.id == AgentProfile.model_profile_id)
                .join(VoiceProfile, VoiceProfile.id == AgentProfile.voice_profile_id)
                .where(
                    AgentProfile.status == "ENABLED",
                    ModelProfile.status == "ENABLED",
                    VoiceProfile.status == "ENABLED",
                    AgentProfile.id.not_in(used_agent_ids) if used_agent_ids else true(),
                )
                .order_by(AgentProfile.created_at, AgentProfile.id)
                .limit(1)
            )
            seat.configured_agent_profile_id = agent_id
        seat.user_id = None
        seat.agent_profile_id = agent_id
        seat.occupant_type = "AGENT" if agent_id is not None else "EMPTY"

    async def _require_consent(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        version: str | None,
    ) -> None:
        current = get_current_human_participation_terms()
        if version != current.version:
            raise AuthError("human_participation_terms_outdated")
        existing = await database_session.scalar(
            select(UserConsent.id).where(
                UserConsent.user_id == user_id,
                UserConsent.consent_type == "human_participation",
                UserConsent.version == current.version,
            )
        )
        if existing is None:
            database_session.add(
                UserConsent(
                    user_id=user_id,
                    consent_type="human_participation",
                    version=current.version,
                )
            )
            await database_session.flush()

    async def _assert_no_active_participation(
        self, database_session: AsyncSession, *, user_id: UUID
    ) -> None:
        user = await database_session.get(User, user_id, with_for_update=True)
        if user is None or user.status != "ACTIVE":
            raise AuthError("not_authenticated")
        result = await database_session.execute(
            select(RoomMember.id)
            .join(Room, Room.id == RoomMember.room_id)
            .where(
                RoomMember.user_id == user_id,
                RoomMember.left_at.is_(None),
                not_(
                    and_(
                        RoomMember.member_role == "ORGANIZER",
                        Room.is_all_agent.is_(True),
                    )
                ),
                Room.status.not_in(("FINISHED", "TERMINATED")),
            )
            .limit(1)
        )
        if result.scalar_one_or_none() is not None:
            raise AuthError("user_active_room_conflict")

    async def _assert_no_owned_room(self, database_session: AsyncSession, *, user_id: UUID) -> None:
        owned = await database_session.scalar(
            select(Room.id)
            .where(
                Room.organizer_user_id == user_id,
                Room.status.not_in(("FINISHED", "TERMINATED")),
            )
            .limit(1)
        )
        if owned is not None:
            raise AuthError("room_owner_conflict")

    async def _rule_snapshot(
        self, database_session: AsyncSession, *, rule_id: UUID
    ) -> tuple[Rule, dict[str, Any]]:
        rule = await database_session.get(Rule, rule_id)
        if rule is None or rule.status != "ENABLED" or rule.audio_reviewed_at is None:
            raise AuthError("rule_unavailable")
        stages = list(
            (
                await database_session.scalars(
                    select(RuleStage)
                    .where(RuleStage.rule_id == rule.id)
                    .order_by(RuleStage.position)
                )
            ).all()
        )
        audio_assets = list(
            (
                await database_session.scalars(
                    select(HostAudioAsset)
                    .where(HostAudioAsset.rule_id == rule.id)
                    .order_by(HostAudioAsset.segment_key)
                )
            ).all()
        )
        if any(asset.status != "READY" or not asset.storage_path for asset in audio_assets):
            raise AuthError("rule_unavailable")
        stage_snapshot: list[dict[str, Any]] = []
        for stage in stages:
            actions = list(
                (
                    await database_session.scalars(
                        select(StageAction)
                        .where(StageAction.stage_id == stage.id)
                        .order_by(StageAction.position)
                    )
                ).all()
            )
            stage_snapshot.append(
                {
                    "position": stage.position,
                    "name": stage.name,
                    "stage_kind": stage.stage_kind,
                    "duration_seconds": stage.duration_seconds,
                    "start_host_text": stage.start_host_text,
                    "end_host_text": stage.end_host_text,
                    "parameters": stage.parameters,
                    "actions": [
                        {
                            "position": action.position,
                            "action_kind": action.action_kind,
                            "side": action.side,
                            "seat_no": action.seat_no,
                            "duration_seconds": action.duration_seconds,
                            "parameters": action.parameters,
                        }
                        for action in actions
                    ],
                }
            )
        return rule, {
            "id": str(rule.id),
            "rule_key": rule.rule_key,
            "version": rule.version,
            "name": rule.name,
            "side_size": rule.side_size,
            "estimated_seconds": rule.estimated_seconds,
            "stages": stage_snapshot,
            "host_audio": [
                {
                    "segment_key": asset.segment_key,
                    "storage_path": asset.storage_path,
                    "text_hash": asset.text_hash,
                }
                for asset in audio_assets
            ],
        }

    async def _topic_snapshot(
        self,
        database_session: AsyncSession,
        *,
        payload: RoomCreateRequest,
    ) -> tuple[UUID | None, dict[str, str]]:
        if payload.topic_id is not None:
            topic = await database_session.get(Topic, payload.topic_id)
            if topic is None or topic.status != "ENABLED":
                raise AuthError("topic_unavailable")
            return topic.id, {
                "title": topic.title,
                "affirmative_text": topic.affirmative_text,
                "negative_text": topic.negative_text,
                "topic_key": topic.topic_key,
                "version": str(topic.version),
            }
        assert payload.custom_topic_title and payload.affirmative_text and payload.negative_text
        return None, {
            "title": payload.custom_topic_title.strip(),
            "affirmative_text": payload.affirmative_text.strip(),
            "negative_text": payload.negative_text.strip(),
            "custom": "true",
        }

    async def create_room(
        self,
        database_session: AsyncSession,
        *,
        organizer_user_id: UUID,
        organizer_role: str,
        payload: RoomCreateRequest,
    ) -> Room:
        async with database_session.begin():
            user = await database_session.get(User, organizer_user_id)
            if user is None or user.status != "ACTIVE":
                raise AuthError("not_authenticated")
            if payload.is_all_agent and organizer_role != "ADMIN":
                raise AuthError("forbidden")
            if not payload.is_all_agent:
                await self._assert_no_owned_room(database_session, user_id=organizer_user_id)
                await self._assert_no_active_participation(
                    database_session, user_id=organizer_user_id
                )
            rule, rule_snapshot = await self._rule_snapshot(
                database_session, rule_id=payload.rule_id
            )
            topic_id, topic_snapshot = await self._topic_snapshot(database_session, payload=payload)
            if not payload.is_all_agent:
                await self._require_consent(
                    database_session,
                    user_id=organizer_user_id,
                    version=payload.human_participation_terms_version,
                )
            assignments = {
                (item.side, item.seat_no): item.agent_profile_id
                for item in payload.agent_assignments
            }
            if len(assignments) != len(payload.agent_assignments):
                raise AuthError("seat_unavailable")
            ensure_unique_agent_ids([item.agent_profile_id for item in payload.agent_assignments])
            for item in payload.agent_assignments:
                if item.seat_no > rule.side_size:
                    raise AuthError("seat_unavailable")
                agent = await database_session.get(AgentProfile, item.agent_profile_id)
                if agent is None or agent.status != "ENABLED":
                    raise AuthError("agent_unavailable")
                model = await database_session.get(ModelProfile, agent.model_profile_id)
                voice = await database_session.get(VoiceProfile, agent.voice_profile_id)
                if (
                    model is None
                    or model.status != "ENABLED"
                    or voice is None
                    or voice.status != "ENABLED"
                ):
                    raise AuthError("agent_unavailable")
            configured_agent_ids = set(assignments.values())
            available_agent_ids = list(
                (
                    await database_session.scalars(
                        select(AgentProfile.id)
                        .join(ModelProfile, ModelProfile.id == AgentProfile.model_profile_id)
                        .join(VoiceProfile, VoiceProfile.id == AgentProfile.voice_profile_id)
                        .where(
                            AgentProfile.status == "ENABLED",
                            ModelProfile.status == "ENABLED",
                            VoiceProfile.status == "ENABLED",
                            AgentProfile.id.not_in(configured_agent_ids)
                            if configured_agent_ids
                            else true(),
                        )
                        .order_by(AgentProfile.created_at, AgentProfile.id)
                    )
                ).all()
            )
            required_agent_count = 2 * rule.side_size - len(assignments)
            if len(available_agent_ids) < required_agent_count:
                raise AuthError("agent_capacity_insufficient")
            next_agent = 0
            for side in ("AFFIRMATIVE", "NEGATIVE"):
                for seat_no in range(1, rule.side_size + 1):
                    key = (side, seat_no)
                    if key in assignments:
                        continue
                    assignments[key] = available_agent_ids[next_agent]
                    next_agent += 1
            code = _room_code()
            for _ in range(5):
                if await database_session.scalar(select(Room.id).where(Room.code == code)) is None:
                    break
                code = _room_code()
            else:
                raise AuthError("room_code_collision")
            room = Room(
                code=code,
                title=payload.title,
                label=payload.label,
                topic_id=topic_id,
                topic_snapshot=topic_snapshot,
                rule_id=rule.id,
                rule_snapshot=rule_snapshot,
                organizer_user_id=organizer_user_id,
                is_all_agent=payload.is_all_agent,
                auto_fill_agents=True,
            )
            database_session.add(room)
            await database_session.flush()
            organizer_role_name = "ORGANIZER" if payload.is_all_agent else "DEBATER"
            database_session.add(
                RoomMember(
                    room_id=room.id,
                    user_id=organizer_user_id,
                    member_role=organizer_role_name,
                    ready=False,
                )
            )
            for side in ("AFFIRMATIVE", "NEGATIVE"):
                for seat_no in range(1, rule.side_size + 1):
                    assignment = assignments.get((side, seat_no))
                    database_session.add(
                        Seat(
                            room_id=room.id,
                            side=side,
                            seat_no=seat_no,
                            occupant_type="AGENT",
                            agent_profile_id=assignment,
                            configured_agent_profile_id=assignment,
                            user_id=None,
                        )
                    )
            await database_session.flush()
        return room

    async def join_room(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        room_id: UUID,
        payload: RoomJoinRequest,
    ) -> RoomMember:
        async with database_session.begin():
            room = await database_session.get(Room, room_id, with_for_update=True)
            if room is None or room.status in ("FINISHED", "TERMINATED"):
                raise AuthError("room_unavailable")
            existing = await database_session.scalar(
                select(RoomMember).where(
                    RoomMember.room_id == room_id,
                    RoomMember.user_id == user_id,
                )
            )
            if existing is not None and existing.left_at is None:
                return existing
            if room.status != "WAITING" and payload.member_role != "SPECTATOR":
                raise AuthError("room_locked")
            if room.is_all_agent and payload.member_role == "DEBATER":
                raise AuthError("forbidden")
            await self._assert_no_active_participation(database_session, user_id=user_id)
            if payload.member_role == "DEBATER":
                await self._require_consent(
                    database_session,
                    user_id=user_id,
                    version=payload.human_participation_terms_version,
                )
            if payload.member_role == "SPECTATOR":
                await self._assert_spectator_capacity(database_session)
            if existing is None:
                member = RoomMember(
                    room_id=room_id, user_id=user_id, member_role=payload.member_role
                )
                database_session.add(member)
            else:
                member = existing
                member.member_role = payload.member_role
                member.left_at = None
                member.online = True
                member.ready = False
            room.sequence += 1
            await database_session.flush()
        return member

    async def select_seat(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        room_id: UUID,
        payload: SeatSelectRequest,
    ) -> Seat:
        async with database_session.begin():
            user = await database_session.get(User, user_id, with_for_update=True)
            if user is None or user.status != "ACTIVE":
                raise AuthError("not_authenticated")
            room = await database_session.get(Room, room_id, with_for_update=True)
            if room is None or room.status != "WAITING":
                raise AuthError("room_locked")
            if room.is_all_agent:
                raise AuthError("forbidden")
            member = await database_session.scalar(
                select(RoomMember).where(
                    RoomMember.room_id == room_id,
                    RoomMember.user_id == user_id,
                    RoomMember.left_at.is_(None),
                )
            )
            if member is None:
                raise AuthError("room_member_required")
            await self._require_consent(
                database_session, user_id=user_id, version=payload.human_participation_terms_version
            )
            seats = list(
                (
                    await database_session.scalars(
                        select(Seat)
                        .where(Seat.room_id == room_id)
                        .order_by(Seat.side, Seat.seat_no)
                        .with_for_update()
                    )
                ).all()
            )
            seat = next(
                (
                    item
                    for item in seats
                    if item.side == payload.side and item.seat_no == payload.seat_no
                ),
                None,
            )
            old_seat = next((item for item in seats if item.user_id == user_id), None)
            if seat is None:
                raise AuthError("seat_unavailable")
            if old_seat is seat:
                return seat
            if seat.occupant_type == "HUMAN":
                raise AuthError("seat_human_occupied")
            await self._cancel_user_seat_swaps(database_session, room_id=room_id, user_id=user_id)
            if seat.occupant_type == "AGENT" and seat.configured_agent_profile_id is None:
                seat.configured_agent_profile_id = seat.agent_profile_id
            if old_seat is not None:
                await self._restore_agent_or_empty(database_session, room=room, seat=old_seat)
            seat.user_id = user_id
            seat.agent_profile_id = None
            seat.occupant_type = "HUMAN"
            member.member_role = "DEBATER"
            latest_check = await database_session.scalar(
                select(DeviceCheck)
                .where(
                    DeviceCheck.room_id == room_id,
                    DeviceCheck.user_id == user_id,
                    DeviceCheck.status.in_(("PASS", "WARN")),
                    DeviceCheck.valid_until > datetime.now(UTC),
                )
                .order_by(DeviceCheck.check_version.desc())
                .limit(1)
            )
            if member.ready and latest_check is None:
                member.ready = False
            room.sequence += 1
            await database_session.flush()
        return seat

    async def change_role(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        room_id: UUID,
        payload: RoleChangeRequest,
    ) -> RoomMember:
        async with database_session.begin():
            room = await database_session.get(Room, room_id, with_for_update=True)
            member = await database_session.scalar(
                select(RoomMember)
                .where(
                    RoomMember.room_id == room_id,
                    RoomMember.user_id == user_id,
                    RoomMember.left_at.is_(None),
                )
                .with_for_update()
            )
            if room is None or room.status != "WAITING" or member is None:
                raise AuthError("room_locked")
            if member.member_role == "ORGANIZER":
                raise AuthError("forbidden")
            if member.member_role == payload.member_role:
                return member
            await self._cancel_user_seat_swaps(database_session, room_id=room_id, user_id=user_id)
            if room.is_all_agent and payload.member_role == "DEBATER":
                raise AuthError("forbidden")
            if payload.member_role == "DEBATER":
                await self._require_consent(
                    database_session,
                    user_id=user_id,
                    version=payload.human_participation_terms_version,
                )
            else:
                await self._assert_spectator_capacity(database_session)
                seat = await database_session.scalar(
                    select(Seat)
                    .where(Seat.room_id == room_id, Seat.user_id == user_id)
                    .with_for_update()
                )
                if seat is not None:
                    await self._restore_agent_or_empty(database_session, room=room, seat=seat)
                member.ready = False
            member.member_role = payload.member_role
            room.sequence += 1
            await database_session.flush()
        return member

    async def save_device_check(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        room_id: UUID,
        payload: DeviceCheckRequest,
    ) -> DeviceCheck:
        if payload.status == "FAIL" or (payload.status == "WARN" and not payload.warning_confirmed):
            raise AuthError("device_check_failed")
        current = datetime.now(UTC)
        async with database_session.begin():
            room = await database_session.get(Room, room_id, with_for_update=True)
            if room is None or room.status in ("FINISHED", "TERMINATED"):
                raise AuthError("room_unavailable")
            member = await database_session.scalar(
                select(RoomMember)
                .where(
                    RoomMember.room_id == room_id,
                    RoomMember.user_id == user_id,
                    RoomMember.left_at.is_(None),
                    RoomMember.member_role == "DEBATER",
                )
                .with_for_update()
            )
            if member is None:
                raise AuthError("room_member_required")
            latest_version = await database_session.scalar(
                select(func.max(DeviceCheck.check_version)).where(
                    DeviceCheck.room_id == room_id,
                    DeviceCheck.user_id == user_id,
                )
            )
            check = DeviceCheck(
                room_id=room_id,
                user_id=user_id,
                check_version=int(latest_version or 0) + 1,
                status=payload.status,
                details=payload.details,
                warning_confirmed_at=current if payload.warning_confirmed else None,
                checked_at=current,
                valid_until=current + DEVICE_CHECK_TTL,
            )
            database_session.add(check)
            member.ready = False
            room.sequence += 1
            await database_session.flush()
        return check

    async def ready(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        room_id: UUID,
        check_version: int,
    ) -> RoomMember:
        current = datetime.now(UTC)
        async with database_session.begin():
            room = await database_session.get(Room, room_id, with_for_update=True)
            member = await database_session.scalar(
                select(RoomMember)
                .where(
                    RoomMember.room_id == room_id,
                    RoomMember.user_id == user_id,
                    RoomMember.left_at.is_(None),
                    RoomMember.member_role == "DEBATER",
                )
                .with_for_update()
            )
            check = await database_session.scalar(
                select(DeviceCheck).where(
                    DeviceCheck.room_id == room_id,
                    DeviceCheck.user_id == user_id,
                    DeviceCheck.check_version == check_version,
                    DeviceCheck.valid_until > current,
                    DeviceCheck.status.in_(("PASS", "WARN")),
                )
            )
            if room is None or room.status in ("FINISHED", "TERMINATED"):
                raise AuthError("room_unavailable")
            if member is None or check is None:
                raise AuthError("device_check_required")
            if member.ready:
                return member
            member.ready = True
            room.sequence += 1
            await database_session.flush()
        return member

    async def invalidate_device_check(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        room_id: UUID,
    ) -> Room:
        current = datetime.now(UTC)
        async with database_session.begin():
            room = await database_session.get(Room, room_id, with_for_update=True)
            if room is None or room.status in ("FINISHED", "TERMINATED"):
                raise AuthError("room_unavailable")
            member = await database_session.scalar(
                select(RoomMember)
                .where(
                    RoomMember.room_id == room_id,
                    RoomMember.user_id == user_id,
                    RoomMember.left_at.is_(None),
                    RoomMember.member_role == "DEBATER",
                )
                .with_for_update()
            )
            if member is None:
                raise AuthError("room_member_required")
            latest = await database_session.scalar(
                select(DeviceCheck)
                .where(DeviceCheck.room_id == room_id, DeviceCheck.user_id == user_id)
                .order_by(DeviceCheck.check_version.desc())
                .limit(1)
                .with_for_update()
            )
            changed = member.ready
            member.ready = False
            if latest is not None and latest.valid_until > current:
                latest.valid_until = current
                changed = True
            if changed:
                room.sequence += 1
            await database_session.flush()
        return room

    async def start_room(
        self,
        database_session: AsyncSession,
        *,
        actor_user_id: UUID,
        actor_role: str,
        room_id: UUID,
        storage_path: str | None = None,
    ) -> Room:
        if storage_path is not None:
            ensure_storage_capacity(storage_path)
        current = datetime.now(UTC)
        async with database_session.begin():
            room = await database_session.get(Room, room_id, with_for_update=True)
            if room is None or room.status != "WAITING":
                raise AuthError("room_unavailable")
            if actor_role != "ADMIN" and room.organizer_user_id != actor_user_id:
                raise AuthError("forbidden")
            await database_session.execute(
                select(CapacityGuard).where(CapacityGuard.id == 1).with_for_update()
            )
            active_count = await database_session.scalar(
                select(func.count()).select_from(Room).where(Room.status.in_(ACTIVE_ROOM_STATUSES))
            )
            if int(active_count or 0) >= 5:
                raise AuthError("match_capacity_full")
            seats = list(
                (
                    await database_session.scalars(
                        select(Seat).where(Seat.room_id == room_id).with_for_update()
                    )
                ).all()
            )
            if not seats or any(seat.occupant_type == "EMPTY" for seat in seats):
                raise AuthError("room_seats_incomplete")
            seated_user_ids = {
                seat.user_id
                for seat in seats
                if seat.occupant_type == "HUMAN" and seat.user_id is not None
            }
            unseated_debater = await database_session.scalar(
                select(RoomMember.id)
                .where(
                    RoomMember.room_id == room_id,
                    RoomMember.member_role == "DEBATER",
                    RoomMember.left_at.is_(None),
                    RoomMember.user_id.not_in(seated_user_ids) if seated_user_ids else true(),
                )
                .limit(1)
            )
            if unseated_debater is not None:
                raise AuthError("room_debater_unseated")
            for seat in seats:
                if seat.occupant_type == "HUMAN":
                    assert seat.user_id is not None
                    member = await database_session.scalar(
                        select(RoomMember).where(
                            RoomMember.room_id == room_id,
                            RoomMember.user_id == seat.user_id,
                            RoomMember.left_at.is_(None),
                        )
                    )
                    check = await database_session.scalar(
                        select(DeviceCheck)
                        .where(
                            DeviceCheck.room_id == room_id,
                            DeviceCheck.user_id == seat.user_id,
                            DeviceCheck.valid_until > current,
                            DeviceCheck.status.in_(("PASS", "WARN")),
                        )
                        .order_by(DeviceCheck.check_version.desc())
                        .limit(1)
                    )
                    if member is None or not member.online or not member.ready or check is None:
                        raise AuthError("device_check_required")
                else:
                    assert seat.agent_profile_id is not None
                    agent = await database_session.get(AgentProfile, seat.agent_profile_id)
                    if agent is None or agent.status != "ENABLED":
                        raise AuthError("agent_unavailable")
                    model = await database_session.get(ModelProfile, agent.model_profile_id)
                    voice = await database_session.get(VoiceProfile, agent.voice_profile_id)
                    if (
                        model is None
                        or model.status != "ENABLED"
                        or voice is None
                        or voice.status != "ENABLED"
                    ):
                        raise AuthError("agent_unavailable")
                seat.locked_at = current
            agent_ids = [
                seat.agent_profile_id
                for seat in seats
                if seat.occupant_type == "AGENT" and seat.agent_profile_id is not None
            ]
            ensure_unique_agent_ids(agent_ids)
            room.status = "START_PENDING_RUNTIME"
            room.sequence += 1
            room.started_at = current
            await database_session.flush()
        return room

    async def leave_room(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        room_id: UUID,
    ) -> Room:
        current = datetime.now(UTC)
        async with database_session.begin():
            room = await database_session.get(Room, room_id, with_for_update=True)
            member = await database_session.scalar(
                select(RoomMember)
                .where(
                    RoomMember.room_id == room_id,
                    RoomMember.user_id == user_id,
                    RoomMember.left_at.is_(None),
                )
                .with_for_update()
            )
            if room is None:
                raise AuthError("room_unavailable")
            if member is None:
                previous_member = await database_session.scalar(
                    select(RoomMember).where(
                        RoomMember.room_id == room_id,
                        RoomMember.user_id == user_id,
                    )
                )
                if previous_member is not None and previous_member.left_at is not None:
                    return room
                raise AuthError("room_member_required")
            if room.status == "WAITING":
                await self._cancel_user_seat_swaps(
                    database_session, room_id=room_id, user_id=user_id
                )
                member.left_at = current
                member.online = False
                member.ready = False
                seat = await database_session.scalar(
                    select(Seat)
                    .where(Seat.room_id == room_id, Seat.user_id == user_id)
                    .with_for_update()
                )
                if seat is not None:
                    await self._restore_agent_or_empty(database_session, room=room, seat=seat)
                if room.organizer_user_id == user_id and not room.is_all_agent:
                    successor = await database_session.scalar(
                        select(RoomMember)
                        .where(
                            RoomMember.room_id == room_id,
                            RoomMember.left_at.is_(None),
                            RoomMember.online.is_(True),
                        )
                        .order_by(
                            (RoomMember.member_role == "DEBATER").desc(),
                            RoomMember.joined_at,
                        )
                        .limit(1)
                    )
                    if successor is None:
                        room.status = "TERMINATED"
                        room.ended_at = current
                    else:
                        room.organizer_user_id = successor.user_id
            elif member.member_role == "SPECTATOR":
                member.left_at = current
                member.online = False
            else:
                member.online = False
            room.sequence += 1
            await database_session.flush()
        return room

    async def terminate_room(
        self,
        database_session: AsyncSession,
        *,
        actor_user_id: UUID,
        actor_role: str,
        room_id: UUID,
    ) -> Room:
        async with database_session.begin():
            room = await database_session.get(Room, room_id, with_for_update=True)
            if room is None:
                raise AuthError("room_unavailable")
            if actor_role != "ADMIN" and room.organizer_user_id != actor_user_id:
                raise AuthError("forbidden")
            if room.status not in ("FINISHED", "TERMINATED"):
                room.status = "TERMINATED"
                room.ended_at = datetime.now(UTC)
                room.sequence += 1
                await database_session.flush()
        return room

    async def snapshot(
        self, database_session: AsyncSession, *, room_id: UUID
    ) -> tuple[Room, list[RoomMember], list[Seat]]:
        room = await database_session.get(Room, room_id)
        if room is None:
            raise AuthError("room_unavailable")
        members = list(
            (
                await database_session.scalars(
                    select(RoomMember)
                    .where(RoomMember.room_id == room_id, RoomMember.left_at.is_(None))
                    .order_by(RoomMember.joined_at)
                )
            ).all()
        )
        seats = list(
            (
                await database_session.scalars(
                    select(Seat).where(Seat.room_id == room_id).order_by(Seat.side, Seat.seat_no)
                )
            ).all()
        )
        return room, members, seats

    async def viewer_context(
        self, database_session: AsyncSession, *, room_id: UUID, user_id: UUID
    ) -> RoomViewerContext:
        member = await database_session.scalar(
            select(RoomMember).where(
                RoomMember.room_id == room_id,
                RoomMember.user_id == user_id,
            )
        )
        latest_device_check = await database_session.scalar(
            select(DeviceCheck)
            .where(DeviceCheck.room_id == room_id, DeviceCheck.user_id == user_id)
            .order_by(DeviceCheck.checked_at.desc(), DeviceCheck.check_version.desc())
            .limit(1)
        )
        match_id = await database_session.scalar(
            select(Match.id).where(Match.room_id == room_id).limit(1)
        )
        return RoomViewerContext(
            member=member,
            latest_device_check=latest_device_check,
            match_id=match_id,
        )

    async def lobby(self, database_session: AsyncSession) -> list[Room]:
        return list(
            (
                await database_session.scalars(
                    select(Room)
                    .where(Room.status.not_in(("FINISHED", "TERMINATED")))
                    .order_by(Room.created_at.desc())
                )
            ).all()
        )

    async def active_spectator_count(self, database_session: AsyncSession) -> int:
        count = await database_session.scalar(
            select(func.count())
            .select_from(RoomMember)
            .join(Room, Room.id == RoomMember.room_id)
            .where(
                RoomMember.member_role == "SPECTATOR",
                RoomMember.left_at.is_(None),
                Room.status.not_in(("FINISHED", "TERMINATED")),
            )
        )
        return int(count or 0)


__all__ = ["RoomService", "RoomViewerContext", "normalize_room_code"]

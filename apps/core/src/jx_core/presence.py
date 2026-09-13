"""Combine business WebSocket leases and LiveKit media presence."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from time import time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .matches.domain import MatchCommand, MatchDomainError
from .matches.service import MatchRuntimeManager
from .models import Match, RoomConnectionLease, RoomMember


class MatchPresenceCoordinator:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        manager: MatchRuntimeManager,
        *,
        wall_clock: Callable[[], float] = time,
    ) -> None:
        self._session_factory = session_factory
        self._manager = manager
        self._wall_clock = wall_clock
        self._lock = asyncio.Lock()
        self._websocket: dict[tuple[UUID, UUID, int], set[UUID]] = defaultdict(set)
        self._media: dict[tuple[UUID, UUID, int], set[str]] = defaultdict(set)
        self._seen_events: dict[UUID, set[str]] = defaultdict(set)
        self._online_state: dict[tuple[UUID, UUID], bool] = {}
        self._announced_epoch: dict[tuple[UUID, UUID], int] = {}
        self._transition_sequence = 0

    def _complete_epochs(self, match_id: UUID, user_id: UUID, active_epochs: set[int]) -> set[int]:
        return {
            epoch
            for epoch in active_epochs
            if self._websocket.get((match_id, user_id, epoch))
            and self._media.get((match_id, user_id, epoch))
        }

    def _offline_timestamp_ms(self) -> int:
        """Use the same injectable wall-clock boundary as MatchActor."""
        return int(self._wall_clock() * 1000)

    async def websocket_joined(
        self, match_id: UUID, user_id: UUID, epoch: int, connection_id: UUID
    ) -> None:
        async with self._lock:
            key = (match_id, user_id, epoch)
            previous = set(self._websocket.get(key, ()))
            self._websocket[key].add(connection_id)
            try:
                await self._transition(match_id, user_id, epoch)
            except Exception:
                self._restore_members(self._websocket, key, previous)
                raise

    async def websocket_left(
        self, match_id: UUID, user_id: UUID, epoch: int, connection_id: UUID
    ) -> None:
        async with self._lock:
            key = (match_id, user_id, epoch)
            previous = set(self._websocket.get(key, ()))
            leases = self._websocket.get(key)
            if leases:
                leases.discard(connection_id)
                if not leases:
                    self._websocket.pop(key, None)
            try:
                await self._transition(match_id, user_id, epoch)
            except Exception:
                self._restore_members(self._websocket, key, previous)
                raise

    async def media_event(
        self,
        *,
        match_id: UUID,
        user_id: UUID,
        epoch: int,
        sid: str,
        event_id: str,
        joined: bool,
    ) -> None:
        async with self._lock:
            key = (match_id, user_id, epoch)
            previous = set(self._media.get(key, ()))
            if not self._apply_media_event(
                match_id=match_id,
                user_id=user_id,
                epoch=epoch,
                sid=sid,
                event_id=event_id,
                joined=joined,
            ):
                return
            try:
                await self._transition(match_id, user_id, epoch)
            except Exception:
                self._restore_members(self._media, key, previous)
                self._seen_events[match_id].discard(event_id)
                raise

    @staticmethod
    def _restore_members(
        collection: dict[tuple[UUID, UUID, int], set[UUID]]
        | dict[tuple[UUID, UUID, int], set[str]],
        key: tuple[UUID, UUID, int],
        previous: set[UUID] | set[str],
    ) -> None:
        if previous:
            collection[key] = previous  # type: ignore[assignment]
        else:
            collection.pop(key, None)

    async def revoke_user(self, user_id: UUID) -> None:
        """Drop in-memory media/socket presence after auth invalidation."""
        async with self._lock:
            subjects = {
                (match_id, subject_user_id)
                for match_id, subject_user_id, _ in self._websocket
                if subject_user_id == user_id
            }
            subjects.update(
                (match_id, subject_user_id)
                for match_id, subject_user_id, _ in self._media
                if subject_user_id == user_id
            )
            for key in tuple(self._websocket):
                if key[1] == user_id:
                    self._websocket.pop(key, None)
            for key in tuple(self._media):
                if key[1] == user_id:
                    self._media.pop(key, None)
            for match_id, subject_user_id in subjects:
                await self._transition(match_id, subject_user_id, 0)

    def _apply_media_event(
        self,
        *,
        match_id: UUID,
        user_id: UUID,
        epoch: int,
        sid: str,
        event_id: str,
        joined: bool,
    ) -> bool:
        if event_id in self._seen_events[match_id]:
            return False
        self._seen_events[match_id].add(event_id)
        if len(self._seen_events[match_id]) > 2048:
            self._seen_events[match_id].pop()
        key = (match_id, user_id, epoch)
        if joined:
            self._media[key].add(sid)
        else:
            self._media[key].discard(sid)
            if not self._media[key]:
                self._media.pop(key, None)
        return True

    async def _transition(self, match_id: UUID, user_id: UUID, epoch: int) -> None:
        subject = (match_id, user_id)
        active_epochs: set[int] = set()
        async with self._session_factory() as session:
            async with session.begin():
                room_id = await session.scalar(select(Match.room_id).where(Match.id == match_id))
                if room_id is not None:
                    active_epochs = set(
                        (
                            await session.scalars(
                                select(RoomConnectionLease.connection_epoch).where(
                                    RoomConnectionLease.user_id == user_id,
                                    RoomConnectionLease.room_id == room_id,
                                )
                            )
                        ).all()
                    )
                complete_epochs = self._complete_epochs(match_id, user_id, active_epochs)
                online = bool(complete_epochs)
                if room_id is not None:
                    member = await session.scalar(
                        select(RoomMember)
                        .where(
                            RoomMember.room_id == room_id,
                            RoomMember.user_id == user_id,
                            RoomMember.left_at.is_(None),
                        )
                        .with_for_update()
                    )
                    if member is not None:
                        member.online = online
        previous_online = self._online_state.get(subject)
        previous_epoch = self._announced_epoch.get(subject, 0)
        event_epoch = (
            max(complete_epochs) if online else max({epoch, previous_epoch, *active_epochs})
        )
        if previous_online is online and event_epoch <= previous_epoch:
            return
        self._transition_sequence += 1
        # The actor's epoch filter makes late events harmless; the sequence
        # keeps repeated transitions within one epoch independently idempotent.
        command_type = "member.online" if online else "member.offline"
        message_id = (
            f"presence:{command_type}:{match_id}:{user_id}:"
            f"{event_epoch}:{self._transition_sequence}"
        )
        try:
            await self._manager.submit(
                match_id,
                MatchCommand(
                    type=command_type,
                    message_id=message_id,
                    actor_user_id=user_id,
                    payload={
                        "connection_epoch": event_epoch,
                        "offline_since_ms": self._offline_timestamp_ms(),
                    },
                ),
            )
        except MatchDomainError:
            pass
        self._online_state[subject] = online
        self._announced_epoch[subject] = max(previous_epoch, event_epoch)


__all__ = ["MatchPresenceCoordinator"]

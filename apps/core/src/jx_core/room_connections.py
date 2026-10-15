"""PostgreSQL-backed single active room connection lease primitive."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Match, Room, RoomConnection, RoomConnectionLease


@asynccontextmanager
async def _transaction(database_session: AsyncSession) -> AsyncGenerator[None, None]:
    """Run one lease mutation atomically with or without a caller transaction.

    SQLAlchemy starts an implicit transaction for a read.  WebSocket cleanup
    can therefore arrive after a caller has inspected the current lease and
    still needs to invoke this service on the same session.  Starting another
    top-level transaction would raise ``InvalidRequestError``; a savepoint
    preserves the caller's transaction while retaining the service's atomic
    boundary.
    """

    if database_session.in_transaction():
        async with database_session.begin_nested():
            yield
    else:
        async with database_session.begin():
            yield


@dataclass(frozen=True, slots=True)
class ConnectionLease:
    user_id: UUID
    room_id: UUID
    connection_id: UUID
    connection_epoch: int
    replaced_connection_id: UUID | None = None
    connected_at_ms: int = 0


ACTIVE_LEASE_CLEAR_BATCH_SIZE = 500


async def reconcile_terminal_room_end_times(database_session: AsyncSession) -> int:
    """Backfill missing terminal room timestamps without deleting history.

    Older MatchActor commits updated the room status but omitted ``ended_at``.
    Use the durable match timestamp when available so room and match timelines
    remain comparable; rooms without a match receive one UTC repair timestamp.
    The operation is idempotent and only touches terminal rooms with a null
    timestamp.
    """

    async with database_session.begin():
        # Use one set-based UPDATE instead of materialising every historical
        # terminal Room as an ORM object during Core startup.  PostgreSQL takes
        # row locks for the UPDATE itself; the correlated Match timestamp keeps
        # the old "prefer match end" semantics and the UTC value covers rooms
        # that have no match row.
        match_ended_at = (
            select(Match.ended_at)
            .where(Match.room_id == Room.id)
            .limit(1)
            .scalar_subquery()
        )
        result = await database_session.execute(
            update(Room)
            .where(
                Room.status.in_(("FINISHED", "TERMINATED")),
                Room.ended_at.is_(None),
            )
            .values(ended_at=func.coalesce(match_ended_at, datetime.now(UTC)))
        )
    return int(cast(Any, result).rowcount or 0)


class RoomConnectionService:
    """Own connection replacement and stale-release semantics in one transaction."""

    async def acquire(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        room_id: UUID,
        connection_id: UUID | None = None,
        now: datetime | None = None,
    ) -> ConnectionLease:
        current = now or datetime.now(UTC)
        new_connection_id = connection_id or uuid4()
        async with _transaction(database_session):
            existing = (
                await database_session.execute(
                    select(RoomConnection)
                    .where(RoomConnection.user_id == user_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            epoch = 1 if existing is None else existing.connection_epoch + 1
            replaced = None if existing is None else existing.connection_id
            # A browser that disappears without running the WebSocket finally
            # block leaves its lease behind.  The summary row is the sole
            # authority for the current connection, so old lease rows cannot
            # become active again and should be removed at the next atomic
            # replacement.  Keeping them would make every reconnect add an
            # unbounded row and would force presence transitions to scan stale
            # epochs forever.
            await database_session.execute(
                delete(RoomConnectionLease).where(RoomConnectionLease.user_id == user_id)
            )
            database_session.add(
                RoomConnectionLease(
                    user_id=user_id,
                    room_id=room_id,
                    connection_id=new_connection_id,
                    connection_epoch=epoch,
                    connected_at=current,
                    last_seen_at=current,
                )
            )
            if existing is None:
                database_session.add(
                    RoomConnection(
                        user_id=user_id,
                        room_id=room_id,
                        connection_id=new_connection_id,
                        connection_epoch=epoch,
                        connected_at=current,
                        last_seen_at=current,
                    )
                )
            else:
                existing.room_id = room_id
                existing.connection_id = new_connection_id
                existing.connection_epoch = epoch
                existing.connected_at = current
                existing.last_seen_at = current
            await database_session.flush()
        return ConnectionLease(
            user_id,
            room_id,
            new_connection_id,
            epoch,
            replaced,
            int(current.timestamp() * 1000),
        )

    async def heartbeat(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        connection_id: UUID,
        connection_epoch: int,
        now: datetime | None = None,
    ) -> bool:
        current = now or datetime.now(UTC)
        async with _transaction(database_session):
            result = await database_session.execute(
                update(RoomConnectionLease)
                .where(
                    RoomConnectionLease.user_id == user_id,
                    RoomConnectionLease.connection_id == connection_id,
                    RoomConnectionLease.connection_epoch == connection_epoch,
                )
                .values(last_seen_at=current)
                .returning(RoomConnectionLease.user_id)
            )
            if result.scalar_one_or_none() is None:
                return False
            await database_session.execute(
                update(RoomConnection)
                .where(
                    RoomConnection.user_id == user_id,
                    RoomConnection.connection_id == connection_id,
                    RoomConnection.connection_epoch == connection_epoch,
                )
                .values(last_seen_at=current)
            )
        return True

    async def release(
        self,
        database_session: AsyncSession,
        *,
        user_id: UUID,
        connection_id: UUID,
        connection_epoch: int,
    ) -> bool:
        async with _transaction(database_session):
            current = await database_session.scalar(
                select(RoomConnection).where(RoomConnection.user_id == user_id).with_for_update()
            )
            lease = await database_session.scalar(
                select(RoomConnectionLease)
                .where(
                    RoomConnectionLease.user_id == user_id,
                    RoomConnectionLease.connection_id == connection_id,
                    RoomConnectionLease.connection_epoch == connection_epoch,
                )
                .with_for_update()
            )
            if lease is None:
                return False
            is_current = current is not None and (
                current.connection_id == connection_id
                and current.connection_epoch == connection_epoch
            )
            result = await database_session.execute(
                delete(RoomConnectionLease)
                .where(
                    RoomConnectionLease.user_id == user_id,
                    RoomConnectionLease.connection_id == connection_id,
                    RoomConnectionLease.connection_epoch == connection_epoch,
                )
                .returning(RoomConnectionLease.id)
            )
            released = result.scalar_one_or_none() is not None
            if not released:
                return False
            # The summary remains a high-water row. Only releasing that exact
            # connection may tell the caller that the current socket closed.
            return is_current

    async def revoke_for_user(self, database_session: AsyncSession, *, user_id: UUID) -> None:
        async with _transaction(database_session):
            await database_session.execute(
                delete(RoomConnectionLease).where(RoomConnectionLease.user_id == user_id)
            )

    async def clear_active_leases(
        self,
        database_session: AsyncSession,
        *,
        batch_size: int = ACTIVE_LEASE_CLEAR_BATCH_SIZE,
    ) -> list[tuple[UUID, UUID]]:
        """Drop one bounded batch of leases left by a previous Core process.

        The caller repeats until an empty batch is returned.  Returning pairs
        preserves the existing RoomMember offline transition, while the
        explicit limit prevents a crashed process with a large lease backlog
        from materialising the entire table during startup.
        """
        if batch_size <= 0:
            raise ValueError("batch_size_must_be_positive")
        async with _transaction(database_session):
            rows = list(
                (
                    await database_session.execute(
                        select(
                            RoomConnectionLease.id,
                            RoomConnectionLease.user_id,
                            RoomConnectionLease.room_id,
                        )
                        .order_by(RoomConnectionLease.id)
                        .limit(batch_size)
                    )
                ).all()
            )
            if not rows:
                return []
            lease_ids = [row[0] for row in rows]
            await database_session.execute(
                delete(RoomConnectionLease).where(RoomConnectionLease.id.in_(lease_ids))
            )
        return [(row[1], row[2]) for row in rows]


__all__ = [
    "ConnectionLease",
    "RoomConnectionService",
    "ACTIVE_LEASE_CLEAR_BATCH_SIZE",
    "reconcile_terminal_room_end_times",
]

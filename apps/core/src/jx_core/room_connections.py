"""PostgreSQL-backed single active room connection lease primitive."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import RoomConnection, RoomConnectionLease


@dataclass(frozen=True, slots=True)
class ConnectionLease:
    user_id: UUID
    room_id: UUID
    connection_id: UUID
    connection_epoch: int
    replaced_connection_id: UUID | None = None
    connected_at_ms: int = 0


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
        async with database_session.begin():
            existing = (
                await database_session.execute(
                    select(RoomConnection)
                    .where(RoomConnection.user_id == user_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            epoch = 1 if existing is None else existing.connection_epoch + 1
            replaced = None if existing is None else existing.connection_id
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
        async with database_session.begin():
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
        async with database_session.begin():
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
        async with database_session.begin():
            await database_session.execute(
                delete(RoomConnectionLease).where(RoomConnectionLease.user_id == user_id)
            )

    async def clear_active_leases(self, database_session: AsyncSession) -> list[tuple[UUID, UUID]]:
        """Drop leases left by a previous Core process while keeping epochs."""
        async with database_session.begin():
            rows = list(
                (
                    await database_session.execute(
                        select(RoomConnectionLease.user_id, RoomConnectionLease.room_id)
                    )
                ).all()
            )
            await database_session.execute(delete(RoomConnectionLease))
        return [(row[0], row[1]) for row in rows]


__all__ = ["ConnectionLease", "RoomConnectionService"]

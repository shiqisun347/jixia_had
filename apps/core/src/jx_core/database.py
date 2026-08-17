"""Database connectivity used by the foundation health boundary."""

from __future__ import annotations

import asyncio
import math

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DATABASE_CONNECT_TIMEOUT_SECONDS = 10.0
DATABASE_READY_TIMEOUT_SECONDS = 2.0


class Database:
    """Thin async SQLAlchemy adapter; no business repositories belong here."""

    def __init__(
        self,
        database_url: str,
        *,
        connect_timeout_seconds: float = DATABASE_CONNECT_TIMEOUT_SECONDS,
        ready_timeout_seconds: float = DATABASE_READY_TIMEOUT_SECONDS,
    ) -> None:
        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive")
        if ready_timeout_seconds <= 0:
            raise ValueError("ready_timeout_seconds must be positive")
        self._ready_timeout_seconds = ready_timeout_seconds
        self._engine: AsyncEngine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=0,
            pool_timeout=connect_timeout_seconds,
            connect_args={
                # psycopg/libpq requires whole seconds.  The surrounding
                # asyncio timeout remains the authoritative upper bound.
                "connect_timeout": max(1, math.ceil(connect_timeout_seconds)),
            },
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Return the shared factory used by business repositories."""

        return self._session_factory

    async def ping(self) -> bool:
        """Run the one permitted foundation readiness query."""

        async with asyncio.timeout(self._ready_timeout_seconds):
            async with self._engine.connect() as connection:
                result = await connection.scalar(text("SELECT 1"))
        return result == 1

    async def dispose(self) -> None:
        await self._engine.dispose()

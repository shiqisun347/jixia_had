"""Readiness-only database adapter for the foundation job process."""

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
DATABASE_STARTUP_TIMEOUT_SECONDS = 10.0


class Database:
    def __init__(
        self,
        database_url: str,
        *,
        connect_timeout_seconds: float = DATABASE_CONNECT_TIMEOUT_SECONDS,
        startup_timeout_seconds: float = DATABASE_STARTUP_TIMEOUT_SECONDS,
    ) -> None:
        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive")
        if startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive")
        self._startup_timeout_seconds = startup_timeout_seconds
        self._engine: AsyncEngine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=0,
            pool_timeout=connect_timeout_seconds,
            connect_args={
                "connect_timeout": max(1, math.ceil(connect_timeout_seconds)),
            },
        )
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    async def ping(self) -> bool:
        async with asyncio.timeout(self._startup_timeout_seconds):
            async with self._engine.connect() as connection:
                result = await connection.scalar(text("SELECT 1"))
        return result == 1

    async def dispose(self) -> None:
        await self._engine.dispose()

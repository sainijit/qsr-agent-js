"""Database access. All tenant data access MUST go through `tenant_session`,
which sets `app.current_tenant` for the transaction so Postgres RLS applies."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def make_sessionmaker(database_url: str, pool_size: int = 10) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(database_url, pool_size=pool_size, max_overflow=10, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def tenant_session(sm: async_sessionmaker[AsyncSession], tenant_id: str) -> AsyncIterator[AsyncSession]:
    uuid.UUID(tenant_id)  # reject anything that is not a UUID before it reaches SQL
    async with sm() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tenant_id}
            )
            yield session


async def db_ping(sm: async_sessionmaker[AsyncSession]) -> bool:
    try:
        async with sm() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False

import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

URL = os.environ["MIGRATION_DATABASE_URL"]


def _run(connection):
    context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


async def main():
    engine = create_async_engine(URL)
    async with engine.connect() as conn:
        await conn.run_sync(_run)
        await conn.commit()
    await engine.dispose()


asyncio.run(main())
